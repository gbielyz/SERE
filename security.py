import time


MIN_PASSWORD_LENGTH = 8
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_LOCK_SECONDS = 15 * 60
PANEL_ACTIONS = {"update_students", "create_class", "create_student", "create_event", "create_mission"}

_LOGIN_ATTEMPTS = {}


def login_attempt_key(remote_addr, username):
    return f"{remote_addr or 'local'}:{username.strip().lower()}"


def login_is_locked(remote_addr, username):
    key = login_attempt_key(remote_addr, username)
    attempts = _LOGIN_ATTEMPTS.get(key)
    if not attempts:
        return False
    count, first_seen = attempts
    if time.time() - first_seen > LOGIN_LOCK_SECONDS:
        _LOGIN_ATTEMPTS.pop(key, None)
        return False
    return count >= LOGIN_ATTEMPT_LIMIT


def record_login_failure(remote_addr, username):
    key = login_attempt_key(remote_addr, username)
    count, first_seen = _LOGIN_ATTEMPTS.get(key, (0, time.time()))
    _LOGIN_ATTEMPTS[key] = (count + 1, first_seen)


def clear_login_failures(remote_addr, username):
    _LOGIN_ATTEMPTS.pop(login_attempt_key(remote_addr, username), None)
