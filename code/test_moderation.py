"""Violation handling, release and transactional failure regressions."""
from copy import deepcopy
from html.parser import HTMLParser
from unittest.mock import patch

from app import create_app
from storage import load_data, transaction
from test_app import MarketCase


class HiddenFields(HTMLParser):
    """Read exactly the snapshot shown on the actual administrator page."""

    def __init__(self, body):
        super().__init__()
        self.fields = {}
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'input' and values.get('type') == 'hidden':
            self.fields[values['name']] = values['value']


class ModerationTest(MarketCase):
    """Use real routes and temporary JSON without touching live data or uploads."""

    def prepare(self, state='waiting'):
        """Create an effective or completed trade and optional termination request."""
        product_id, reservation = self.waiting_reservation()
        reservation_id = reservation['id']
        if state != 'waiting':
            self.login('user', 'user123')
            self.client.post('/confirm/' + reservation_id)
            self.client.post('/reservations/' + reservation_id + '/termination',
                             data={'reason': '普通申请'})
        if state == 'completed':
            self.login('buyer', 'buyer123')
            self.client.post('/complete/' + reservation_id)
        with transaction(self.path) as data:
            data['users'] = [{'username': 'user', 'contact_info': 'SELLER_PRIVATE'},
                             {'username': 'buyer', 'contact_info': 'BUYER_PRIVATE'}]
        self.login('admin', 'admin123')
        return product_id, reservation_id

    def form(self, product_id):
        """Open the protected preview and capture optimistic concurrency fields."""
        page = self.client.get('/moderation/' + product_id)
        self.assertEqual(page.status_code, 200)
        return {**HiddenFields(page.get_data(as_text=True)).fields,
                'reason': '核实违反校园规定', 'action': 'restrict'}

    def test_effective_reservations_and_pending_applications_terminated(self):
        """Both effective states are terminated with restriction, history and notifications."""
        for state in ('waiting', 'confirmed'):
            with self.subTest(state=state):
                product_id, reservation_id = self.prepare(state)
                fields = self.form(product_id)
                self.assertEqual(self.client.post('/moderation/' + product_id,
                                                  data=fields).status_code, 302)
                data = load_data(self.path)
                product = next(item for item in data['products'] if item['id'] == product_id)
                reservation = next(item for item in data['reservations']
                                   if item['id'] == reservation_id)
                self.assertEqual(product['status'], 'violation')
                self.assertTrue(product['violation_active'])
                self.assertEqual(reservation['status'], 'terminated')
                for application in data['termination_requests']:
                    if application['reservation_id'] == reservation_id:
                        self.assertEqual(application['status'], 'closed')
                event = data['moderation_events'][-1]
                self.assertEqual(event['before']['reservations'][0]['status'], state)
                self.assertEqual(len([item for item in data['notifications']
                                      if item.get('moderation_id') == event['id']]), 2)
                self.assertEqual(self.client.get('/products/' + product_id).status_code, 404)

    def test_completed_history_unchanged_and_restriction_persistent(self):
        """Completed records remain terminal even after restriction and release."""
        product_id, _ = self.prepare('completed')
        before = deepcopy(load_data(self.path))
        self.client.post('/moderation/' + product_id, data=self.form(product_id))
        data = load_data(self.path)
        self.assertEqual(data['reservations'], before['reservations'])
        self.assertEqual(data['termination_requests'], before['termination_requests'])
        self.assertEqual(data['products'][0]['status'], 'completed')
        self.assertTrue(data['products'][0]['violation_active'])
        fields = self.form(product_id)
        self.client.post('/moderation/' + product_id, data={**fields, 'action': 'release'})
        self.assertEqual(load_data(self.path)['products'][0]['status'], 'completed')
        self.assertEqual(load_data(self.path)['reservations'], before['reservations'])
        restarted = create_app(self.path, notification_worker=False).test_client()
        self.assertEqual(restarted.get('/products/' + product_id).status_code, 404)

    def test_release_stays_hidden_until_fresh_review(self):
        """Release never publishes, and old public image links stay inaccessible."""
        product_id = self.publish()
        self.login('admin', 'admin123')
        image = load_data(self.path)['products'][0]['cover']
        route = '/moderation/' + product_id
        self.client.post(route, data=self.form(product_id))
        self.login('user', 'user123')
        self.assertEqual(self.client.get('/edit/' + product_id).status_code, 409)
        self.login('admin', 'admin123')
        self.client.post(route, data={**self.form(product_id), 'action': 'release'})
        self.assertEqual(load_data(self.path)['products'][0]['status'], 'off_shelf')
        self.assertEqual(self.client.get('/products/' + product_id).status_code, 404)
        self.client.post('/logout')
        self.assertEqual(self.client.get(f'/products/{product_id}/images/{image}').status_code, 404)
        self.login('user', 'user123')
        self.assertEqual(self.client.post('/edit/' + product_id, data={
            'title': '重新送审', 'description': '修正后商品', 'delivery_location': '东门',
            'price': '12.50', 'action': 'resubmit'}).status_code, 302)
        self.assertEqual(self.client.get('/products/' + product_id).status_code, 404)
        self.login('admin', 'admin123')
        self.review('/review/' + product_id, data={'decision': 'approved'})
        self.assertEqual(self.client.get('/products/' + product_id).status_code, 200)

    def test_stale_product_and_changed_linked_reservation_rejected(self):
        """Both ordinary completion and linked-data changes invalidate the preview."""
        product_id, reservation_id = self.prepare('confirmed')
        fields = self.form(product_id)
        with transaction(self.path) as data:
            data['reservations'][0]['price'] = '13.00'
        original = self.business_bytes()
        self.assertEqual(self.client.post('/moderation/' + product_id, data=fields).
                         status_code, 409)
        self.assertEqual(self.business_bytes(), original)
        fields = self.form(product_id)
        self.login('buyer', 'buyer123')
        self.client.post('/complete/' + reservation_id)
        original = self.business_bytes()
        self.login('admin', 'admin123')
        self.assertEqual(self.client.post('/moderation/' + product_id, data=fields).
                         status_code, 409)
        self.assertEqual(self.business_bytes(), original)

    def test_permissions_contacts_reasons_and_terminal_contact_scope(self):
        """Contacts are available only before handling eligible transactions."""
        product_id, _ = self.prepare('confirmed')
        route = '/moderation/' + product_id
        fields = self.form(product_id)
        page = self.client.get(route)
        self.assertIn('no-store', page.headers['Cache-Control'])
        self.assertIn('SELLER_PRIVATE', page.get_data(as_text=True))
        for unrelated in ('/', '/moderation', '/admin', '/reviews', '/terminations'):
            self.assertNotIn('PRIVATE', self.client.get(unrelated).get_data(as_text=True))
        original = self.business_bytes()
        for changes in ({'reason': ''}, {'action': 'invalid'}, {'action': 'release'}):
            self.assertIn(self.client.post(route, data={**fields, **changes}).status_code,
                          (400, 409))
            self.assertEqual(self.business_bytes(), original)
        for user in ('user', 'buyer', 'other'):
            self.login(user, user + '123')
            self.assertEqual(self.client.get(route).status_code, 403)
            self.assertEqual(self.client.post(route, data=fields).status_code, 403)
        self.client.post('/logout')
        self.assertEqual(self.client.get(route).status_code, 403)
        self.login('admin', 'admin123')
        self.client.post(route, data=fields)
        self.assertNotIn('PRIVATE', self.client.get(route).get_data(as_text=True))
        self.client.post(route, data={**self.form(product_id), 'action': 'release'})
        self.assertNotIn('PRIVATE', self.client.get(route).get_data(as_text=True))

    def test_failed_atomic_save_preserves_all_records(self):
        """Restrict and release failures leave trade, applications, logs and queues intact."""
        product_id, _ = self.prepare('confirmed')
        route = '/moderation/' + product_id
        for action in ('restrict', 'release'):
            fields = {**self.form(product_id), 'action': action}
            original = self.business_bytes()
            with patch('storage.os.replace', side_effect=OSError('injected failure')):
                with self.assertRaises(OSError):
                    self.client.post(route, data=fields)
            self.assertEqual(self.business_bytes(), original)
            if action == 'restrict':
                self.client.post(route, data=fields)
