"""Versioned schema migrations (Priority 2). Fictional data only."""
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

from app import migrations as m
from app.store import connect, initialize, insert, now, transaction

ROOT = Path(__file__).resolve().parent.parent


class MigrationTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'CROWNOPS_ENV': 'test'}); env.start(); self.addCleanup(env.stop)
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'install.sqlite3'

    def history(self):
        with closing(connect(self.path)) as db:
            return [r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')]

    def version(self):
        with closing(connect(self.path)) as db:
            return m.schema_version(db)

    def test_clean_installation_reaches_current_version_with_complete_history(self):
        initialize(self.path)
        self.assertEqual(self.version(), m.CURRENT_VERSION)
        self.assertEqual(self.history(), list(range(1, m.CURRENT_VERSION + 1)))
        with closing(connect(self.path)) as db:
            self.assertEqual(m.verify_schema(db), [])
            indexes = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertTrue({'idx_outbound_messages_status', 'idx_provider_operations_status'} <= indexes)

    def test_existing_unversioned_installation_upgrades_without_data_loss(self):
        initialize(self.path)
        with transaction(self.path) as db:
            insert(db, 'users', dict(id='u1', email='owner@example.invalid', name='Owner', role='owner', password_hash='x', active=1, created_at=now()))
            insert(db, 'leads', dict(id='L1', client_name='Fictional Existing Client', phone='5145550100', stage='Won', created_at=now(), updated_at=now()))
        with closing(sqlite3.connect(self.path)) as raw:  # model a database created before versioning existed
            raw.execute('DROP TABLE schema_migrations'); raw.execute('DROP INDEX idx_outbound_messages_status')
            raw.execute('DROP INDEX idx_provider_operations_status'); raw.execute('ALTER TABLE jobs DROP COLUMN route_stops_json')
            raw.execute('PRAGMA user_version=0'); raw.commit()
            before = raw.execute('SELECT count(*) FROM leads').fetchone()[0]
        initialize(self.path)
        self.assertEqual(self.version(), m.CURRENT_VERSION); self.assertEqual(self.history(), list(range(1, m.CURRENT_VERSION + 1)))
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM leads').fetchone()[0], before)
            self.assertEqual(db.execute("SELECT client_name FROM leads WHERE id='L1'").fetchone()[0], 'Fictional Existing Client')
            self.assertIn('route_stops_json', {r[1] for r in db.execute('PRAGMA table_info(jobs)')})
            self.assertEqual(db.execute("SELECT value_json FROM settings WHERE key='company_identity_mode'").fetchone(), None)  # test env: not recorded

    def test_repeated_initialization_is_a_no_op(self):
        initialize(self.path); first = self.history()
        with closing(connect(self.path)) as db:
            stamps = [r[0] for r in db.execute('SELECT applied_at FROM schema_migrations')]
        initialize(self.path); initialize(self.path)
        with closing(connect(self.path)) as db:
            self.assertEqual([r[0] for r in db.execute('SELECT applied_at FROM schema_migrations')], stamps)
        self.assertEqual(self.history(), first)

    def test_newer_database_is_refused_before_anything_is_written(self):
        initialize(self.path)
        with closing(sqlite3.connect(self.path)) as raw:
            raw.execute(f'PRAGMA user_version={m.CURRENT_VERSION + 5}'); raw.execute('DROP INDEX idx_outbound_messages_status'); raw.commit()
        with self.assertRaises(m.SchemaError) as refused:
            initialize(self.path)
        self.assertIn('newer than this application', str(refused.exception))
        with closing(connect(self.path)) as db:
            self.assertEqual(m.schema_version(db), m.CURRENT_VERSION + 5)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='idx_outbound_messages_status'").fetchone())

    def test_failed_migration_rolls_back_completely(self):
        initialize(self.path)
        def broken(db):
            db.execute('CREATE TABLE half_done(x)'); db.execute('INSERT INTO half_done VALUES (1)')
            raise ValueError('simulated failure')
        steps = m.MIGRATIONS + [(m.CURRENT_VERSION + 1, 'broken step', broken)]
        with closing(connect(self.path)) as db:
            with self.assertRaises(m.SchemaError) as failed:
                m.apply_migrations(db, 'test', steps)
            self.assertIn('rolled back', str(failed.exception))
            self.assertEqual(m.schema_version(db), m.CURRENT_VERSION)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='half_done'").fetchone())
        self.assertEqual(self.history(), list(range(1, m.CURRENT_VERSION + 1)))

    def test_gap_in_migration_order_is_refused(self):
        initialize(self.path)
        with closing(connect(self.path)) as db, self.assertRaises(m.SchemaError):
            m.apply_migrations(db, 'test', m.MIGRATIONS + [(m.CURRENT_VERSION + 2, 'skips a version', lambda d: None)])

    def test_production_takes_verified_backup_before_backup_required_migrations(self):
        initialize(self.path)
        extra = (m.CURRENT_VERSION + 1, 'alters an existing table', lambda db: db.execute('ALTER TABLE leads ADD COLUMN extra_note TEXT'))
        with patch.object(m, 'MIGRATIONS', m.MIGRATIONS + [extra]), patch.object(m, 'BACKUP_REQUIRED', {extra[0]}), \
                patch.dict(os.environ, {'CROWNOPS_ENV': 'production'}), \
                patch('app.release_recovery.before_manager_migration', side_effect=RuntimeError('no headroom')) as backup:
            with self.assertRaises(RuntimeError):
                initialize(self.path)
            backup.assert_called_once()
        self.assertEqual(self.version(), m.CURRENT_VERSION)  # nothing applied without the backup
        with closing(connect(self.path)) as db:
            self.assertNotIn('extra_note', {r[1] for r in db.execute('PRAGMA table_info(leads)')})

    def test_db_status_command_reports_and_signals_pending_work(self):
        initialize(self.path)
        run = lambda: subprocess.run([sys.executable, str(ROOT / 'manage.py'), '--db', str(self.path), 'db-status'], capture_output=True, text=True, cwd=ROOT, timeout=120)
        ok = run(); self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)['schema_version'], m.CURRENT_VERSION)
        with closing(sqlite3.connect(self.path)) as raw:
            raw.execute('PRAGMA user_version=1'); raw.commit()
        behind = run(); self.assertEqual(behind.returncode, 1)
        self.assertEqual([p['version'] for p in json.loads(behind.stdout)['pending']], [2])


if __name__ == '__main__':
    unittest.main()
