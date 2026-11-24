"""Real TCP/HTTP acceptance with disposable JSON, image files and actual hidden form fields."""
import http.cookiejar
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import tempfile
from threading import Event, Thread
from time import monotonic
import unittest
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

from PIL import Image
from werkzeug.serving import make_server

from app import create_app
from storage import load_data


class HiddenForms(HTMLParser):
    """Read hidden fields from their real form, never substituting server-current versions."""

    def __init__(self, body):
        super().__init__()
        self.forms = {}
        self.active = None
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'form':
            self.active = {}
            self.forms[values.get('action', '')] = self.active
        if tag == 'input' and values.get('type') == 'hidden' and self.active is not None:
            self.active[values['name']] = values.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.active = None


def multipart(fields):
    """Encode a tiny real PNG upload using only the standard HTTP client."""
    boundary = uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n'
                      f'\r\n{value}\r\n').encode())
    with BytesIO() as stream:
        Image.new('RGB', (12, 12), 'green').save(stream, format='PNG')
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="images"; '
                      'filename="physical.png"\r\nContent-Type: image/png\r\n\r\n').encode()
                     + stream.getvalue() + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), 'multipart/form-data; boundary=' + boundary


class HTTPAcceptanceTest(unittest.TestCase):
    """Complete and terminate trades over real HTTP, with the real background worker enabled."""

    def setUp(self):
        # unittest exits this context after the HTTP server and worker cleanups.
        # pylint: disable-next=consider-using-with
        directory = Path(self.enterContext(tempfile.TemporaryDirectory(
            prefix='lab3-http-acceptance-')))
        self.path = directory / 'data.json'
        self.app = create_app(self.path)
        self.app.config['UPLOAD_DIR'] = str(directory / 'images')
        self.addCleanup(self.app.extensions['notification_worker'].stop)
        self.server = make_server('127.0.0.1', 0, self.app, threaded=True)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.clients = {name: urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            for name in ('seller', 'buyer', 'admin', 'public')}

    def stop_server(self):
        """Stop all HTTP work before stopping the worker and removing its files."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def fetch(self, actor, route, fields=None, *, expected=200):
        """Return bytes after checking the actual HTTP status, including redirected responses."""
        headers = {}
        if isinstance(fields, tuple):
            data, headers['Content-Type'] = fields
        else:
            data = None if fields is None else urllib.parse.urlencode(fields).encode()
        try:
            with self.clients[actor].open(urllib.request.Request(
                    self.base + route, data=data, headers=headers), timeout=5) as response:
                status, body = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, body = error.code, error.read()
        self.assertEqual(status, expected, (route, body[:160]))
        return body

    def form(self, actor, route, action=''):
        """Capture page version, confirmed price and request identifiers from rendered forms."""
        forms = HiddenForms(self.fetch(actor, route).decode()).forms
        self.assertIn(action, forms)
        return forms[action]

    def confirmed_trade(self, title):
        """Save incomplete draft, send with photo, review, book and confirm through HTTP."""
        self.fetch('seller', '/submit', {'action': 'save', 'title': title})
        product = load_data(self.path)['products'][-1]
        product_id = product['id']
        fields = self.form('seller', '/edit/' + product_id)
        self.fetch('seller', '/edit/' + product_id, multipart({**fields, 'action': 'resubmit',
                   'title': title, 'description': 'HTTP实物验收', 'category': '其他',
                   'price': '20.00', 'delivery_location': '图书馆东门', 'cover_position': '1'}))
        self.fetch('public', '/products/' + product_id, expected=404)
        fields = self.form('admin', '/admin', '/review/' + product_id)
        self.fetch('admin', '/review/' + product_id, {**fields, 'decision': 'approved'})
        detail = self.fetch('public', '/products/' + product_id).decode()
        self.assertIn('图书馆东门', detail)
        product = next(item for item in load_data(self.path)['products']
                       if item['id'] == product_id)
        self.assertTrue(self.fetch('public', f"/products/{product_id}/images/{product['cover']}").
                        startswith(b'\x89PNG'))
        fields = self.form('buyer', '/products/' + product_id, '/reserve/' + product_id)
        self.fetch('buyer', '/reserve/' + product_id, fields)
        reservation = load_data(self.path)['reservations'][-1]
        route = '/confirm/' + reservation['id']
        self.fetch('seller', route, self.form('seller', '/reservations', route))
        return product_id, reservation['id']

    def test_complete_and_admin_terminate_with_exact_notifications_and_logs(self):
        """Verify both requested flows and one approval notice per participant over real HTTP."""
        self.assertFalse(self.path.exists())
        self.fetch('public', '/')
        self.fetch('public', '/static/forms.css')
        for actor in ('seller', 'buyer'):
            self.fetch(actor, '/register', {'username': actor + '_http', 'password': 'student123',
                       'nickname': actor, 'contact_info': actor + '_FAKE_CONTACT'})
            self.fetch(actor, '/login', {'username': actor + '_http', 'password': 'student123'})
        self.fetch('admin', '/login', {'username': 'admin', 'password': 'admin123'})
        product_id, reservation_id = self.confirmed_trade('HTTP正常完成')
        route = '/complete/' + reservation_id
        self.fetch('buyer', route, self.form('buyer', '/reservations', route))
        data = load_data(self.path)
        self.assertEqual(data['products'][0]['status'], 'completed')
        self.assertEqual(data['reservations'][0]['status'], 'completed')
        product_id, reservation_id = self.confirmed_trade('HTTP终止审批')
        route = '/reservations/' + reservation_id + '/termination'
        self.fetch('seller', route, {**self.form('seller', '/reservations', route),
                                    'reason': '双方协商终止'})
        application = load_data(self.path)['termination_requests'][-1]
        route = '/terminations/' + application['id']
        fields = self.form('admin', route)
        self.fetch('buyer', route, expected=403)
        self.fetch('admin', route, {**fields, 'decision': 'approved', 'reason': '已分别核实同意',
                   'buyer_opinion': '同意', 'seller_opinion': '同意',
                   'buyer_agreed': 'yes', 'seller_agreed': 'yes'})
        deadline = monotonic() + 3
        while monotonic() < deadline:
            notices = [item for item in load_data(self.path)['inbox']
                       if item['subject_id'] == application['id'] and item['event'] == 'approved']
            if len(notices) == 2:
                break
            Event().wait(0.01)
        self.assertEqual(sorted(item['recipient'] for item in notices),
                         ['buyer_http', 'seller_http'])
        self.fetch('admin', '/notifications/retry', {})
        for actor in ('seller', 'buyer'):
            page = self.fetch(actor, '/notifications').decode()
            self.assertIn(application['id'], page)
            self.assertIn('交易终止已批准', page)
        logs = self.fetch('admin', '/logs?' + urllib.parse.urlencode({
            'object_id': product_id, 'operation': 'handle_termination',
            'result': 'success'})).decode()
        self.assertIn(product_id, logs)
        self.assertIn('handle_termination', logs)
        self.assertNotIn('FAKE_CONTACT', logs)
        users = load_data(self.path)['users']
        self.assertNotEqual(users[0]['password_hash'], users[1]['password_hash'])
        for user in users:
            self.assertNotIn(user['password_hash'], logs)
        self.fetch('seller', '/logs', expected=403)
        self.fetch('public', '/products/' + product_id, expected=404)
        data = load_data(self.path)
        self.assertEqual(data['products'][-1]['status'], 'off_shelf')
        self.assertEqual(data['reservations'][-1]['status'], 'terminated')
        self.assertEqual(data['termination_requests'][-1]['status'], 'approved')
        self.assertEqual(sum(item['event'] == 'approved' and item['subject_id'] == application['id']
                             for item in data['inbox']), 2)
