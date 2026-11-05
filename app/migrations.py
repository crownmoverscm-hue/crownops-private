"""Versioned schema migrations for each company's SQLite database.

Version 1 is the baseline: everything `store.initialize()` already creates with its
idempotent CREATE TABLE IF NOT EXISTS / ADD COLUMN steps. Installations created before
versioning existed (user_version 0) are recorded at version 1 without data changes.

Every later change is an ordered, named migration below. Each runs inside a single
BEGIN IMMEDIATE transaction that also records its history row and PRAGMA user_version,
so a failure rolls the whole step back and leaves the previous version intact. A
database whose version is newer than this application is refused before anything is
written, so an older release can never run against (or "downgrade") a newer schema.

Rules for adding a migration: append (version, name, function); never edit or reorder
an existing one; use additive changes (new tables/columns/indexes); never rewrite or
delete business data. Restore the pre-migration backup to roll back (see
docs/COMMERCIAL_DEPLOYMENT_RUNBOOK.md).
"""
import re
from pathlib import Path

HISTORY_TABLE = """CREATE TABLE IF NOT EXISTS schema_migrations(
  version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL, app_version TEXT NOT NULL)"""


def _baseline(db):
    """Recorded only: the baseline schema is created by store.initialize()."""


def _ops_status_indexes(db):
    """Indexes used by operational status checks on large installations."""
    db.execute('CREATE INDEX IF NOT EXISTS idx_outbound_messages_status ON outbound_messages(status)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_provider_operations_status ON provider_operations(status)')


MIGRATIONS = [
    (1, 'baseline schema (store.initialize idempotent steps)', _baseline),
    (2, 'operational status indexes', _ops_status_indexes),
]
CURRENT_VERSION = MIGRATIONS[-1][0]
# Versions that alter existing tables or data must be listed here: production then takes a
# verified backup before applying them. Additive indexes/tables do not need one.
BACKUP_REQUIRED = set()


class SchemaError(RuntimeError):
    pass


def schema_version(db):
    return int(db.execute('PRAGMA user_version').fetchone()[0])


def check_compatible(db, current=None):
    current = current if current is not None else MIGRATIONS[-1][0]
    version = schema_version(db)
    if version > current:
        raise SchemaError(f'This database is at schema version {version}, newer than this application '
                          f'(version {current}). Install the matching or newer release; do not downgrade.')
    return version


def pending(db, migrations=None):
    migrations = migrations or MIGRATIONS
    version = schema_version(db)
    return [m for m in migrations if m[0] > version]


def expected_tables():
    names = set()
    for sql_file in ('schema.sql', 'platform_schema.sql'):
        text = (Path(__file__).with_name(sql_file)).read_text()
        names.update(re.findall(r'CREATE TABLE IF NOT EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)', text))
    return names


def verify_schema(db):
    """Unexpected states: missing baseline tables, or history that disagrees with user_version."""
    present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    problems = sorted('missing table ' + t for t in expected_tables() - present)
    version = schema_version(db)
    if version and 'schema_migrations' in present:
        recorded = [r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')]
        if recorded != list(range(1, version + 1)):
            problems.append(f'migration history {recorded} does not match schema version {version}')
    return problems


def apply_migrations(db, app_version, migrations=None, now=None):
    """Apply pending migrations in order, each atomically. Returns the versions applied."""
    from .store import now as store_now
    stamp = now or store_now
    migrations = migrations or MIGRATIONS
    check_compatible(db, migrations[-1][0])
    db.execute(HISTORY_TABLE)
    applied = []
    for version, name, step in pending(db, migrations):
        if version != schema_version(db) + 1:
            raise SchemaError(f'Migration {version} is out of order after version {schema_version(db)}.')
        db.execute('BEGIN IMMEDIATE')
        try:
            step(db)
            db.execute('INSERT INTO schema_migrations(version,name,applied_at,app_version) VALUES(?,?,?,?)',
                       (version, name, stamp(), app_version))
            db.execute(f'PRAGMA user_version={int(version)}')
            db.execute('COMMIT')
        except Exception as ex:
            db.execute('ROLLBACK')
            raise SchemaError(f'Migration {version} ({name}) failed and was rolled back; the database remains at '
                              f'version {schema_version(db)}. Restore the pre-migration backup if needed. Cause: {ex}') from ex
        applied.append(version)
    return applied


def status(db):
    version = schema_version(db)
    return {'schema_version': version, 'current_version': CURRENT_VERSION,
            'pending': [{'version': v, 'name': n} for v, n, _ in pending(db)],
            'newer_than_app': version > CURRENT_VERSION, 'problems': verify_schema(db)}
