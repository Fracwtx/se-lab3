"""REQ-007/010 regressions for seller off-shelf actions and stable public pages."""
from html.parser import HTMLParser
import unittest
from urllib.parse import parse_qs, urlsplit

from storage import load_data, transaction
from test_app import MarketCase


class CatalogPage(HTMLParser):
    """Read displayed titles and pagination links from actual rendered HTML."""

    def __init__(self, html):
        super().__init__()
        self.titles = []
        self.links = {}
        self.in_title = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'h2':
            self.in_title = True
        if tag == 'a' and values.get('rel') in ('prev', 'next'):
            self.links[values['rel']] = values['href']

    def handle_endtag(self, tag):
        if tag == 'h2':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.titles.append(data)


class OffShelfTest(MarketCase):
    """Only a seller's unreserved published product can be taken off shelf."""

    def test_off_shelf_visibility_and_reaudit_history(self):
        """Off-shelf edits do not republish; reapproval appends rather than replaces history."""
        product_id = self.publish('下架演示')
        self.login('user', 'user123')
        before = load_data(self.path)
        self.assertEqual(self.client.post('/off-shelf/' + product_id).status_code, 302)
        data = load_data(self.path)
        self.assertEqual(data['products'][0]['status'], 'off_shelf')
        self.assertEqual(data['products'][0]['version'], before['products'][0]['version'] + 1)
        self.assertEqual(data['reviews'], before['reviews'])
        self.assertNotIn('<h2>下架演示', self.client.get('/?q=下架').get_data(as_text=True))
        self.assertEqual(self.client.get('/products/' + product_id).status_code, 404)
        self.assertEqual(self.client.post('/off-shelf/' + product_id).status_code, 409)
        fields = {'delivery_location': '图书馆东门',
        'title': '下架后修改', 'description': '更新描述', 'price': '15.00',
                  'action': 'save'}
        self.assertEqual(self.client.get('/edit/' + product_id).status_code, 200)
        self.assertEqual(self.client.post('/edit/' + product_id, data=fields).status_code, 302)
        self.assertEqual(load_data(self.path)['products'][0]['status'], 'off_shelf')
        self.login('buyer', 'buyer123')
        self.assertEqual(self.reserve(product_id).status_code, 409)
        self.login('user', 'user123')
        self.assertEqual(self.client.post('/edit/' + product_id, data={
            **fields, 'action': 'resubmit', 'status': 'approved'}).status_code, 302)
        self.assertEqual(load_data(self.path)['products'][0]['status'], 'pending')
        self.assertEqual(self.client.get('/products/' + product_id).status_code, 404)
        self.login('admin', 'admin123')
        self.review('/review/' + product_id, data={'decision': 'approved'})
        self.assertEqual(self.client.get('/products/' + product_id).status_code, 200)
        self.assertEqual(len(load_data(self.path)['reviews']), 2)
        self.assertIn('<h2>下架后修改', self.client.get('/').get_data(as_text=True))

    def test_off_shelf_permissions_states_and_active_reservations(self):
        """Reject visitors, nonowners, forbidden states and both kinds of active reservation."""
        product_id = self.publish()
        self.client.post('/logout')
        for username in (None, 'buyer', 'admin'):
            if username:
                self.login(username, username + '123')
            before = self.business_bytes()
            self.assertEqual(self.client.post('/off-shelf/' + product_id).status_code, 403)
            self.assertEqual(self.business_bytes(), before)
        self.login('user', 'user123')
        for status in ('draft', 'pending', 'reserved', 'confirmed', 'completed', 'violation'):
            with self.subTest(status=status):
                with transaction(self.path) as data:
                    data['products'][0]['status'] = status
                before = self.business_bytes()
                self.assertEqual(self.client.post('/off-shelf/' + product_id).status_code, 409)
                self.assertEqual(self.business_bytes(), before)
                if status in ('completed', 'violation'):
                    self.assertEqual(self.client.post('/edit/' + product_id, data={
                        'action': 'resubmit'}).status_code, 409)
        with transaction(self.path) as data:
            data['products'][0]['status'] = 'approved'
        self.login('buyer', 'buyer123')
        self.reserve(product_id)
        self.login('user', 'user123')
        reservation_id = load_data(self.path)['reservations'][0]['id']
        self.assertEqual(self.client.post('/off-shelf/' + product_id).status_code, 409)
        self.client.post('/confirm/' + reservation_id)
        self.assertEqual(self.client.post('/off-shelf/' + product_id).status_code, 409)
        # Check the active-reservation guard independently of the product-status guard.
        with transaction(self.path) as data:
            data['products'][0]['status'] = 'approved'
        before = self.business_bytes()
        self.assertEqual(self.client.post('/off-shelf/' + product_id).status_code, 409)
        self.assertEqual(self.business_bytes(), before)


class PaginationTest(MarketCase):
    """Render multiple pages of tied values and assert exact coverage and query retention."""

    def setUp(self):
        super().setUp()
        with transaction(self.path) as data:
            data['products'] = [
                {'id': f'{index:03}', 'delivery_location': '图书馆东门',
                'title': f'教材{index:03}', 'description': '良好',
                 'price': '10.00', 'category': '图书教材' if index < 25 else '其他',
                 'status': 'approved', 'seller': 'user', 'reason': '',
                 'published_at': '2026-11-24T00:00:00+00:00'}
                for index in reversed(range(45))]
            data['products'].append({**data['products'][0], 'id': 'hidden',
                                     'title': '隐藏教材', 'status': 'pending'})

    def page(self, url):
        """Parse an actual public GET response, asserting HTTP success."""
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return CatalogPage(response.get_data(as_text=True))

    def test_tied_sort_pages_have_no_duplicates_or_omissions(self):
        """ID order remains ascending across page boundaries for every primary sort."""
        for sort in ('newest', 'oldest', 'price_asc', 'price_desc'):
            with self.subTest(sort=sort):
                first = self.page('/?sort=' + sort)
                second = self.page(first.links['next'])
                third = self.page(second.links['next'])
                self.assertEqual([len(page.titles) for page in (first, second, third)],
                                 [20, 20, 5])
                self.assertEqual(first.titles + second.titles + third.titles,
                                 [f'教材{index:03}' for index in range(45)])
                self.assertNotIn('prev', first.links)
                self.assertNotIn('next', third.links)
                self.assertEqual(self.page(second.links['prev']).titles, first.titles)
                self.assertEqual(self.page(first.links['next']).titles, second.titles)

    def test_filtered_pages_preserve_all_query_parameters(self):
        """Filter before slicing and retain the complete intersection when following links."""
        query = '/?q=教材&category=图书教材&min_price=10&max_price=10&sort=price_desc'
        first = self.page(query)
        second = self.page(first.links['next'])
        self.assertEqual([len(first.titles), len(second.titles)], [20, 5])
        self.assertEqual(first.titles + second.titles,
                         [f'教材{index:03}' for index in range(25)])
        parameters = parse_qs(urlsplit(first.links['next']).query)
        self.assertEqual(parameters, {'q': ['教材'], 'category': ['图书教材'],
                                      'min_price': ['10'], 'max_price': ['10'],
                                      'sort': ['price_desc'], 'page': ['2']})
        self.assertEqual(self.page(second.links['prev']).titles, first.titles)
        self.assertNotIn('next', second.links)

    def test_page_validation_and_empty_results(self):
        """Invalid pages return400, unavailable pages404, empty first pages remain useful."""
        for value in ('0', '-1', 'abc', '1.5', ''):
            self.assertEqual(self.client.get('/?page=' + value).status_code, 400)
        self.assertEqual(self.client.get('/?page=4').status_code, 404)
        empty = self.page('/?q=不存在')
        self.assertEqual(empty.titles, [])
        self.assertEqual(empty.links, {})
        self.assertEqual(self.client.get('/?q=不存在&page=2').status_code, 404)


if __name__ == '__main__':
    unittest.main()
