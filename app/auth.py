"""
This material is based upon work supported by the United States Air Force under contract number FA8750-24-S-B079 (Prime Contractor Smart Information Flow Technologies (SIFT)).  Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Air Force.
 Copyright (c) 2026 RTX BBN Technologies. Licensed to US Government with unlimited rights.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
This is distributed in the hope that it will be useful, but without any warranty, without even the implied warranty of merchantability or fitness for a particular purpose.  See the GNU General Public License for more details. https://www.gnu.org/licenses/

Authentication module - Flask-Login based session auth + API key support

Credentials come from environment variables:
  ADMIN_USERNAME  (default: admin)
  ADMIN_PASSWORD  (default: changeme)

API keys are stored in DATA_DIR/api_keys.json
"""
import os
import json
import hmac
import secrets
import hashlib
import logging
import functools
from datetime import datetime, timezone

from flask import request, jsonify, redirect, url_for
from flask_login import LoginManager, UserMixin, current_user

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------

class User(UserMixin):
    """Simple user model backed by environment variables."""
    def __init__(self, user_id, username, is_default_password=False):
        self.id = user_id
        self.username = username
        self.is_default_password = is_default_password


# Resolve admin credentials from env
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'changeme')

_DEFAULT_CREDS = (ADMIN_PASSWORD == 'changeme')

# In-memory user store (single admin user)
_admin_user = User('1', ADMIN_USERNAME, is_default_password=_DEFAULT_CREDS)


def _get_user_by_id(user_id):
    if user_id == _admin_user.id:
        return _admin_user
    return None


def _check_credentials(username, password):
    """Return User or None. Uses constant-time comparison to resist timing attacks."""
    if not isinstance(username, str) or not isinstance(password, str):
        return None
    user_ok = hmac.compare_digest(username.encode('utf-8'), ADMIN_USERNAME.encode('utf-8'))
    pass_ok = hmac.compare_digest(password.encode('utf-8'), ADMIN_PASSWORD.encode('utf-8'))
    if user_ok and pass_ok:
        return _admin_user
    return None


# ---------------------------------------------------------------------------
# API key helpers
# ---------------------------------------------------------------------------

_API_KEYS_FILE = os.path.join(DATA_DIR, 'api_keys.json')


def _load_api_keys() -> dict:
    """Return {key_hash: {name, created}} dict."""
    try:
        if os.path.exists(_API_KEYS_FILE):
            with open(_API_KEYS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading API keys: {e}")
    return {}


def _save_api_keys(keys: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(_API_KEYS_FILE, 'w') as f:
        json.dump(keys, f, indent=2)


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key(name: str) -> str:
    """Generate a new API key, persist the hash, return the raw key."""
    raw_key = f"tvr_{secrets.token_hex(24)}"
    keys = _load_api_keys()
    keys[_hash_key(raw_key)] = {
        'name': name,
        'created': datetime.now(timezone.utc).isoformat(),
    }
    _save_api_keys(keys)
    logger.info(f"API key created: {name}")
    return raw_key


def revoke_api_key(key_hash: str) -> bool:
    """Revoke an API key by its hash."""
    keys = _load_api_keys()
    if key_hash in keys:
        name = keys[key_hash]['name']
        del keys[key_hash]
        _save_api_keys(keys)
        logger.info(f"API key revoked: {name}")
        return True
    return False


def list_api_keys() -> list:
    """List API keys (hashes + metadata, never the raw key)."""
    keys = _load_api_keys()
    return [{'hash': h, **meta} for h, meta in keys.items()]


def _validate_api_key(raw_key: str) -> bool:
    """Check if a raw API key is valid."""
    h = _hash_key(raw_key)
    return h in _load_api_keys()


# ---------------------------------------------------------------------------
# Auth-required decorator that also accepts API keys & Basic auth
# ---------------------------------------------------------------------------

def auth_required(f):
    """
    Protect a route. Accepts:
      1. Flask-Login session cookie
      2. X-API-Key header
      3. Basic auth header  (username:password)
    Unauthenticated browser requests redirect to /login.
    Unauthenticated API requests get 401.
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        # 1. Session-based login
        if current_user.is_authenticated:
            return f(*args, **kwargs)

        # 2. API key
        api_key = request.headers.get('X-API-Key')
        if api_key and _validate_api_key(api_key):
            return f(*args, **kwargs)

        # 3. Basic auth
        auth = request.authorization
        if auth and _check_credentials(auth.username, auth.password):
            return f(*args, **kwargs)

        # Not authenticated
        if _wants_json():
            return jsonify({'error': 'Authentication required'}), 401
        return redirect(url_for('login_page'))

    return decorated


def _wants_json():
    """Heuristic: is this an API/XHR call?"""
    if request.path.startswith('/api/'):
        return True
    accept = request.headers.get('Accept', '')
    if 'application/json' in accept:
        return True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    return False


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

_AUDIT_LOG_FILE = os.path.join(DATA_DIR, 'audit.log')


def audit_log(action: str, detail: str = '', user: str = ''):
    """Append a line to the audit log file."""
    if not user:
        try:
            if current_user.is_authenticated:
                user = current_user.username
        except Exception:
            pass
        if not user:
            user = request.remote_addr if request else 'system'
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts} | {user} | {action} | {detail}\n"
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(_AUDIT_LOG_FILE, 'a') as f:
            f.write(line)
    except Exception as e:
        logger.error(f"Audit log write error: {e}")


def read_audit_log(lines: int = 200) -> list:
    """Return the last N lines of the audit log."""
    try:
        if not os.path.exists(_AUDIT_LOG_FILE):
            return []
        with open(_AUDIT_LOG_FILE, 'r') as f:
            all_lines = f.readlines()
        return [l.strip() for l in all_lines[-lines:]]
    except Exception as e:
        logger.error(f"Audit log read error: {e}")
        return []


# ---------------------------------------------------------------------------
# Init function called from create_app
# ---------------------------------------------------------------------------

login_manager = LoginManager()


def init_auth(app):
    """Initialize Flask-Login on the app."""
    login_manager.init_app(app)
    login_manager.login_view = 'login_page'

    @login_manager.user_loader
    def load_user(user_id):
        return _get_user_by_id(user_id)

    if _DEFAULT_CREDS:
        logger.warning("=" * 60)
        logger.warning("DEFAULT ADMIN PASSWORD IN USE — CHANGE IMMEDIATELY")
        logger.warning("Set ADMIN_PASSWORD environment variable in docker-compose.yml")
        logger.warning("=" * 60)
