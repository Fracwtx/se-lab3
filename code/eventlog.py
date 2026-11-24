"""Allowlisted transaction metadata and outbox registration; no repository dependencies."""
from uuid import uuid4

from flask import current_app, g, has_request_context, request, session

WRITE_OPERATIONS = {
    'submit_product', 'edit_product', 'withdraw_product', 'take_off_shelf', 'review_product',
    'reserve_product', 'cancel_reservation', 'reject_reservation', 'confirm_reservation',
    'complete_reservation', 'request_termination', 'handle_termination', 'moderate_product'}


def operation_entry(now, *, operation, object_id, states=('', ''), result='success'):
    """Never copy forms, free-text reasons, account records or response text into logs."""
    return {'id': uuid4().hex, 'request_id': g.operation_request_id,
            'actor': session.get('username', 'anonymous'), 'at': now,
            'operation': operation, 'object_id': object_id, 'object_type': 'unknown',
            'before': states[0], 'after': states[1], 'result': result,
            'reason': 'committed' if result == 'success' else 'rejected_' + result}


def changed_records(data, original):
    """Yield business changes, excluding credentials, private fields and log-only writes."""
    for table in ('products', 'reservations', 'reviews', 'termination_requests',
                  'termination_events', 'moderation_events'):
        previous = {item['id']: item for item in original[table]}
        for item in data[table]:
            before = previous.get(item['id'])
            if before != item:
                yield table, before, item


def journal_changes(data, original):
    """Append allowlisted success metadata before the same atomic JSON replacement."""
    if not has_request_context():
        return
    changes = list(changed_records(data, original))
    for table, before, after in changes:
        timeout = table == 'reservations' and after.get('status') == 'timed_out'
        if request.method != 'POST' and not timeout:
            continue
        entry = operation_entry(g.operation_time, operation=(
            'timeout' if timeout else request.endpoint), object_id=after['id'],
            states=((before or {}).get('status', ''), after.get('status', '')))
        entry['object_type'] = table
        if timeout:
            entry['actor'] = 'system'
        data['operation_logs'].append(entry)
    queue_business_notifications(data, changes)
    old_requests = {(item['buyer'], item['request_id']) for item in original['booking_requests']}
    for item in data['booking_requests']:
        if ((item['buyer'], item['request_id']) not in old_requests
                and item['status_code'] >= 400):
            entry = operation_entry(g.operation_time, operation='reserve_product',
                                    object_id=next((product['id'] for product in data['products']
                                                    if product['id'] ==
                                                    item['content']['product_id']), 'unknown'),
                                    result=str(item['status_code']))
            entry['reason'] = item['result_code']
            data['operation_logs'].append(entry)
            g.booking_failure_logged = True


def save_failure_metadata(original, path, attempted, writer):
    """Best effort only: a broken data store cannot guarantee durable failure logging."""
    if not has_request_context() or request.endpoint not in WRITE_OPERATIONS:
        return
    if not list(changed_records(attempted, original)) and (
            attempted['booking_requests'] == original['booking_requests']):
        return
    entry = operation_entry(g.operation_time, operation=request.endpoint,
                            object_id='storage', result='500')
    entry['reason'] = 'storage_error'
    original['operation_logs'].append(entry)
    try:
        writer(original, path)
    except OSError:
        current_app.logger.error('Operation failure log unavailable: storage error')


def queue_business_notifications(data, changes):
    """Register trade/review events in the business save; special handling reuses its outbox."""
    for table, before, after in changes:
        if table == 'reservations' and (before or {}).get('status') != after['status']:
            if after['status'] == 'terminated':
                continue  # Termination/moderation already registers the recipients atomically.
            recipients = (after['seller'], after['buyer'])
            event = 'reservation_' + after['status']
        elif table == 'reviews' and before is None:
            recipients = (after['seller'],)
            event = 'review_' + after['decision']
        else:
            continue
        for recipient in set(recipients):
            data['notifications'].append({'id': uuid4().hex, 'recipient': recipient,
                                          'event': event, 'subject_id': after['id'],
                                          'status': 'pending', 'created_at': g.operation_time})
