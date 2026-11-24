"""Product delivery, transaction privacy and protected local image regressions."""
from datetime import datetime
from io import BytesIO
import unittest
from unittest.mock import patch

from images import decode_upload
from werkzeug.datastructures import FileStorage

from storage import load_data, transaction
from test_app import MarketCase, image_upload


class DeliveryTest(MarketCase):
    """Incomplete drafts and legacy goods remain usable without inventing a location."""

    def test_delivery_required_only_when_submitting(self):
        """Saving a draft allows blank location, but submitting requires a real value."""
        self.login('user', 'user123')
        fields = {'title': '交付测试', 'description': '可当面检查', 'price': '10'}
        self.assertEqual(self.client.post('/submit', data={
            **fields, 'action': 'save'}).status_code, 302)
        product = load_data(self.path)['products'][0]
        self.assertEqual(product['delivery_location'], '')
        self.assertEqual(self.client.post('/edit/' + product['id'], data={
            **fields, 'action': 'resubmit'}).status_code, 400)
        self.assertEqual(self.client.post('/edit/' + product['id'], data={
            **fields, 'delivery_location': ' 图书馆东门 ', 'action': 'resubmit',
            'images': image_upload()}).status_code, 302)
        self.login('admin', 'admin123')
        self.review('/review/' + product['id'], data={'decision': 'approved'})
        self.assertIn('图书馆东门', self.client.get('/products/' + product['id']).get_data(
            as_text=True))
        self.login('user', 'user123')
        self.assertEqual(self.client.post('/edit/' + product['id'], data={
            **fields, 'delivery_location': 'x' * 201, 'action': 'resubmit'}).status_code, 400)

    def test_legacy_location_missing_is_displayed(self):
        """An existing approved product without location is still readable and public."""
        product_id = self.publish('旧商品')
        with transaction(self.path) as data:
            del data['products'][0]['delivery_location']
        self.assertEqual(load_data(self.path)['products'][0]['delivery_location'], '')
        page = self.client.get('/products/' + product_id)
        self.assertEqual(page.status_code, 200)
        self.assertIn('未填写', page.get_data(as_text=True))


class ContactTest(MarketCase):
    """REQ-030 contacts are exposed only to the matching transaction participants."""

    def setUp(self):
        super().setUp()
        for username, contact in (('seller_student', 'SELLER_PRIVATE_123'),
                                  ('buyer_student', 'BUYER_PRIVATE_456')):
            self.client.post('/register', data={
                'username': username, 'password': 'student123',
                'nickname': username, 'contact_info': contact})
        self.login('seller_student', 'student123')
        self.product_id = self.submit('联系方式测试')
        self.login('admin', 'admin123')
        self.review('/review/' + self.product_id, data={'decision': 'approved'})
        self.login('buyer_student', 'student123')
        self.reserve(self.product_id)
        self.reservation_id = load_data(self.path)['reservations'][0]['id']

    def test_participant_state_matrix_and_unrelated_users(self):
        """Check every current and planned reservation terminal state on page and endpoint."""
        for status in ('waiting', 'confirmed', 'completed', 'cancelled', 'rejected',
                       'timed_out', 'terminated'):
            with self.subTest(status=status):
                allowed = status in ('waiting', 'confirmed', 'completed')
                with transaction(self.path) as data:
                    data['reservations'][0]['status'] = status
                    data['products'][0]['status'] = {
                        'waiting': 'reserved', 'confirmed': 'confirmed',
                        'completed': 'completed'}.get(status, 'approved')
                for username, expected_contact in (
                        ('seller_student', 'BUYER_PRIVATE_456'),
                        ('buyer_student', 'SELLER_PRIVATE_123')):
                    self.login(username, 'student123')
                    page = self.client.get('/reservations').get_data(as_text=True)
                    response = self.client.get('/reservations/' + self.reservation_id + '/contact')
                    if allowed:
                        self.assertIn(expected_contact, page)
                        self.assertEqual(response.json, {'contact_info': expected_contact})
                        self.assertIn('no-store', response.headers['Cache-Control'])
                    else:
                        self.assertNotIn('PRIVATE_', page)
                        self.assertEqual(response.status_code, 403)
                    for user in load_data(self.path)['users']:
                        self.assertNotIn(user['password_hash'], page)
                for username in ('other', 'admin'):
                    self.login(username, username + '123')
                    self.assertEqual(self.client.get('/reservations/' + self.reservation_id +
                                                     '/contact').status_code, 403)
                    self.assertNotIn('PRIVATE_', self.client.get('/').get_data(as_text=True))
                    self.assertNotIn('PRIVATE_', self.client.get(
                        '/products/' + self.product_id).get_data(as_text=True))
                self.login('other', 'other123')
                self.assertNotIn('PRIVATE_', self.client.get('/reservations').get_data(
                    as_text=True))
        self.client.post('/logout')
        self.assertEqual(self.client.get('/reservations/' + self.reservation_id +
                                         '/contact').status_code, 403)

    def test_missing_contact_and_expired_link(self):
        """Legacy/demo accounts show missing contact; an expired URL stops exposing data."""
        self.login('buyer_student', 'student123')
        link = '/reservations/' + self.reservation_id + '/contact'
        self.assertEqual(self.client.get(link, buffered=True).status_code, 200)
        with transaction(self.path) as data:
            data['users'] = []
            data['reservations'][0].update(buyer='buyer', seller='user')
            data['products'][0]['seller'] = 'user'
        self.login('buyer', 'buyer123')
        self.assertEqual(self.client.get(link, buffered=True).json, {'contact_info': '未填写'})
        self.assertIn('未填写', self.client.get('/reservations').get_data(as_text=True))
        deadline = datetime.fromisoformat(load_data(self.path)['reservations'][0]['expires_at'])
        self.app.config['CLOCK'] = lambda: deadline
        self.assertEqual(self.client.get(link, buffered=True).status_code, 403)
        self.assertEqual(load_data(self.path)['reservations'][0]['status'], 'timed_out')


class ImageTest(MarketCase):
    """Validate uploads, cover selection, dynamic visibility and JSON/file rollback."""

    def setUp(self):
        super().setUp()
        self.upload_dir = self.path.parent / 'uploads'
        self.app.config['UPLOAD_DIR'] = self.upload_dir
        self.login('user', 'user123')

    def fields(self, **changes):
        """Build a fully populated seller form with a fresh image."""
        return {'title': '实物图片', 'description': '可当面检查', 'price': '10',
                'delivery_location': '图书馆东门', 'action': 'resubmit',
                'images': image_upload(), **changes}

    def test_upload_cover_and_public_gallery(self):
        """New image contents, rather than filenames, determine safe stored names."""
        response = self.client.post('/submit', data=self.fields(
            images=[image_upload('../../original.png'), image_upload('second.png', 'blue')],
            cover_position='2'))
        self.assertEqual(response.status_code, 302)
        product = load_data(self.path)['products'][0]
        self.assertEqual(len(product['images']), 2)
        self.assertEqual(product['cover'], product['images'][1])
        for name in product['images']:
            self.assertRegex(name, r'^[0-9a-f]{32}\.png$')
            self.assertTrue((self.upload_dir / name).is_file())
        self.assertNotIn('original.png', self.path.read_text())
        self.login('admin', 'admin123')
        self.review('/review/' + product['id'], data={'decision': 'approved'})
        public = self.app.test_client()
        page = public.get('/').get_data(as_text=True)
        self.assertIn('/images/' + product['cover'], page)
        self.assertNotIn('/images/' + product['images'][0], page)
        detail = public.get('/products/' + product['id']).get_data(as_text=True)
        for name in product['images']:
            self.assertIn('/images/' + name, detail)
        response = public.get('/products/' + product['id'] + '/images/' + product['cover'],
                              buffered=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/png')
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_hidden_image_urls_and_private_roles(self):
        """Published links stop working publicly after off-shelf or re-review."""
        self.client.post('/submit', data=self.fields())
        product = load_data(self.path)['products'][0]
        link = '/products/' + product['id'] + '/images/' + product['cover']
        public = self.app.test_client()
        self.assertEqual(public.get(link, buffered=True).status_code, 404)
        self.assertEqual(self.client.get(link, buffered=True).status_code, 200)
        self.login('other', 'other123')
        self.assertEqual(self.client.get(link, buffered=True).status_code, 404)
        self.login('admin', 'admin123')
        self.assertEqual(self.client.get(link, buffered=True).status_code, 200)
        self.review('/review/' + product['id'], data={'decision': 'approved'})
        self.assertEqual(public.get(link, buffered=True).status_code, 200)
        self.login('user', 'user123')
        self.client.post('/off-shelf/' + product['id'])
        self.assertEqual(public.get(link, buffered=True).status_code, 404)
        self.assertEqual(public.get(link, headers={'If-Modified-Since':
                         'Wed, 31 Dec 2099 23:59:59 GMT'}).status_code, 404)
        self.assertEqual(self.client.get(link, buffered=True).status_code, 200)
        fields = self.fields()
        fields.pop('images')
        self.client.post('/edit/' + product['id'], data=fields)
        self.assertEqual(load_data(self.path)['products'][0]['status'], 'pending')
        self.assertEqual(public.get(link, buffered=True).status_code, 404)
        self.login('admin', 'admin123')
        self.assertEqual(self.client.get(link, buffered=True).status_code, 200)
        self.assertEqual(public.get('/static/uploads/' + product['cover']).status_code, 404)
        self.assertEqual(self.client.get('/products/' + product['id'] +
                                         '/images/unknown.png').status_code, 404)

    def test_edit_remove_cover_and_reservation_lock(self):
        """Keep photos by default, choose a new cover, and reject image edits during booking."""
        self.client.post('/submit', data=self.fields())
        product = load_data(self.path)['products'][0]
        old = product['cover']
        self.client.post('/withdraw/' + product['id'])
        self.assertEqual(self.client.post('/edit/' + product['id'], data=self.fields(
            cover_position='2')).status_code, 302)
        product = load_data(self.path)['products'][0]
        self.assertEqual(product['cover'], product['images'][1])
        self.client.post('/withdraw/' + product['id'])
        fields = self.fields(action='save', remove_images=product['images'])
        fields.pop('images')
        self.assertEqual(self.client.post('/edit/' + product['id'], data=fields).status_code, 302)
        self.assertEqual(load_data(self.path)['products'][0]['images'], [])
        self.assertEqual(self.client.get('/products/' + product['id'] + '/images/' + old).
                         status_code, 404)
        self.assertEqual(self.client.post('/edit/' + product['id'], data={
            **fields, 'action': 'resubmit', 'remove_images': []}).status_code, 400)
        self.client.post('/edit/' + product['id'], data=self.fields())
        self.login('admin', 'admin123')
        self.review('/review/' + product['id'], data={'decision': 'approved'})
        self.login('buyer', 'buyer123')
        self.reserve(product['id'])
        self.login('user', 'user123')
        before = self.path.read_bytes()
        files_before = set(self.upload_dir.iterdir())
        self.assertEqual(self.client.post('/edit/' + product['id'], data=self.fields()).
                         status_code, 409)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(set(self.upload_dir.iterdir()), files_before)

    def test_image_limits_invalid_contents_and_cover(self):
        """No images on submission, six images, SVG, invalid bytes or cover are rejected."""
        invalid_forms = [
            self.fields(images=[]),
            self.fields(images=[image_upload() for _ in range(6)]),
            self.fields(images=(BytesIO(b'<svg xmlns="http://www.w3.org/2000/svg"/>'), 'x.svg')),
            self.fields(images=(BytesIO(b'not a picture'), 'pretend.png')),
            self.fields(cover_position='2'),
            self.fields(remove_images=['someone-else.png']),
        ]
        with BytesIO(b'x' * (5 * 1024 * 1024 + 1)) as stream:
            with self.assertRaises(ValueError):
                decode_upload(FileStorage(stream=stream))
        for fields in invalid_forms:
            self.assertEqual(self.client.post('/submit', data=fields).status_code, 400)
            self.assertEqual(load_data(self.path)['products'], [])
            self.assertFalse(self.upload_dir.exists() and any(self.upload_dir.iterdir()))
        self.assertEqual(self.client.post('/submit', data=self.fields(
            images=[image_upload() for _ in range(5)], cover_position='5')).status_code, 302)
        self.assertEqual(len(load_data(self.path)['products'][0]['images']), 5)

    def test_json_failure_cleans_new_files_and_preserves_existing_images(self):
        """A failed JSON replacement never destroys existing referenced image files."""
        self.client.post('/submit', data=self.fields(action='save'))
        before = self.path.read_bytes()
        files_before = {p.name: p.read_bytes() for p in self.upload_dir.iterdir()}
        with patch('storage.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.client.post('/submit', data=self.fields())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual({p.name: p.read_bytes() for p in self.upload_dir.iterdir()}, files_before)

    def test_legacy_images_placeholder(self):
        """Missing image fields remain readable and show a placeholder."""
        product_id = self.publish('旧图片数据')
        with transaction(self.path) as data:
            del data['products'][0]['images']
            del data['products'][0]['cover']
        self.assertEqual(load_data(self.path)['products'][0]['images'], [])
        page = self.client.get('/products/' + product_id)
        self.assertEqual(page.status_code, 200)
        self.assertIn('暂无商品图片', page.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
