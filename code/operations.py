"""Version guards and administrator read-only operation-log queries."""
from datetime import datetime, timezone
from uuid import uuid4

from flask import abort, g, render_template, request

from accounts import require_role
from eventlog import WRITE_OPERATIONS, operation_entry
from privacy import private_response
from storage import load_data, transaction


def check_version(product):
    """Require the version shown to the operator, inside the caller's transaction."""
    expected = request.form.get('expected_version', '')
    if not expected.isascii() or not expected.isdecimal() or len(expected) > 10:
        abort(400, '页面版本缺失或无效，请刷新页面')
    if expected != str(product['version']):
        abort(409, '页面版本已失效，请刷新页面后重新确认')


def register_failure_logging(app):
    """Commit rejected-operation metadata separately, preserving all business records."""
    @app.before_request
    def identify_operation():
        g.operation_request_id = uuid4().hex
        g.operation_time = datetime.now(timezone.utc).isoformat()

    @app.after_request
    def record_rejection(response):
        if (request.method == 'POST' and request.endpoint in WRITE_OPERATIONS
                and 400 <= response.status_code < 500
                and not g.get('booking_failure_logged') and not g.get('booking_replay')):
            with transaction(app.config['DATA_PATH']) as data:
                identifier = next(iter((request.view_args or {}).values()), 'new')
                known = next((item for table in ('products', 'reservations', 'termination_requests')
                              for item in data[table] if item['id'] == identifier), None)
                data['operation_logs'].append(operation_entry(
                    g.operation_time, operation=request.endpoint,
                    object_id=known['id'] if known else 'unknown',
                    states=((known or {}).get('status', ''), (known or {}).get('status', '')),
                    result=str(response.status_code)))
        return response


def register_log_routes(app):
    """Admins may combine exact filters and an inclusive-start/exclusive-end UTC range."""
    @app.get('/logs')
    @require_role('admin')
    def operation_logs():
        try:
            bounds = {key: datetime.fromisoformat(request.args[key]).replace(
                tzinfo=timezone.utc) if len(request.args[key]) == 10 else
                datetime.fromisoformat(request.args[key]) for key in ('start', 'end')
                      if request.args.get(key)}
            bounds = {key: value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
                      for key, value in bounds.items()}
            if 'start' in bounds and 'end' in bounds and bounds['start'] >= bounds['end']:
                raise ValueError('Invalid time range')
        except ValueError:
            abort(400, '日志时间区间无效，结束时间须晚于开始时间')
        entries = [entry for entry in load_data(app.config['DATA_PATH'])['operation_logs']
                   if all(not request.args.get(key) or entry.get(key) == request.args[key]
                          for key in ('actor', 'object_id', 'operation', 'result'))
                   and ('start' not in bounds or
                        datetime.fromisoformat(entry['at']) >= bounds['start'])
                   and ('end' not in bounds or datetime.fromisoformat(entry['at']) < bounds['end'])]
        return private_response(render_template('logs.html', entries=entries))
