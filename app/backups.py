"""Consistent DB snapshot plus private blobs. Restore never overwrites live data."""
from pathlib import Path
from contextlib import closing
import json,sqlite3,tempfile,zipfile,os,shutil,hashlib
from .store import connect,now

def make_backup(path,dest,uploads):
    dest=dest.resolve()
    if dest.exists():raise ValueError('Choose a new private backup filename.')
    if dest.is_relative_to(uploads.resolve()):raise ValueError('Do not store backups inside the upload directory.')
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        snapshot=Path(tmp)/'database.sqlite3'
        with closing(connect(path)) as source,closing(sqlite3.connect(snapshot)) as target:source.backup(target)
        with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED) as z:
            z.write(snapshot,'database.sqlite3')
            if uploads.exists():
                for f in uploads.iterdir():
                    if f.is_file() and not f.is_symlink():z.write(f,'uploads/'+f.name)
            z.writestr('BACKUP_INFO.json',json.dumps({'created_at':now(),'db_sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest(),'contains_private_data':True,'encryption_key_included':False,'instruction':'Keep the original CROWNOPS_SECRET_KEY in a separate secure vault. Stop writes during operational cutover; restore only to a new instance.'},indent=2))
    dest.chmod(0o600);return str(dest)

def restore_archive(archive,path):
    if path.exists() or (path.parent/'uploads').exists():raise ValueError('Use a new empty target instance, never an existing database/uploads folder.')
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if 'database.sqlite3' not in names:raise ValueError('Not a CrownOps backup archive.')
        if sum(i.file_size for i in z.infolist())>20_000_000_000:raise ValueError('Archive too large for this restore command; use a reviewed administrative procedure.')
        for n in names:
            p=Path(n)
            if p.is_absolute() or '..' in p.parts or '\\' in n or not (n=='database.sqlite3' or n=='BACKUP_INFO.json' or n.startswith('uploads/')):raise ValueError('Unsafe or unsupported archive member.')
            if n.startswith('uploads/') and len(p.parts)!=2:raise ValueError('Nested upload folders are not supported.')
        with tempfile.TemporaryDirectory(dir=path.parent.parent if path.parent.parent.exists() else None) as tmp:
            root=Path(tmp);z.extractall(root)
            with closing(sqlite3.connect(root/'database.sqlite3')) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup integrity check failed.')
                # Sessions from a restored copy must not remain usable.
                for table in ('sessions','portal_sessions','portal_invites','account_resets','oauth_states'):
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():db.execute('DELETE FROM '+table)
                db.commit()
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copy2(root/'database.sqlite3',path);path.chmod(0o600)
            if (root/'uploads').exists():shutil.copytree(root/'uploads',path.parent/'uploads')
    return 'Restored privately to '+str(path)+'. User sessions and invitations were revoked. Keep external actions OFF until reconciliation.'
