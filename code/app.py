"""Campus market: submit, review and display products using Flask forms."""
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from flask import Flask, abort, redirect, render_template, request, session, url_for

from accounts import current_role, register_account_routes, require_role
from booking import (associated_reservation, find_booking_request, parse_booking_form,
                     process_booking, reservation_times)
from catalog import register_catalog_routes
from storage import DEFAULT_PATH, load_data, load_products, transaction

STATUS_LABELS = {'draft': '草稿', 'pending': '待审核', 'approved': '已发布',
                 'reserved': '已预约', 'waiting': '待卖家确认',
                 'confirmed': '已确认', 'completed': '已完成',
                 'off_shelf': '已下架', 'cancelled': '已取消', 'rejected': '已拒绝', 'timed_out': '已超时'}


def validate_product(form, *, partial=False):
    """Validate text and an exact positive decimal amount, stored as a string."""
    title = form.get('title', '').strip()
    description = form.get('description', '').strip()
    if (len(title) > 100 or len(description) > 2000
            or (not partial and (not title or not description))):
        raise ValueError('标题须为1–100字，描述须为1–2000字。')
    category = form.get('category', '其他').strip() or '其他'
    if len(category) > 50:
        raise ValueError('分类不能超过50字。')
    price_text = form.get('price', '').strip()
    if partial and not price_text:
        return {'title': title, 'description': description, 'price': '', 'category': category}
    try:
        price = Decimal(price_text)
    except InvalidOperation as error:
        raise ValueError('请填写有效价格。') from error
    if (not price.is_finite() or not Decimal('0') < price <= Decimal('100000')
            or price.as_tuple().exponent < -2):
        raise ValueError('价格须大于0且不超过100000元，最多两位小数。')
    return {'title': title, 'description': description, 'price': format(price, '.2f'),
            'category': category}


def find_record(records, record_id):
    """Find an existing record or return a clear HTTP 404."""
    record = next((item for item in records if item['id'] == record_id), None)
    if record is None:
        abort(404)
    return record


def check_owner(username):
    """Reject actions by anyone other than the stored owner."""
    if username != session['username']:
        abort(403)


def check_status(record, expected):
    """Reject stale or out-of-order actions."""
    if record['status'] != expected:
        abort(409, '当前状态不允许此操作，请刷新页面')


def create_app(data_path=None):
    """Create an independent application; tests may supply an isolated JSON path."""
    application = Flask(__name__)
    application.config.update(
        SECRET_KEY=os.environ.get('SECRET_KEY', 'campus-market-local-demo-only'),
        DATA_PATH=data_path or os.environ.get('MARKET_DATA_PATH', DEFAULT_PATH),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        MAX_CONTENT_LENGTH=64 * 1024,
        CLOCK=lambda: datetime.now(timezone.utc),
    )

    @application.context_processor
    def template_context():
        return {'role': current_role(), 'status_labels': STATUS_LABELS,
                'new_request_id': lambda: uuid4().hex}

    @application.route('/submit', methods=['GET', 'POST'])
    @require_role('user')
    def submit_product():
        """Create a product with server-assigned pending status and owner."""
        if request.method == 'POST':
            action = request.form.get('action', 'resubmit')
            if action not in ('save', 'resubmit'):
                abort(400)
            try:
                product = validate_product(request.form, partial=action == 'save')
            except ValueError as error:
                return render_template('submit.html', error=str(error)), 400
            product.update(id=uuid4().hex, seller=session['username'],
                           status='draft' if action == 'save' else 'pending',
                           reason='', reviewer='', version=1, published_at='')
            with transaction(application.config['DATA_PATH']) as data:
                data['products'].append(product)
            return redirect(url_for('my_products'))
        return render_template('submit.html')

    @application.get('/mine')
    @require_role('user')
    def my_products():
        products = load_products(application.config['DATA_PATH'])
        return render_template('products.html', heading='我的商品', products=[
            product for product in products if product['seller'] == session['username']])

    register_account_routes(application)
    register_review_routes(application)
    register_catalog_routes(application)
    register_expiry_check(application)
    register_product_routes(application)
    register_trade_routes(application)
    return application


def register_review_routes(application):
    """Register administrator decisions and role-filtered audit history."""
    @application.get('/admin')
    @require_role('admin')
    def review_queue():
        products = load_products(application.config['DATA_PATH'])
        return render_template('products.html', heading='商品审核', review=True, products=[
            product for product in products if product['status'] == 'pending'])

    @application.post('/review/<product_id>')
    @require_role('admin')
    def review_product(product_id):
        """Apply one administrator decision to a still-pending product."""
        decision = request.form.get('decision')
        reason = request.form.get('reason', '').strip()
        if decision not in ('approved', 'rejected'):
            abort(400, '无效审核结果')
        if decision == 'rejected' and not 1 <= len(reason) <= 500:
            abort(400, '驳回须填写1–500字原因')
        with transaction(application.config['DATA_PATH']) as data:
            product = find_record(data['products'], product_id)
            if product['status'] != 'pending':
                abort(409, '该商品已处理，请刷新列表')
            expected = request.form.get('expected_version', '')
            if not expected.isascii() or not expected.isdecimal() or len(expected) > 10:
                abort(400, '审核版本缺失或无效，请刷新页面')
            if int(expected) != product['version']:
                abort(409, '商品已撤回或修改后重新送审，请刷新页面再审核')
            product.update(status='draft' if decision == 'rejected' else 'approved',
                           reason=reason, reviewer=session['username'])
            reviewed_at = application.config['CLOCK']().isoformat()
            data['reviews'].append({
                'id': uuid4().hex, 'product_id': product_id, 'title': product['title'],
                'seller': product['seller'], 'reviewer': session['username'],
                'reviewed_at': reviewed_at, 'decision': decision, 'reason': reason,
                'reviewed_version': product.get('version', 1)})
            if decision == 'approved':
                product['published_at'] = reviewed_at
            product['version'] += 1
        return redirect(url_for('review_queue'))

    @application.get('/reviews')
    def review_history():
        """Admins see all records; ordinary users see only their own products."""
        role = current_role()
        if role not in ('admin', 'user'):
            abort(403)
        history = load_data(application.config['DATA_PATH'])['reviews']
        product_id = request.args.get('product_id')
        visible = [entry for entry in history
                   if (role == 'admin' or entry['seller'] == session['username'])
                   and (not product_id or entry['product_id'] == product_id)]
        return render_template('reviews.html', reviews=visible)


def expire_reservations(data, now):
    """Expire only waiting reservations, restoring their product in the same document.

    Legacy waiting records have no recoverable creation time: start their 24h window
    on first access and persist it once, rather than reset it on every request.
    """
    for reservation in data['reservations']:
        if reservation['status'] != 'waiting':
            continue
        if 'expires_at' not in reservation:
            created = datetime.fromisoformat(reservation.get('created_at', now.isoformat()))
            reservation.update(reservation_times(created))
        if now >= datetime.fromisoformat(reservation['expires_at']):
            product = find_record(data['products'], reservation['product_id'])
            check_status(product, 'reserved')
            reservation.update(status='timed_out', ended_at=now.isoformat())
            product['status'] = 'approved'
            product['version'] += 1


def register_expiry_check(application):
    """Refresh overdue reservations on page visits and direct business requests."""
    @application.before_request
    def refresh_expired_reservations():
        with transaction(application.config['DATA_PATH']) as data:
            expire_reservations(data, application.config['CLOCK']())


def has_active_reservation(data, product_id):
    """Check effective reservations independently of the product's status."""
    return any(item['product_id'] == product_id and item['status'] in ('waiting', 'confirmed')
               for item in data['reservations'])


def check_editable(data, product):
    """Only the owner may edit permitted products without an active reservation."""
    check_owner(product['seller'])
    if (product['status'] not in ('draft', 'approved', 'off_shelf')
            or has_active_reservation(data, product['id'])):
        abort(409, '当前商品不能编辑')


def register_product_routes(application):
    """Register seller draft, edit and withdrawal actions."""
    @application.route('/edit/<product_id>', methods=['GET', 'POST'])
    @require_role('user')
    def edit_product(product_id):
        """Save an incomplete draft or resubmit validated draft/published information."""
        if request.method == 'GET':
            data = load_data(application.config['DATA_PATH'])
            product = find_record(data['products'], product_id)
            check_editable(data, product)
            return render_template('submit.html', values=product, editing=True,
                                   published=product['status'] == 'approved',
                                   off_shelf=product['status'] == 'off_shelf')
        try:
            with transaction(application.config['DATA_PATH']) as data:
                product = find_record(data['products'], product_id)
                check_editable(data, product)
                action = request.form.get('action')
                if action not in ('save', 'resubmit') or (
                        product['status'] == 'approved' and action != 'resubmit'):
                    abort(400)
                fields = validate_product(request.form, partial=action == 'save')
                product.update(fields)
                if action == 'resubmit':
                    product['status'] = 'pending'
                # Saving off-shelf edits keeps that state; only audit can republish.
                product['version'] += 1
        except ValueError as error:
            return render_template('submit.html', error=str(error), editing=True,
                                   published=product['status'] == 'approved',
                                   off_shelf=product['status'] == 'off_shelf'), 400
        return redirect(url_for('my_products'))

    @application.post('/off-shelf/<product_id>')
    @require_role('user')
    def take_off_shelf(product_id):
        """Only the seller may hide an unreserved published product."""
        with transaction(application.config['DATA_PATH']) as data:
            product = find_record(data['products'], product_id)
            check_owner(product['seller'])
            check_status(product, 'approved')
            if has_active_reservation(data, product_id):
                abort(409, '存在有效预约，不能普通下架')
            product['status'] = 'off_shelf'
            product['version'] += 1
        return redirect(url_for('my_products'))

    @application.post('/withdraw/<product_id>')
    @require_role('user')
    def withdraw_product(product_id):
        """Withdraw only the seller's own still-pending product into draft."""
        with transaction(application.config['DATA_PATH']) as data:
            product = find_record(data['products'], product_id)
            check_owner(product['seller'])
            check_status(product, 'pending')
            product['status'] = 'draft'
            product['version'] += 1
        return redirect(url_for('my_products'))


def register_trade_routes(application):
    """Register reservation and transaction actions."""
    @application.post('/reserve/<product_id>')
    @require_role('user')
    def reserve_product(product_id):
        """Lock, expire, replay or check the buyer's explicitly confirmed version and price."""
        try:
            request_id, content = parse_booking_form(product_id, request.form)
        except ValueError as error:
            abort(400, str(error))
        with transaction(application.config['DATA_PATH']) as data:
            now = application.config['CLOCK']()
            expire_reservations(data, now)
            result = process_booking(data, session['username'], request_id, content, now)
        if result['status_code'] == 302:
            return redirect(url_for('booking_result', request_id=request_id))
        return render_template('booking_result.html', result=result,
                               reservation=None), result['status_code']

    @application.get('/booking-requests/<request_id>')
    @require_role('user')
    def booking_result(request_id):
        """Let only the request's buyer inspect the first result and current progress."""
        data = load_data(application.config['DATA_PATH'])
        result = find_booking_request(data, session['username'], request_id)
        if result is None:
            abort(404)
        return render_template('booking_result.html', result=result,
                               reservation=associated_reservation(data, result))

    @application.get('/reservations')
    @require_role('user')
    def my_reservations():
        """Show only reservations in which the current user participates."""
        records = load_data(application.config['DATA_PATH'])['reservations']
        return render_template('reservations.html', reservations=[
            item for item in records if session['username'] in (item['seller'], item['buyer'])])

    def transition_reservation(reservation_id, owner, old_status, new_status, reason=''):
        """Authorize a transition and commit both records; recheck time under the lock."""
        with transaction(application.config['DATA_PATH']) as data:
            reservation = find_record(data['reservations'], reservation_id)
            check_owner(reservation[owner])
            check_status(reservation, old_status)
            product = find_record(data['products'], reservation['product_id'])
            check_status(product, 'reserved' if old_status == 'waiting' else 'confirmed')
            now = application.config['CLOCK']()
            expired = (old_status == 'waiting' and
                       now >= datetime.fromisoformat(reservation['expires_at']))
            if expired:
                expire_reservations(data, now)
            else:
                reservation.update(status=new_status, reason=reason)
                product['status'] = ('approved' if new_status in ('cancelled', 'rejected')
                                     else new_status)
                product['version'] += 1
                reservation[new_status + '_at'] = now.isoformat()
        if expired:
            abort(409, '预约已超时，商品已恢复发布')
        return redirect(url_for('my_reservations'))

    @application.post('/cancel/<reservation_id>')
    @require_role('user')
    def cancel_reservation(reservation_id):
        """Only the buyer may cancel a waiting reservation."""
        return transition_reservation(reservation_id, 'buyer', 'waiting', 'cancelled')

    @application.post('/reject/<reservation_id>')
    @require_role('user')
    def reject_reservation(reservation_id):
        """Only the seller may reject a waiting reservation, providing a reason."""
        reason = request.form.get('reason', '').strip()
        if not 1 <= len(reason) <= 500:
            abort(400, '拒绝须填写1–500字原因')
        return transition_reservation(reservation_id, 'seller', 'waiting', 'rejected', reason)

    @application.post('/confirm/<reservation_id>')
    @require_role('user')
    def confirm_reservation(reservation_id):
        """Only the seller may confirm a waiting reservation."""
        return transition_reservation(reservation_id, 'seller', 'waiting', 'confirmed')

    @application.post('/complete/<reservation_id>')
    @require_role('user')
    def complete_reservation(reservation_id):
        """Only the buyer may complete a confirmed reservation."""
        return transition_reservation(reservation_id, 'buyer', 'confirmed', 'completed')


if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=5000, debug=False)
