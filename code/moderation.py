"""Atomic violation handling and release, with state-scoped administrator contacts."""
from copy import deepcopy
from hashlib import sha256
import json
from uuid import uuid4

from flask import abort, redirect, render_template, request, session, url_for

from accounts import require_role
from privacy import CONTACT_STATES, private_response
from storage import load_data, transaction
from termination import audit, record


def related_reservations(data, product_id):
    """Return all linked history, not merely the current effective reservation."""
    return [item for item in data['reservations'] if item['product_id'] == product_id]


def handling_snapshot(product, reservations):
    """Detect changes in the product and any linked reservation since page rendering."""
    content = {'product': product,
               'reservations': sorted(reservations, key=lambda item: item['id'])}
    return sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def check_links(product, reservations):
    """Reject inconsistent effective links rather than partially repairing trade history."""
    active = [item for item in reservations if item['status'] in ('waiting', 'confirmed')]
    expected = {'reserved': 'waiting', 'confirmed': 'confirmed'}.get(product['status'])
    if len(active) > 1:
        abort(409, '同一商品存在多条有效预约')
    if ((expected and (len(active) != 1 or active[0]['status'] != expected))
            or (not expected and active)):
        abort(409, '商品与有效预约状态不一致，请刷新并核对')


def processing_contacts(data, reservations, product):
    """Disclose contacts only for eligible trades before new violation processing."""
    if product.get('violation_active'):
        return {}
    return {item['id']: {side: next((user.get('contact_info') or '未填写'
            for user in data['users'] if user['username'] == item[side]), '未填写')
            for side in ('buyer', 'seller')} for item in reservations
            if item['status'] in CONTACT_STATES}


def save_moderation_event(data, entry):
    """Keep before/after history and notification intents in the shared atomic document."""
    data['moderation_events'].append(entry)
    recipients = {entry['before']['product']['seller']} | {
        item['buyer'] for item in entry['before']['reservations']
        if item['status'] in CONTACT_STATES}
    for recipient in sorted(recipients):
        data['notifications'].append({'id': uuid4().hex, 'recipient': recipient,
                                      'moderation_id': entry['id'], 'event': entry['action'],
                                      'status': 'pending', 'created_at': entry['at']})


def apply_moderation(data, product, reservations, decision):
    """Apply an already validated decision without reviving completed trades."""
    action, reason, now = (decision[key] for key in ('action', 'reason', 'at'))
    before = deepcopy({'product': product, 'reservations': reservations})
    if action == 'release':
        if not product.get('violation_active'):
            abort(409, '当前没有违规限制')
        product['violation_active'] = False
        if product['status'] != 'completed':
            product['status'] = 'off_shelf'
    else:
        product['violation_active'] = True
        if product['status'] != 'completed':
            product['status'] = 'violation'
        for reservation in reservations:
            if reservation['status'] not in ('waiting', 'confirmed'):
                continue
            reservation.update(status='terminated', reason=reason, ended_at=now)
            for application in data['termination_requests']:
                if (application['reservation_id'] == reservation['id']
                        and application['status'] == 'pending'):
                    application.update(status='closed', reason='管理员违规处置关闭申请',
                                       closed_at=now)
                    audit(data, application, 'closed_by_violation', now)
    product['version'] += 1
    save_moderation_event(data, {
        'id': uuid4().hex, 'product_id': product['id'], 'action': action, 'reason': reason,
        'actor': session['username'], 'at': now, 'before': before,
        'after': deepcopy({'product': product, 'reservations': reservations})})


def register_moderation_routes(app):
    """Expose protected preview/action pages; history never includes account contacts."""
    @app.get('/moderation')
    @require_role('admin')
    def moderation_queue():
        data = load_data(app.config['DATA_PATH'])
        return private_response(render_template('moderation_queue.html',
                                products=data['products'], events=data['moderation_events']))

    @app.route('/moderation/<product_id>', methods=['GET', 'POST'])
    @require_role('admin')
    def moderate_product(product_id):
        with transaction(app.config['DATA_PATH']) as data:
            product = record(data['products'], product_id)
            reservations = related_reservations(data, product_id)
            check_links(product, reservations)
            if request.method == 'POST':
                if (request.form.get('expected_version') != str(product['version'])
                        or request.form.get('snapshot') !=
                        handling_snapshot(product, reservations)):
                    abort(409, '商品或关联预约已变化，请刷新处置页')
                reason = request.form.get('reason', '').strip()
                action = request.form.get('action')
                if action not in ('restrict', 'release') or not 1 <= len(reason) <= 500:
                    abort(400, '请选择处置并填写1–500字原因')
                apply_moderation(data, product, reservations, {
                    'action': action, 'reason': reason, 'at': app.config['CLOCK']().isoformat()})
            else:
                return private_response(render_template('moderation.html', product=product,
                                        reservations=reservations,
                                        snapshot=handling_snapshot(product, reservations),
                                        contacts=processing_contacts(data, reservations, product)))
        return redirect(url_for('moderation_queue'))
