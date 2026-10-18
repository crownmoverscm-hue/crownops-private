"""Verified recovery before the production Manager-role migration."""
from datetime import datetime, timezone
import logging
import secrets
import shutil
from .backups import make_backup, verify_backup, file_hash
from .store import transaction, now, audit


def before_manager_migration(path, migration='Manager'):
    from .production import storage_paths
    from .platform_common import put_setting, secret_box
    path, uploads, directory, persisted = storage_paths(path)
    if not persisted:
        raise RuntimeError('Manager migration requires persistent database, upload and recovery storage.')
    secret_box()  # Preserve the configured key. Never generate or print another key.
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_bytes = path.stat().st_size
    if uploads.exists():
        source_bytes += sum(p.stat().st_size for p in uploads.iterdir() if p.is_file() and not p.is_symlink())
    if shutil.disk_usage(directory).free < source_bytes * 2 + 256 * 1024 * 1024:
        raise RuntimeError('Insufficient recovery headroom. No Manager migration was attempted.')
    archive = directory / ('crownops-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(8) + '.zip')
    make_backup(path, archive, uploads)
    verified = verify_backup(archive)
    if not verified['restore_verified'] or not verified['all_files_hashed']:
        raise RuntimeError('Recovery verification failed. No Manager migration was attempted.')
    record = {'filename': archive.name, 'created_at': verified['created_at'], 'verified_at': now(),
              'bytes': archive.stat().st_size, 'sha256': file_hash(archive),
              'upload_files': verified['upload_files'], 'restore_verified': True}
    with transaction(path) as db:
        put_setting(db, 'verified_backup', record)
        audit(db, 'deployment', 'Recovery verified before '+migration+' migration', 'backups', archive.name,
              {'bytes': record['bytes'], 'upload_files': record['upload_files']})
    logging.getLogger('crownops').warning('Pre-migration recovery verified: %s; uploads=%d; sha256=%s', archive.name, record['upload_files'], record['sha256'])
    return record
