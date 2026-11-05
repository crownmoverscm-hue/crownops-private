"""Optional backup encryption (CROWNOPS_BACKUP_KEY): authenticated, streamed, auto-detected
on verify and restore. Synthetic data only; nothing leaves this machine."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import backup_crypto  # noqa: E402
from app.backup_crypto import BackupKeyError, decrypt_file, encrypt_file, generate_key, is_encrypted, parse_key  # noqa: E402

MARKER = 'Fictional-Encryption-Marker-7c1e'


class StreamFormat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name); self.key = parse_key(generate_key())
        chunk = patch.object(backup_crypto, 'CHUNK', 64); chunk.start(); self.addCleanup(chunk.stop)

    def roundtrip(self, data, name='a'):
        plain, sealed, back = self.dir / (name + '.zip'), self.dir / (name + '.enc'), self.dir / (name + '.out')
        plain.write_bytes(data); encrypt_file(plain, sealed, self.key); decrypt_file(sealed, back, self.key)
        return sealed, back.read_bytes()

    def test_roundtrip_for_empty_exact_and_multi_chunk_files(self):
        for size in (0, 1, 64, 65, 640, 1000):
            data = os.urandom(size)
            sealed, back = self.roundtrip(data, f's{size}')
            self.assertEqual(back, data); self.assertTrue(is_encrypted(sealed))
            self.assertEqual(sealed.stat().st_mode & 0o777, 0o600)

    def test_plaintext_does_not_appear_in_the_encrypted_file(self):
        sealed, _ = self.roundtrip((MARKER * 20).encode())
        self.assertNotIn(MARKER.encode(), sealed.read_bytes())

    def test_tampering_truncation_reordering_and_header_swaps_are_refused(self):
        sealed, _ = self.roundtrip(os.urandom(300), 'base')
        raw = sealed.read_bytes(); header, body = raw[:32], raw[32:]
        chunks, i = [], 0
        while i < len(body):
            n = int.from_bytes(body[i:i + 4], 'big'); chunks.append(body[i:i + 4 + n]); i += 4 + n
        self.assertEqual(len(chunks), 5)
        flipped = bytearray(raw); flipped[len(raw) // 2] ^= 1
        other, _ = self.roundtrip(os.urandom(300), 'other')
        cases = {'flipped byte': bytes(flipped), 'truncated mid-chunk': raw[:-7],
                 'truncated on a chunk boundary': header + b''.join(chunks[:3]),
                 'reordered chunks': header + chunks[1] + chunks[0] + b''.join(chunks[2:]),
                 'duplicated chunk': header + chunks[0] + b''.join(chunks),
                 'header from another backup': other.read_bytes()[:32] + body,
                 'appended data': raw + chunks[-1]}
        for label, data in cases.items():
            with self.subTest(case=label):
                bad = self.dir / ('bad-' + label.replace(' ', '-')); bad.write_bytes(data)
                out = self.dir / ('out-' + label.replace(' ', '-'))
                with self.assertRaises(ValueError):
                    decrypt_file(bad, out, self.key)
                self.assertFalse(out.exists())  # no partial plaintext is left behind

    def test_wrong_key_is_named_by_key_id_and_invalid_keys_are_refused(self):
        sealed, _ = self.roundtrip(b'data')
        with self.assertRaises(BackupKeyError) as wrong:
            decrypt_file(sealed, self.dir / 'w.out', parse_key(generate_key()))
        self.assertIn('different backup key', str(wrong.exception))
        for value in ('short', generate_key()[:20], 'not base64 !!!'):
            with self.subTest(value=value), self.assertRaises(BackupKeyError):
                parse_key(value)
        shared = Fernet.generate_key().decode()
        with patch.dict(os.environ, {'CROWNOPS_SECRET_KEY': shared, 'CROWNOPS_BACKUP_KEY': shared}):
            with self.assertRaises(BackupKeyError):
                backup_crypto.configured_key()


class EncryptedScheduledBackups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name); live = base / 'live'; live.mkdir()
        self.db, self.uploads, self.backups = live / 'crownops.sqlite3', live / 'uploads', base / 'backups'
        self.key = generate_key()
        self.env = {'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', 'CROWNOPS_UPLOADS': str(self.uploads),
                    'CROWNOPS_BACKUPS': str(self.backups), 'CROWNOPS_SECRET_KEY': Fernet.generate_key().decode(), 'CROWNOPS_BACKUP_KEY': self.key}
        env = patch.dict(os.environ, self.env); env.start(); self.addCleanup(env.stop)
        from app.platform_common import put_setting
        from app.store import initialize, transaction
        initialize(self.db)
        with transaction(self.db) as db:
            put_setting(db, 'drill_marker', {'text': MARKER})
        self.uploads.mkdir(mode=0o700); (self.uploads / 'note.txt').write_text(MARKER)

    def test_scheduled_backup_is_encrypted_verified_restorable_and_leaves_no_plaintext(self):
        from app.backups import restore_archive, run_scheduled_backup, verify_backup
        from app.platform_common import settings
        from app.store import connect
        record = run_scheduled_backup(self.db, keep=3)
        archive = self.backups / record['archive']
        self.assertTrue(archive.name.endswith('.zip.enc')); self.assertTrue(is_encrypted(archive))
        self.assertTrue(record['encrypted']); self.assertTrue(record['restore_verified'])
        self.assertEqual(sorted(p.name for p in self.backups.iterdir()), [archive.name])  # no plaintext or temp residue
        self.assertNotIn(MARKER.encode(), archive.read_bytes())
        self.assertTrue(verify_backup(archive)['encrypted'])
        target = Path(self.tmp.name) / 'restored' / 'crownops.sqlite3'
        restore_archive(archive, target)
        with closing(connect(target)) as db:
            self.assertEqual(settings(db, 'drill_marker')['text'], MARKER)
        self.assertEqual((target.parent / 'uploads' / 'note.txt').read_text(), MARKER)
        with closing(connect(self.db)) as db:
            self.assertTrue(settings(db, 'backup_status')['encrypted'])

    def test_restore_without_or_with_the_wrong_key_is_refused_and_writes_nothing(self):
        from app.backups import restore_archive, run_scheduled_backup
        archive = self.backups / run_scheduled_backup(self.db, keep=3, verify=False)['archive']
        for label, key in (('missing key', ''), ('wrong key', generate_key())):
            with self.subTest(case=label), patch.dict(os.environ, {'CROWNOPS_BACKUP_KEY': key}):
                target = Path(self.tmp.name) / label.replace(' ', '-') / 'crownops.sqlite3'
                with self.assertRaises(BackupKeyError):
                    restore_archive(archive, target)
                self.assertFalse(target.exists()); self.assertFalse((target.parent / 'uploads').exists())

    def test_retention_counts_plain_and_encrypted_archives_and_unencrypted_production_backups_warn(self):
        from app.backups import run_scheduled_backup
        from app.ops_status import collect
        with patch.dict(os.environ, {'CROWNOPS_BACKUP_KEY': ''}):
            plain = run_scheduled_backup(self.db, keep=2, verify=False)['archive']
            with patch.dict(os.environ, {'CROWNOPS_ENV': 'production'}):
                self.assertIn('backup_unencrypted', [f['code'] for f in collect(self.db)['findings']])
        made = [run_scheduled_backup(self.db, keep=2, verify=False)['archive'] for _ in range(2)]
        self.assertEqual(sorted(p.name for p in self.backups.iterdir()), sorted(made))
        self.assertNotIn(plain, made)
        with patch.dict(os.environ, {'CROWNOPS_ENV': 'production'}):
            report = collect(self.db)
        self.assertNotIn('backup_unencrypted', [f['code'] for f in report['findings']]); self.assertTrue(report['backup']['encrypted'])
        self.assertNotIn(self.key, json.dumps(report))

    def test_a_bad_backup_key_is_a_recorded_backup_failure(self):
        from app.backups import run_scheduled_backup
        from app.platform_common import settings
        from app.store import connect
        with patch.dict(os.environ, {'CROWNOPS_BACKUP_KEY': 'not-a-key'}):
            with self.assertRaises(BackupKeyError):
                run_scheduled_backup(self.db, keep=2)
        with closing(connect(self.db)) as db:
            status = settings(db, 'backup_status')
        self.assertEqual(status['state'], 'failed'); self.assertNotIn('not-a-key', status['last_error'])
        self.assertFalse(self.backups.exists() and any(self.backups.iterdir()))

    def test_cli_generates_a_usable_key_and_restores_an_encrypted_archive(self):
        from app.backups import run_scheduled_backup
        env = {**os.environ}
        out = subprocess.run([sys.executable, 'manage.py', '--db', str(self.db), 'backup-key'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        generated = out.stdout.split('CROWNOPS_BACKUP_KEY=')[1].strip()
        self.assertEqual(len(parse_key(generated)), 32)
        doctor = json.loads(subprocess.run([sys.executable, 'manage.py', '--db', str(self.db), 'doctor'], cwd=ROOT, env=env,
                                           capture_output=True, text=True, timeout=60).stdout)
        self.assertTrue(doctor['backup_encryption_configured']); self.assertNotIn(self.key, json.dumps(doctor))
        archive = self.backups / run_scheduled_backup(self.db, keep=2, verify=False)['archive']
        target = Path(self.tmp.name) / 'cli-restore' / 'crownops.sqlite3'
        done = subprocess.run([sys.executable, 'manage.py', '--db', str(target), 'restore', str(archive)], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        with closing(sqlite3.connect(target)) as db:
            self.assertIn(MARKER, db.execute("SELECT value_json FROM settings WHERE key='drill_marker'").fetchone()[0])
        refused = subprocess.run([sys.executable, 'manage.py', '--db', str(Path(self.tmp.name) / 'x' / 'c.sqlite3'), 'restore', str(archive)],
                                 cwd=ROOT, env={**env, 'CROWNOPS_BACKUP_KEY': ''}, capture_output=True, text=True, timeout=120)
        self.assertEqual(refused.returncode, 1); self.assertIn('encrypted', refused.stderr)


if __name__ == '__main__':
    unittest.main()
