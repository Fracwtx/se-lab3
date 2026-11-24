"""Exercise the real HTTP routes and JSON persistence using isolated files."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from threading import Barrier
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app import create_app
from storage import load_data, load_products, transaction


class MarketCase(unittest.TestCase):
    """Shared temporary storage, login and product/reservation setup."""

    def setUp(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        self.path = Path(directory) / 'data.json'
        self.app = create_app(self.path)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()


    def login(self, username, password):
        """Sign in through the same form as a browser."""
        return self.client.post('/login', data={'username': username, 'password': password})


    def submit(self, title):
        """Submit a valid product and return its persisted identifier."""
        response = self.client.post('/submit', data={
            'title': title, 'description': '正常使用，可当面检查', 'price': '12.50',
            'status': 'approved'})
        self.assertEqual(response.status_code, 302)
        return load_products(self.path)[-1]['id']


    def publish(self, title='交易商品'):
        """Prepare a real published product through submission and review routes."""
        self.login('user', 'user123')
        product_id = self.submit(title)
        self.login('admin', 'admin123')
        self.review(f'/review/{product_id}', data={'decision': 'approved'})
        return product_id


    def review(self, route, data=None):
        """Submit the review version supplied by a freshly loaded admin page."""
        product_id = route.rsplit('/', 1)[-1]
        product = next((item for item in load_data(self.path)['products']
                        if item['id'] == product_id), {})
        return self.client.post(route, data={
            'expected_version': str(product.get('version', 1)), **(data or {})})

    def booking_form(self, product_id, request_id=None):
        """Capture the page version and price before sending an explicit booking request."""
        product = next(item for item in load_data(self.path)['products']
                       if item['id'] == product_id)
        return {'request_id': request_id or uuid4().hex,
                'expected_version': str(product.get('version', 1)),
                'expected_price': product['price']}

    def reserve(self, product_id, client=None):
        """Submit a new action with the fields a current page supplies."""
        return (client or self.client).post(f'/reserve/{product_id}',
                                            data=self.booking_form(product_id))

    def waiting_reservation(self):
        """Create a reservation at a deterministic UTC time."""
        self.app.config['CLOCK'] = lambda: datetime(2026, 11, 24, 0, tzinfo=timezone.utc)
        product_id = self.publish()
        self.login('buyer', 'buyer123')
        self.reserve(product_id)
        return product_id, load_data(self.path)['reservations'][-1]


class ProductTest(MarketCase):
    """Product lifecycle, access control, concurrency and original regressions."""

    def test_review_flow_and_restart(self):
        """Only approved products are public, including after a fresh app starts."""
        self.login('user', 'user123')
        approved_id = self.submit('通过商品')
        rejected_id = self.submit('驳回商品')
        self.assertNotIn('通过商品', self.client.get('/').get_data(as_text=True))
        self.assertEqual(self.client.get('/admin').status_code, 403)
        self.assertEqual(self.review(f'/review/{approved_id}',
                                         data={'decision': 'approved'}).status_code, 403)
        self.assertEqual(load_products(self.path)[0]['status'], 'pending')
        self.login('admin', 'admin123')
        self.assertEqual(self.review(f'/review/{approved_id}',
                                         data={'decision': 'approved'}).status_code, 302)
        self.assertEqual(self.review(f'/review/{rejected_id}', data={
            'decision': 'rejected', 'reason': '描述不充分'}).status_code, 302)
        self.assertEqual(self.review(f'/review/{approved_id}',
                                         data={'decision': 'rejected',
                                               'reason': '重复处理'}).status_code, 409)
        fresh_client = create_app(self.path).test_client()
        page = fresh_client.get('/').get_data(as_text=True)
        self.assertIn('通过商品', page)
        self.assertNotIn('驳回商品', page)
        self.assertEqual([item['status'] for item in load_products(self.path)],
                         ['approved', 'draft'])
        self.assertEqual(load_products(self.path)[1]['reason'], '描述不充分')


    def test_separate_process_reads_persisted_review(self):
        """A new Python process must read saved products and review decisions."""
        self.login('user', 'user123')
        product_id = self.submit('跨进程商品')
        self.login('admin', 'admin123')
        self.review(f'/review/{product_id}', data={'decision': 'approved'})
        result = subprocess.run(
            [sys.executable, '-c',
             "import sys; from app import create_app; "
             "from storage import load_data, load_products, transaction; "
             "assert load_products(sys.argv[1])[0]['status'] == 'approved'; "
             "page = create_app(sys.argv[1]).test_client().get('/').get_data(as_text=True); "
             "assert '跨进程商品' in page; print('restart persistence OK')",
             str(self.path)], cwd=Path(__file__).parent,
            check=True, capture_output=True, text=True)
        self.assertIn('restart persistence OK', result.stdout)


    def test_reject_edit_and_resubmit(self):
        """A rejection becomes a private editable draft owned by its seller."""
        self.login('user', 'user123')
        product_id = self.submit('旧标题')
        self.login('admin', 'admin123')
        self.review(f'/review/{product_id}',
                         data={'decision': 'rejected', 'reason': '补充描述'})
        self.assertEqual(load_products(self.path)[0]['status'], 'draft')
        self.login('buyer', 'buyer123')
        fields = {'title': '新标题', 'description': '完整描述', 'price': '15.00',
                  'action': 'resubmit', 'seller': 'buyer', 'status': 'approved'}
        self.assertEqual(self.client.get(f'/edit/{product_id}').status_code, 403)
        self.assertEqual(self.client.post(f'/edit/{product_id}', data=fields).status_code, 403)
        self.login('user', 'user123')
        self.assertIn('旧标题', self.client.get(f'/edit/{product_id}').get_data(as_text=True))
        self.assertEqual(self.client.post(f'/edit/{product_id}', data={
            **fields, 'action': 'save'}).status_code, 302)
        self.assertEqual(load_products(self.path)[0]['status'], 'draft')
        self.assertNotIn('新标题', self.client.get('/').get_data(as_text=True))
        self.assertEqual(self.client.post(f'/edit/{product_id}', data=fields).status_code, 302)
        product = load_products(self.path)[0]
        self.assertEqual((product['status'], product['seller'], product['reason']),
                         ('pending', 'user', '补充描述'))
        self.assertEqual(self.client.post(f'/edit/{product_id}', data=fields).status_code, 409)
        self.login('admin', 'admin123')
        self.review(f'/review/{product_id}', data={'decision': 'approved'})
        self.assertIn('新标题', self.client.get('/').get_data(as_text=True))


    def test_trade_flow_authorization_and_persistence(self):
        """Both records advance together, with ownership and state checks at each step."""
        product_id = self.publish()
        self.assertEqual(self.reserve(product_id).status_code, 403)
        self.login('user', 'user123')
        self.assertEqual(self.reserve(product_id).status_code, 403)
        self.login('buyer', 'buyer123')
        self.assertEqual(self.reserve(product_id).status_code, 302)
        reservation = load_data(self.path)['reservations'][0]
        reservation_id = reservation['id']
        self.assertEqual(reservation['price'], '12.50')
        self.assertEqual(self.client.post(f'/complete/{reservation_id}').status_code, 409)
        self.assertEqual(self.client.post(f'/confirm/{reservation_id}').status_code, 403)
        self.login('other', 'other123')
        before = self.path.read_bytes()
        for action in ('confirm', 'complete'):
            self.assertEqual(self.client.post(f'/{action}/{reservation_id}').status_code, 403)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn('交易商品', self.client.get('/reservations').get_data(as_text=True))
        self.login('user', 'user123')
        self.assertEqual(self.client.post(f'/confirm/{reservation_id}').status_code, 302)
        self.assertEqual(self.client.post(f'/confirm/{reservation_id}').status_code, 409)
        self.assertEqual(self.client.post(f'/complete/{reservation_id}').status_code, 403)
        self.login('buyer', 'buyer123')
        self.assertEqual(self.client.post(f'/complete/{reservation_id}').status_code, 302)
        self.assertEqual(self.client.post(f'/complete/{reservation_id}').status_code, 409)
        data = load_data(self.path)
        self.assertEqual(data['products'][0]['status'], 'completed')
        self.assertEqual(data['reservations'][0]['status'], 'completed')
        fresh = create_app(self.path).test_client()
        fresh.post('/login', data={'username': 'buyer', 'password': 'buyer123'})
        self.assertIn('已完成', fresh.get('/reservations').get_data(as_text=True))
        self.assertNotIn('交易商品', fresh.get('/').get_data(as_text=True))
        # Subsequent submit and review must preserve the existing reservation.
        self.publish('下一件商品')
        self.assertEqual(load_data(self.path)['reservations'], data['reservations'])
        result = subprocess.run(
            [sys.executable, '-c',
             "import sys; from storage import load_data; d=load_data(sys.argv[1]); "
             "assert d['products'][0]['status'] == 'completed'; "
             "assert d['reservations'][0]['status'] == 'completed'", str(self.path)],
            cwd=Path(__file__).parent, check=False, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_competing_buyers(self):
        """Two simultaneous requests yield exactly one effective reservation."""
        product_id = self.publish()
        barrier = Barrier(2)

        def reserve(username):
            client = self.app.test_client()
            client.post('/login', data={'username': username, 'password': username + '123'})
            barrier.wait(timeout=5)
            return self.reserve(product_id, client=client).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, ('buyer', 'other')))
        self.assertEqual(sorted(results), [302, 409])
        data = load_data(self.path)
        self.assertEqual(len(data['reservations']), 1)
        self.assertEqual(data['products'][0]['status'], 'reserved')
        self.assertNotIn('交易商品', self.client.get('/').get_data(as_text=True))


    def test_legacy_rejected_data(self):
        """A legacy product list remains readable and migrates on the next write."""
        legacy = [{'id': 'legacy', 'seller': 'user', 'title': '旧商品',
                   'description': '旧描述', 'price': '10.00', 'status': 'rejected',
                   'reason': '旧驳回原因'}]
        self.path.write_text(json.dumps(legacy), encoding='utf-8')
        self.assertEqual(load_products(self.path)[0]['status'], 'draft')
        self.assertNotIn('旧商品', self.client.get('/').get_data(as_text=True))
        self.login('user', 'user123')
        self.assertEqual(self.client.post('/edit/legacy', data={
            'title': '修订商品', 'description': '修订描述', 'price': '10.00',
            'action': 'resubmit'}).status_code, 302)
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(data['reservations'], [])
        self.assertEqual(data['products'][0]['status'], 'pending')
        self.assertEqual(data['products'][0]['reason'], '旧驳回原因')


    def test_atomic_save_failure(self):
        """A failed replacement cannot partially commit either business record."""
        self.publish()
        before = self.path.read_bytes()
        with patch('storage.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                with transaction(self.path) as data:
                    data['products'][0]['status'] = 'reserved'
                    data['reservations'].append({'id': 'unsaved'})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


    def test_nonpublished_products_cannot_be_reserved(self):
        """Direct requests cannot reserve pending, draft or already sold products."""
        self.login('user', 'user123')
        product_id = self.submit('未发布商品')
        self.login('buyer', 'buyer123')
        for status in ('pending', 'draft', 'reserved', 'confirmed', 'completed'):
            with self.subTest(status=status):
                with transaction(self.path) as data:
                    data['products'][0]['status'] = status
                before = load_data(self.path)
                self.assertEqual(self.reserve(product_id).status_code, 409)
                after = load_data(self.path)
                self.assertEqual(after['products'], before['products'])
                self.assertEqual(after['reservations'], before['reservations'])


    def test_incomplete_draft_and_submission_validation(self):
        """Drafts allow missing fields, but provided values and submissions are validated."""
        self.login('user', 'user123')
        self.assertEqual(self.client.post('/submit', data={
            'title': '未完成', 'action': 'save'}).status_code, 302)
        product = load_products(self.path)[0]
        self.assertEqual((product['status'], product['price']), ('draft', ''))
        product_id = product['id']
        self.assertNotIn('未完成', self.client.get('/').get_data(as_text=True))
        before = self.path.read_bytes()
        self.assertEqual(self.client.post(f'/edit/{product_id}', data={
            'title': '未完成', 'action': 'resubmit'}).status_code, 400)
        self.assertEqual(self.path.read_bytes(), before)
        for price in ('-1', 'abc', 'NaN'):
            self.assertEqual(self.client.post('/submit', data={
                'price': price, 'action': 'save'}).status_code, 400)
        self.assertEqual(self.client.post(f'/edit/{product_id}', data={
            'action': 'save'}).status_code, 302)
        self.assertEqual(load_products(self.path)[0]['title'], '')
        self.assertEqual(self.client.post(f'/edit/{product_id}', data={
            'title': '完整', 'description': '补充', 'price': '8',
            'action': 'resubmit'}).status_code, 302)
        self.assertEqual(load_products(self.path)[0]['status'], 'pending')


    def test_withdraw_owner_and_current_state(self):
        """Only the seller can withdraw a still-pending product into draft."""
        self.login('user', 'user123')
        product_id = self.submit('撤回商品')
        self.login('buyer', 'buyer123')
        self.assertEqual(self.client.post(f'/withdraw/{product_id}').status_code, 403)
        self.login('admin', 'admin123')
        self.assertEqual(self.client.post(f'/withdraw/{product_id}').status_code, 403)
        self.login('user', 'user123')
        self.assertEqual(self.client.post(f'/withdraw/{product_id}').status_code, 302)
        self.assertEqual(load_products(self.path)[0]['status'], 'draft')
        self.assertEqual(self.client.post(f'/withdraw/{product_id}').status_code, 409)
        self.login('admin', 'admin123')
        self.assertEqual(self.review(f'/review/{product_id}',
                                         data={'decision': 'approved'}).status_code, 409)


    def test_published_edit_reaudit_and_active_reservation(self):
        """Published edits stop public display; an active reservation forbids edits."""
        product_id = self.publish()
        fields = {'title': '修改后商品', 'description': '新描述', 'price': '20',
                  'action': 'resubmit'}
        self.login('buyer', 'buyer123')
        self.assertEqual(self.client.post(f'/edit/{product_id}', data=fields).status_code, 403)
        self.login('user', 'user123')
        self.assertEqual(self.client.get(f'/edit/{product_id}').status_code, 200)
        self.assertEqual(self.client.post(f'/edit/{product_id}', data={
            **fields, 'action': 'save'}).status_code, 400)
        self.assertEqual(self.client.post(f'/edit/{product_id}', data=fields).status_code, 302)
        self.assertEqual(load_products(self.path)[0]['status'], 'pending')
        self.assertNotIn('修改后商品', self.client.get('/').get_data(as_text=True))
        self.login('admin', 'admin123')
        self.review(f'/review/{product_id}', data={'decision': 'approved'})
        self.assertIn('修改后商品', self.client.get('/').get_data(as_text=True))
        self.login('buyer', 'buyer123')
        self.reserve(product_id)
        self.login('user', 'user123')
        before = self.path.read_bytes()
        self.assertEqual(self.client.get(f'/edit/{product_id}').status_code, 409)
        self.assertEqual(self.client.post(f'/edit/{product_id}', data=fields).status_code, 409)
        self.assertEqual(self.path.read_bytes(), before)
        reservation_id = load_data(self.path)['reservations'][0]['id']
        self.client.post(f'/confirm/{reservation_id}')
        self.assertEqual(self.client.post(f'/edit/{product_id}', data=fields).status_code, 409)


    def test_authentication_and_validation(self):
        """Unauthorized or invalid requests cannot create or review products."""
        self.assertEqual(self.client.post('/submit').status_code, 403)
        self.assertEqual(self.review('/review/missing').status_code, 403)
        self.assertEqual(self.login('admin', 'wrong').status_code, 401)
        self.login('user', 'user123')
        for price in ('0', '-1', 'NaN', 'Infinity', '1.001', 'abc', '100001'):
            with self.subTest(price=price):
                self.assertEqual(self.client.post('/submit', data={
                    'title': '书', 'description': '旧书', 'price': price}).status_code, 400)
        self.assertEqual(load_products(self.path), [])
        self.login('admin', 'admin123')
        self.assertEqual(self.client.post('/submit').status_code, 403)
        self.assertEqual(self.review('/review/missing',
                                         data={'decision': 'approved'}).status_code, 404)


class ReservationTest(MarketCase):
    """Reservation ending, fixed-clock boundaries and legacy deadlines."""

    def test_cancel_reject_permissions_and_terminal_states(self):
        """Only the matching participant may end a waiting reservation."""
        for action, username, final_status in (
                ('cancel', 'buyer', 'cancelled'), ('reject', 'user', 'rejected')):
            with self.subTest(action=action):
                product_id, reservation = self.waiting_reservation()
                reservation_id = reservation['id']
                payload = {'reason': '不方便交易'}
                for stranger in ('other', 'admin', 'user' if action == 'cancel' else 'buyer'):
                    self.login(stranger, stranger + '123')
                    before = self.path.read_bytes()
                    self.assertEqual(self.client.post(f'/{action}/{reservation_id}',
                                                      data=payload).status_code, 403)
                    self.assertEqual(self.path.read_bytes(), before)
                self.login(username, username + '123')
                if action == 'reject':
                    self.assertEqual(self.client.post(f'/{action}/{reservation_id}',
                                                      data={'reason': ' '}).status_code, 400)
                self.assertEqual(self.client.post(f'/{action}/{reservation_id}',
                                                  data=payload).status_code, 302)
                data = load_data(self.path)
                self.assertEqual(data['reservations'][-1]['status'], final_status)
                product = next(item for item in data['products'] if item['id'] == product_id)
                self.assertEqual(product['status'], 'approved')
                if action == 'reject':
                    self.assertEqual(data['reservations'][-1]['reason'], '不方便交易')
                self.assertEqual(self.client.post(f'/{action}/{reservation_id}',
                                                  data=payload).status_code, 409)
                self.login('other', 'other123')
                self.assertEqual(self.reserve(product_id).status_code, 302)
                self.assertNotEqual(load_data(self.path)['reservations'][-1]['id'],
                                    reservation_id)


    def test_confirmation_deadline_boundary(self):
        """Confirmation is legal strictly before 24h, and illegal at or after it."""
        for offset, expected in ((-1, 302), (0, 409), (1, 409)):
            with self.subTest(seconds_from_deadline=offset):
                product_id, reservation = self.waiting_reservation()
                created = datetime.fromisoformat(reservation['created_at'])
                deadline = datetime.fromisoformat(reservation['expires_at'])
                self.assertEqual(deadline - created, timedelta(hours=24))
                self.login('user', 'user123')
                now = deadline + timedelta(seconds=offset)
                self.app.config['CLOCK'] = lambda now=now: now
                self.assertEqual(self.client.post(
                    f"/confirm/{reservation['id']}").status_code, expected)
                data = load_data(self.path)
                product = next(item for item in data['products'] if item['id'] == product_id)
                self.assertEqual(data['reservations'][-1]['status'],
                                 'confirmed' if offset < 0 else 'timed_out')
                self.assertEqual(product['status'], 'confirmed' if offset < 0 else 'approved')


    def test_expiry_on_page_and_direct_operations(self):
        """Pages and direct operations expire waiting records and reopen the product."""
        for trigger in ('/', '/mine', '/reservations', 'reserve', 'cancel', 'reject'):
            with self.subTest(trigger=trigger):
                product_id, reservation = self.waiting_reservation()
                deadline = datetime.fromisoformat(reservation['expires_at'])
                self.login('user' if trigger == 'reject' else 'buyer',
                           'user123' if trigger == 'reject' else 'buyer123')
                self.app.config['CLOCK'] = lambda deadline=deadline: deadline
                if trigger == 'reserve':
                    # The request expires the old booking, invalidating its page version.
                    self.assertEqual(self.reserve(product_id).status_code, 409)
                    self.assertEqual(load_data(self.path)['reservations'][-1]['status'],
                                     'timed_out')
                    self.assertEqual(self.reserve(product_id).status_code, 302)
                elif trigger in ('cancel', 'reject'):
                    self.assertEqual(self.client.post(f"/{trigger}/{reservation['id']}",
                                                      data={'reason': '到期'}).status_code, 409)
                else:
                    self.assertEqual(self.client.get(trigger).status_code, 200)
                data = load_data(self.path)
                old = next(item for item in data['reservations']
                           if item['id'] == reservation['id'])
                self.assertEqual(old['status'], 'timed_out')
                product = next(item for item in data['products'] if item['id'] == product_id)
                self.assertEqual(product['status'], 'reserved' if trigger == 'reserve'
                                 else 'approved')
                before = self.path.read_bytes()
                self.client.get('/reservations')
                self.assertEqual(self.path.read_bytes(), before)


    def test_confirmed_cannot_cancel_reject_or_timeout(self):
        """Confirmed reservations remain active beyond the original confirmation deadline."""
        _, reservation = self.waiting_reservation()
        self.login('user', 'user123')
        self.client.post(f"/confirm/{reservation['id']}")
        self.assertEqual(self.client.post(f"/reject/{reservation['id']}",
                                         data={'reason': '不行'}).status_code, 409)
        self.login('buyer', 'buyer123')
        self.assertEqual(self.client.post(f"/cancel/{reservation['id']}").status_code, 409)
        self.app.config['CLOCK'] = lambda: datetime.fromisoformat(
            reservation['expires_at']) + timedelta(days=1)
        self.client.get('/reservations')
        self.assertEqual(load_data(self.path)['reservations'][-1]['status'], 'confirmed')


    def test_deadline_rechecked_in_transaction(self):
        """A request crossing its deadline commits timeout rather than confirmation."""
        _, reservation = self.waiting_reservation()
        self.login('user', 'user123')
        deadline = datetime.fromisoformat(reservation['expires_at'])
        instants = iter((deadline - timedelta(seconds=1), deadline))
        self.app.config['CLOCK'] = lambda: next(instants)
        self.assertEqual(self.client.post(f"/confirm/{reservation['id']}").status_code, 409)
        data = load_data(self.path)
        self.assertEqual(data['reservations'][0]['status'], 'timed_out')
        self.assertEqual(data['products'][0]['status'], 'approved')


    def test_legacy_waiting_deadline_is_assigned_once(self):
        """Old waiting records get a persisted deadline without losing the reservation."""
        _, reservation = self.waiting_reservation()
        with transaction(self.path) as data:
            del data['reservations'][0]['created_at']
            del data['reservations'][0]['expires_at']
        self.client.get('/reservations')
        first = load_data(self.path)['reservations'][0]
        self.assertEqual(first['id'], reservation['id'])
        deadline = datetime.fromisoformat(first['expires_at'])
        self.app.config['CLOCK'] = lambda: deadline - timedelta(seconds=1)
        self.client.get('/')
        self.assertEqual(load_data(self.path)['reservations'][0]['expires_at'],
                         first['expires_at'])
        self.app.config['CLOCK'] = lambda: deadline
        self.client.get('/')
        self.assertEqual(load_data(self.path)['reservations'][0]['status'], 'timed_out')


    def test_failed_cancel_save_preserves_both_records(self):
        """Disk replacement failure cannot release the product without ending its reservation."""
        _, reservation = self.waiting_reservation()
        before = self.path.read_bytes()
        with patch('storage.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.client.post(f"/cancel/{reservation['id']}")
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
