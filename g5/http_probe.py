"""Loopback-only fictional runtime smoke; secrets/cookies never printed."""
import os,sys,json,time,urllib.request,urllib.error,stat
from pathlib import Path
sys.path.insert(0,'/app')
from app.security import totp
p=Path('/data/private');stage=sys.argv[1]
def req(method,path,data=None,cookie=None,csrf=None):
    headers={'Content-Type':'application/json','X-Forwarded-For':'192.0.2.11' if stage=='resumed' else '192.0.2.10'}
    if cookie:headers['Cookie']=cookie
    if csrf:headers['X-CSRF-Token']=csrf
    request=urllib.request.Request('http://127.0.0.1:8080'+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
    try:
        with urllib.request.urlopen(request,timeout=3) as r:return r.status,json.loads(r.read()),r.headers
    except urllib.error.HTTPError as r:return r.code,{},r.headers
status,body,_=req('GET','/api/health');assert status==200
if stage=='health':print('HEALTH_OK');raise SystemExit(0)
proc=Path('/proc/1/status').read_text(); fields={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in proc.splitlines() if ':' in line}
assert all(int(n)==10001 for n in fields['Uid'].split())
assert int(fields['CapEff'],16)==0 and fields['NoNewPrivs']=='1'
try:os.setuid(0)
except PermissionError:pass
else:raise AssertionError('Root regained')
db=Path(os.environ['CROWNOPS_DB']);assert stat.S_IMODE(db.stat().st_mode)==0o600 and db.stat().st_uid==10001
for suffix in ('-wal','-shm','-journal'):
    side=Path(str(db)+suffix)
    if side.exists():assert stat.S_IMODE(side.stat().st_mode)==0o600 and side.stat().st_uid==10001
for method,path in [('POST','/api/quotes'),('POST','/api/jobs'),('POST','/api/webhooks/square'),('POST','/api/forgot-password'),('GET','/api/quickbooks/test-connection')]:assert req(method,path,{} if method=='POST' else None)[0]==403
if stage=='resumed':assert req('GET','/api/me',cookie=(p/'fixture-old-cookie').read_text())[0]==401
accounts=json.loads((p/'fixture-identities.json').read_text());last_cookie=None
for a in accounts:
    base={'email':a['email'],'password':a['password']}
    assert req('POST','/api/login',base)[0]==401
    step=int(time.time()//30)+1
    if stage=='resumed':
        # Wait only for a new simulated code step after the pre-backup login.
        previous=json.loads((p/'fixture-login-steps.json').read_text())[a['role']]
        while step<=previous:time.sleep(1);step=int(time.time()//30)+1
    good={**base,'mfa_code':totp(a['totp_secret'],step)}
    code,data,headers=req('POST','/api/login',good);assert code==200
    assert data['user']['role']==a['role']
    cookie=headers['Set-Cookie'].split(';',1)[0];csrf=data['csrf']
    assert req('POST','/api/login',good)[0]==401
    a['login_step']=step;last_cookie=cookie
if stage=='initial':
    body='TEST G5 original email. Fictional data only.'
    data={'request_key':'G5-MAIL-001','client':{'client_name':'TEST G5 Client','email':'g5-client@example.invalid','phone':'514-555-0101'},'service':{'service_type':'Moving','move_date':'2026-11-09','pickup':'TEST A','dropoff':'TEST B'},'source':{'kind':'Email','subject':'TEST G5','body':body}}
    code,created,_=req('POST','/api/core-v1/inquiries',data,cookie,csrf);assert code==200
    code,record,_=req('GET','/api/core-v1/inquiries/'+created['inquiry_id'],cookie=cookie);assert code==200 and record['source']['original_body']==body
    (p/'fixture-old-cookie').write_text(last_cookie)
    (p/'fixture-login-steps.json').write_text(json.dumps({a['role']:a['login_step'] for a in accounts}))
print(json.dumps({'stage':stage,'pid1_uid':10001,'effective_caps':0,'no_new_privileges':True,'root_regain':'refused','database_and_sidecars':'0600 UID10001','password_only':'401 both','fresh_MFA':'200 both','replay':'401 both','old_cookie':'401' if stage=='resumed' else 'captured privately','forbidden_routes':'403','actual_external_operations':0,'network':'Docker network=none; loopback probes only; no deployed egress claim'}))
