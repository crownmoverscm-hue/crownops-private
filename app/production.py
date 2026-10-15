"""Owner-only hosting checks and verified portable recovery copies."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import re
import shutil
import sqlite3
import zipfile
from urllib.parse import urlparse
from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from .store import connect, transaction, one, now, audit
from .platform_common import settings, put_setting, require_role, secret_box, uploads_enabled
from .account_access import recovery_email_status
from .backups import make_backup, verify_backup, file_hash


def storage_paths(path):
    path = Path(path).resolve()
    uploads = Path(os.getenv('CROWNOPS_UPLOADS',os.getenv('CROWNOPS_UPLOAD_DIR',str(path.parent/'uploads')))).resolve()
    backups = Path(os.getenv('CROWNOPS_BACKUPS',str(path.parent/'backups'))).resolve()
    mount = os.getenv('RAILWAY_VOLUME_MOUNT_PATH','').strip()
    volume = Path(mount).resolve() if mount else None
    persisted = bool(volume and volume.is_dir() and str(volume) not in ('/','/app') and
                     all(p.is_relative_to(volume) for p in (path,uploads,backups)))
    return path, uploads, backups, persisted


def validate_runtime_storage(path):
    railway = bool(os.getenv('RAILWAY_PROJECT_ID') or os.getenv('RAILWAY_PUBLIC_DOMAIN') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH'))
    if os.getenv('CROWNOPS_ENV','test')=='production' and railway:
        path, _, _, persisted = storage_paths(path)
        if not persisted:
            raise RuntimeError('Production CrownOps on Railway requires its database, uploads and backups on the attached persistent volume.')
        if not path.is_file():
            raise RuntimeError('The existing production database was not found. Verify the volume/path or restore privately before starting; no empty database was created.')


def latest_backup(path):
    with closing(connect(path)) as db:
        record = settings(db,'verified_backup',{}) or {}
    _, _, directory, _ = storage_paths(path)
    name = record.get('filename','')
    safe = bool(re.fullmatch(r'crownops-[0-9TZ-]+-[a-f0-9]{16}\.zip',name))
    file = directory/name if safe else None
    present = bool(file and file.is_file() and not file.is_symlink() and file.stat().st_size==record.get('bytes'))
    return {**record,'available':present}


def hosting_status(path, public_url, secure):
    path, uploads, backups, persisted = storage_paths(path)
    with closing(connect(path)) as db:
        owners = db.execute("SELECT count(*) FROM users WHERE active=1 AND role='owner'").fetchone()[0]
        protected = db.execute("SELECT count(*) FROM users u JOIN auth_factors f ON f.user_id=u.id WHERE u.active=1 AND u.role='owner' AND f.enabled=1").fetchone()[0]
    try:
        secret_box(); encrypted = True
    except Exception:
        encrypted = False
    email = recovery_email_status(path,public_url)
    backup = latest_backup(path)
    try:
        fresh = bool(backup['available'] and datetime.fromisoformat(backup['verified_at'])>datetime.now(timezone.utc)-timedelta(hours=24))
    except (KeyError,ValueError,TypeError):
        fresh = False
    free = shutil.disk_usage(path.parent).free
    mode = os.getenv('CROWNOPS_ENV','test')
    checks = [
        {'key':'mode','label':'Production mode','ready':mode=='production','detail':'Current mode: '+mode+'. Account emails remain independently controlled.','page':'access'},
        {'key':'storage','label':'Persistent company storage','ready':persisted,'detail':'Database, uploads and backup paths are configured under the attached Railway volume.' if persisted else 'Verify the attached Railway volume and data paths before production.','page':''},
        {'key':'https','label':'HTTPS and secure sign-ins','ready':secure and urlparse(public_url).scheme=='https','detail':'Secure cookies and an HTTPS public address are required.','page':''},
        {'key':'encryption','label':'Server encryption key','ready':encrypted,'detail':'Keep the existing key in a separate company password vault. Its value is never shown here.','page':''},
        {'key':'mfa','label':'Owner two-factor authentication','ready':owners>0 and protected==owners,'detail':str(protected)+' of '+str(owners)+' active owners have an authenticator enabled.','page':'account'},
        {'key':'email','label':'Staff invitation and recovery emails','ready':email['available'],'detail':'Configured for '+email['mode']+'. Configuration does not prove inbox delivery.','page':'access'},
        {'key':'uploads','label':'Private upload protection','ready':not uploads_enabled() or bool(os.getenv('CROWNOPS_SCAN_COMMAND','').strip()),'detail':'Uploads are disabled or have a configured scanner. Scanner operation still needs a real file test.','page':''},
        {'key':'space','label':'Storage headroom','ready':free>=256*1024*1024,'detail':str(round(free/1024/1024))+' MB free on the database filesystem.','page':''},
        {'key':'backup','label':'Verified recovery copy','ready':fresh,'detail':'A portable backup passed a separate restore drill within 24 hours.' if fresh else 'Create and download a verified recovery copy. Store a copy outside this Railway volume.','page':''},
    ]
    return {'mode':mode,'public_url':public_url,'checks':checks,'automated_checks_passed':all(c['ready'] for c in checks),
            'backup':backup,'hosting_verification_required':['Enable scheduled Railway volume backups.','Keep one web instance for the current SQLite database.','Verify restart policy, continuous uptime alerts and Railway billing.','Keep an encrypted recovery copy and the original server key separately outside this service.'],
            'notice':'These checks report runtime configuration and local recovery evidence. They do not verify Railway backup schedules, offsite storage, billing or continuous uptime monitoring.'}


def register_production(app, path, auth, public_url, secure):
    @app.get('/api/hosting-status')
    def status(u=Depends(auth)):
        require_role(u,{'owner'})
        return hosting_status(path,public_url,secure)

    @app.post('/api/owner-backups')
    def create_backup(u=Depends(auth)):
        require_role(u,{'owner'})
        import fcntl
        _, uploads, directory, _ = storage_paths(path)
        if directory.is_relative_to(uploads):
            raise HTTPException(503,'Configure a private backup directory outside uploads.')
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        descriptor = os.open(directory/'.backup.lock',os.O_RDWR|os.O_CREAT,0o600)
        archive = None
        try:
            try:
                fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise HTTPException(409,'A backup is already running. Wait for it to finish.')
            recent = latest_backup(path)
            if recent.get('available') and datetime.fromisoformat(recent['verified_at'])>datetime.now(timezone.utc)-timedelta(seconds=60):
                return recent
            import secrets
            name = 'crownops-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(8)+'.zip'
            archive = directory/name
            make_backup(path,archive,uploads)
            verified = verify_backup(archive)
            record = {'filename':name,'created_at':verified['created_at'],'verified_at':now(),
                      'bytes':archive.stat().st_size,'sha256':file_hash(archive),'upload_files':verified['upload_files'],'restore_verified':True}
            with transaction(path) as db:
                put_setting(db,'verified_backup',record)
                audit(db,u['id'],'Recovery backup created and restore verified','backups',name,{'bytes':record['bytes']})
            return {**record,'available':True}
        except (OSError,ValueError,sqlite3.Error,zipfile.BadZipFile):
            import logging
            logging.getLogger('crownops').error('Private recovery backup failed storage or integrity validation. No recovery success was recorded.')
            if archive:
                archive.unlink(missing_ok=True)
            raise HTTPException(503,'Backup verification failed. Review storage and server logs; no recovery success was recorded.')
        finally:
            os.close(descriptor)

    @app.get('/api/owner-backups/{filename}')
    def download_backup(filename:str,u=Depends(auth)):
        require_role(u,{'owner'})
        record = latest_backup(path)
        if filename!=record.get('filename') or not record['available']:
            raise HTTPException(404,'Verified backup not found. Create a fresh recovery copy.')
        file = storage_paths(path)[2]/filename
        if file_hash(file)!=record['sha256']:
            raise HTTPException(409,'Backup checksum changed. Create a fresh verified recovery copy.')
        return FileResponse(file,media_type='application/zip',filename=filename,
                            headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
