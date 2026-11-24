"""Durable local outbox delivery and recipient-scoped inbox/read acknowledgements."""
from uuid import uuid4

from flask import abort, redirect, render_template, request, session, url_for

from accounts import require_role
from notification_worker import NotificationWorker
from privacy import private_response
from storage import load_data, transaction


EVENT_LABELS = {
    'reservation_waiting': '预约已创建，等待卖家确认',
    'reservation_confirmed': '卖家已确认预约', 'reservation_completed': '交易已完成',
    'reservation_cancelled': '买家已取消预约', 'reservation_rejected': '卖家已拒绝预约',
    'reservation_timed_out': '预约已超时，商品恢复发布',
    'review_approved': '商品审核通过', 'review_rejected': '商品审核被驳回',
    'requested': '交易终止申请已提交', 'approved': '交易终止已批准',
    'rejected': '交易终止申请被驳回', 'closed': '交易完成，终止申请已关闭',
    'closed_by_violation': '违规处置已关闭终止申请',
    'restrict': '商品已被违规限制', 'release': '商品违规限制已解除',
}


def deliver_one(app, outbox_id):
    """Commit inbox insertion and acknowledgement together, deduplicating by event/recipient."""
    snapshot = next((entry for entry in load_data(app.config['DATA_PATH'])['notifications']
                     if entry['id'] == outbox_id), None)
    if snapshot is None or snapshot['status'] == 'sent':
        return
    transport = app.config.get('NOTIFICATION_TRANSPORT')
    if transport:
        transport(dict(snapshot))  # Never hold the business lock during slow/failing delivery.
    with transaction(app.config['DATA_PATH']) as data:
        item = next((entry for entry in data['notifications'] if entry['id'] == outbox_id), None)
        if item is None or item['status'] == 'sent':
            return
        existing = any(entry['event_key'] == item['id'] and entry['recipient'] == item['recipient']
                       for entry in data['inbox'])
        if not existing:
            data['inbox'].append({
                'id': uuid4().hex, 'event_key': item['id'], 'recipient': item['recipient'],
                'event': item['event'], 'content': EVENT_LABELS.get(
                    item['event'], '交易状态已更新，请查看我的交易'),
                'subject_id': item.get('subject_id') or item.get(
                    'application_id') or item.get('moderation_id', ''),
                'sent_at': app.config['NOTIFICATION_CLOCK']().isoformat(), 'read_at': ''})
        item.update(status='sent', attempts=item.get('attempts', 0) + 1,
                    sent_at=app.config['NOTIFICATION_CLOCK']().isoformat())
        item.pop('last_error', None)


def deliver_pending(app, recipient=None):
    """Scan durable pending/retry records; delivery failures never replay business actions."""
    pending = [item['id'] for item in load_data(app.config['DATA_PATH'])['notifications']
               if item['status'] in ('pending', 'retry')
               and (recipient is None or item['recipient'] == recipient)]
    for identifier in pending:
        try:
            deliver_one(app, identifier)
        except (OSError, RuntimeError):
            try:
                with transaction(app.config['DATA_PATH']) as data:
                    item = next(entry for entry in data['notifications']
                                if entry['id'] == identifier)
                    if item['status'] != 'sent':
                        item.update(status='retry', last_error='delivery_failed',
                                    attempts=item.get('attempts', 0) + 1)
            except OSError:
                app.logger.error('Notification retry marker unavailable: storage error')


def register_notification_routes(app):
    """Deliver only after commit and keep inbox objects private to their recipient."""
    worker = NotificationWorker(app, deliver_pending)
    app.extensions['notification_worker'] = worker
    if app.config['NOTIFICATION_WORKER']:
        worker.start()

    @app.before_request
    def ensure_notification_worker():
        if app.config['NOTIFICATION_WORKER']:
            worker.start()

    @app.after_request
    def dispatch_after_commit(response):
        if app.config['NOTIFICATION_WORKER']:
            worker.wakeup.set()
        return response

    @app.get('/notifications')
    @require_role('user')
    def my_notifications():
        items = [item for item in load_data(app.config['DATA_PATH'])['inbox']
                 if item['recipient'] == session['username']]
        return private_response(render_template('notifications.html', notifications=items))

    @app.route('/notifications/<notification_id>', methods=['GET', 'POST'])
    @require_role('user')
    def read_notification(notification_id):
        with transaction(app.config['DATA_PATH']) as data:
            item = next((entry for entry in data['inbox'] if entry['id'] == notification_id), None)
            if item is None:
                abort(404)
            if item['recipient'] != session['username']:
                abort(403)
            if request.method == 'POST' and not item['read_at']:
                item['read_at'] = app.config['NOTIFICATION_CLOCK']().isoformat()
            if request.method == 'GET':
                return private_response(render_template('notifications.html', notifications=[item]))
        return redirect(url_for('my_notifications'))

    @app.post('/notifications/retry')
    @require_role('admin')
    def retry_notifications():
        worker.start()
        return redirect(url_for('operation_logs'))
