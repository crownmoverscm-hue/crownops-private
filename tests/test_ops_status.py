"""Monitoring and operational status (Priority 6). Fictional data only; no external service."""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from collections import namedtuple
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app, sha
from app.ops_status import collect, record_job
from app.platform_common import put_setting
from app.store import connect, insert, now, transaction

ROOT = Path(__file__).resolve().parent.parent


class OpsStatusTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'ops.sqlite3'
        env = patch.dict(os.environ, {'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', 'CROWNOPS_UPLOADS': str(Path(temp.name) / 'uploads'),
                                      'CROWNOPS_BACKUPS': str(Path(temp.name) / 'backups')})
        env.start(); self.addCleanup(env.stop)
        usage = namedtuple('usage', 'total used free')
        disk = patch('app.ops_status.shutil.disk_usage', return_value=usage(1000, 500, 500))  # this machine's real disk is not the subject here
        disk.start(); self.addCleanup(disk.stop)
        self.app = create_app(self.path)
        with transaction(self.path) as db:
            insert(db, 'leads', dict(id='L1', client_name='Secret Client Name', email='secret.client@example.invalid', phone='5145550100', stage='New', created_at=now(), updated_at=now()))

    def codes(self, report=None):
        return {f['code']: f['level'] for f in (report or collect(self.path))['findings']}

    def backup_status(self, **values):
        with transaction(self.path) as db:
            put_setting(db, 'backup_status', values)

    def test_fresh_installation_warns_only_that_no_backup_exists(self):
        report = collect(self.path)
        self.assertEqual(report['state'], 'warning'); self.assertEqual(self.codes(report), {'backup_missing': 'warning'})
        self.assertEqual(report['database']['quick_check'], 'ok'); self.assertFalse(report['schema']['newer_than_app'])

    def test_recent_successful_backup_is_ok_and_stale_or_failed_is_critical(self):
        self.backup_status(state='ok', last_success_at=now(), restore_verified=True, bytes=100)
        self.assertEqual(collect(self.path)['state'], 'ok')
        self.backup_status(state='ok', last_success_at=(datetime.now(timezone.utc) - timedelta(hours=40)).isoformat())
        self.assertEqual(self.codes()['backup_stale'], 'critical')
        self.backup_status(state='failed', last_success_at=now(), last_error='A referenced private upload is missing.')
        self.assertEqual(self.codes()['backup_failed'], 'critical')

    def test_pending_migration_low_disk_unconfirmed_mail_and_stuck_integrations_are_reported(self):
        self.backup_status(state='ok', last_success_at=now())
        with closing(sqlite3.connect(self.path)) as raw:
            raw.execute('PRAGMA user_version=1'); raw.commit()
        self.assertEqual(self.codes()['schema'], 'critical')
        with closing(sqlite3.connect(self.path)) as raw:
            raw.execute('PRAGMA user_version=2'); raw.commit()
        usage = namedtuple('usage', 'total used free')
        with patch('app.ops_status.shutil.disk_usage', return_value=usage(1000, 970, 30)):
            self.assertEqual(self.codes()['disk_low'], 'critical')
        with patch('app.ops_status.shutil.disk_usage', return_value=usage(1000, 900, 100)):
            self.assertEqual(self.codes()['disk_low'], 'warning')
        with transaction(self.path) as db:
            insert(db, 'outbound_messages', dict(id='M1', event_key='k1', lead_id='L1', subject='Private subject', body='Private body',
                                                 recipient='secret.client@example.invalid', category='Reply', status='Unknown', created_by='x', created_at=now()))
            insert(db, 'provider_operations', dict(id='op1', provider='quickbooks', object_id='INV1', kind='Invoice export', status='Mismatch', created_at=now(), updated_at=now()))
        report = collect(self.path)
        self.assertEqual(self.codes(report)['messages_unconfirmed'], 'warning'); self.assertEqual(self.codes(report)['integration_reconciliation'], 'warning')
        text = json.dumps(report)
        for private in ('Secret Client Name', 'secret.client@example.invalid', 'Private subject', 'Private body', 'INV1'):
            self.assertNotIn(private, text)

    def test_background_job_failures_are_counted_and_reset_on_success(self):
        self.backup_status(state='ok', last_success_at=now())
        for _ in range(3):
            record_job(self.path, 'intake', 'ConnectionError')
        self.assertEqual(self.codes()['background_job_failing'], 'critical')
        record_job(self.path, 'intake')
        self.assertNotIn('background_job_failing', self.codes())

    def test_intake_failure_no_longer_stops_the_background_loop(self):
        source = (ROOT / 'app' / 'main.py').read_text()
        self.assertIn("await run_job('intake'", source)
        self.assertNotIn('await asyncio.to_thread(run_intake,path,new_lead)\n', source)

    def test_endpoint_is_manager_only(self):
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with transaction(self.path) as db:
            for role in ('owner', 'sales'):
                insert(db, 'users', dict(id=role, email=role + '@example.invalid', name=role, role=role, password_hash='x', active=1, created_at=now()))
                insert(db, 'sessions', dict(token_hash=sha(role), user_id=role, csrf=role, expires_at=expires))
        for role, code in (('owner', 200), ('sales', 403)):
            client = TestClient(self.app); client.cookies.set('crownops_session', role)
            self.assertEqual(client.get('/api/ops/status').status_code, code)
        self.assertEqual(TestClient(self.app).get('/api/ops/status').status_code, 401)

    def test_cli_exit_codes_follow_state(self):
        run = lambda: subprocess.run([sys.executable, str(ROOT / 'manage.py'), '--db', str(self.path), 'ops-check'], capture_output=True, text=True,
                                     cwd=ROOT, timeout=120, env={**os.environ, 'CROWNOPS_DISK_WARN_PERCENT': '0', 'CROWNOPS_DISK_CRITICAL_PERCENT': '0'})
        self.assertEqual(run().returncode, 1)  # no backup yet: warning
        self.backup_status(state='ok', last_success_at=now())
        ok = run(); self.assertEqual(ok.returncode, 0, ok.stdout); self.assertEqual(json.loads(ok.stdout)['state'], 'ok')
        self.backup_status(state='failed', last_error='disk full')
        self.assertEqual(run().returncode, 2)


if __name__ == '__main__':
    unittest.main()
