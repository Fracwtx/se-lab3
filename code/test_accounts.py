"""Regressions for stale review forms and persistent ordinary-user registration."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
from threading import Barrier
import unittest

from werkzeug.security import check_password_hash

from storage import load_data
from test_app import MarketCase


class ReviewVersionTest(MarketCase):
    """An old review page must never authorize publishing a newer submission."""

    def test_withdraw_edit_resubmit_invalidates_old_review_form(self):
        """Exercise old-page approval/rejection, then successfully review the current version."""
        for decision in ('approved', 'rejected'):
            with self.subTest(decision=decision):
                self.login('user', 'user123')
                product_id = self.submit('审核版本商品')
                old_version = load_data(self.path)['products'][-1]['version']
                self.login('admin', 'admin123')
                old_page = self.client.get('/admin').get_data(as_text=True)
                self.assertIn('审核版本商品', old_page)
                self.assertIn('12.50', old_page)
                self.login('user', 'user123')
                self.client.post('/withdraw/' + product_id)
                self.client.post('/edit/' + product_id, data={
                    'delivery_location': '图书馆东门',
                    'title': '审核版本商品', 'description': '改价后重新审核',
                    'price': '19.00', 'action': 'resubmit'})
                self.login('admin', 'admin123')
                before = self.path.read_bytes()
                response = self.client.post('/review/' + product_id, data={
                    'decision': decision, 'reason': '审核意见', 'expected_version': old_version})
                self.assertEqual(response.status_code, 409)
                self.assertIn('刷新', response.get_data(as_text=True))
                self.assertEqual(self.path.read_bytes(), before)
                data = load_data(self.path)
                self.assertEqual(data['products'][-1]['status'], 'pending')
                self.assertEqual(data['products'][-1]['price'], '19.00')
                self.assertIn('name="expected_version"', old_page)
                current_version = data['products'][-1]['version']
                self.assertEqual(self.client.post('/review/' + product_id, data={
                    'decision': decision, 'reason': '审核意见',
                    'expected_version': current_version}).status_code, 302)
                data = load_data(self.path)
                self.assertEqual(data['products'][-1]['status'],
                                 'approved' if decision == 'approved' else 'draft')
                self.assertEqual(data['reviews'][-1]['reviewed_version'], current_version)


class RegistrationTest(MarketCase):
    """Registration creates durable ordinary accounts without storing plaintext passwords."""

    def registration(self, **overrides):
        """Submit the four prototype fields plus optional malicious or invalid values."""
        fields = {'username': 'new_student', 'password': 'secret123',
                  'nickname': '新同学', 'contact_info': 'student@example.test'}
        return self.client.post('/register', data={**fields, **overrides})

    def test_registration_login_permissions_and_restart(self):
        """A registered account logs in after restart, but never acquires admin privileges."""
        self.assertIn('注册', self.client.get('/login').get_data(as_text=True))
        response = self.registration(role='admin')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login'))
        self.assertEqual(self.client.get('/mine').status_code, 403)
        user = load_data(self.path)['users'][0]
        self.assertEqual(user['role'], 'user')
        self.assertEqual(user['nickname'], '新同学')
        self.assertEqual(user['contact_info'], 'student@example.test')
        self.assertNotIn('password', user)
        self.assertTrue(check_password_hash(user['password_hash'], 'secret123'))
        self.assertNotIn('secret123', self.path.read_text())
        self.assertEqual(self.login('new_student', 'secret123').status_code, 302)
        self.assertEqual(self.client.get('/mine').status_code, 200)
        self.assertEqual(self.client.get('/admin').status_code, 403)
        self.assertEqual(self.client.post('/review/missing', data={
            'decision': 'approved', 'expected_version': '1'}).status_code, 403)
        self.assertEqual(self.client.post('/submit', data={
            'action': 'save', 'title': '新用户草稿'}).status_code, 302)
        self.assertEqual(load_data(self.path)['products'][0]['seller'], 'new_student')
        result = subprocess.run([
            sys.executable, '-c',
            "import sys; from app import create_app; "
            "client=create_app(sys.argv[1]).test_client(); "
            "assert client.post('/login', data={'username':'new_student', "
            "'password':'secret123'}).status_code == 302; "
            "assert client.get('/mine').status_code == 200; "
            "assert client.get('/admin').status_code == 403", str(self.path)],
            cwd=Path(__file__).parent, check=False, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_duplicate_reserved_names_and_validation(self):
        """Reserved demo usernames and malformed/short fields never create accounts."""
        self.assertEqual(self.registration().status_code, 302)
        before = self.path.read_bytes()
        self.assertEqual(self.registration().status_code, 409)
        for name in ('user', 'buyer', 'other', 'admin', ' new_student '):
            self.assertEqual(self.registration(username=name).status_code, 409)
        for fields in ({'username': ''}, {'username': 'a b'}, {'username': 'x' * 51},
                       {'password': '1234567'}, {'nickname': ''}, {'nickname': 'x' * 51},
                       {'contact_info': ''}, {'contact_info': 'x' * 201}):
            with self.subTest(fields=fields):
                self.assertEqual(self.registration(**fields).status_code, 400)
        self.assertEqual(self.path.read_bytes(), before)
        unknown = self.login('missing_user', 'wrong').get_data(as_text=True)
        wrong = self.login('new_student', 'wrong').get_data(as_text=True)
        self.assertEqual(unknown, wrong)

    def test_concurrent_registration_and_legacy_demo_login(self):
        """Same-username submissions serialize; documents without users still allow demos."""
        self.path.write_text('{"products": [], "reservations": []}', encoding='utf-8')
        for username in ('user', 'buyer', 'other', 'admin'):
            self.assertEqual(self.login(username, username + '123').status_code, 302)
        barrier = Barrier(2)

        def register(_):
            client = self.app.test_client()
            barrier.wait(timeout=5)
            return client.post('/register', data={
                'username': 'racing_user', 'password': 'password123',
                'nickname': '同学', 'contact_info': 'contact'}).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(register, range(2))), [302, 409])
        self.assertEqual(len(load_data(self.path)['users']), 1)


if __name__ == '__main__':
    unittest.main()
