"""Offline maintenance revision 2; separate from the immutable accepted application.
No transport, server or background process is created. Secrets must never be logged.
Private directories assume a trusted owner; arbitrary code/file access is outside this boundary.
"""
from contextlib import closing
from pathlib import Path
import hashlib
import io
import json
import os
import sqlite3
import stat
import tempfile
import time
import uuid
import zipfile
from cryptography.fernet import Fernet

CANDIDATE = 'core-v1-pilot-2026-11-09-rc1'
SOURCE_HASHES = {'app/schema.sql': 'ba5f4aa8a3635f26f489fb8bb9c71e4dad4f23596f6c99a1f2dca8e9862e4392', 'app/platform_schema.sql': 'e87857e73da4c0ac2bf61ec85ba5b8aaeff913fdabeb3a4fc9f39a64c3a1dfc9', 'app/core_v1.py': '4ad03e30e94304aceeff2ffd2ddacd6779adc481335e4a9ca2c64719edd28eb9', 'app/store.py': 'ce14698d2c69be0980309bb29ec4aa61353075af8a3ec599681949851cf15b5d', 'app/security.py': 'ba7ebdcd568d33c426a02a07e18f807f66d66f7778fe68440f6d042510d250bf', 'app/platform_common.py': '262351a047eef787662081c90bbe55d3c0c134c1edcc2e1872db13f861ce7e4e', 'app/core_pilot.py': '4fec9a03f71024484c5d24024bb6bcf94379c3e443b526a333f369a3da6728d7'}
LIMIT = 64 * 1024 * 1024
EMPTY = ('quotes','jobs','assignments','payments','payment_links','deposit_links','job_invoices',
         'invoice_payment_allocations','outbound_messages','provider_operations','oauth_credentials',
         'oauth_states','ai_runs','document_approvals','participation_access','portal_invites',
         'portal_sessions','invoice_access_invites','invoice_access_sessions','invitations','account_resets',
         'attachments','attachment_media','staff_documents','fleet_documents','positions',
         'pricing_versions','daily_rates','annual_rate_periods','material_catalog')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def path_check(path, exists=True, mode=0o600):
    p = Path(path)
    if not p.is_absolute() or any(x.is_symlink() for x in (p,*p.parents)):
        raise ValueError('Absolute nonsymlink path required')
    s = p.parent.stat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
        raise ValueError('Private owned parent required')
    if not exists:
        if os.path.lexists(p):
            raise FileExistsError('No-overwrite destination exists')
    else:
        s = p.stat()
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != mode:
            raise ValueError('Private owned regular file required')
    return p


def key_file(path):
    p = path_check(path)
    data = p.read_bytes().strip()
    Fernet(data)
    return data


def ro(path):
    return sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True)


def integrity(path):
    with closing(ro(path)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity failed')
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('SQLite foreign keys failed')


def summary(path):
    with closing(ro(path)) as db:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts={n:db.execute('SELECT count(*) FROM "'+n.replace('"','""')+'"').fetchone()[0] for n in names}
        originals=[list(r) for r in db.execute('SELECT id,source_sha256 FROM manual_source_submissions ORDER BY id')]
        for body,reference,recorded in db.execute('SELECT original_body,source_reference,source_sha256 FROM manual_source_submissions'):
            if sha((body or reference).encode())!=recorded:
                raise ValueError('Original submission hash does not match content')
        audit=[list(r) for r in db.execute('SELECT * FROM audit ORDER BY id')]
        return {'counts':counts,'originals':originals,'audit_sha256':sha(json.dumps(audit).encode()),
                'logical_sha256':sha('\n'.join(db.iterdump()).encode())}


def scope(path, app_key=None):
    with closing(ro(path)) as db:
        allowed={'users','auth_factors','settings','sessions','login_attempts','audit','leads','client_accounts','client_account_leads','manual_source_submissions','core_v1_client_requests','manual_action_requests','messages','tasks','sqlite_sequence'}
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in sorted((names-allowed)|set(EMPTY)):
            if db.execute('SELECT count(*) FROM '+name).fetchone()[0]:
                raise ValueError('Out-of-scope pilot record: '+name)
        users=db.execute('SELECT id,role,active FROM users ORDER BY role').fetchall()
        if len(users)!=2 or sorted((u[1],u[2]) for u in users)!=[('owner',1),('sales',1)]:
            raise ValueError('Exactly active owner/sales required')
        factors=db.execute('SELECT user_id,secret_ciphertext,enabled,last_step FROM auth_factors').fetchall()
        if len(factors)!=2 or {f[0] for f in factors}!={u[0] for u in users} or any(f[2]!=1 or f[3]<0 for f in factors):
            raise ValueError('Both enrolled MFA factors required')
        if app_key:
            import base64
            for f in factors:
                secret=json.loads(Fernet(app_key).decrypt(f[1].encode()))['secret']
                if len(base64.b32decode(secret)) < 20:
                    raise ValueError('Invalid MFA factor')


def private_uploads_empty(path):
    p=Path(path)
    if not p.is_absolute() or any(x.is_symlink() for x in (p,*p.parents)):
        raise ValueError('Unsafe private-upload scope')
    s=p.stat()
    if not stat.S_ISDIR(s.st_mode) or stat.S_IMODE(s.st_mode)!=0o700 or s.st_uid!=os.getuid() or any(p.iterdir()):
        raise ValueError('Dedicated private-upload directory must be owned, 0700 and empty')


def publish(path, data):
    """Race-safe no-overwrite with same-directory temporary file; fsync data and directory."""
    path=path_check(path,False)
    fd,tmp=tempfile.mkstemp(prefix='.publish-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.link(tmp,path)
        dfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        os.unlink(tmp)


def provision(path,source,accounts,app_key):
    """Fresh offline schema, exactly owner/sales, MFA confirmed before publication.
    Secret-bearing account dictionaries exist only in process memory, never output.
    """
    from argon2 import PasswordHasher
    from app.core_v1 import ensure_schema
    import app.core_v1
    if Path(source).resolve() != Path(app.core_v1.__file__).resolve().parents[1]:
        raise ValueError("Source/import roots differ")
    for name, expected in SOURCE_HASHES.items():
        if sha((Path(source)/name).read_bytes()) != expected:
            raise ValueError("Accepted source fingerprint mismatch")
    from app.security import totp
    path=path_check(path,False)
    if len(accounts)!=2 or [a['role'] for a in accounts]!=['owner','sales']:
        raise ValueError('Exactly owner then sales required')
    if accounts[0]['email'].casefold()==accounts[1]['email'].casefold() or accounts[0]['password']==accounts[1]['password'] or accounts[0]['totp_secret']==accounts[1]['totp_secret']:
        raise ValueError('Separate identities, passwords and MFA secrets required')
    import hmac,base64,re
    step=int(time.time()//30)
    for a in accounts:
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',a['email']) or not a['name'].strip() or not 12<=len(a['password'])<=200:
            raise ValueError('Invalid account input')
        if len(base64.b32decode(a['totp_secret']))<20 or not hmac.compare_digest(totp(a['totp_secret'],step),a['code']):
            raise ValueError('MFA confirmation failed')
    box=Fernet(app_key)
    with tempfile.TemporaryDirectory(prefix='.provision-',dir=path.parent) as work:
        staging=Path(work)/'database.sqlite3'
        with closing(sqlite3.connect(staging)) as db:
            db.executescript((Path(source)/'app/schema.sql').read_text())
            db.executescript((Path(source)/'app/platform_schema.sql').read_text())
        os.chmod(staging,0o600)
        ensure_schema(staging)
        hasher=PasswordHasher(time_cost=2,memory_cost=65536,parallelism=2)
        with closing(sqlite3.connect(staging)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
            for a in accounts:
                uid='USER-'+uuid.uuid4().hex[:16]
                db.execute('INSERT INTO users(id,email,name,role,password_hash,active,created_at) VALUES(?,?,?,?,?,1,?)',
                           (uid,a['email'].lower(),a['name'],a['role'],hasher.hash(a['password']),stamp))
                cipher=box.encrypt(json.dumps({'secret':a['totp_secret']}).encode()).decode()
                db.execute('INSERT INTO auth_factors VALUES(?,?,1,?,?)',(uid,cipher,step,stamp))
                if a['role']=='owner': db.execute('INSERT INTO settings VALUES(?,?)',('master_owner',json.dumps({'user_id':uid})))
                db.execute('INSERT INTO audit(actor,action,object_type,object_id,details_json,created_at) VALUES(?,?,?,?,?,?)',
                           (uid,'Offline account and MFA provisioned','users',uid,'{}',stamp))
            db.commit()
        integrity(staging); scope(staging,app_key)
        publish(path,staging.read_bytes())
    return {'candidate':CANDIDATE,'accounts':2,'mfa_enabled':2}


def unpack(token,key):
    if len(token)>2*LIMIT: raise ValueError('Ciphertext size limit')
    plain=Fernet(key).decrypt(token)
    with zipfile.ZipFile(io.BytesIO(plain)) as z:
        if sorted(z.namelist())!=['manifest.json','snapshot.db'] or any(i.file_size>LIMIT for i in z.infolist()):
            raise ValueError('Unexpected or oversized archive member')
        m=json.loads(z.read('manifest.json')); data=z.read('snapshot.db')
        if m['format']!=2 or m['candidate']!=CANDIDATE or m.get('source_hashes')!=SOURCE_HASHES or m['snapshot_sha256']!=sha(data) or m['snapshot_size']!=len(data):
            raise ValueError('Manifest mismatch')
    return data,m


def backup(source,out,key,uploads,quiescence,operation_id=None,app_key=None):
    if app_key is None or app_key==key: raise ValueError('Distinct verified app and backup keys required')
    if not quiescence: raise ValueError('Recorded owner write-quiescence acknowledgement required')
    # This acknowledgement is not a process lock; operator must independently prove no writers.
    source=path_check(source); out=path_check(out,False); private_uploads_empty(uploads)
    for suffix in ('-wal','-shm','-journal'):
        p=Path(str(source)+suffix)
        if os.path.lexists(p): path_check(p)
    integrity(source); scope(source,app_key)
    with tempfile.TemporaryDirectory(prefix='.snapshot-',dir=out.parent) as work:
        staging=Path(work)/'snapshot.db'
        with closing(ro(source)) as src,closing(sqlite3.connect(staging)) as dst: src.backup(dst)
        os.chmod(staging,0o600); integrity(staging); scope(staging)
        if staging.stat().st_size>LIMIT: raise ValueError('64 MiB snapshot reference-tool limit')
        data=staging.read_bytes()
        m={'format':2,'candidate':CANDIDATE,'source_hashes':SOURCE_HASHES,'created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
           'operation_id':operation_id or uuid.uuid4().hex,
           'snapshot_sha256':sha(data),'snapshot_size':len(data),'summary':summary(staging),
           'scope':'SQLite only; inspected dedicated private-upload directory empty'}
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_STORED) as z:
            z.writestr('manifest.json',json.dumps(m,sort_keys=True)); z.writestr('snapshot.db',data)
        token=Fernet(key).encrypt(buf.getvalue())
        if unpack(token,key)!=(data,m): raise ValueError('Encryption readback failure')
        publish(out,token)
    return {'format':2,'candidate':CANDIDATE,'source_hashes':SOURCE_HASHES,'operation_id':m['operation_id'],'archive_sha256':sha(token),'archive_bytes':len(token),
            'snapshot_sha256':m['snapshot_sha256'],'summary':m['summary']}


def restore(archive,dest,key,app_key,receipt):
    dest=path_check(dest,False); archive=path_check(archive)
    if key==app_key: raise ValueError('Backup and application keys must differ')
    with open(archive,'rb') as f: token=f.read(2*LIMIT+1)
    if sha(token)!=receipt['archive_sha256'] or len(token)!=receipt['archive_bytes'] or receipt['format']!=2 or receipt['candidate']!=CANDIDATE or receipt.get('source_hashes')!=SOURCE_HASHES:
        raise ValueError('Offsite receipt readback mismatch')
    data,m=unpack(token,key)
    if m['operation_id']!=receipt['operation_id'] or m['snapshot_sha256']!=receipt['snapshot_sha256'] or m['summary']!=receipt['summary']:
        raise ValueError('Receipt/manifest mismatch')
    with tempfile.TemporaryDirectory(prefix='.restore-',dir=dest.parent) as work:
        staging=Path(work)/'snapshot.db'; staging.write_bytes(data); os.chmod(staging,0o600)
        integrity(staging); scope(staging,app_key)
        before=summary(staging)
        if before!=m['summary']: raise ValueError('Restored logical summary mismatch')
        # Deliberate OFFLINE raw maintenance access, not a protected runtime connection.
        with closing(sqlite3.connect(staging)) as db:
            db.execute('BEGIN IMMEDIATE')
            revoked=db.execute('SELECT count(*) FROM sessions').fetchone()[0]
            db.execute('DELETE FROM sessions')
            # All other bearer capability tables must be EMPTY under this narrow pilot scope.
            step=int(time.time()//30)
            db.execute('UPDATE auth_factors SET last_step=max(last_step,?)',(step,))
            stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
            db.execute('INSERT INTO audit(actor,action,object_type,object_id,details_json,created_at) VALUES(?,?,?,?,?,?)',
                       ('owner-offline-maintenance','Recovery sessions revoked','recovery',uuid.uuid4().hex,
                        json.dumps({'sessions_revoked':revoked,'source_archive_sha256':sha(token)}),stamp))
            db.commit()
        integrity(staging); scope(staging,app_key)
        after=summary(staging)
        if before['originals']!=after['originals']: raise ValueError('Original hash list changed')
        publish(dest,staging.read_bytes())
    return {'candidate':CANDIDATE,'sessions_revoked':revoked,'pre_revocation':before,'post_revocation':after,
            'revocation':'sessions deleted; other bearer tables proven empty; MFA counters advanced; audit appended'}


def verify_ready(database,app_key,expected_emails):
    """Offline pre-start gate; confirms enabled factors decrypt, not just key shape.
    Host/file custody still required between this separate process and locked startup.
    """
    database=path_check(database)
    integrity(database); scope(database,app_key)
    with closing(ro(database)) as db:
        actual=dict(db.execute('SELECT role,lower(email) FROM users').fetchall())
        staff_ids=dict(db.execute('SELECT role,id FROM users').fetchall())
        expected={role:email.lower() for role,email in expected_emails.items()}
        if actual!=expected: raise ValueError('Confirmed pilot identities do not match')
    return {'candidate':CANDIDATE,'accounts':2,'enabled_mfa_factors':2,'mfa_decryption':'verified','database_integrity':'verified','staff_ids':staff_ids}


def verify_readback(archive,key,app_key,receipt,trusted_receipt):
    """Offline downloaded-file verification. No provider connection or publication.

    The separately retained trusted receipt must come from the owner capture
    record, not only from the same potentially replaced remote archive pair.
    """
    archive=path_check(archive)
    receipt_path=path_check(receipt); trusted_path=path_check(trusted_receipt)
    if os.path.samefile(receipt_path,trusted_path):
        raise ValueError('Independent retained receipt copy required')
    if receipt_path.read_bytes()!=trusted_path.read_bytes():
        raise ValueError('Trusted and downloaded receipts differ')
    record=json.loads(trusted_path.read_text())
    if key==app_key: raise ValueError('Distinct application and backup keys required')
    with archive.open('rb') as f: token=f.read(2*LIMIT+1)
    if record['candidate']!=CANDIDATE or record.get('source_hashes')!=SOURCE_HASHES or record['format']!=2 or len(token)!=record['archive_bytes'] or sha(token)!=record['archive_sha256']:
        raise ValueError('Offsite checksum, size or candidate mismatch')
    data,manifest=unpack(token,key)
    if manifest['operation_id']!=record['operation_id'] or manifest['snapshot_sha256']!=record['snapshot_sha256'] or manifest['summary']!=record['summary']:
        raise ValueError('Trusted receipt and authenticated manifest differ')
    with tempfile.TemporaryDirectory(prefix='.readback-',dir=archive.parent) as work:
        snapshot=Path(work)/'snapshot.db'; snapshot.write_bytes(data); snapshot.chmod(0o600)
        integrity(snapshot); scope(snapshot,app_key)
        if summary(snapshot)!=record['summary']: raise ValueError('Readback logical summary mismatch')
    return {'candidate':CANDIDATE,'operation_id':record['operation_id'],
            'archive_sha256':sha(token),'archive_bytes':len(token),
            'trusted_receipt_matched':True,'integrity':'ok','foreign_key_violations':0,
            'published_database':False,'provider_operations':0}
