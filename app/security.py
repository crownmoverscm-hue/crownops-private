"""Account recovery, optional TOTP MFA and deployment guards."""
from contextlib import closing
from datetime import datetime,timezone,timedelta
from urllib.parse import quote
import base64,hashlib,hmac,json,os,secrets,struct,time
from fastapi import BackgroundTasks,Depends,HTTPException,Request
from .store import connect,transaction,one,rows,insert,audit,now,dumps,uid
from .platform_common import *
from .account_access import protect_owner_account, recovery_rate_limit, notify_password_reset


def validate_deployment(url,secure):
    if os.getenv('CROWNOPS_ENV','test')=='production':
        if not secure or not url.startswith('https://'):raise RuntimeError('Production requires HTTPS public URL and secure cookies.')
        secret_box()
        from .photo_safety import photo_mode,photo_processor_ready,photo_processor_self_test
        if uploads_enabled() and photo_mode():
            if not photo_processor_ready():raise RuntimeError('Production photo uploads require the restricted image processor.')
            try:photo_processor_self_test()
            except Exception as error:raise RuntimeError('The photo processor failed its startup test. Keep uploads disabled until it is working.') from error
            return
        if uploads_enabled() and not os.getenv('CROWNOPS_SCAN_COMMAND'):
            raise RuntimeError('Production uploads require a malware scanning command. Keep CROWNOPS_UPLOADS_ENABLED=0 until scanning is configured.')


def totp(secret,step):
    key=base64.b32decode(secret);msg=struct.pack('>Q',step);digest=hmac.new(key,msg,hashlib.sha1).digest();offset=digest[-1]&15
    return str((struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff)%1000000).zfill(6)


def verify_user_mfa(path,user_id,code):
    with transaction(path) as db:
        f=one(db,'SELECT * FROM auth_factors WHERE user_id=? AND enabled=1',(user_id,))
        if not f:return os.getenv('CROWNOPS_REQUIRE_MFA','0')!='1'
        if not isinstance(code,str) or not code.isdigit() or len(code)!=6:return False
        try:secret=decrypt(f['secret_ciphertext'])['secret']
        except Exception:return False
        step=int(time.time()//30)
        for n in (step-1,step,step+1):
            if n>f['last_step'] and hmac.compare_digest(totp(secret,n),code):
                db.execute('UPDATE auth_factors SET last_step=? WHERE user_id=?',(n,user_id));return True
    return False


def job_resource_blockers(db,j):
    crew=rows(db,"SELECT resource_id FROM assignments WHERE job_id=? AND resource_type='employee'",(j['id'],))
    trucks=rows(db,"SELECT resource_id FROM assignments WHERE job_id=? AND resource_type='vehicle'",(j['id'],))
    missing=[]
    required_crew,required_trucks=1,0 if j['phase'] in ('Packing','Unpacking') else 1
    q=one(db,'SELECT result_json FROM quotes WHERE id=?',(j.get('quote_id'),)) if j.get('quote_id') else None
    if q:
        result=json.loads(q['result_json'])
        required_crew=max(required_crew,int(result.get('movers') or 0))
        if j['phase'] not in ('Packing','Unpacking'):
            required_trucks=max(required_trucks,int(result.get('trucks') or 0))
    if len(crew)<required_crew:missing.append(f'Assign {required_crew} crew member'+('s' if required_crew!=1 else ''))
    if len(trucks)<required_trucks:missing.append(f'Assign {required_trucks} truck'+('s' if required_trucks!=1 else ''))
    drivers=0
    for a in crew:
        e=one(db,'SELECT * FROM employees WHERE id=?',(a['resource_id'],))
        if not e or not e['active']:
            missing.append('An assigned employee is inactive or missing');continue
        drivers+=int(bool(e['driver_verified']))
    if drivers<len(trucks):missing.append('Assign a verified driver for every truck')
    for a in trucks:
        truck=one(db,'SELECT active,name FROM vehicles WHERE id=?',(a['resource_id'],))
        if not truck or not truck['active']:missing.append((truck['name'] if truck else 'An assigned truck')+' is inactive; assign another truck')
    return missing


def check_preuse(db,jid):
    j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
    blockers=job_resource_blockers(db,j)
    if blockers:raise HTTPException(409,'; '.join(blockers)+'.')
    from .company_ops import fleet_reference_blockers
    for a in rows(db,"SELECT resource_id FROM assignments WHERE job_id=? AND resource_type='vehicle'",(jid,)):
        vid=a['resource_id'];v=one(db,'SELECT * FROM vehicles WHERE id=?',(vid,))
        if fleet_reference_blockers(db,vid):raise HTTPException(409,'Review the imported truck information and repairs before starting.')
        if not v or not v['active'] or one(db,"SELECT id FROM inspections WHERE vehicle_id=? AND severity='Major' AND cleared_at IS NULL",(vid,)):raise HTTPException(409,'A job truck is unavailable. Dispatch must assign a safe vehicle.')
        p=one(db,'SELECT * FROM fleet_profiles WHERE vehicle_id=?',(vid,))
        if p and p['inspection_due'] and p['inspection_due']<datetime.now(timezone.utc).date().isoformat():raise HTTPException(409,'Truck inspection has expired. Update the inspection evidence before starting.')
        if p and p['check_required']:
            check=one(db,'SELECT * FROM inspections WHERE vehicle_id=? ORDER BY checked_at DESC LIMIT 1',(vid,))
            if not check or datetime.fromisoformat(check['checked_at'])<datetime.now(timezone.utc)-timedelta(hours=p['inspection_interval_hours']):raise HTTPException(409,'Complete the required pre-use truck check before starting.')


def register_security(app,path,auth,public_url,passwords):
    @app.post('/api/users/{uid_}/reset-link')
    def reset_link(uid_:str,u=Depends(auth)):
        require_role(u,MANAGERS);raw=secrets.token_urlsafe(40)
        with transaction(path) as db:
            person=one(db,'SELECT * FROM users WHERE id=? AND active=1',(uid_,))
            if not person:raise HTTPException(404,'Active account not found.')
            protect_owner_account(db,u,person)
            db.execute('DELETE FROM account_resets WHERE user_id=?',(uid_,));insert(db,'account_resets',dict(token_hash=stamp_hash(raw),user_id=uid_,expires_at=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(timespec='seconds')))
            audit(db,u['id'],'One-use password reset prepared','users',uid_)
        return {'url':public_url+'/#reset='+raw,'sent':False,'expires_in':'30 minutes'}

    @app.post('/api/reset-password')
    def reset(data:dict,request:Request,background:BackgroundTasks):
        raw=text(data.get('token',''),'Token',180);password=data.get('password','')
        if not isinstance(password,str) or not 12<=len(password)<=200:raise HTTPException(422,'Use 12 to 200 characters.')
        if 'confirm_password' in data and password!=data['confirm_password']:raise HTTPException(422,'Passwords do not match.')
        with transaction(path) as db:allowed=recovery_rate_limit(db,request,purpose='reset')
        if not allowed:raise HTTPException(429,'Too many recovery attempts. Wait 15 minutes and try again.')
        with transaction(path) as db:
            r=one(db,'SELECT r.* FROM account_resets r JOIN users u ON u.id=r.user_id WHERE r.token_hash=? AND r.expires_at>? AND r.used_at IS NULL AND u.active=1',(stamp_hash(raw),now()))
            if not r:raise HTTPException(401,'Reset link is invalid or expired.')
            db.execute('UPDATE users SET password_hash=? WHERE id=?',(passwords.hash(password),r['user_id']));db.execute('DELETE FROM account_resets WHERE user_id=?',(r['user_id'],));db.execute('DELETE FROM sessions WHERE user_id=?',(r['user_id'],));audit(db,r['user_id'],'Password reset; sessions revoked','users',r['user_id'])
        background.add_task(notify_password_reset,path,r['user_id'],public_url)
        return {'ok':True,'mfa_still_required':True}

    @app.get('/api/security')
    def my_security(u=Depends(auth)):
        with closing(connect(path)) as db:f=one(db,'SELECT enabled FROM auth_factors WHERE user_id=?',(u['id'],))
        return {'mfa_enabled':bool(f and f['enabled']),'encryption_configured':bool(os.getenv('CROWNOPS_SECRET_KEY'))}

    @app.post('/api/security/mfa-setup')
    def setup(data:dict,u=Depends(auth)):
        try:passwords.verify(u['password_hash'],data.get('password',''))
        except Exception:raise HTTPException(401,'Confirm your current password.')
        secret=base64.b32encode(secrets.token_bytes(20)).decode()
        with transaction(path) as db:
            if one(db,'SELECT user_id FROM auth_factors WHERE user_id=? AND enabled=1',(u['id'],)):raise HTTPException(409,'MFA is already enabled.')
            db.execute('DELETE FROM auth_factors WHERE user_id=?',(u['id'],));insert(db,'auth_factors',dict(user_id=u['id'],secret_ciphertext=encrypt({'secret':secret}),created_at=now()))
        return {'secret':secret,'uri':'otpauth://totp/'+quote('CrownOps:'+u['email'])+'?secret='+secret+'&issuer=CrownOps','instruction':'Store this setup key in your authenticator, then confirm a six-digit code.'}

    @app.post('/api/security/mfa-enable')
    def enable(data:dict,u=Depends(auth)):
        code=text(data.get('code',''),'Code',6)
        with transaction(path) as db:
            f=one(db,'SELECT * FROM auth_factors WHERE user_id=?',(u['id'],))
            if not f:raise HTTPException(409,'Set up your authenticator first.')
            key=decrypt(f['secret_ciphertext'])['secret'];step=int(time.time()//30)
            valid=[n for n in (step-1,step,step+1) if hmac.compare_digest(totp(key,n),code)]
            if not valid:raise HTTPException(401,'Authenticator code does not match.')
            db.execute('UPDATE auth_factors SET enabled=1,last_step=? WHERE user_id=?',(valid[0],u['id']));audit(db,u['id'],'MFA enabled','users',u['id'])
        return {'ok':True}

    @app.post('/api/security/mfa-disable')
    def disable(data:dict,request:Request,u=Depends(auth)):
        with transaction(path) as db:
            allowed=recovery_rate_limit(db,request,u['email'],purpose='mfa-disable')
        if not allowed:raise HTTPException(429,'Too many attempts. Wait 15 minutes before trying again.')
        try:passwords.verify(u['password_hash'],data.get('password',''))
        except Exception:raise HTTPException(401,'Confirm your current password.')
        code=data.get('code','')
        with transaction(path) as db:
            factor=one(db,'SELECT * FROM auth_factors WHERE user_id=? AND enabled=1',(u['id'],))
            if factor:
                step=int(time.time()//30);key=decrypt(factor['secret_ciphertext'])['secret']
                valid=isinstance(code,str) and len(code)==6 and code.isdigit() and any(n>factor['last_step'] and hmac.compare_digest(totp(key,n),code) for n in (step-1,step,step+1))
                if not valid:raise HTTPException(401,'Enter a fresh six-digit code from your authenticator. Wait for the next code if you just used this one.')
            db.execute('DELETE FROM auth_factors WHERE user_id=?',(u['id'],))
            db.execute('DELETE FROM sessions WHERE user_id=? AND csrf<>?',(u['id'],u['csrf']))
            audit(db,u['id'],'Authenticator disabled by account holder; other sessions revoked','users',u['id'])
        return {'ok':True,'mfa_enabled':False}

    @app.post('/api/security/change-password')
    def change_password(data:dict,u=Depends(auth)):
        try:passwords.verify(u['password_hash'],data.get('current_password',''))
        except Exception:raise HTTPException(401,'Current password is incorrect.')
        new=text(data.get('new_password',''),'New password',200)
        if len(new)<12:raise HTTPException(422,'Use at least 12 characters.')
        with transaction(path) as db:
            db.execute('UPDATE users SET password_hash=? WHERE id=?',(passwords.hash(new),u['id']));db.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],));db.execute('DELETE FROM account_resets WHERE user_id=?',(u['id'],));audit(db,u['id'],'Password changed; sessions revoked','users',u['id'])
        return {'ok':True,'sign_in_again':True}
