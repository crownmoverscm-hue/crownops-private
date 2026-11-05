"""Backup and disaster-recovery drill (Priority 1). Synthetic data only; the "offsite"
copy is a separate local directory — nothing leaves this machine."""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tests'))
from test_independent_installations import CONFIGS  # noqa: E402

TABLES = ('leads', 'client_accounts', 'quotes', 'jobs', 'assignments', 'employees', 'vehicles', 'job_invoices',
          'invoice_revisions', 'payments', 'outbound_messages', 'attachments', 'annual_rate_periods', 'audit')
SETTINGS = ('company_profile', 'pricing_policy', 'company_logo', 'billing_policy', 'client_terms')
DRILL = {}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def snapshot(db_path):
    with closing(sqlite3.connect(db_path)) as db:
        counts = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in TABLES}
        settings = {k: (db.execute('SELECT value_json FROM settings WHERE key=?', (k,)).fetchone() or [None])[0] for k in SETTINGS}
        invoices = db.execute('SELECT id,status,revision FROM job_invoices ORDER BY id').fetchall()
    return counts, settings, invoices


class BackupDrill(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); base = Path(cls.temp.name)
        cls.live = base / 'live'; cls.live.mkdir()
        cls.db = cls.live / 'crownops.sqlite3'; cls.uploads = cls.live / 'uploads'; cls.backups = base / 'backups'
        cls.env = {'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', 'CROWNOPS_UPLOADS': str(cls.uploads),
                   'CROWNOPS_BACKUPS': str(cls.backups), 'CROWNOPS_SECRET_KEY': Fernet.generate_key().decode()}
        cfg = base / 'cfg.json'; cfg.write_text(json.dumps(CONFIGS['B']))
        run = subprocess.run([sys.executable, '-I', str(ROOT / 'tests' / 'installation_workflow.py'), str(cls.db), str(cfg)],
                             capture_output=True, text=True, env={**os.environ, **cls.env}, cwd=ROOT, timeout=300)
        assert run.returncode == 0, run.stderr[-1500:]
        cls.workflow = json.loads(run.stdout.strip().splitlines()[-1])
        env = patch.dict(os.environ, cls.env); env.start(); cls.addClassCleanup(env.stop)
        from app.company_profile import store_logo
        from app.store import insert, now, transaction
        from io import BytesIO
        from PIL import Image
        cls.uploads.mkdir(mode=0o700, exist_ok=True)
        document = b'%PDF-1.4 fictional signed inventory for the drill\n'
        (cls.uploads / 'DRILL-DOC.pdf').write_bytes(document)
        with transaction(cls.db) as db:
            lead = db.execute('SELECT id FROM leads LIMIT 1').fetchone()[0]
            insert(db, 'attachments', dict(id='ATT-DRILL', lead_id=lead, category='Documents', original_name='inventory.pdf', stored_name='DRILL-DOC.pdf',
                                           mime='application/pdf', size=len(document), sha256=hashlib.sha256(document).hexdigest(), scan_status='Clean', author='owner', created_at=now()))
            out = BytesIO(); Image.new('RGB', (40, 20), 'navy').save(out, 'PNG'); store_logo(db, out.getvalue(), 'owner')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_1_scheduled_backup_offsite_copy_restore_and_validation(self):
        from app.backups import run_scheduled_backup, restore_archive, inspect_archive
        before = snapshot(self.db)
        started = time.monotonic()
        result = run_scheduled_backup(self.db, keep=5)
        self.assertEqual(result['state'], 'ok'); self.assertTrue(result['restore_verified']); self.assertEqual(result['upload_files'], 1)
        archive = self.backups / result['archive']
        self.assertEqual(oct(archive.stat().st_mode & 0o777), '0o600')
        offsite = Path(self.temp.name) / 'offsite'; offsite.mkdir()          # simulated offsite copy
        copy = offsite / archive.name; shutil.copy2(archive, copy)
        self.assertEqual(sha(copy), result['sha256'])
        info = json.loads(zipfile.ZipFile(copy).read('BACKUP_INFO.json'))
        manifest_text = json.dumps(info)
        self.assertFalse(info['secret_values_included']); self.assertNotIn(self.env['CROWNOPS_SECRET_KEY'], manifest_text)
        for value in self.env.values():
            self.assertNotIn(value, manifest_text) if len(value) > 8 else None  # no configured values, only setting names
        self.assertNotIn('CROWNOPS_SECRET_KEY=', manifest_text); self.assertEqual(info['schema_version'], inspect_archive(copy)['schema_version'])
        target = Path(self.temp.name) / 'restored' / 'crownops.sqlite3'
        restore_started = time.monotonic()
        restore_archive(copy, target)
        restore_seconds = round(time.monotonic() - restore_started, 3)
        after = snapshot(target)
        self.assertEqual(after[0], before[0]); self.assertEqual(after[1], before[1]); self.assertEqual(after[2], before[2])
        self.assertTrue(all(before[0][t] > 0 for t in ('leads', 'quotes', 'jobs', 'assignments', 'employees', 'vehicles', 'job_invoices', 'invoice_revisions', 'attachments')))
        self.assertEqual((target.parent / 'uploads' / 'DRILL-DOC.pdf').read_bytes(), (self.uploads / 'DRILL-DOC.pdf').read_bytes())
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0], 0)  # restored sessions revoked
        # Application behaviour on the restored, isolated instance
        from app.main import create_app, sha as session_hash
        from app.store import insert, now, transaction
        with patch.dict(os.environ, {'CROWNOPS_UPLOADS': str(target.parent / 'uploads')}):
            app = create_app(target)
            expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            with transaction(target) as db:
                insert(db, 'sessions', dict(token_hash=session_hash('restored'), user_id='owner', csrf='restored-csrf', expires_at=expires))
            client = TestClient(app); client.cookies.set('crownops_session', 'restored'); client.headers['x-csrf-token'] = 'restored-csrf'
            old = TestClient(app); old.cookies.set('crownops_session', self.workflow['session_token'])
            self.assertEqual(old.get('/api/me').status_code, 401)
            invoice = client.get('/api/invoices/' + self.workflow['invoice_id'])
            self.assertEqual(invoice.status_code, 200); self.assertEqual(invoice.json()['status'], 'Issued')
            self.assertEqual(invoice.json()['calculation']['total_cents'], self.workflow['invoice_total_cents'])
            self.assertEqual(client.get('/api/company/public-profile').json()['name'], CONFIGS['B']['profile']['name'])
            self.assertEqual(client.get('/api/company/logo').status_code, 200)
            self.assertTrue(client.get('/api/company/setup-status').json()['ready'])
            from app.business_zone import zone_name
            self.assertEqual(zone_name(), 'America/Vancouver')
        DRILL.update(backup_seconds=result['backup_seconds'], verified_total_seconds=result['total_seconds'], restore_seconds=restore_seconds,
                     archive_bytes=result['bytes'], end_to_end_seconds=round(time.monotonic() - started, 3), records=before[0])
        print('\nBACKUP_DRILL ' + json.dumps(DRILL, sort_keys=True))

    def test_2_restore_never_overwrites_an_existing_installation(self):
        from app.backups import run_scheduled_backup, restore_archive
        archive = self.backups / run_scheduled_backup(self.db, keep=5, verify=False)['archive']
        with self.assertRaises(ValueError):
            restore_archive(archive, self.db)
        self.assertGreater(snapshot(self.db)[0]['leads'], 0)

    def test_3_corrupt_incomplete_missing_tampered_and_newer_backups_are_refused(self):
        from app.backups import run_scheduled_backup, verify_backup
        archive = self.backups / run_scheduled_backup(self.db, keep=5, verify=False)['archive']
        bad = Path(self.temp.name) / 'bad'; bad.mkdir(exist_ok=True)
        raw = bytearray(archive.read_bytes())
        corrupt = bad / 'corrupt.zip'; flipped = raw.copy(); flipped[len(flipped) // 3] ^= 0xFF; corrupt.write_bytes(bytes(flipped))
        truncated = bad / 'truncated.zip'; truncated.write_bytes(bytes(raw[:len(raw) // 2]))
        with zipfile.ZipFile(archive) as src:
            members = {n: src.read(n) for n in src.namelist()}
        tampered = bad / 'tampered.zip'
        with zipfile.ZipFile(tampered, 'w') as z:
            for n, data in members.items():
                z.writestr(n, data + b'tamper' if n.startswith('uploads/') else data)
        newer = bad / 'newer.zip'
        info = json.loads(members['BACKUP_INFO.json']); info['schema_version'] = 999
        with zipfile.ZipFile(newer, 'w') as z:
            for n, data in members.items():
                z.writestr(n, json.dumps(info) if n == 'BACKUP_INFO.json' else data)
        for label, path, needle in (('corrupt', corrupt, ''), ('truncated', truncated, ''), ('tampered', tampered, 'checksum'),
                                    ('newer schema', newer, 'newer CrownOps schema'), ('missing', bad / 'absent.zip', '')):
            with self.subTest(case=label):
                with self.assertRaises((ValueError, zipfile.BadZipFile, OSError)) as refused:
                    verify_backup(path)
                if needle:
                    self.assertIn(needle, str(refused.exception))

    def test_4_failed_backup_is_reported_and_retention_only_prunes_its_own_archives(self):
        from app.backups import run_scheduled_backup
        from app.platform_common import settings
        from app.store import connect
        unrelated = self.backups / 'operator-notes.txt'; self.backups.mkdir(exist_ok=True); unrelated.write_text('keep me')
        made = [run_scheduled_backup(self.db, keep=2, verify=False)['archive'] for _ in range(3)]
        remaining = sorted(p.name for p in self.backups.glob('crownops-backup-*.zip'))
        self.assertEqual(len(remaining), 2); self.assertIn(made[-1], remaining); self.assertTrue(unrelated.exists())
        moved = self.uploads / 'DRILL-DOC.pdf'; hidden = moved.with_suffix('.hidden'); moved.rename(hidden)
        try:
            with self.assertRaises(ValueError):
                run_scheduled_backup(self.db, keep=2)
        finally:
            hidden.rename(moved)
        with closing(connect(self.db)) as db:
            status = settings(db, 'backup_status')
        self.assertEqual(status['state'], 'failed'); self.assertIn('upload', status['last_error'])
        self.assertEqual(status['archive'], made[-1])  # the last good backup stays on record
        self.assertEqual(len(list(self.backups.glob('crownops-backup-*.zip'))), 2)  # nothing partial was published


if __name__ == '__main__':
    unittest.main()
