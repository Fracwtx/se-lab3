"""Key regressions for catalog queries, review history and booking requests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
import unittest
from unittest.mock import patch

from app import STATUS_LABELS, create_app
from storage import load_data, transaction
from test_app import MarketCase


class SearchTest(MarketCase):
    """Public queries must filter authorized data before sorting or displaying it."""

    def setUp(self):
        super().setUp()
        with transaction(self.path) as data:
            data['products'] = [
                {'id': key, 'delivery_location': '图书馆东门',
                'title': title, 'description': description, 'price': price,
                 'status': status, 'category': category, 'seller': 'user', 'reason': '',
                 'published_at': date}
                for key, title, description, price, status, category, date in (
                    ('a', '数学教材', '附笔记', '20.00', 'approved', '图书教材', '2026-01-01'),
                    ('b', '英语教材', '全新', '10.00', 'approved', '图书教材', '2026-02-01'),
                    ('c', '相机', '附教材', '30.00', 'approved', '数码电子', '2026-03-01'),
                    ('d', '秘密教材', '不公开', '12.00', 'pending', '图书教材', '2026-04-01'),
                    ('e', '草稿教材', '不公开', '', 'draft', '图书教材', ''),
                )]

    def test_search_intersection_visibility_and_sort(self):
        """Combine title/description keyword, category and inclusive price bounds."""
        page = self.client.get('/?q=教材&category=图书教材&min_price=10&max_price=20'
                               '&sort=price_asc').get_data(as_text=True)
        self.assertLess(page.index('<h2>英语教材'), page.index('<h2>数学教材'))
        for title in ('相机', '秘密教材', '草稿教材'):
            self.assertNotIn('<h2>' + title, page)
        page = self.client.get('/?q=教材&sort=price_desc').get_data(as_text=True)
        self.assertLess(page.index('<h2>相机'), page.index('<h2>数学教材'))
        page = self.client.get('/?sort=newest').get_data(as_text=True)
        self.assertLess(page.index('<h2>相机'), page.index('<h2>英语教材'))
        self.assertEqual(self.client.get('/products/a').status_code, 200)
        for key in ('d', 'e', 'missing'):
            self.assertEqual(self.client.get('/products/' + key).status_code, 404)
        self.login('admin', 'admin123')
        self.assertEqual(self.client.get('/products/d').status_code, 404)

    def test_query_validation_and_legacy_defaults(self):
        """Reject invalid ranges and preserve legacy records with default category."""
        for query in ('min_price=30&max_price=10', 'min_price=NaN', 'max_price=abc',
                      'min_price=-1', 'max_price=1.001', 'sort=unknown'):
            self.assertEqual(self.client.get('/?' + query).status_code, 400)
        with transaction(self.path) as data:
            del data['products'][0]['category']
            del data['products'][0]['published_at']
        self.assertEqual(load_data(self.path)['products'][0]['category'], '其他')
        page = self.client.get('/?category=其他').get_data(as_text=True)
        self.assertIn('<h2>数学教材', page)
        self.assertNotIn('<h2>英语教材', page)


class ReviewHistoryTest(MarketCase):
    """History is append-only, authorized and atomic with the current decision."""

    def test_history_survives_resubmission_and_filters_ownership(self):
        """A later approval never removes the previous rejection and its reason."""
        self.login('user', 'user123')
        product_id = self.submit('历史商品')
        self.login('admin', 'admin123')
        self.review(f'/review/{product_id}',
                         data={'decision': 'rejected', 'reason': '描述不清楚'})
        self.login('user', 'user123')
        self.client.post(f'/edit/{product_id}', data={
            'delivery_location': '图书馆东门',
            'title': '历史商品', 'description': '完整描述', 'price': '15', 'action': 'resubmit'})
        self.assertIn('描述不清楚', self.client.get('/reviews').get_data(as_text=True))
        self.login('admin', 'admin123')
        self.review(f'/review/{product_id}', data={'decision': 'approved', 'reason': '合规'})
        history = load_data(self.path)['reviews']
        self.assertEqual([entry['decision'] for entry in history], ['rejected', 'approved'])
        self.assertTrue(all(entry['product_id'] == product_id and entry['reviewer'] == 'admin'
                            and entry['reviewed_at'] for entry in history))
        self.assertIn('描述不清楚', self.client.get('/reviews').get_data(as_text=True))
        self.assertEqual(self.review(f'/review/{product_id}',
                                         data={'decision': 'approved'}).status_code, 409)
        self.assertEqual(len(load_data(self.path)['reviews']), 2)
        self.login('buyer', 'buyer123')
        self.assertNotIn('描述不清楚', self.client.get('/reviews').get_data(as_text=True))
        other_id = self.submit('别人的商品')
        self.login('admin', 'admin123')
        self.review(f'/review/{other_id}',
                         data={'decision': 'rejected', 'reason': '他人的私有意见'})
        self.assertIn('他人的私有意见', self.client.get('/reviews').get_data(as_text=True))
        self.login('user', 'user123')
        page = self.client.get('/reviews').get_data(as_text=True)
        self.assertIn('描述不清楚', page)
        self.assertNotIn('他人的私有意见', page)
        self.client.post('/logout')
        self.assertEqual(self.client.get('/reviews').status_code, 403)

    def test_review_save_failure_is_atomic(self):
        """Failure cannot save a decision without history, or history without a decision."""
        self.login('user', 'user123')
        product_id = self.submit('故障审核')
        self.login('admin', 'admin123')
        before = self.business_bytes()
        with patch('storage.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.review(f'/review/{product_id}', data={'decision': 'approved'})
        self.assertEqual(self.business_bytes(), before)
        self.assertEqual(load_data(self.path)['reviews'], [])


class BookingTest(MarketCase):
    """REQ-017/018: immutable request identity, live state and confirmed price."""

    def test_old_price_and_version_require_new_confirmation(self):
        """Stale failures are replayed; only a new explicitly confirmed request may succeed."""
        product_id = self.publish()
        old = self.booking_form(product_id, 'old-page')
        self.login('user', 'user123')
        self.client.post(f'/edit/{product_id}', data={
            'delivery_location': '图书馆东门',
            'title': '改价商品', 'description': '已调整', 'price': '19.00', 'action': 'resubmit'})
        self.login('admin', 'admin123')
        self.review(f'/review/{product_id}', data={'decision': 'approved'})
        self.login('buyer', 'buyer123')
        response = self.client.post(f'/reserve/{product_id}', data=old)
        self.assertEqual(response.status_code, 409)
        self.assertIn('19.00', response.get_data(as_text=True))
        self.assertIn('刷新', response.get_data(as_text=True))
        self.assertEqual(load_data(self.path)['reservations'], [])
        self.assertEqual(self.client.post(f'/reserve/{product_id}', data=old).status_code, 409)
        current = self.booking_form(product_id, 'old-page')
        self.assertEqual(self.client.post(f'/reserve/{product_id}', data=current).status_code, 409)
        version_only = {**current, 'request_id': 'stale-version',
                        'expected_version': old['expected_version']}
        self.assertEqual(self.client.post(f'/reserve/{product_id}',
                                         data=version_only).status_code, 409)
        self.assertEqual(self.reserve(product_id).status_code, 302)
        self.assertEqual(len(load_data(self.path)['reservations']), 1)

    def test_replay_after_each_terminal_state(self):
        """Old keys always identify their original reservation, even after it ends."""
        for state in ('cancelled', 'rejected', 'timed_out', 'completed'):
            with self.subTest(state=state):
                self.app.config['CLOCK'] = lambda: datetime(2026, 11, 24, tzinfo=timezone.utc)
                product_id = self.publish()
                form = self.booking_form(product_id, 'same-key-' + state)
                self.login('buyer', 'buyer123')
                self.assertEqual(self.client.post(f'/reserve/{product_id}', data=form).status_code,
                                 302)
                reservation = load_data(self.path)['reservations'][-1]
                key = reservation['id']
                # A new request key while active returns the same reservation.
                self.assertEqual(self.reserve(product_id).status_code, 302)
                if state == 'cancelled':
                    self.client.post('/cancel/' + key)
                elif state == 'rejected':
                    self.login('user', 'user123')
                    self.client.post('/reject/' + key, data={'reason': '暂不交易'})
                elif state == 'timed_out':
                    deadline = datetime.fromisoformat(reservation['expires_at'])
                    self.app.config['CLOCK'] = lambda deadline=deadline: deadline
                else:
                    self.login('user', 'user123')
                    self.client.post('/confirm/' + key)
                    self.login('buyer', 'buyer123')
                    self.client.post('/complete/' + key)
                self.login('buyer', 'buyer123')
                count = len(load_data(self.path)['reservations'])
                response = self.client.post(f'/reserve/{product_id}', data=form,
                                            follow_redirects=True)
                self.assertEqual(response.status_code, 200)
                self.assertIn(key, response.get_data(as_text=True))
                self.assertIn(STATUS_LABELS[state], response.get_data(as_text=True))
                self.assertEqual(len(load_data(self.path)['reservations']), count)
                if state != 'completed':
                    self.assertEqual(self.reserve(product_id).status_code, 302)
                    self.assertEqual(len(load_data(self.path)['reservations']), count + 1)

    def test_same_key_concurrency_and_owner_visibility(self):
        """Same-buyer retries serialize and other buyers cannot inspect their result."""
        product_id = self.publish()
        form = self.booking_form(product_id, 'retry-key')
        barrier = Barrier(2)

        def retry(_):
            client = self.app.test_client()
            client.post('/login', data={'username': 'buyer', 'password': 'buyer123'})
            barrier.wait(timeout=5)
            return client.post(f'/reserve/{product_id}', data=form).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(list(executor.map(retry, range(2))), [302, 302])
        data = load_data(self.path)
        self.assertEqual(len(data['reservations']), 1)
        self.assertEqual(len(data['booking_requests']), 1)
        self.login('other', 'other123')
        self.assertEqual(self.client.get('/booking-requests/retry-key').status_code, 404)
        self.assertEqual(self.client.post(f'/reserve/{product_id}', data=form).status_code, 409)
        self.assertEqual(len(load_data(self.path)['booking_requests']), 2)
        self.login('buyer', 'buyer123')
        self.assertEqual(self.client.post(f'/reserve/{product_id}', data={
            **form, 'expected_price': '88.00'}).status_code, 409)
        fresh = create_app(self.path, notification_worker=False).test_client()
        fresh.post('/login', data={'username': 'buyer', 'password': 'buyer123'})
        self.assertEqual(fresh.post(f'/reserve/{product_id}', data=form).status_code, 302)
        self.assertEqual(len(load_data(self.path)['reservations']), 1)

    def test_request_parameters_and_failed_save(self):
        """No identifier or confirmed version/price means no booking; I/O failure rolls back all."""
        product_id = self.publish()
        self.login('buyer', 'buyer123')
        form = self.booking_form(product_id, 'save-failure')
        for field in ('request_id', 'expected_version', 'expected_price'):
            invalid = {name: value for name, value in form.items() if name != field}
            self.assertEqual(self.client.post(f'/reserve/{product_id}',
                                             data=invalid).status_code, 400)
        before = self.business_bytes()
        with patch('storage.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.client.post(f'/reserve/{product_id}', data=form)
        self.assertEqual(self.business_bytes(), before)
        self.assertEqual(self.client.post(f'/reserve/{product_id}', data=form).status_code, 302)


if __name__ == '__main__':
    unittest.main()
