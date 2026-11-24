"""Public catalog filtering and detail pages; hidden products never enter queries."""
from decimal import Decimal, InvalidOperation
import re

from flask import abort, render_template, request, url_for

from storage import load_products

CATEGORIES = ('图书教材', '数码电子', '生活用品', '其他')
SORT_LABELS = {'newest': '发布时间从新到旧', 'oldest': '发布时间从旧到新',
               'price_asc': '价格从低到高', 'price_desc': '价格从高到低'}


def parse_bound(text):
    """Parse an optional nonnegative currency bound without float rounding."""
    if not text.strip():
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation as error:
        raise ValueError('请输入合法的价格区间。') from error
    if (not amount.is_finite() or not 0 <= amount <= 100000
            or amount.as_tuple().exponent < -2):
        raise ValueError('价格区间须为0至100000元，最多两位小数。')
    return amount


def query_products(products, filters):
    """Intersect filters and sort stably, using product id to break equal values."""
    minimum = parse_bound(filters.get('min_price', ''))
    maximum = parse_bound(filters.get('max_price', ''))
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError('最低价不能高于最高价。')
    sort = filters.get('sort', 'newest')
    if sort not in SORT_LABELS:
        raise ValueError('请选择有效的排序方式。')
    keyword = filters.get('q', '').strip().casefold()
    category = filters.get('category', '')
    found = [item for item in products if item['status'] == 'approved'
             and keyword in (item['title'] + ' ' + item['description']).casefold()
             and (not category or item['category'] == category)
             and (minimum is None or Decimal(item['price']) >= minimum)
             and (maximum is None or Decimal(item['price']) <= maximum)]
    found.sort(key=lambda item: item['id'])
    key = ((lambda item: Decimal(item['price'])) if sort.startswith('price')
           else (lambda item: item['published_at']))
    return sorted(found, key=key, reverse=sort in ('newest', 'price_desc'))


def paginate_products(products, filters):
    """Slice the already-filtered, sorted result into stable 20-item pages."""
    text = filters.get('page', '1')
    if not re.fullmatch(r'[0-9]{1,9}', text) or int(text) < 1:
        raise ValueError('页码须为正整数。')
    page = int(text)
    total = len(products)
    pages = max(1, (total + 19) // 20)
    if page > pages:
        abort(404, '该页不存在，请返回商品列表')
    parameters = {key: filters[key] for key in
                  ('q', 'category', 'min_price', 'max_price', 'sort') if key in filters}
    previous = url_for('public_list', **parameters, page=page - 1) if page > 1 else None
    following = url_for('public_list', **parameters, page=page + 1) if page < pages else None
    return products[(page - 1) * 20:page * 20], {
        'page': page, 'pages': pages, 'total': total, 'previous': previous, 'next': following}


def register_catalog_routes(application):
    """Register public list and public-only details using the existing card template."""
    @application.get('/')
    def public_list():
        products = load_products(application.config['DATA_PATH'])
        categories = sorted(set(CATEGORIES) | {
            item['category'] for item in products if item['status'] == 'approved'})
        try:
            found = query_products(products, request.args)
            page_products, pagination = paginate_products(found, request.args)
        except ValueError as error:
            return render_template('products.html', heading='校园好物', products=[],
                                   search=True, categories=categories, sorts=SORT_LABELS,
                                   error=str(error)), 400
        return render_template('products.html', heading='校园好物', products=page_products,
                               pagination=pagination, search=True, categories=categories,
                               sorts=SORT_LABELS)

    @application.get('/products/<product_id>')
    def public_detail(product_id):
        product = next((item for item in load_products(application.config['DATA_PATH'])
                        if item['id'] == product_id and item['status'] == 'approved'), None)
        if product is None:
            abort(404)
        return render_template('products.html', heading='商品详情', products=[product])
