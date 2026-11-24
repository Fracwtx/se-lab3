"""Demo and persisted ordinary accounts with server-controlled roles."""
from functools import wraps

from flask import (abort, current_app, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from storage import load_data, transaction

DEMO_ACCOUNTS = {
    'user': {'password_hash': generate_password_hash('user123'), 'role': 'user'},
    'buyer': {'password_hash': generate_password_hash('buyer123'), 'role': 'user'},
    'other': {'password_hash': generate_password_hash('other123'), 'role': 'user'},
    'admin': {'password_hash': generate_password_hash('admin123'), 'role': 'admin'},
}


def find_account(username):
    """Resolve demos first, then read persisted users without a process-local cache."""
    if username in DEMO_ACCOUNTS:
        return DEMO_ACCOUNTS[username]
    users = load_data(current_app.config['DATA_PATH'])['users']
    return next((user for user in users if user['username'] == username), None)


def current_role():
    """Only a preset administrator can have the administrator role."""
    username = session.get('username')
    if username in DEMO_ACCOUNTS:
        return DEMO_ACCOUNTS[username]['role']
    return 'user' if username and find_account(username) else None


def require_role(role):
    """Authorize protected endpoints using the server account, never form input."""
    def decorate(function):
        @wraps(function)
        def guarded(*args, **kwargs):
            if current_role() != role:
                abort(403)
            return function(*args, **kwargs)
        return guarded
    return decorate


def validate_registration(form):
    """Require the prototype's four fields and hash the password before storage."""
    username = form.get('username', '').strip()
    nickname = form.get('nickname', '').strip()
    contact = form.get('contact_info', '').strip()
    password = form.get('password', '')
    if not 1 <= len(username) <= 50 or any(char.isspace() for char in username):
        raise ValueError('用户名须为1–50字，且不能包含空白字符。')
    if not 8 <= len(password) <= 128 or not password.strip():
        raise ValueError('密码须为8–128位。')
    if not 1 <= len(nickname) <= 50:
        raise ValueError('昵称须为1–50字。')
    if not 1 <= len(contact) <= 200:
        raise ValueError('联系方式须为1–200字。')
    return {'username': username, 'nickname': nickname, 'contact_info': contact,
            'password_hash': generate_password_hash(password), 'role': 'user'}


def register_account_routes(application):
    """Register signup, login and logout while preserving all demo credentials."""
    @application.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            try:
                account = validate_registration(request.form)
            except ValueError as error:
                return render_template('register.html', error=str(error)), 400
            with transaction(application.config['DATA_PATH']) as data:
                username = account['username']
                if username in DEMO_ACCOUNTS or any(
                        user['username'] == username for user in data['users']):
                    abort(409, '用户名已被使用，请选择其他用户名。')
                data['users'].append(account)
            return redirect(url_for('login'))
        return render_template('register.html')

    @application.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            account = find_account(username)
            if account and check_password_hash(account['password_hash'],
                                               request.form.get('password', '')):
                session.clear()
                session['username'] = username
                return redirect(url_for('public_list'))
            return render_template('login.html', error='账号或密码错误'), 401
        return render_template('login.html')

    @application.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('public_list'))
