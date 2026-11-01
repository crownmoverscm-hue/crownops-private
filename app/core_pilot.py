"""Dedicated fail-closed Core v1 factory. Existing service entrypoints are unchanged.

Use only after separate pilot release authorization and protected-storage provisioning:
    uvicorn app.core_pilot:create_pilot_app --factory
This factory never initializes or migrates storage and never selects the legacy app.
"""
import os
import stat
from pathlib import Path
from contextlib import closing
from urllib.parse import urlparse

REQUIRED = {
    'CROWNOPS_PROFILE': 'core-v1',
    'CROWNOPS_ENV': 'pilot',
    'CROWNOPS_ALLOW_EXTERNAL': '0',
    'CROWNOPS_RUN_WORKER': '0',
    'CROWNOPS_PILOT_ACK': 'manual-inquiry-only',
    'CROWNOPS_SECURE_COOKIES': '1',
}


def refused(item):
    raise RuntimeError('Core pilot startup refused: missing or invalid ' + item + '.')


def validate_pilot_config(database_path=None):
    for key, value in REQUIRED.items():
        if os.environ.get(key) != value:
            refused(key)
    configured = os.environ.get('CROWNOPS_DB', '')
    if not configured:
        refused('CROWNOPS_DB')
    path = Path(configured)
    if not path.is_absolute() or not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
        refused('protected database path')
    path = path.resolve(strict=True)
    if database_path is not None and Path(database_path).resolve() != path:
        refused('database path consistency')
    if not hasattr(os, 'getuid'):
        refused('POSIX storage controls')
    for p, mode in ((path.parent, 0o700), (path, 0o600)):
        st = p.stat()
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != mode:
            refused('private storage ownership or permissions')
    for suffix in ('-wal', '-shm', '-journal'):
        p = Path(str(path) + suffix)
        if p.exists() or p.is_symlink():
            st = p.lstat()
            if p.is_symlink() or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600:
                refused('private SQLite sidecar permissions')
    url = os.environ.get('CROWNOPS_PUBLIC_URL', '')
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        refused('CROWNOPS_PUBLIC_URL')
    try:
        from cryptography.fernet import Fernet
        Fernet(os.environ.get('CROWNOPS_SECRET_KEY', '').encode())
    except (ValueError, TypeError):
        refused('CROWNOPS_SECRET_KEY')
    from .store import connect, protect_core_v1_path
    protect_core_v1_path(path)
    required = {
        'users': {'id', 'role', 'active'}, 'sessions': {'token_hash'},
        'login_attempts': {'ip_hash'}, 'auth_factors': {'user_id', 'last_step'},
        'leads': {'id', 'version'}, 'client_accounts': {'id', 'version'},
        'client_account_leads': {'client_id', 'lead_id'},
        'tasks': {'id', 'version'}, 'messages': {'id', 'kind'},
        'manual_source_submissions': {'id', 'source_sha256'},
        'manual_action_requests': {'kind', 'actor', 'request_key', 'request_sha256', 'result_id'},
        'core_v1_client_requests': {'request_key'}, 'audit': {'id', 'actor', 'action'},
    }
    try:
        with closing(connect(path)) as db:
            for table, columns in required.items():
                actual = {row[1] for row in db.execute('PRAGMA table_info(' + table + ')')}
                if not columns <= actual:
                    refused('prequalified database schema')
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
            if not {'manual_source_no_update', 'manual_source_no_delete', 'core_audit_no_update', 'core_audit_no_delete'} <= names:
                refused('immutability trigger schema')
    except RuntimeError:
        raise
    except Exception:
        refused('prequalified database schema')
    return path


def create_pilot_app():
    path = validate_pilot_config()
    # Import/construction only after validation; the implementation validates again.
    from .main import create_app
    app = create_app(path, core_pilot=True)
    if not app.state.core_v1_profile or not app.state.core_v1_workers_disabled:
        refused('locked application construction')
    app.state.core_v1_pilot_entrypoint = True
    return app
