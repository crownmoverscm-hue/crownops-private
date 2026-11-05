"""Fictional offline fixture + maintenance, no real secrets/accounts/customer data."""
import os,sys,json,time,base64,secrets,tempfile,hashlib
from pathlib import Path
sys.path.insert(0,'/app');sys.path.insert(0,'/app/tools/pilot/maintenance')
os.umask(0o077)
attempts=[]
def guard(event,args):
    if event in ('socket.connect','socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr','subprocess.Popen','os.system','os.posix_spawn'):
        attempts.append(event);raise RuntimeError('Fictional offline transport denied')
sys.addaudithook(guard)
from cryptography.fernet import Fernet
import core_maintenance as m
from app.security import totp
p=Path('/data/private');db=p/'pilot.sqlite3'
assert os.getuid()==10001 and os.getgid()==10001
if sys.argv[1]=='provision':
    key=Fernet.generate_key();(p/'fixture-app.key').write_bytes(key)
    step=int(time.time()//30);accounts=[]
    for role in ('owner','sales'):
        secret=base64.b32encode(secrets.token_bytes(20)).decode()
        accounts.append({'role':role,'email':role+'@example.invalid','name':'TEST '+role,'password':secrets.token_urlsafe(24),'totp_secret':secret,'code':totp(secret,step)})
    m.provision(db,'/app',accounts,key)
    (p/'fixture-identities.json').write_text(json.dumps(accounts))
    print(json.dumps({'stage':'fixture','uid':os.getuid(),'gid':os.getgid(),'accounts':2,'enabled_factors':2,'database_mode':oct(db.stat().st_mode&0o777),'network_attempts':len(attempts)}))
elif sys.argv[1]=='maintain':
    appkey=(p/'fixture-app.key').read_bytes()
    with tempfile.TemporaryDirectory(prefix='g5-fictional-maintenance-') as tmp:
        q=Path(tmp);key=Fernet.generate_key();archive=q/'archive.enc';receipt=m.backup(db,archive,key,'/data/uploads',True,app_key=appkey)
        trusted=q/'trusted.json';downloaded=q/'downloaded.json'
        for target in (trusted,downloaded):target.write_text(json.dumps(receipt));target.chmod(0o600)
        readback=m.verify_readback(archive,key,appkey,downloaded,trusted)
        destination=p/'recovered.sqlite3';result=m.restore(archive,destination,key,appkey,receipt)
        assert result['pre_revocation']['originals']==result['post_revocation']['originals']
        print(json.dumps({'stage':'exclusive-maintenance','uid':os.getuid(),'integrity':'ok','foreign_key_violations':0,'readback':readback,'pre_fingerprint':result['pre_revocation']['logical_sha256'],'post_fingerprint':result['post_revocation']['logical_sha256'],'sessions_revoked':result['sessions_revoked'],'destination_mode':oct(destination.stat().st_mode&0o777),'network_attempts':len(attempts)}))
else:raise SystemExit('Unknown fixture operation')
assert not attempts
