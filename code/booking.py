"""REQ-017/018 booking identity and optimistic checks inside the JSON transaction."""
from datetime import timedelta
from decimal import Decimal
import re
from uuid import uuid4

from flask import abort, g

from catalog import parse_bound


def reservation_times(now):
    """Serialize creation and a strict 24-hour confirmation deadline."""
    return {'created_at': now.isoformat(), 'expires_at': (now + timedelta(hours=24)).isoformat()}


def parse_booking_form(product_id, form):
    """Require a bounded action id and normalize all fingerprint inputs."""
    request_id = form.get('request_id', '')
    version = form.get('expected_version', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id):
        raise ValueError('预约请求标识无效，请刷新页面后重新确认。')
    if not re.fullmatch(r'[0-9]{1,10}', version) or int(version) < 1:
        raise ValueError('商品版本缺失或无效，请刷新页面后重新确认。')
    price = parse_bound(form.get('expected_price', ''))
    if price is None or price <= 0:
        raise ValueError('请先确认页面中的商品价格。')
    return request_id, {'product_id': product_id, 'expected_version': int(version),
                        'expected_price': format(price, '.2f')}


def find_booking_request(data, buyer, request_id):
    """Action ids are unique per buyer, never global across all buyers."""
    return next((item for item in data['booking_requests']
                 if item['buyer'] == buyer and item['request_id'] == request_id), None)


def booking_decision(data, buyer, content):
    """Return a business result without writing; active own reservations take priority."""
    product = next((item for item in data['products']
                    if item['id'] == content['product_id']), None)
    if product is None:
        return 'not_found', '商品不存在。', 404, None
    if product['seller'] == buyer:
        return 'forbidden', '不能预约自己的商品。', 403, None
    active = next((item for item in data['reservations']
                   if item['product_id'] == product['id']
                   and item['status'] in ('waiting', 'confirmed')), None)
    if active and active['buyer'] == buyer:
        return 'existing', '已有有效预约，沿用原预约。', 302, active
    if active or product['status'] != 'approved' or product.get('violation_active'):
        return 'unavailable', '商品已被预约或当前不可售，请刷新列表。', 409, None
    if (product['version'] != content['expected_version']
            or Decimal(product['price']) != Decimal(content['expected_price'])):
        return ('changed', f"商品内容或价格已变化，当前价格 ¥ {product['price']}；"
                '请刷新页面并重新确认，使用新的预约请求。', 409, None)
    return 'created', '预约成功。', 302, None


def process_booking(data, buyer, request_id, content, now):
    """Replay first results or atomically append one request, reservation and state change.

    The caller must hold transaction() throughout this function. Business failures
    are recorded too; I/O failures leave no misleading committed request result.
    """
    previous = find_booking_request(data, buyer, request_id)
    if previous is not None:
        if previous['content'] != content:
            abort(409, '相同请求标识携带不同内容；请保留原请求，新动作使用新标识。')
        g.booking_replay = True
        return previous
    code, message, status, reservation = booking_decision(data, buyer, content)
    if code == 'created':
        product = next(item for item in data['products'] if item['id'] == content['product_id'])
        reservation = {
            'id': uuid4().hex, 'product_id': product['id'], 'seller': product['seller'],
            'buyer': buyer, 'title': product['title'], 'price': product['price'],
            'product_version': product['version'], 'status': 'waiting', 'reason': '',
            **reservation_times(now)}
        data['reservations'].append(reservation)
        product['status'] = 'reserved'
        product['version'] += 1
    result = {'buyer': buyer, 'request_id': request_id, 'content': content,
              'result_code': code, 'message': message, 'status_code': status,
              'reservation_id': reservation['id'] if reservation else None,
              'created_at': now.isoformat()}
    data['booking_requests'].append(result)
    return result


def associated_reservation(data, result):
    """Read the linked reservation's current state, not its original waiting state."""
    return next((item for item in data['reservations']
                 if item['id'] == result['reservation_id'] and item['buyer'] == result['buyer']),
                None)
