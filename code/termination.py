"""Confirmed-trade termination requests, scoped administrator handling and audit."""
from uuid import uuid4

from flask import abort, redirect, render_template, request, session, url_for

from accounts import require_role
from operations import check_version
from privacy import private_response
from storage import load_data, transaction


def record(records, identifier):
    """Find a linked record without exposing other objects."""
    result = next((item for item in records if item['id'] == identifier), None)
    if result is None:
        abort(404)
    return result


def linked_records(data, application):
    """Resolve the reservation and product for a stored application."""
    reservation = record(data['reservations'], application['reservation_id'])
    return reservation, record(data['products'], reservation['product_id'])


def audit(data, application, event, now):
    """Persist processing history and notification intents in the same document."""
    data['termination_events'].append({
        'id': uuid4().hex, 'application_id': application['id'], 'event': event,
        'actor': session['username'], 'at': now})
    reservation, _ = linked_records(data, application)
    for recipient in (reservation['buyer'], reservation['seller']):
        data['notifications'].append({'id': uuid4().hex, 'recipient': recipient,
                                      'application_id': application['id'],
                                      'event': event, 'status': 'pending', 'created_at': now})


def close_on_completion(data, reservation_id, now):
    """Completing a trade closes pending applications before releasing the write lock."""
    for application in data['termination_requests']:
        if application['reservation_id'] == reservation_id and application['status'] == 'pending':
            application.update(status='closed', closed_at=now, reason='买家已确认完成交易')
            audit(data, application, 'closed', now)


def validate_decision(form):
    """Require recorded opinions and explicit administrator verification of consent."""
    fields = {key: form.get(key, '').strip() for key in
              ('decision', 'reason', 'buyer_opinion', 'seller_opinion')}
    if fields['decision'] not in ('approved', 'rejected'):
        abort(400, '无效审批决定')
    if any(not 1 <= len(fields[key]) <= 500 for key in
           ('reason', 'buyer_opinion', 'seller_opinion')):
        abort(400, '须填写双方意见及1–500字处理原因')
    fields['buyer_agreed'] = form.get('buyer_agreed') == 'yes'
    fields['seller_agreed'] = form.get('seller_agreed') == 'yes'
    if fields['decision'] == 'approved' and not (
            fields['buyer_agreed'] and fields['seller_agreed']):
        abort(400, '未核实双方同意，不能普通终止交易')
    return fields


def register_termination_routes(app):
    """Keep contact disclosure exclusively on a live, assigned handling page."""
    @app.post('/reservations/<reservation_id>/termination')
    @require_role('user')
    def request_termination(reservation_id):
        reason = request.form.get('reason', '').strip()
        if not 1 <= len(reason) <= 500:
            abort(400, '申请须填写1–500字理由')
        with transaction(app.config['DATA_PATH']) as data:
            reservation = record(data['reservations'], reservation_id)
            if session['username'] not in (reservation['buyer'], reservation['seller']):
                abort(403)
            product = record(data['products'], reservation['product_id'])
            if reservation['status'] != 'confirmed' or product['status'] != 'confirmed':
                abort(409, '仅已确认且未完成的交易可以申请终止')
            if not any(item['reservation_id'] == reservation_id and item['status'] == 'pending'
                       for item in data['termination_requests']):
                check_version(product)
                now = app.config['CLOCK']().isoformat()
                application = {'id': uuid4().hex, 'reservation_id': reservation_id,
                               'applicant': session['username'], 'request_reason': reason,
                               'created_at': now, 'status': 'pending', 'handler': ''}
                data['termination_requests'].append(application)
                audit(data, application, 'requested', now)
        return redirect(url_for('my_reservations'))

    @app.get('/terminations')
    @require_role('admin')
    def termination_queue():
        return private_response(render_template('terminations.html', applications=
                                load_data(app.config['DATA_PATH'])['termination_requests']))

    @app.route('/terminations/<application_id>', methods=['GET', 'POST'])
    @require_role('admin')
    def handle_termination(application_id):
        with transaction(app.config['DATA_PATH']) as data:
            application = record(data['termination_requests'], application_id)
            reservation, product = linked_records(data, application)
            if (application['status'] != 'pending' or reservation['status'] != 'confirmed'
                    or product['status'] != 'confirmed'):
                abort(409, '申请或交易已处理，请刷新页面')
            if application['handler'] not in ('', session['username']):
                abort(403)
            if request.method == 'POST':
                expected = request.form.get('expected_version', '')
                if expected != str(product['version']):
                    abort(409, '交易版本已变化，请刷新页面')
                if not application['handler']:
                    abort(409, '请先打开处置页核实双方意见')
                fields = validate_decision(request.form)
                now = app.config['CLOCK']().isoformat()
                application.update(fields, status=fields['decision'], handled_at=now)
                if fields['decision'] == 'approved':
                    reservation.update(status='terminated', ended_at=now,
                                       reason=fields['reason'])
                    product.update(status='off_shelf', reason=fields['reason'])
                    product['version'] += 1
                audit(data, application, fields['decision'], now)
            else:
                application['handler'] = session['username']
                contacts = {side: next((user.get('contact_info') or '未填写'
                            for user in data['users'] if user['username'] == reservation[side]),
                            '未填写') for side in ('buyer', 'seller')}
                return private_response(render_template('termination.html',
                                        application=application, reservation=reservation,
                                        product=product, contacts=contacts))
        return redirect(url_for('termination_queue'))
