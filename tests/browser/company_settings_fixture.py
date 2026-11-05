"""Fictional NEW company installation (unconfigured identity) for the owner setup browser test."""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
os.environ.update({'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', 'CROWNOPS_PUBLIC_URL': 'http://127.0.0.1:8095'})
from app.main import create_app, sha
from app.platform_common import put_setting
from app.store import initialize, insert, now, transaction
import uvicorn

path = Path(tempfile.mkdtemp()) / 'new-company.sqlite3'
initialize(path)
with transaction(path) as db:
    put_setting(db, 'company_identity_mode', 'unconfigured')
app = create_app(path)
expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
with transaction(path) as db:
    for role in ('owner', 'sales'):
        insert(db, 'users', dict(id=role, email=role + '@example.invalid', name='Fictional ' + role.title(), role=role, password_hash='x', active=1, created_at=now()))
        insert(db, 'sessions', dict(token_hash=sha(role + '-token'), user_id=role, csrf=role + '-csrf', expires_at=expires))
    insert(db, 'annual_rate_periods', dict(id='R1', label='All year', start_date='2026-01-01', end_date='2099-12-31', rate_2_cents=12000, rate_3_cents=16000,
                                           rate_4_cents=20000, rate_5_cents=24000, rate_6_cents=28000, active=1, version=1, note='', approved_by='owner', created_at=now()))
uvicorn.run(app, host='127.0.0.1', port=8095, log_level='warning')
