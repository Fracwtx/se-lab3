"""Termination invariants, stale handling pages and contact scope regressions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from storage import load_data, transaction
from test_app import MarketCase


class TerminationTest(MarketCase):
    """Exercise processing through real Flask routes and isolated JSON files."""

    def setUp(self):
        super().setUp()
        self.product_id, reservation = self.waiting_reservation()
        self.reservation_id = reservation['id']
        self.login('user', 'user123')
        self.client.post('/confirm/' + self.reservation_id)
        with transaction(self.path) as data:
            data['users'] = [{'username': 'user', 'contact_info': 'SELLER_PRIVATE'},
                             {'username': 'buyer', 'contact_info': 'BUYER_PRIVATE'}]
        self.request_url = '/reservations/' + self.reservation_id + '/termination'
        self.assertEqual(self.client.post(self.request_url, data={'reason': '协商终止'}).
                         status_code, 302)
        self.application_id = load_data(self.path)['termination_requests'][0]['id']
        self.url = '/terminations/' + self.application_id
        self.login('admin', 'admin123')
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.fields = {'expected_version': str(load_data(self.path)['products'][0]['version']),
                       'decision': 'approved', 'buyer_opinion': '同意终止',
                       'seller_opinion': '同意终止', 'buyer_agreed': 'yes',
                       'seller_agreed': 'yes', 'reason': '分别联系双方核实同意'}

    def test_approve_atomically_and_disable_contacts(self):
        """Approval persists all state, audit and notification records together."""
        self.assertEqual(self.client.post(self.url, data=self.fields).status_code, 302)
        data = load_data(self.path)
        self.assertEqual(data['products'][0]['status'], 'off_shelf')
        self.assertEqual(data['reservations'][0]['status'], 'terminated')
        self.assertEqual(data['termination_requests'][0]['status'], 'approved')
        self.assertEqual(len(data['termination_events']), 2)
        self.assertEqual(len([item for item in data['notifications']
                              if item.get('application_id') == self.application_id]), 4)
        original = self.business_bytes()
        self.assertEqual(self.client.post(self.url, data=self.fields).status_code, 409)
        self.assertEqual(self.client.get(self.url).status_code, 409)
        self.assertEqual(self.business_bytes(), original)
        for user in ('user', 'buyer'):
            self.login(user, user + '123')
            self.assertEqual(self.client.get('/reservations/' + self.reservation_id +
                                             '/contact').status_code, 403)

    def test_completion_closes_application_and_rejects_stale_approval(self):
        """Opening an admin page never freezes the buyer's right to finish."""
        self.login('buyer', 'buyer123')
        self.assertEqual(self.client.post('/complete/' + self.reservation_id).status_code, 302)
        original = self.business_bytes()
        self.login('admin', 'admin123')
        self.assertEqual(self.client.post(self.url, data=self.fields).status_code, 409)
        self.assertEqual(self.client.get(self.url).status_code, 409)
        self.assertEqual(self.business_bytes(), original)
        data = load_data(self.path)
        self.assertEqual(data['products'][0]['status'], 'completed')
        self.assertEqual(data['reservations'][0]['status'], 'completed')
        self.assertEqual(data['termination_requests'][0]['status'], 'closed')

    def test_consent_validation_rejection_and_repeat_request(self):
        """Both recorded opinions and explicit consent are needed for approval."""
        original = self.business_bytes()
        for changes in ({'buyer_agreed': ''}, {'seller_agreed': ''}, {'reason': ''},
                        {'buyer_opinion': ''}, {'decision': 'invalid'},
                        {'expected_version': '0'}):
            response = self.client.post(self.url, data={**self.fields, **changes})
            self.assertIn(response.status_code, (400, 409))
            self.assertEqual(self.business_bytes(), original)
        self.assertEqual(self.client.post(self.url, data={**self.fields, 'decision': 'rejected',
                         'buyer_agreed': '', 'buyer_opinion': '不同意'}).status_code, 302)
        data = load_data(self.path)
        self.assertEqual(data['products'][0]['status'], 'confirmed')
        self.assertEqual(data['reservations'][0]['status'], 'confirmed')
        self.assertEqual(data['termination_requests'][0]['status'], 'rejected')
        self.login('buyer', 'buyer123')
        for _ in range(2):
            self.assertEqual(self.client.post(self.request_url, data={'reason': '重新协商'}).
                             status_code, 302)
        self.assertEqual(len(load_data(self.path)['termination_requests']), 2)

    def test_contacts_only_on_active_admin_handling_page(self):
        """No admin blanket permission, unrelated users or public page leaks."""
        page = self.client.get(self.url)
        self.assertIn('no-store', page.headers['Cache-Control'])
        self.assertIn('SELLER_PRIVATE', page.get_data(as_text=True))
        self.assertIn('BUYER_PRIVATE', page.get_data(as_text=True))
        for route in ('/', '/terminations', '/admin', '/reviews'):
            self.assertNotIn('PRIVATE', self.client.get(route).get_data(as_text=True))
        self.assertEqual(self.client.get('/reservations/' + self.reservation_id +
                                         '/contact').status_code, 403)
        for user in ('user', 'buyer', 'other'):
            self.login(user, user + '123')
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.post(self.url, data=self.fields).status_code, 403)
        self.assertEqual(self.client.post(self.request_url, data={'reason': '无权'}).
                         status_code, 403)
        self.client.post('/logout')
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_save_failure_rolls_back_every_record(self):
        """No partial states, processing history or notification intents on save failure."""
        original = self.business_bytes()
        with patch('storage.os.replace', side_effect=OSError('injected failure')):
            with self.assertRaises(OSError):
                self.client.post(self.url, data=self.fields)
        self.assertEqual(self.business_bytes(), original)

    def test_completion_and_approval_compete_under_one_lock(self):
        """Only one terminal outcome wins, with its corresponding application state."""
        barrier = Barrier(2)

        def act(user):
            client = self.app.test_client()
            client.post('/login', data={'username': user, 'password': user + '123'})
            barrier.wait()
            return client.post(self.url if user == 'admin' else
                               '/complete/' + self.reservation_id,
                               data=self.fields if user == 'admin' else {}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(act, ('admin', 'buyer')))
        self.assertEqual(sorted(results), [302, 409])
        data = load_data(self.path)
        status = data['reservations'][0]['status']
        self.assertIn(status, ('completed', 'terminated'))
        self.assertEqual(data['products'][0]['status'],
                         'completed' if status == 'completed' else 'off_shelf')
        self.assertEqual(data['termination_requests'][0]['status'],
                         'closed' if status == 'completed' else 'approved')
