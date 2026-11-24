"""Version checks preserve business records and retain sanitized rejection evidence."""
import json
from unittest.mock import patch

from storage import _save_data, load_data, transaction
from test_app import MarketCase


class VersionTest(MarketCase):
    """Explicitly supply stale/missing versions instead of the fresh-form fixture."""

    def test_product_aba_and_missing_version(self):
        """Withdraw/resubmit cycles cannot make an old edit or withdraw form valid."""
        product_id = self.publish()
        self.login('user', 'user123')
        old = str(load_data(self.path)['products'][0]['version'])
        self.client.post('/off-shelf/' + product_id)
        before = self.business_bytes()
        response = self.client.post('/edit/' + product_id, data={
            'action': 'save', 'expected_version': old, 'title': '旧页面'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.business_bytes(), before)
        self.assertEqual(load_data(self.path)['operation_logs'][-1]['result'], '409')
        self.assertEqual(self.client.open('/edit/' + product_id, method='POST',
                                         data={'action': 'save'}).status_code, 400)
        self.assertEqual(self.business_bytes(), before)

    def test_reservation_and_termination_versions(self):
        """Reservation writes and termination requests reject stale pages under the lock."""
        product_id, reservation = self.waiting_reservation()
        self.login('user', 'user123')
        version = load_data(self.path)['products'][0]['version']
        before = self.business_bytes()
        self.assertEqual(self.client.post('/confirm/' + reservation['id'], data={
            'expected_version': str(version - 1)}).status_code, 409)
        self.assertEqual(self.business_bytes(), before)
        self.client.post('/confirm/' + reservation['id'])
        before = self.business_bytes()
        self.assertEqual(self.client.post('/reservations/' + reservation['id'] + '/termination',
                         data={'reason': '协商', 'expected_version': str(version)}).
                         status_code, 409)
        self.assertEqual(self.business_bytes(), before)
        with transaction(self.path) as data:
            next(item for item in data['products'] if item['id'] == product_id)['version'] += 1
        before = self.business_bytes()
        self.login('buyer', 'buyer123')
        self.assertEqual(self.client.post('/complete/' + reservation['id'],
                         data={'expected_version': str(version + 1)}).status_code, 409)
        self.assertEqual(self.business_bytes(), before)


class LogTest(MarketCase):
    """Success metadata shares the business commit; rejected writes leave only audit data."""

    def test_log_scope_filters_redaction_and_read_only(self):
        """Only admins may read combined filters; logs never copy arbitrary input text."""
        product_id = self.publish()
        self.login('other', 'other123')
        before = load_data(self.path)['products']
        self.client.post('/off-shelf/' + product_id, data={
            'password': 'SECRET_DO_NOT_LOG', 'reason': 'CONTACT_DO_NOT_LOG',
            'contact_info': 'PRIVATE_DO_NOT_LOG'})
        self.assertEqual(load_data(self.path)['products'], before)
        self.assertEqual(self.client.get('/logs').status_code, 403)
        self.login('admin', 'admin123')
        entries = load_data(self.path)['operation_logs']
        serialized = json.dumps(entries)
        for secret in ('SECRET_DO_NOT_LOG', 'CONTACT_DO_NOT_LOG', 'PRIVATE_DO_NOT_LOG',
                       'password_hash', 'contact_info'):
            self.assertNotIn(secret, serialized)
        filtered = self.client.get('/logs', query_string={
            'actor': 'other', 'object_id': product_id, 'operation': 'take_off_shelf',
            'result': '403', 'start': '2020-01-01', 'end': '2099-01-01'})
        self.assertIn(entries[-1]['id'], filtered.get_data(as_text=True))
        self.assertEqual(self.client.get('/logs?start=invalid').status_code, 400)
        original = self.path.read_bytes()
        self.assertEqual(self.client.post('/logs', data={'delete': 'all'}).status_code, 405)
        self.assertEqual(self.path.read_bytes(), original)

    def test_log_save_failure_and_idempotent_replay(self):
        """The whole save rolls back, and replay produces no duplicate success metadata."""
        product_id = self.publish()
        self.login('user', 'user123')
        original = self.path.read_bytes()
        with patch('storage.os.replace', side_effect=OSError('unavailable')):
            with self.assertRaises(OSError):
                self.client.post('/off-shelf/' + product_id)
        self.assertEqual(self.path.read_bytes(), original)
        self.login('buyer', 'buyer123')
        form = self.booking_form(product_id)
        self.client.post('/reserve/' + product_id, data=form)
        original = self.path.read_bytes()
        self.client.post('/reserve/' + product_id, data=form)
        self.assertEqual(self.path.read_bytes(), original)
        attempts = []

        def fail_first_save(data, path):
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError('once')
            return _save_data(data, path)

        original_business = self.business_bytes()
        with patch('storage._save_data', side_effect=fail_first_save):
            with self.assertRaises(OSError):
                self.login('user', 'user123')
                self.client.post('/confirm/' + load_data(self.path)['reservations'][0]['id'])
        self.assertEqual(self.business_bytes(), original_business)
        self.assertEqual(load_data(self.path)['operation_logs'][-1]['reason'], 'storage_error')


class CoverageTest(MarketCase):
    """Explicit stale forms cover each newly versioned state-changing route."""

    def test_each_missing_write_protocol_and_rendered_fields(self):
        """No fixture fallback: every tested request carries an explicitly old version."""
        product_id = self.publish()
        self.login('user', 'user123')
        version = load_data(self.path)['products'][0]['version']
        self.assertIn('name="expected_version"', self.client.get('/edit/' + product_id).
                      get_data(as_text=True))
        for route in ('/edit/' + product_id, '/off-shelf/' + product_id):
            before = self.business_bytes()
            self.assertEqual(self.client.post(route, data={
                'expected_version': str(version - 1), 'action': 'resubmit'}).status_code, 409)
            self.assertEqual(self.business_bytes(), before)
        with transaction(self.path) as data:
            data['products'][0]['status'] = 'pending'
        before = self.business_bytes()
        self.assertEqual(self.client.post('/withdraw/' + product_id, data={
            'expected_version': str(version - 1)}).status_code, 409)
        self.assertEqual(self.business_bytes(), before)
        _, reservation = self.waiting_reservation()
        version = load_data(self.path)['products'][-1]['version']
        for action, actor in (('confirm', 'user'), ('reject', 'user'), ('cancel', 'buyer')):
            self.login(actor, actor + '123')
            before = self.business_bytes()
            self.assertEqual(self.client.post('/' + action + '/' + reservation['id'], data={
                'expected_version': str(version - 1), 'reason': '不方便'}).status_code, 409)
            self.assertEqual(self.business_bytes(), before)
        self.assertIn('name="expected_version"', self.client.get('/reservations').
                      get_data(as_text=True))

    def test_system_timeout_logs_and_old_logs_retained(self):
        """System expiry is audited and old logs are never pruned by normal operations."""
        from datetime import timedelta  # pylint: disable=import-outside-toplevel
        _, reservation = self.waiting_reservation()
        with transaction(self.path) as data:
            data['operation_logs'].append({'id': 'retained-old-log', 'request_id': 'historical',
                'actor': 'system', 'at': '2000-01-01T00:00:00+00:00', 'operation': 'old',
                'object_id': reservation['id'], 'before': '', 'after': '', 'result': 'success',
                'reason': 'historical'})
        now = self.app.config['CLOCK']()
        self.app.config['CLOCK'] = lambda: now + timedelta(hours=24)
        self.client.get('/')
        entries = load_data(self.path)['operation_logs']
        self.assertTrue(any(item['id'] == 'retained-old-log' for item in entries))
        expired = next(item for item in entries if item['operation'] == 'timeout')
        self.assertEqual(expired['actor'], 'system')
        self.assertEqual((expired['before'], expired['after']), ('waiting', 'timed_out'))
