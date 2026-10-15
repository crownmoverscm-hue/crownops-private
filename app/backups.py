"""Private SQLite snapshots and uploads; restoration never overwrites live data."""
from pathlib import Path
from contextlib import closing
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from .store import connect, now


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def referenced_uploads(db):
    names = set()
    for table in ('attachments', 'fleet_documents'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            names.update(r[0] for r in db.execute('SELECT stored_name FROM '+table+' WHERE deleted_at IS NULL'))
    return names


def verify_referenced_files(db, uploads):
    for table in ('attachments','fleet_documents'):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue
        for name, size, digest in db.execute('SELECT stored_name,size,sha256 FROM '+table+' WHERE deleted_at IS NULL'):
            file = uploads/name
            if (Path(name).name!=name or '\\' in name or not file.is_file() or file.is_symlink() or
                    file.stat().st_size!=size or file_hash(file)!=digest):
                raise ValueError('A referenced private upload failed its stored checksum. Repair storage before recovery.')


def make_backup(path, dest, uploads):
    path, dest, uploads = Path(path).resolve(), Path(dest).absolute(), Path(uploads).resolve()
    if not path.is_file():
        raise ValueError('The source database does not exist. No empty backup was created.')
    if dest.exists() or dest.is_symlink():
        raise ValueError('Choose a new private backup filename.')
    if dest.resolve().is_relative_to(uploads):
        raise ValueError('Do not store backups inside the upload directory.')
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp)/'database.sqlite3'
            with closing(connect(path)) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
                needed = referenced_uploads(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise ValueError('Source database integrity check failed.')
                verify_referenced_files(target,uploads)
            files = [f for f in uploads.iterdir() if f.is_file() and not f.is_symlink() and not f.name.endswith('.upload')] if uploads.exists() else []
            if needed-{f.name for f in files}:
                raise ValueError('A referenced private upload is missing. Repair storage before backing up.')
            handle, name = tempfile.mkstemp(prefix='.crownops-backup-', suffix='.partial', dir=dest.parent)
            os.close(handle); temporary = Path(name)
            manifest = {'database.sqlite3':{'sha256':file_hash(snapshot),'bytes':snapshot.stat().st_size}}
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot, 'database.sqlite3')
                for file in sorted(files):
                    member = 'uploads/'+file.name
                    manifest[member] = {'sha256':file_hash(file),'bytes':file.stat().st_size}
                    archive.write(file, member)
                archive.writestr('BACKUP_INFO.json', json.dumps({
                    'format_version':2,'created_at':now(),'files':manifest,
                    'db_sha256':manifest['database.sqlite3']['sha256'],
                    'contains_private_data':True,'encryption_key_included':False,
                    'instruction':'Keep this private archive and the original CROWNOPS_SECRET_KEY separately. Restore only to a new instance with external actions off.'}, indent=2))
            inspect_archive(temporary)
            # Publish atomically and exclusively on the same filesystem.
            os.link(temporary, dest)
        return str(dest)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def inspect_archive(archive):
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        if 'database.sqlite3' not in names or len(names)!=len(set(names)):
            raise ValueError('Missing database or duplicate backup members.')
        if sum(i.file_size for i in z.infolist())>20_000_000_000:
            raise ValueError('Archive too large for this restore command.')
        for member in z.infolist():
            name, p = member.filename, Path(member.filename)
            if (p.is_absolute() or '..' in p.parts or '\\' in name or
                    not (name in ('database.sqlite3','BACKUP_INFO.json') or (name.startswith('uploads/') and len(p.parts)==2)) or
                    member.is_dir() or ((member.external_attr >> 16) & 0o170000)==0o120000):
                raise ValueError('Unsafe or unsupported archive member.')
        if z.testzip():
            raise ValueError('Backup archive integrity check failed.')
        info = json.loads(z.read('BACKUP_INFO.json')) if 'BACKUP_INFO.json' in names else {}
        if not isinstance(info,dict):
            raise ValueError('Invalid backup metadata.')
        manifest = info.get('files')
        if manifest is not None:
            if not isinstance(manifest,dict) or set(manifest)!=set(names)-{'BACKUP_INFO.json'}:
                raise ValueError('Backup file manifest does not match its contents.')
            for name, expected in manifest.items():
                with z.open(name) as source:
                    digest = hashlib.file_digest(source, 'sha256').hexdigest()
                if not isinstance(expected,dict) or digest!=expected.get('sha256') or z.getinfo(name).file_size!=expected.get('bytes'):
                    raise ValueError('Backup file checksum failed.')
        elif info.get('db_sha256'):
            with z.open('database.sqlite3') as source:
                if hashlib.file_digest(source,'sha256').hexdigest()!=info['db_sha256']:
                    raise ValueError('Backup database checksum failed.')
        return {'created_at':info.get('created_at'),'format_version':info.get('format_version',1),
                'upload_files':sum(name.startswith('uploads/') for name in names),
                'all_files_hashed':manifest is not None}


def restore_archive(archive, path):
    path = Path(path).absolute()
    if path.exists() or path.is_symlink() or (path.parent/'uploads').exists():
        raise ValueError('Use a new empty target instance, never an existing database/uploads folder.')
    inspect_archive(archive)
    with zipfile.ZipFile(archive) as z, tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); z.extractall(root)
        with closing(sqlite3.connect(root/'database.sqlite3')) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('Backup integrity check failed.')
            if referenced_uploads(db)-{f.name for f in (root/'uploads').glob('*')}:
                raise ValueError('The backup is missing a referenced private upload.')
            verify_referenced_files(db,root/'uploads')
            for table in ('sessions','invitations','portal_sessions','portal_invites','account_resets','oauth_states'):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    db.execute('DELETE FROM '+table)
            db.commit()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor,'wb') as target, (root/'database.sqlite3').open('rb') as source:
                shutil.copyfileobj(source,target)
            if (root/'uploads').exists():
                shutil.copytree(root/'uploads',path.parent/'uploads')
                (path.parent/'uploads').chmod(0o700)
                for file in (path.parent/'uploads').iterdir():
                    file.chmod(0o600)
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return 'Restored privately to '+str(path)+'. User sessions and invitations were revoked. Keep external actions OFF until reconciliation.'


def verify_backup(archive):
    result = inspect_archive(archive)
    with tempfile.TemporaryDirectory() as tmp:
        restore_archive(archive, Path(tmp)/'restore'/'crownops.sqlite3')
    return {**result,'restore_verified':True}
