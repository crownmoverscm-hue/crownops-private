"""Persistent SQLite storage with explicit transactions and no shipped accounts."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3
import uuid
import re


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def uid(prefix):
    return prefix + '-' + uuid.uuid4().hex[:16]


def dumps(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False)


def connect(path):
    db = sqlite3.connect(str(path), timeout=20, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=20000')
    return db


def migrate_manager_role(db):
    """Widen the legacy users CHECK while retaining rows and referencing tables."""
    sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()[0]
    constraint = re.search(r'CHECK\s*\(\s*role\s+IN\s*\(([^)]+)\)\s*\)', sql, re.I)
    if not constraint or re.search(r"['\"]manager['\"]", constraint.group(1)):
        return
    original_accounts = [tuple(row) for row in db.execute('SELECT * FROM users ORDER BY id')]
    widened = sql[:constraint.start()] + "CHECK(role IN (" + constraint.group(1) + ",'manager'))" + sql[constraint.end():]
    widened = re.sub(r'^(CREATE\s+TABLE\s+)(?:"users"|users)(?=\s*\()', r'\1users_manager_migration', widened, count=1, flags=re.I)
    if widened == sql or not widened.startswith('CREATE TABLE users_manager_migration'):
        raise RuntimeError('The staff schema needs a reviewed migration before adding Manager access.')
    objects = db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='users' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute(widened)
        columns = ','.join('"' + row[1].replace('"', '""') + '"' for row in db.execute('PRAGMA table_info(users)'))
        db.execute('INSERT INTO users_manager_migration (' + columns + ') SELECT ' + columns + ' FROM users')
        db.execute('DROP TABLE users')
        db.execute('ALTER TABLE users_manager_migration RENAME TO users')
        for item in objects:
            db.execute(item[0])
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Staff migration failed its reference check. No changes were committed.')
        if original_accounts != [tuple(row) for row in db.execute('SELECT * FROM users ORDER BY id')]:
            raise RuntimeError('Staff migration did not preserve all accounts. No changes were committed.')
        db.commit()
        import logging
        logging.getLogger('crownops').warning('Manager-role migration complete: %d accounts preserved; foreign-key check passed.', len(original_accounts))
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute('PRAGMA foreign_keys=ON')


def initialize(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = connect(path)
    legacy = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
    pending_manager=bool(legacy and not re.search(r"['\"]manager['\"]",legacy[0]))
    pending_journey=bool(legacy and any(not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() for name in ('schedule_days','saved_media_links','detail_requests','portal_detail_drafts','quote_views','invoice_deposit_reviews','job_handoffs','invoice_access_invites','invoice_access_sessions','invoice_change_orders')))
    if os.getenv('CROWNOPS_ENV') == 'production' and (pending_manager or pending_journey):
        # Back up and restore-test the existing database/uploads before any new schema.
        from .release_recovery import before_manager_migration
        try:
            if pending_manager:before_manager_migration(path)
            else:before_manager_migration(path, migration='client journey')
        except Exception:
            db.close()
            raise
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript(Path(__file__).with_name('schema.sql').read_text())
    db.executescript(Path(__file__).with_name('platform_schema.sql').read_text())
    migrate_manager_role(db)
    lead_cols={r[1] for r in db.execute('PRAGMA table_info(leads)').fetchall()}
    if 'intake_json' not in lead_cols:db.execute("ALTER TABLE leads ADD COLUMN intake_json TEXT NOT NULL DEFAULT '{}'")
    # Lightweight forward migration for the private review builds.
    job_cols={r[1] for r in db.execute('PRAGMA table_info(jobs)').fetchall()}
    for column,definition in [('plan_review_required','INTEGER NOT NULL DEFAULT 0'),('route_stops_json',"TEXT NOT NULL DEFAULT '[]'")]:
        if column not in job_cols:db.execute('ALTER TABLE jobs ADD COLUMN '+column+' '+definition)
    vehicle_cols={r[1] for r in db.execute('PRAGMA table_info(vehicles)').fetchall()}
    added_color=False
    if 'color_hex' not in vehicle_cols:
        db.execute("ALTER TABLE vehicles ADD COLUMN color_hex TEXT NOT NULL DEFAULT '#3B82F6'")
        added_color=True
    if 'display_order' not in vehicle_cols:
        db.execute('ALTER TABLE vehicles ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0')
    if added_color:
        palette=['#3B82F6','#22C55E','#F97316','#8B5CF6','#14B8A6','#EF4444','#EAB308','#94A3B8','#EC4899','#06B6D4','#84CC16','#A855F7']
        for i,row in enumerate(db.execute('SELECT id FROM vehicles ORDER BY name').fetchall()):
            db.execute('UPDATE vehicles SET color_hex=?,display_order=? WHERE id=?',(palette[i%len(palette)],i+1,row[0]))
    db.close()
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


@contextmanager
def transaction(path):
    db = connect(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def rows(db, sql, params=()):
    return [dict(x) for x in db.execute(sql, params).fetchall()]


def one(db, sql, params=()):
    r = db.execute(sql, params).fetchone()
    return dict(r) if r else None


def insert(db, table, data):
    # Table/column names are internal constants, never taken from HTTP input.
    cols = ','.join(data)
    marks = ','.join('?' for _ in data)
    db.execute(f'INSERT INTO {table} ({cols}) VALUES ({marks})', tuple(data.values()))


def audit(db, actor, action, table, record, details=None):
    insert(db, 'audit', dict(actor=actor, action=action, object_type=table,
           object_id=record, details_json=dumps(details or {}), created_at=now()))
