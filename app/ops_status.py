"""Operational status for one company installation (Priority 6).

Used by GET /api/ops/status (Owner/Manager) and `manage.py ops-check` (exit 0 ok,
1 warning, 2 critical) so any external monitor can alert on it. Findings contain
codes, counts and ages only: no customer names, emails, message bodies, tokens or
amounts. Thresholds are environment settings; nothing here contacts external services.
"""
import os
import shutil
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

JOB_KEY = 'background_jobs'


def _setting_float(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def record_job(path, name, error=None):
    """Background job heartbeat: last success, last failure (exception type only), consecutive failures."""
    from .platform_common import put_setting, settings
    from .store import now, transaction
    with transaction(path) as db:
        jobs = settings(db, JOB_KEY, {}) or {}
        job = dict(jobs.get(name) or {})
        if error is None:
            job.update(last_ok_at=now(), consecutive_failures=0)
        else:
            job.update(last_error_at=now(), last_error=str(error)[:80], consecutive_failures=int(job.get('consecutive_failures', 0)) + 1)
        jobs[name] = job
        put_setting(db, JOB_KEY, jobs)


def _age_hours(stamp):
    try:
        moment = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - moment).total_seconds() / 3600


def collect(path):
    from .migrations import status as schema_status
    from .platform_common import settings
    from .production import storage_paths
    from .store import connect
    from .version import APP_VERSION
    findings = []

    def add(level, code, message, **data):
        findings.append({'level': level, 'code': code, 'message': message, **data})

    path, uploads, backups, _ = storage_paths(path)
    report = {'app_version': APP_VERSION, 'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    if not path.exists():
        add('critical', 'database_missing', 'The database file does not exist.')
        return {**report, 'state': 'critical', 'findings': findings}
    try:
        with closing(connect(path)) as db:
            report['database'] = {'quick_check': db.execute('PRAGMA quick_check').fetchone()[0], 'bytes': path.stat().st_size}
            if report['database']['quick_check'] != 'ok':
                add('critical', 'database_integrity', 'SQLite quick_check failed. Stop writes and restore from the latest verified backup.')
            schema = schema_status(db)
            report['schema'] = {k: schema[k] for k in ('schema_version', 'current_version', 'newer_than_app')}
            if schema['pending'] or schema['newer_than_app'] or schema['problems']:
                add('critical', 'schema', 'Schema is not at the version this release expects.', pending=len(schema['pending']),
                    problems=len(schema['problems']), newer_than_app=schema['newer_than_app'])
            backup = settings(db, 'backup_status', {}) or {}
            max_age = _setting_float('CROWNOPS_BACKUP_MAX_AGE_HOURS', 26)
            age = _age_hours(backup['last_success_at']) if backup.get('last_success_at') else None
            report['backup'] = {'state': backup.get('state', 'never'), 'age_hours': round(age, 1) if age is not None else None,
                                'restore_verified': backup.get('restore_verified'), 'bytes': backup.get('bytes')}
            if backup.get('state') == 'failed':
                add('critical', 'backup_failed', 'The most recent backup failed: ' + str(backup.get('last_error', ''))[:120])
            if age is None:
                add('warning' if backup.get('state') != 'failed' else 'critical', 'backup_missing', 'No successful backup is recorded.')
            elif age > max_age:
                add('critical', 'backup_stale', f'The last successful backup is {age:.0f} hours old (limit {max_age:.0f}).')
            unknown = {r[0]: r[1] for r in db.execute("SELECT status,count(*) FROM outbound_messages WHERE status IN ('Unknown','Failed','Sending') GROUP BY status")}
            report['client_messages_needing_review'] = unknown
            if unknown.get('Unknown') or unknown.get('Failed'):
                add('warning', 'messages_unconfirmed', 'Client messages need reconciliation before any resend.', **{k.lower(): v for k, v in unknown.items()})
            stuck = {r[0]: r[1] for r in db.execute("SELECT status,count(*) FROM provider_operations WHERE status IN ('Unknown','Mismatch','Running','Failed') GROUP BY status")}
            report['integration_operations_needing_review'] = stuck
            if stuck.get('Unknown') or stuck.get('Mismatch'):
                add('warning', 'integration_reconciliation', 'Accounting or payment operations need reconciliation.', **{k.lower(): v for k, v in stuck.items()})
            jobs = settings(db, JOB_KEY, {}) or {}
            report['background_jobs'] = {k: {'consecutive_failures': v.get('consecutive_failures', 0), 'last_ok_at': v.get('last_ok_at')} for k, v in jobs.items()}
            for name, job in jobs.items():
                if int(job.get('consecutive_failures', 0)) >= int(_setting_float('CROWNOPS_JOB_FAILURE_ALERT', 3)):
                    add('critical', 'background_job_failing', f'Background job {name} has failed {job["consecutive_failures"]} times in a row.', job=name)
    except Exception as ex:
        add('critical', 'database_unavailable', 'The database could not be read: ' + type(ex).__name__)
    warn_pct, crit_pct = _setting_float('CROWNOPS_DISK_WARN_PERCENT', 15), _setting_float('CROWNOPS_DISK_CRITICAL_PERCENT', 5)
    report['storage'] = {}
    for label, folder in (('database', path.parent), ('uploads', uploads), ('backups', backups)):
        target = folder if folder.exists() else folder.parent
        if not target.exists():
            continue
        usage = shutil.disk_usage(target)
        free_pct = round(usage.free * 100 / usage.total, 1) if usage.total else 0
        report['storage'][label] = {'free_bytes': usage.free, 'free_percent': free_pct}
        if free_pct < crit_pct:
            add('critical', 'disk_low', f'Only {free_pct}% free on the {label} volume.', volume=label)
        elif free_pct < warn_pct:
            add('warning', 'disk_low', f'Only {free_pct}% free on the {label} volume.', volume=label)
    state = 'critical' if any(f['level'] == 'critical' for f in findings) else ('warning' if findings else 'ok')
    return {**report, 'state': state, 'findings': findings}


def register_ops_status(app, path, auth):
    from fastapi import Depends
    from .platform_common import MANAGERS, require_role

    @app.get('/api/ops/status')
    def ops_status(u=Depends(auth)):
        require_role(u, MANAGERS)
        return collect(path)
