"""State- and participant-scoped contact disclosure; never return full user records."""
from flask import abort, make_response, render_template, session

from accounts import require_role
from storage import load_data

CONTACT_STATES = ('waiting', 'confirmed', 'completed')


def counterparty_contact(data, reservation, viewer):
    """Return only the other party's contact, or None when access is forbidden."""
    if (reservation['status'] not in CONTACT_STATES
            or viewer not in (reservation['seller'], reservation['buyer'])):
        return None
    other = reservation['buyer'] if viewer == reservation['seller'] else reservation['seller']
    user = next((item for item in data['users'] if item['username'] == other), {})
    return user.get('contact_info') or '未填写'


def private_response(body):
    """Keep state-dependent private pages and responses out of shared/browser caches."""
    response = make_response(body)
    response.headers['Cache-Control'] = 'private, no-store, max-age=0'
    return response


def register_contact_routes(application):
    """Expose a guarded endpoint so a stale page cannot bypass current-state checks."""
    @application.get('/reservations/<reservation_id>/contact')
    @require_role('user')
    def reservation_contact(reservation_id):
        data = load_data(application.config['DATA_PATH'])
        reservation = next((item for item in data['reservations']
                            if item['id'] == reservation_id), None)
        if reservation is None:
            abort(404)
        contact = counterparty_contact(data, reservation, session['username'])
        if contact is None:
            abort(403)
        return private_response({'contact_info': contact})

    @application.get('/reservations')
    @require_role('user')
    def my_reservations():
        data = load_data(application.config['DATA_PATH'])
        records = [{**item, 'expected_version': next(product['version']
                    for product in data['products'] if product['id'] == item['product_id']),
                    'counterparty_contact': counterparty_contact(
            data, item, session['username'])} for item in data['reservations']
                   if session['username'] in (item['seller'], item['buyer'])]
        return private_response(render_template('reservations.html', reservations=records))
