"""In-process daily backups (CROWNOPS_BACKUP_HOUR) and the operator's offsite copy command
(CROWNOPS_BACKUP_OFFSITE_COMMAND). The "offsite" target is a local folder; nothing leaves
this machine."""
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TORONTO = ZoneInfo('America/Toronto')


def local(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TORONTO).astimezone(timezone.utc)


class BackupSchedule(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name); live = base / 'live'; live.mkdir()
        self.db, self.backups, self.offsite = live / 'crownops.sqlite3', base / 'backups', base / 'offsite'
        self.offsite.mkdir()
        copier = base / 'copy_offsite.py'
        copier.write_text('import shutil, sys\nprint("https://secret-bucket.invalid/path")\n'
                          'shutil.copy(sys.argv[-1], sys.argv[-2])\n')
        self.copy_command = shlex.join([sys.executable, '-I', str(copier), str(self.offsite)])
        self.env = {'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', 'CROWNOPS_UPLOADS': str(live / 'uploads'),
                    'CROWNOPS_BACKUPS': str(self.backups), 'CROWNOPS_SECRET_KEY': Fernet.generate_key().decode(),
                    'CROWNOPS_BACKUP_KEY': '', 'CROWNOPS_BACKUP_HOUR': '', 'CROWNOPS_BACKUP_OFFSITE_COMMAND': ''}
        env = patch.dict(os.environ, self.env); env.start(); self.addCleanup(env.stop)
        from app.business_zone import set_business_zone
        from app.store import initialize
        initialize(self.db); set_business_zone('America/Toronto')

    def status(self, **values):
        from app.platform_common import put_setting, settings
        from app.store import connect, transaction
        if values:
            with transaction(self.db) as db:
                put_setting(db, 'backup_status', values)
        with closing(connect(self.db)) as db:
            return settings(db, 'backup_status', {}) or {}

    def test_backup_is_due_once_per_local_day_after_the_hour_with_a_failure_cooldown(self):
        from app.backups import backup_due
        self.assertFalse(backup_due(self.db, local(2026, 11, 20, 3)))  # not configured
        with patch.dict(os.environ, {'CROWNOPS_BACKUP_HOUR': '25'}), self.assertRaises(ValueError):
            backup_due(self.db, local(2026, 11, 20, 3))
        with patch.dict(os.environ, {'CROWNOPS_BACKUP_HOUR': '2'}):
            self.assertFalse(backup_due(self.db, local(2026, 11, 20, 1, 59)))
            self.assertTrue(backup_due(self.db, local(2026, 11, 20, 2)))
            self.status(state='ok', last_success_at=local(2026, 11, 20, 2, 5).isoformat())
            self.assertFalse(backup_due(self.db, local(2026, 11, 20, 23, 59)))
            self.assertTrue(backup_due(self.db, local(2026, 11, 21, 2)))  # next local day
            # 03:30 UTC on the 21st is still the evening of the 20th in Toronto: the local date decides.
            self.assertFalse(backup_due(self.db, datetime(2026, 11, 21, 3, 30, tzinfo=timezone.utc)))
            self.status(state='failed', last_success_at=local(2026, 11, 19, 2).isoformat(), last_failure_at=local(2026, 11, 20, 2, 30).isoformat())
            self.assertFalse(backup_due(self.db, local(2026, 11, 20, 3, 0)))  # within an hour of a failure
            self.assertTrue(backup_due(self.db, local(2026, 11, 20, 3, 31)))

    def test_cycle_copies_the_verified_archive_offsite_and_records_only_the_outcome(self):
        from app.backups import run_backup_cycle
        from app.ops_status import collect
        with patch.dict(os.environ, {'CROWNOPS_BACKUP_OFFSITE_COMMAND': self.copy_command}):
            record = run_backup_cycle(self.db, keep=3)
        archive = self.backups / record['archive']
        copied = self.offsite / archive.name
        self.assertEqual(record['offsite']['state'], 'ok'); self.assertTrue(record['restore_verified'])
        self.assertEqual(hashlib.sha256(copied.read_bytes()).hexdigest(), record['sha256'])
        stored = self.status()
        self.assertEqual(stored['offsite']['state'], 'ok'); self.assertNotIn('secret-bucket', json.dumps(stored))
        with patch.dict(os.environ, {'CROWNOPS_ENV': 'production', 'CROWNOPS_BACKUP_OFFSITE_COMMAND': self.copy_command}):
            codes = [f['code'] for f in collect(self.db)['findings']]
        self.assertNotIn('offsite_copy_failed', codes); self.assertNotIn('offsite_not_configured', codes)

    def test_failed_or_missing_offsite_command_is_critical_and_fails_the_cli(self):
        from app.backups import run_backup_cycle
        from app.ops_status import collect
        failing = shlex.join([sys.executable, '-I', '-c', 'import sys; print("token=abc"); sys.exit(3)'])
        for label, command, needle in (('exit status', failing, 'status 3'), ('missing program', '/nonexistent/uploader', 'could not run')):
            with self.subTest(case=label), patch.dict(os.environ, {'CROWNOPS_BACKUP_OFFSITE_COMMAND': command}):
                record = run_backup_cycle(self.db, keep=3, verify=False)
                self.assertEqual(record['offsite']['state'], 'failed'); self.assertIn(needle, record['offsite']['error'])
                self.assertTrue((self.backups / record['archive']).exists())  # the local backup is kept
                report = collect(self.db)
                self.assertEqual(report['state'], 'critical')
                self.assertIn('offsite_copy_failed', [f['code'] for f in report['findings']])
                self.assertNotIn('token=abc', json.dumps(report))
        cli_env = {**os.environ, 'CROWNOPS_BACKUP_OFFSITE_COMMAND': failing}
        done = subprocess.run([sys.executable, 'manage.py', '--db', str(self.db), 'backup-run', '--keep', '3', '--no-verify'],
                              cwd=ROOT, env=cli_env, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)['offsite']['state'], 'failed')
        with patch.dict(os.environ, {'CROWNOPS_ENV': 'production'}):
            self.assertIn('offsite_copy_failed', [f['code'] for f in collect(self.db)['findings']])

    def test_production_without_offsite_copy_warns(self):
        from app.backups import run_backup_cycle
        from app.ops_status import collect
        record = run_backup_cycle(self.db, keep=3, verify=False)
        self.assertIsNone(record['offsite'])
        with patch.dict(os.environ, {'CROWNOPS_ENV': 'production'}):
            codes = [f['code'] for f in collect(self.db)['findings']]
        self.assertIn('offsite_not_configured', codes); self.assertIn('backup_unencrypted', codes)

    def test_application_process_runs_the_daily_backup_when_the_hour_is_set(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        with patch.dict(os.environ, {'CROWNOPS_BACKUP_HOUR': '0', 'CROWNOPS_BACKUP_OFFSITE_COMMAND': self.copy_command}):
            with TestClient(create_app(self.db)):
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and self.status().get('state') != 'ok':
                    time.sleep(0.2)
                status = self.status()
        self.assertEqual(status.get('state'), 'ok'); self.assertTrue(status.get('restore_verified'))
        self.assertEqual(len(list(self.backups.glob('crownops-backup-*.zip'))), 1)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self.status().get('offsite'):
            time.sleep(0.2)
        self.assertEqual(self.status()['offsite']['state'], 'ok')

    def test_backups_stay_off_without_the_hour(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        with TestClient(create_app(self.db)):
            time.sleep(0.5)
        self.assertEqual(self.status(), {})


if __name__ == '__main__':
    unittest.main()
