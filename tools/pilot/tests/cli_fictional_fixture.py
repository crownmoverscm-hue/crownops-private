"""TEST ONLY: reserved-address fixture and temporary keys; never used for real provisioning."""
from pathlib import Path
import base64,json,os,secrets,sys,time
root=Path(sys.argv[1]);tools=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(tools/'maintenance'))
from core_maintenance import provision
from cryptography.fernet import Fernet
from app.security import totp
os.umask(0o077)
for name in ('source','uploads','appkeys','backupkeys','offsite','restored'):(root/name).mkdir(mode=0o700)
appkey=Fernet.generate_key();backupkey=Fernet.generate_key()
(root/'appkeys/app.key').write_bytes(appkey);(root/'backupkeys/backup.key').write_bytes(backupkey)
accounts=[]
for role in ('owner','sales'):
    secret=base64.b32encode(secrets.token_bytes(20)).decode()
    accounts.append(dict(role=role,email=role+'@example.invalid',name='TEST '+role,
        password='TEST-'+role+'-unique-password-2026!',totp_secret=secret,code=totp(secret,int(time.time()//30))))
result=provision(root/'source/pilot.sqlite3',os.environ['CROWNOPS_TEST_SOURCE'],accounts,appkey)
(root/'quiescence.json').write_text(json.dumps({'operation_id':'CLI-FICTIONAL-001','writer_stopped':True,'uploads_disabled':True}))
print(json.dumps(result))
