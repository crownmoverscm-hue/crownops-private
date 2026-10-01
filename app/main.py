"""CrownOps private pilot API and responsive workspace.

No production account is connected by this code. The source is newly written,
not an export or modification of Carl's scheduling application.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from contextlib import closing
from email.message import EmailMessage
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import FastAPI, Request, HTTPException, Depends, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .store import initialize, connect, transaction, rows, one, insert, audit, uid, now, dumps
from .pricing import calculate, missing_fields, number, cents
from .importer import import_workbook
from . import ai

ROOT=Path(__file__).resolve().parent.parent
PASSWORDS=PasswordHasher(time_cost=2,memory_cost=65536,parallelism=2)
ROLES={'owner','sales','dispatch','crew','accounting'}
LEAD_FIELDS={'client_name','email','phone','move_date','pickup','dropoff','home_size','boxes','pickup_access',
    'dropoff_access','pickup_walk','dropoff_walk','inventory','heavy_items','photo_link','language','source',
    'lead_type','stage','assigned_to','priority','notes'}
OPERATORS={'owner','sales','dispatch'}
READERS=OPERATORS|{'accounting'}
QUOTERS={'owner','sales'}  # owner is displayed as Manager in this pilot
COOKIE='crownops_session'
from .pricing import calculate_with_overrides
from .operations import check_fleet_dispatch
from .platform_common import settings as platform_settings


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def require(user, allowed):
    if user['role'] not in allowed:
        raise HTTPException(403,'Your role does not allow this action.')


def safe_user(user):
    return {k:user.get(k) for k in ('id','name','email','role','employee_id')}


def bounded(value, name, limit=20000):
    if value is None:
        return ''
    if not isinstance(value,str) or len(value)>limit:
        raise HTTPException(422,f'{name} must be text under {limit} characters.')
    return value.strip()


def valid_email(s):
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',s):
        raise HTTPException(422,'Enter a valid email address.')
    return s.lower()


def local_to_utc(value):
    try:
        d=datetime.fromisoformat(value)
        if d.tzinfo is None:
            zone=ZoneInfo('America/Toronto')
            a=d.replace(tzinfo=zone,fold=0)
            b=d.replace(tzinfo=zone,fold=1)
            if a.utcoffset()!=b.utcoffset():
                raise ValueError('DST transition requires an explicit UTC offset.')
            d=a
        return d.astimezone(timezone.utc).isoformat(timespec='seconds')
    except (ValueError,TypeError):
        raise HTTPException(422,'Use a valid date/time, with an explicit offset during daylight-saving transitions.')


def create_user(path, email, name, password, role='owner', employee_id=None):
    email=valid_email(email.strip())
    if role not in ROLES or not 12<=len(password)<=200:
        raise ValueError('Choose a valid role and a password of 12 to 200 characters.')
    u=dict(id=uid('USER'),email=email,name=name,role=role,password_hash=PASSWORDS.hash(password),
           employee_id=employee_id,active=1,created_at=now())
    with transaction(path) as db:
        insert(db,'users',u)
    return safe_user(u)


def quote_document(q, lead):
    r=json.loads(q['result_json']);lang=str(lead.get('language','English')).lower()
    fr=lang.startswith('fr')
    money=lambda c: f'CA${c/100:,.2f}'
    name=lead.get('client_name','')
    if fr:
        subject='Votre estimation de demenagement | Crown Movers'
        body=(f'Bonjour {name},\n\nJonathan ici! Merci pour les details de votre demenagement.\n\n'
          f'Depart : {lead.get("pickup","")}\nDestination : {lead.get("dropoff","")}\n'
          f'Date souhaitee : {lead.get("move_date","")} (reservation a confirmer)\n\n'
          f'Equipe : {r["movers"]} demenageurs et {r["trucks"]} camion(s).\n'
          f'Tarif de l\'equipe : {money(r["hourly_rate_cents"])}/h.\n'
          f'Duree estimee : {r["service_low"]:g} a {r["service_high"]:g} heures, trajet entre les adresses inclus.\n'
          f'Transport depuis/vers notre base : {money(r["transport_cents"])}.\n'
          f'Carburant : {money(r["fuel_cents"])}.\n'
          f'Materiaux prevus : {money(r["materials_cents"])}. Autres frais convenus : {money(r["other_cents"])}.\n'
          f'Rabais : {money(r["discount_cents"])}.\n\n'
          f'Estimation avant taxes : {money(r["low_cents"])} a {money(r["high_cents"])}.\n'
          f'Le temps reel sera facture selon les modalites acceptees, avec un minimum de {r["minimum_hours"]:g} heures de service. '
          'Les frais distincts de transport, de carburant et les materiaux utilises restent applicables.\n'
          'La disponibilite, les acces et le traitement fiscal doivent etre confirmes avant la reservation.\n\n'
          'N\'hesitez pas a me faire signe si vous avez des questions.\nAu plaisir,\nJonathan\nCrown Movers\n514-606-4030')
    else:
        subject='Your moving estimate | Crown Movers'
        body=(f'Hi {name},\n\nJonathan here! I hope all is well. Thanks for the details of your move.\n\n'
          f'Pickup: {lead.get("pickup","")}\nDestination: {lead.get("dropoff","")}\n'
          f'Requested date: {lead.get("move_date","")} (booking to be confirmed)\n\n'
          f'Team: {r["movers"]} movers and {r["trucks"]} truck(s).\n'
          f'Crew rate: {money(r["hourly_rate_cents"])}/hour.\n'
          f'Estimated service: {r["service_low"]:g} to {r["service_high"]:g} hours, including travel between your locations.\n'
          f'Separate transport to/from our base: {money(r["transport_cents"])}.\n'
          f'Fuel: {money(r["fuel_cents"])}.\n'
          f'Materials allowance: {money(r["materials_cents"])}. Other agreed charges: {money(r["other_cents"])}.\n'
          f'Discount: {money(r["discount_cents"])}.\n\n'
          f'Estimated total before taxes: {money(r["low_cents"])} to {money(r["high_cents"])}.\n'
          f'Final service time is billed under the accepted terms, with a {r["minimum_hours"]:g}-hour service minimum. '
          'The separate transport and fuel charges and materials used still apply.\n'
          'Availability, access and tax treatment need confirmation before booking.\n\n'
          'Let me know if you have any questions.\nTalk soon,\nJonathan\nCrown Movers\n514-606-4030')
    return subject,body


def create_app(database_path=None):
    path=Path(database_path or os.environ.get('CROWNOPS_DB',str(ROOT/'instance'/'crownops.sqlite3')))
    initialize(path)
    import asyncio
    from contextlib import asynccontextmanager,suppress
    @asynccontextmanager
    async def lifespan(_app):
        async def review_worker():
            from .worker import tick
            while True:
                try:await asyncio.to_thread(tick,path)
                except Exception:
                    import logging
                    logging.getLogger('crownops').error('Draft workflow run failed; inspect the database and workflow configuration. No automatic payment or email was attempted.')
                await asyncio.sleep(60)
        task=asyncio.create_task(review_worker()) if os.getenv('CROWNOPS_RUN_WORKER')=='1' else None
        try:yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):await task
    app=FastAPI(title='CrownOps Unified Application',version='0.5.1',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.db_path=path
    secure=os.environ.get('CROWNOPS_SECURE_COOKIES','0')=='1'
    public_url=os.environ.get('CROWNOPS_PUBLIC_URL','http://127.0.0.1:8080').rstrip('/')

    @app.middleware('http')
    async def safety(request,call_next):
        # API mutations must originate from this app. Non-browser signed webhooks
        # are authenticated independently and cannot use cookie access.
        if request.method not in ('GET','HEAD','OPTIONS') and request.url.path.startswith('/api/'):
            origin=request.headers.get('origin')
            accepted={public_url,str(request.base_url).rstrip('/')}
            if origin and origin not in accepted:
                return JSONResponse({'detail':'Cross-origin requests are not permitted.'},403)
        length=request.headers.get('content-length','0')
        try:
            if int(length)>(110_000_000 if request.url.path.endswith('/uploads') else 13_000_000):
                return JSONResponse({'detail':'Request too large.'},413)
        except ValueError:
            return JSONResponse({'detail':'Invalid request length.'},400)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Permissions-Policy']='camera=(self), microphone=(self), geolocation=()'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith('/api/') or request.url.path in ('/','/portal','/request','/approval'):
            response.headers['Cache-Control']='no-store'
        if secure:
            response.headers['Strict-Transport-Security']='max-age=31536000'
        return response

    def auth(request:Request):
        raw=request.cookies.get(COOKIE,'')
        with closing(connect(path)) as db:
            row=one(db,'SELECT u.*,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1',(sha(raw),now()))
        if not raw or not row:
            raise HTTPException(401,'Please sign in.')
        if request.method not in ('GET','HEAD','OPTIONS'):
            if not hmac.compare_digest(request.headers.get('x-csrf-token',''),row['csrf']):
                raise HTTPException(403,'Session verification failed. Refresh and try again.')
        return row

    def get_lead(db,lid):
        lead=one(db,'SELECT * FROM leads WHERE id=?',(lid,))
        if not lead:
            raise HTTPException(404,'Client record not found.')
        lead['missing']=missing_fields(lead)
        return lead

    def visible_job(db,jid,u):
        j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
        if not j:
            raise HTTPException(404,'Job not found.')
        if u['role']=='crew' and not one(db,'SELECT job_id FROM assignments WHERE job_id=? AND resource_type=? AND resource_id=?',(jid,'employee',u.get('employee_id') or '')):
            raise HTTPException(403,'You can only view your assigned jobs.')
        return j

    def emit_quote(q):
        for k in ('inputs_json','result_json','lead_snapshot_json'):
            q[k.replace('_json','')]=json.loads(q.pop(k))
        return q

    @app.get('/api/health')
    def health():
        with closing(connect(path)) as db:
            setup=not bool(db.execute('SELECT 1 FROM users LIMIT 1').fetchone())
        return {'ok':True,'mode':os.getenv('CROWNOPS_ENV','test'),'setup_required':setup,'version':'0.5.5.1'}

    @app.post('/api/login')
    def login(data:dict,request:Request):
        email=bounded(data.get('email',''),'Email',254).lower()
        password=data.get('password','')
        if not isinstance(password,str) or len(password)>200:raise HTTPException(422,'Invalid password input.')
        ip=sha(request.client.host if request.client else 'local')
        since=(datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat(timespec='seconds')
        with transaction(path) as db:
            count=db.execute('SELECT count(*) FROM login_attempts WHERE ip_hash=? AND attempted_at>?',(ip,since)).fetchone()[0]
            if count>=12:
                raise HTTPException(429,'Too many attempts. Wait 15 minutes before trying again.')
            insert(db,'login_attempts',dict(ip_hash=ip,attempted_at=now()))
            u=one(db,'SELECT * FROM users WHERE email=? AND active=1',(email,))
        valid=False
        if u:
            try:valid=PASSWORDS.verify(u['password_hash'],password)
            except VerificationError:pass
        if valid:
            from .security import verify_user_mfa
            valid=verify_user_mfa(path,u['id'],data.get('mfa_code',data.get('otp','')))
        if not valid:
            # Same message for unknown users and incorrect passwords.
            raise HTTPException(401,'Email or password is incorrect.')
        token=secrets.token_urlsafe(40);csrf=secrets.token_urlsafe(32)
        expiry=(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat(timespec='seconds')
        with transaction(path) as db:
            db.execute('DELETE FROM sessions WHERE expires_at<?',(now(),))
            insert(db,'sessions',dict(token_hash=sha(token),user_id=u['id'],csrf=csrf,expires_at=expiry))
            audit(db,u['id'],'Signed in','users',u['id'])
        resp=JSONResponse({'user':safe_user(u),'csrf':csrf})
        resp.set_cookie(COOKIE,token,httponly=True,secure=secure,samesite='lax',max_age=43200,path='/')
        return resp

    @app.get('/api/me')
    def me(u=Depends(auth)):
        return {'user':safe_user(u),'csrf':u['csrf'],'mode':os.getenv('CROWNOPS_ENV','test')}

    @app.post('/api/logout')
    def logout(request:Request,u=Depends(auth)):
        with transaction(path) as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?',(sha(request.cookies.get(COOKIE,'')),))
        resp=JSONResponse({'ok':True});resp.delete_cookie(COOKIE,path='/');return resp

    @app.get('/api/users')
    def users(u=Depends(auth)):
        require(u,READERS)
        with closing(connect(path)) as db:
            return rows(db,'SELECT id,name,email,role,active,employee_id FROM users ORDER BY name')

    @app.post('/api/invitations')
    def invite(data:dict,u=Depends(auth)):
        require(u,{'owner'})
        role=data.get('role','crew')
        if role not in ROLES:
            raise HTTPException(422,'Unknown role.')
        token=secrets.token_urlsafe(32)
        with transaction(path) as db:
            emp=data.get('employee_id') or None
            if emp and not one(db,'SELECT id FROM employees WHERE id=?',(emp,)):
                raise HTTPException(422,'Employee record does not exist.')
            if role=='crew' and not emp:
                raise HTTPException(422,'Link a crew login to an employee record first.')
            email=valid_email(bounded(data.get('email',''),'Email',254))
            if one(db,'SELECT id FROM users WHERE email=?',(email,)):
                raise HTTPException(409,'An account already exists for this email.')
            insert(db,'invitations',dict(token_hash=sha(token),email=email,
                name=bounded(data.get('name',''),'Name',150),role=role,employee_id=emp,
                expires_at=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat(timespec='seconds'),created_by=u['id']))
            audit(db,u['id'],'Invitation created','users',email,{'role':role})
        return {'url':public_url+'/#invite='+token,'expires_in':'48 hours','sent':False}

    @app.post('/api/accept-invitation')
    def accept_invite(data:dict):
        token=bounded(data.get('token',''),'Invitation',150)
        password=data.get('password','')
        if not isinstance(password,str) or len(password)>200:raise HTTPException(422,'Invalid password input.')
        if len(password)<12:
            raise HTTPException(422,'Use at least 12 characters for the password.')
        with transaction(path) as db:
            inv=one(db,'SELECT * FROM invitations WHERE token_hash=? AND used_at IS NULL AND expires_at>?',(sha(token),now()))
            if not inv:
                raise HTTPException(400,'Invitation is invalid or expired.')
            if one(db,'SELECT id FROM users WHERE email=?',(inv['email'],)):
                raise HTTPException(409,'Account already exists. Ask an owner for help.')
            user=dict(id=uid('USER'),email=inv['email'],name=inv['name'],role=inv['role'],
                      password_hash=PASSWORDS.hash(password),employee_id=inv['employee_id'],active=1,created_at=now())
            insert(db,'users',user)
            db.execute('UPDATE invitations SET used_at=? WHERE token_hash=?',(now(),sha(token)))
            audit(db,user['id'],'Invitation accepted','users',user['id'])
        return {'ok':True,'email':inv['email']}

    @app.post('/api/users/{user_id}/disable')
    def disable_user(user_id:str,u=Depends(auth)):
        require(u,{'owner'})
        if user_id==u['id']:
            raise HTTPException(422,'You cannot disable your own account.')
        with transaction(path) as db:
            db.execute('UPDATE users SET active=0 WHERE id=?',(user_id,))
            db.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
            audit(db,u['id'],'Access revoked','users',user_id)
        return {'ok':True}

    @app.get('/api/stats')
    def stats(u=Depends(auth)):
        with closing(connect(path)) as db:
            if u['role']=='crew':
                c=db.execute('SELECT count(*) FROM assignments WHERE resource_type=? AND resource_id=?',('employee',u.get('employee_id') or '')).fetchone()[0]
                return {'assigned_jobs':c}
            count=lambda sql:db.execute(sql).fetchone()[0]
            return {'leads':count('SELECT count(*) FROM leads'),'imported':count('SELECT count(*) FROM leads WHERE archived_import=1'),
                'active_leads':count("SELECT count(*) FROM leads WHERE archived_import=0 AND stage NOT IN ('Lost','Closed')"),
                'unassigned':count("SELECT count(*) FROM leads WHERE assigned_to=''"),
                'historical_tasks':count("SELECT count(*) FROM tasks WHERE historical=1 AND status!='Done'"),
                'tasks_due':db.execute("SELECT count(*) FROM tasks WHERE historical=0 AND status='Open' AND due_date<=?",(now()[:10],)).fetchone()[0],
                'quotes':count('SELECT count(*) FROM quotes'),'jobs':count("SELECT count(*) FROM jobs WHERE status!='Cancelled'"),
                'unverified_payments':count('SELECT count(*) FROM payments WHERE verified=0'),
                'ai_tokens':count('SELECT coalesce(sum(input_tokens+output_tokens),0) FROM ai_runs'),
                'latest_import':one(db,'SELECT created_at,report_json FROM imports ORDER BY created_at DESC LIMIT 1')}

    @app.get('/api/leads')
    def leads(q:str='',stage:str='',kind:str='',scope:str='all',limit:int=100,offset:int=0,u=Depends(auth)):
        require(u,QUOTERS)
        limit=min(max(limit,1),200);offset=max(0,offset)
        where=['1=1'];params=[]
        if q:
            where.append('(client_name LIKE ? OR email LIKE ? OR phone LIKE ? OR pickup LIKE ? OR dropoff LIKE ?)')
            params+=['%'+q[:200]+'%']*5
        if stage:where.append('stage=?');params.append(stage)
        if kind:where.append('lead_type=?');params.append(kind)
        if scope=='active':where.append('archived_import=0')
        if scope=='imported':where.append('archived_import=1')
        if scope=='mine':where.append('assigned_to IN (?,?)');params.extend([u['id'],u['name']])
        clause=' AND '.join(where)
        with closing(connect(path)) as db:
            total=db.execute('SELECT count(*) FROM leads WHERE '+clause,params).fetchone()[0]
            result=rows(db,'SELECT * FROM leads WHERE '+clause+' ORDER BY created_at DESC LIMIT ? OFFSET ?',params+[limit,offset])
        for r in result:
            r.pop('raw_json',None);r['missing']=missing_fields(r)
        return {'items':result,'total':total}

    @app.get('/api/leads/{lid}')
    def lead_detail(lid:str,u=Depends(auth)):
        require(u,QUOTERS)
        with closing(connect(path)) as db:
            lead=get_lead(db,lid)
            lead['quotes']=[emit_quote(q) for q in rows(db,'SELECT * FROM quotes WHERE lead_id=? ORDER BY revision DESC',(lid,))]
            lead['messages']=rows(db,'SELECT * FROM messages WHERE lead_id=? ORDER BY created_at DESC',(lid,))
            lead['tasks']=rows(db,'SELECT * FROM tasks WHERE lead_id=? ORDER BY due_date DESC',(lid,))
        return lead

    def new_lead(db,data,actor,external_id=None,suppressed=1):
        t=now();record={k:bounded(data.get(k,''),k) for k in LEAD_FIELDS}
        if not record['client_name']:
            raise HTTPException(422,'A client name is required.')
        if record['email']:valid_email(record['email'])
        record.update(id=uid('LEAD'),external_id=external_id,stage=record['stage'] or 'New Lead',
            source=record['source'] or 'Manual',lead_type=record['lead_type'] or 'Form',language=record['language'] or 'English',
            priority=record['priority'] or 'Normal',created_at=t,updated_at=t,notifications_suppressed=suppressed)
        insert(db,'leads',record)
        insert(db,'tasks',dict(id=uid('TASK'),lead_id=record['id'],title='Review new inquiry',due_date=t[:10],
                              owner=record.get('assigned_to',''),created_at=t))
        audit(db,actor,'Lead created','leads',record['id'])
        return record

    @app.post('/api/leads')
    def add_lead(data:dict,u=Depends(auth)):
        require(u,QUOTERS)
        with transaction(path) as db:
            return new_lead(db,data,u['id'])

    @app.patch('/api/leads/{lid}')
    def update_lead(lid:str,data:dict,u=Depends(auth)):
        require(u,QUOTERS)
        fields={k:bounded(v,k) for k,v in data.items() if k in LEAD_FIELDS}
        if not fields:
            raise HTTPException(422,'No editable fields provided.')
        if 'email' in fields and fields['email']:valid_email(fields['email'])
        if 'client_name' in fields and not fields['client_name']:
            raise HTTPException(422,'Client name cannot be blank.')
        with transaction(path) as db:
            lead=get_lead(db,lid)
            if data.get('version')!=lead['version']:
                raise HTTPException(409,'This client was updated by someone else. Reload before saving.')
            if fields.get('assigned_to') and fields['assigned_to'] not in (u['id'],u['name']) and u['role']=='sales':
                raise HTTPException(403,'Only dispatch or an owner can reassign another team member.')
            assignments=','.join(k+'=?' for k in fields)
            db.execute('UPDATE leads SET '+assignments+',version=version+1,updated_at=? WHERE id=?',list(fields.values())+[now(),lid])
            audit(db,u['id'],'Lead updated','leads',lid,{'fields':list(fields)})
        return {'ok':True}

    @app.post('/api/leads/{lid}/claim')
    def claim(lid:str,u=Depends(auth)):
        require(u,QUOTERS)
        with transaction(path) as db:
            lead=get_lead(db,lid)
            if lead['assigned_to'] not in ('',u['id'],u['name']):
                raise HTTPException(409,'This inquiry has already been claimed.')
            db.execute('UPDATE leads SET assigned_to=?,version=version+1,updated_at=? WHERE id=?',(u['id'],now(),lid))
            audit(db,u['id'],'Lead claimed','leads',lid)
        return {'ok':True}

    @app.post('/api/leads/{lid}/activate')
    def activate(lid:str,u=Depends(auth)):
        require(u,QUOTERS)
        with transaction(path) as db:
            get_lead(db,lid)
            db.execute('UPDATE leads SET archived_import=0,updated_at=? WHERE id=?',(now(),lid))
            # It stays notifications_suppressed; activation NEVER sends history.
            audit(db,u['id'],'Imported inquiry activated for manual work','leads',lid)
        return {'ok':True,'messages_sent':0}

    @app.post('/api/leads/{lid}/messages')
    def message(lid:str,data:dict,u=Depends(auth)):
        require(u,QUOTERS)
        kind=data.get('kind','Note')
        if kind not in ('Note','Incoming pasted email','Draft reply','External communication recorded'):
            raise HTTPException(422,'Invalid message type.')
        body=bounded(data.get('body',''),'Message')
        if not body:raise HTTPException(422,'Message is empty.')
        with transaction(path) as db:
            lead=get_lead(db,lid)
            record=dict(id=uid('MSG'),lead_id=lid,kind=kind,subject=bounded(data.get('subject',''),'Subject',300),
                body=body,language=lead['language'],author=u['id'],created_at=now())
            insert(db,'messages',record)
            if kind=='Incoming pasted email':
                db.execute("UPDATE tasks SET status='Needs review' WHERE lead_id=? AND status='Open'",(lid,))
            audit(db,u['id'],'Message recorded; no send','messages',record['id'],{'kind':kind})
        return record

    @app.get('/api/tasks')
    def tasks(scope:str='active',q:str='',u=Depends(auth)):
        require(u,QUOTERS)
        with closing(connect(path)) as db:
            where='t.historical=?';params=[int(scope=='imported')]
            if q:where+=' AND (t.title LIKE ? OR l.client_name LIKE ?)';params+=['%'+q+'%']*2
            return rows(db,'SELECT t.*,l.client_name,l.phone,l.email FROM tasks t LEFT JOIN leads l ON l.id=t.lead_id WHERE '+where+' ORDER BY t.due_date DESC LIMIT 200',params)

    @app.post('/api/tasks')
    def add_task(data:dict,u=Depends(auth)):
        require(u,QUOTERS)
        with transaction(path) as db:
            lead=get_lead(db,data.get('lead_id',''))
            rec=dict(id=uid('TASK'),lead_id=lead['id'],title=bounded(data.get('title','Follow up'),'Task',300),
                due_date=bounded(data.get('due_date',''),'Due date',10),owner=u['id'],created_at=now())
            insert(db,'tasks',rec);audit(db,u['id'],'Task added','tasks',rec['id'])
        return rec

    @app.post('/api/tasks/{tid}/complete')
    def complete_task(tid:str,u=Depends(auth)):
        require(u,QUOTERS)
        with transaction(path) as db:
            db.execute("UPDATE tasks SET status='Done' WHERE id=?",(tid,));audit(db,u['id'],'Task completed','tasks',tid)
        return {'ok':True}

    @app.get('/api/pricing')
    def pricing(u=Depends(auth)):
        require(u,QUOTERS)
        with closing(connect(path)) as db:
            out=rows(db,'SELECT * FROM pricing_versions ORDER BY created_at DESC')
        for p in out:
            p['rates']=json.loads(p.pop('rates_json'));p['settings']=json.loads(p.pop('settings_json'))
        return out

    @app.post('/api/pricing/{pid}/approve')
    def approve_pricing(pid:str,data:dict,u=Depends(auth)):
        require(u,{'owner'})
        note=bounded(data.get('note',''),'Approval note',2000)
        if data.get('acknowledge') is not True or not note:
            raise HTTPException(422,'An approval note and explicit acknowledgement are required.')
        with transaction(path) as db:
            p=one(db,'SELECT * FROM pricing_versions WHERE id=?',(pid,))
            if not p:raise HTTPException(404,'Rate snapshot not found.')
            # Validate every date, including leap day, before publishing.
            from .pricing import seasonal_rate
            from datetime import date
            table=json.loads(p['rates_json'])
            d=date(2024,1,1)
            try:
                while d.year==2024:
                    for crew in range(2,7):seasonal_rate(table,d.isoformat(),crew)
                    d+=timedelta(days=1)
            except ValueError as e:raise HTTPException(422,str(e))
            db.execute('UPDATE pricing_versions SET approved=1,approved_by=?,approved_at=? WHERE id=?',(u['id'],now(),pid))
            audit(db,u['id'],'Rate snapshot approved','pricing_versions',pid,{'note':note,
                  'minimum_and_fuel':'Explicit selections still required on every quote.'})
        return {'ok':True}

    @app.post('/api/quotes/calculate')
    def calc_quote(data:dict,u=Depends(auth)):
        require(u,QUOTERS)
        with closing(connect(path)) as db:
            p=one(db,'SELECT * FROM pricing_versions WHERE id=?',(data.get('pricing_id',''),))
        if not p:raise HTTPException(422,'Select a pricing snapshot.')
        try:
            with closing(connect(path)) as db:return calculate_with_overrides(data,p,db)
        except (ValueError,TypeError,KeyError) as e:raise HTTPException(422,str(e))

    @app.get('/api/quotes')
    def quotes(u=Depends(auth)):
        require(u,QUOTERS)
        with closing(connect(path)) as db:
            return [emit_quote(q) for q in rows(db,'SELECT q.*,l.client_name FROM quotes q JOIN leads l ON l.id=q.lead_id ORDER BY q.created_at DESC LIMIT 300')]

    @app.post('/api/quotes')
    def save_quote(data:dict,u=Depends(auth)):
        require(u,QUOTERS)
        with transaction(path) as db:
            lead=get_lead(db,data.get('lead_id',''))
            if data.get('lead_version')!=lead['version']:
                raise HTTPException(409,'Client details changed. Reload before creating a quote.')
            p=one(db,'SELECT * FROM pricing_versions WHERE id=?',(data.get('pricing_id',''),))
            if not p:raise HTTPException(422,'Select a pricing snapshot.')
            try:
                result=calculate_with_overrides(data,p,db)
                number(data.get('deposit_required',300),'Required deposit',0,100000)
            except (ValueError,TypeError,KeyError) as e:raise HTTPException(422,str(e))
            rev=db.execute('SELECT coalesce(max(revision),0)+1 FROM quotes WHERE lead_id=?',(lead['id'],)).fetchone()[0]
            snapshot={k:lead.get(k,'') for k in LEAD_FIELDS}
            rec=dict(id=uid('QUOTE'),lead_id=lead['id'],revision=rev,status='Draft',inputs_json=dumps(data),
                result_json=dumps(result),pricing_id=p['id'],lead_snapshot_json=dumps(snapshot),lead_version=lead['version'],
                created_by=u['id'],created_at=now())
            insert(db,'quotes',rec)
            audit(db,u['id'],'Quote revision saved','quotes',rec['id'],{'revision':rev,'low_cents':result['low_cents'],'high_cents':result['high_cents']})
        return emit_quote(rec)

    @app.post('/api/jobs/{jid}/quote-revision')
    def revise_job_quote(jid:str,data:dict,u=Depends(auth)):
        """Create a new draft estimate revision from the job without overwriting history.

        The quick editor intentionally covers only the everyday fields requested by Crown:
        estimated service range and separate base transportation hours. Full scope/rate
        changes remain available in the quote studio. Accepted estimates are preserved and
        the new revision must be reviewed and accepted again.
        """
        require(u,QUOTERS)
        with transaction(path) as db:
            j=visible_job(db,jid,u)
            if j['status']!='Tentative':
                raise HTTPException(409,'Create quote revisions from a tentative job. A confirmed/in-progress job needs a reviewed billing change instead.')
            if not j['quote_id']:raise HTTPException(422,'This job does not have a quote to revise.')
            if one(db,'SELECT id FROM job_invoices WHERE job_id=?',(jid,)):
                raise HTTPException(409,'This job already has invoice terms. Review billing before changing the linked quote.')
            q=one(db,'SELECT * FROM quotes WHERE id=? AND lead_id=?',(j['quote_id'],j['lead_id']))
            if not q:raise HTTPException(404,'Linked quote not found.')
            lead=get_lead(db,j['lead_id'])
            inputs=json.loads(q['inputs_json'])
            inputs['lead_id']=lead['id'];inputs['lead_version']=lead['version']
            inputs['hours_low']=float(number(data.get('hours_low'),'Estimate from',.25,80))
            inputs['hours_high']=float(number(data.get('hours_high'),'Estimate to',.25,100))
            if inputs['hours_high']<inputs['hours_low']:raise HTTPException(422,'Estimate to cannot be lower than estimate from.')
            inputs['transport_hours']=float(number(data.get('transport_hours'),'Transportation fee hours',0,100))
            # In the job quick-editor the entered range is already the planned client-facing
            # range, so do not silently add another planning percentage.
            inputs['allowance_percent']=0
            p=one(db,'SELECT * FROM pricing_versions WHERE id=?',(q['pricing_id'],))
            if not p:raise HTTPException(422,'The quote pricing snapshot is unavailable.')
            try:result=calculate_with_overrides(inputs,p,db)
            except (ValueError,TypeError,KeyError) as e:raise HTTPException(422,str(e))
            rev=db.execute('SELECT coalesce(max(revision),0)+1 FROM quotes WHERE lead_id=?',(lead['id'],)).fetchone()[0]
            snapshot={k:lead.get(k,'') for k in LEAD_FIELDS}
            rec=dict(id=uid('QUOTE'),lead_id=lead['id'],revision=rev,status='Draft',inputs_json=dumps(inputs),
                result_json=dumps(result),pricing_id=p['id'],lead_snapshot_json=dumps(snapshot),lead_version=lead['version'],
                created_by=u['id'],created_at=now())
            insert(db,'quotes',rec)
            db.execute('UPDATE jobs SET quote_id=?,version=version+1 WHERE id=?',(rec['id'],jid))
            audit(db,u['id'],'Quick quote revision created from job','quotes',rec['id'],{'job_id':jid,'replaces_linked_quote':q['id'],'previous_status':q['status'],'revision':rev})
        return emit_quote(rec)

    @app.post('/api/quotes/{qid}/approve')
    def approve_quote(qid:str,data:dict,u=Depends(auth)):
        require(u,{'owner'})
        with transaction(path) as db:
            q=one(db,'SELECT * FROM quotes WHERE id=?',(qid,))
            if not q:raise HTTPException(404,'Quote not found.')
            if q['status']!='Draft':raise HTTPException(409,'Only a draft can be approved.')
            lead=get_lead(db,q['lead_id'])
            p=one(db,'SELECT * FROM pricing_versions WHERE id=?',(q['pricing_id'],))
            if not p['approved']:raise HTTPException(422,'Owner approval of this pricing snapshot is required first.')
            if q['lead_version']!=lead['version']:
                raise HTTPException(409,'Client details changed after this quote. Create a new revision.')
            if missing_fields(lead):
                raise HTTPException(422,'Complete the client record first: '+', '.join(missing_fields(lead)))
            if data.get('reviewed') is not True or not bounded(data.get('note',''),'Review note',3000):
                raise HTTPException(422,'Confirm inventory, route, access, capacity and timing review, and add a review note.')
            db.execute("UPDATE quotes SET status='Approved',approved_by=?,approved_at=? WHERE id=?",(u['id'],now(),qid))
            insert(db,'integration_events',dict(id=uid('EVENT'),event_key='quote-approved:'+qid,event_type='quickbooks.draft.requested',
                object_id=qid,payload_json=dumps({'quote_id':qid,'lead_id':lead['id'],'result':json.loads(q['result_json']),
                'instructions':'Create draft only. No external write or send has occurred.'}),created_at=now()))
            audit(db,u['id'],'Quote approved','quotes',qid,{'note':data['note']})
        return {'ok':True,'external_estimate_created':False,'messages_sent':0}

    @app.post('/api/quotes/{qid}/accept')
    def record_acceptance(qid:str,data:dict,u=Depends(auth)):
        require(u,QUOTERS)
        ref=bounded(data.get('reference',''),'Acceptance evidence',2000)
        if not ref:raise HTTPException(422,'Provide an email, document or external acceptance reference.')
        with transaction(path) as db:
            q=one(db,'SELECT * FROM quotes WHERE id=?',(qid,))
            if not q or q['status']!='Approved':raise HTTPException(409,'Only an approved quote can be accepted.')
            lead=get_lead(db,q['lead_id'])
            if lead['version']!=q['lead_version']:raise HTTPException(409,'Quote is stale. Review a new revision before recording acceptance.')
            db.execute("UPDATE quotes SET status='Accepted',acceptance_ref=?,accepted_at=? WHERE id=?",(ref,now(),qid))
            audit(db,u['id'],'External client acceptance recorded','quotes',qid,{'reference':ref})
        return {'ok':True,'booking_created':False}

    @app.get('/api/quotes/{qid}/email')
    def email_export(qid:str,u=Depends(auth)):
        require(u,QUOTERS)
        with closing(connect(path)) as db:
            q=one(db,'SELECT * FROM quotes WHERE id=?',(qid,))
        if not q:raise HTTPException(404,'Quote not found.')
        if q['status'] not in ('Approved','Accepted'):raise HTTPException(422,'Approve the quote before exporting a client email.')
        lead=json.loads(q['lead_snapshot_json']);subject,body=quote_document(q,lead)
        msg=EmailMessage();msg['To']=lead['email'];msg['Subject']=subject;msg['X-Unsent']='1';msg.set_content(body)
        return Response(msg.as_bytes(),media_type='message/rfc822',headers={'Content-Disposition':f'attachment; filename="{qid}.eml"'})

    @app.get('/api/quotes/{qid}/draft-text')
    def draft_text(qid:str,u=Depends(auth)):
        require(u,QUOTERS)
        with closing(connect(path)) as db:q=one(db,'SELECT * FROM quotes WHERE id=?',(qid,))
        if not q:raise HTTPException(404,'Quote not found.')
        subject,body=quote_document(q,json.loads(q['lead_snapshot_json']))
        if q['status']=='Draft':body='INTERNAL DRAFT - NOT APPROVED FOR CLIENT DELIVERY\n\n'+body
        return {'subject':subject,'body':body,'sent':False}

    def decorate_job(db,j,u):
        j['stops']=json.loads(j.pop('stops_json'))
        a=rows(db,'SELECT * FROM assignments WHERE job_id=?',(j['id'],))
        j['crew_ids']=[x['resource_id'] for x in a if x['resource_type']=='employee']
        j['truck_ids']=[x['resource_id'] for x in a if x['resource_type']=='vehicle']
        j['crew_names']=[r['name'] for r in rows(db,'SELECT e.name FROM employees e JOIN assignments a ON a.resource_id=e.id WHERE a.resource_type=? AND a.job_id=?',('employee',j['id']))]
        j['truck_names']=[r['name'] for r in rows(db,'SELECT v.name FROM vehicles v JOIN assignments a ON a.resource_id=v.id WHERE a.resource_type=? AND a.job_id=?',('vehicle',j['id']))]
        lead=get_lead(db,j['lead_id'])
        j['client']={k:lead.get(k,'') for k in ['client_name','phone','email','pickup','dropoff','pickup_access','dropoff_access','pickup_walk','dropoff_walk','inventory','heavy_items','home_size','boxes','notes','photo_link']}
        j['lead_id']=lead['id']
        if j.get('quote_id'):
            quoted=one(db,"SELECT * FROM quotes WHERE id=?",(j['quote_id'],))
            if quoted and (quoted['status']=='Accepted' or u['role'] in QUOTERS or u['role'] in {'dispatch','accounting'}):
                qr=json.loads(quoted['result_json']);qi=json.loads(quoted['inputs_json'])
                j['quote_summary']={
                    'id':quoted['id'],'revision':quoted['revision'],'status':quoted['status'],
                    'hourly_rate_cents':qr.get('hourly_rate_cents'),'minimum_hours':qr.get('minimum_hours'),
                    'service_low':qr.get('service_low'),'service_high':qr.get('service_high'),
                    'transport_hours':qr.get('transport_hours'),'transport_cents':qr.get('transport_cents'),
                    'fuel_cents':qr.get('fuel_cents'),'movers':qr.get('movers'),'trucks':qr.get('trucks'),
                    'low_cents':qr.get('low_cents'),'high_cents':qr.get('high_cents'),
                    'deposit_required':qi.get('deposit_required',300)
                }
                if quoted['status']=='Accepted':
                    j['accepted_charges']={k:qr.get(k) for k in ('hourly_rate_cents','minimum_hours','transport_cents','fuel_cents','movers','trucks')}
        if u['role'] not in QUOTERS:
            j.pop('quote_id',None);j.pop('exception_reason',None)
        return j

    @app.get('/api/jobs')
    def jobs(u=Depends(auth)):
        with closing(connect(path)) as db:
            result=rows(db,'SELECT * FROM jobs ORDER BY start_at')
            return [decorate_job(db,j,u) for j in result]

    def check_job_resources(db,data,ignore_id=''):
        start,end=local_to_utc(data.get('start_at','')),local_to_utc(data.get('end_at',''))
        if end<=start:raise HTTPException(422,'End time must follow the start time.')
        if datetime.fromisoformat(end)-datetime.fromisoformat(start)>timedelta(days=14):
            raise HTTPException(422,'Split assignments exceeding 14 days into separate phases.')
        crew=data.get('crew_ids',[]);trucks=data.get('truck_ids',[])
        if not isinstance(crew,list) or not isinstance(trucks,list) or len(crew)>30 or len(trucks)>15:
            raise HTTPException(422,'Invalid crew or truck selections.')
        if not all(isinstance(x,str) for x in crew+trucks):raise HTTPException(422,'Invalid resource IDs.')
        if len(set(crew))!=len(crew) or len(set(trucks))!=len(trucks):raise HTTPException(422,'A resource was selected twice.')
        for typ,ids,table in [('employee',crew,'employees'),('vehicle',trucks,'vehicles')]:
            for rid in ids:
                resource=one(db,f'SELECT * FROM {table} WHERE id=? AND active=1',(rid,))
                if not resource:raise HTTPException(422,'Resource is inactive or missing: '+rid)
                conflict=one(db,"SELECT j.id,j.title FROM assignments a JOIN jobs j ON j.id=a.job_id WHERE a.resource_type=? AND a.resource_id=? AND j.status!='Cancelled' AND j.id!=? AND j.start_at<? AND j.end_at>?",(typ,rid,ignore_id,end,start))
                if conflict:raise HTTPException(409,resource['name']+' overlaps with '+conflict['title']+'.')
                if typ=='employee':
                    start_day=datetime.fromisoformat(start).astimezone(ZoneInfo('America/Toronto')).date().isoformat()
                    end_day=datetime.fromisoformat(end).astimezone(ZoneInfo('America/Toronto')).date().isoformat()
                    off=one(db,"SELECT id,start_date,end_date FROM time_off_requests WHERE employee_id=? AND status='Approved' AND start_date<=? AND end_date>=? LIMIT 1",(rid,end_day,start_day))
                    if off:raise HTTPException(409,resource['name']+' is approved OFF '+off['start_date']+' to '+off['end_date']+' and cannot be assigned.')
        check_fleet_dispatch(db,trucks,crew,start)
        return start,end,crew,trucks

    def check_confirmation(db,lead,q,crew,trucks,start,end,exception,u,reviewed):
        if reviewed is not True:
            raise HTTPException(422,'Confirm timing, access, truck capacity and driver coverage.')
        if not crew:raise HTTPException(422,'Assign a crew before confirming.')
        owner_exception=u['role']=='owner' and len(exception)>=12
        if not q or q['status']!='Accepted':
            if not owner_exception:
                raise HTTPException(422,'Accepted quote required, or an owner-documented manual booking exception.')
        if q:
            if q['lead_version']!=lead['version']:
                raise HTTPException(409,'Client details changed after the quote. Review a new revision before booking.')
            result=json.loads(q['result_json']);inputs=json.loads(q['inputs_json'])
            if len(crew)<result['movers'] or len(trucks)<result['trucks']:
                raise HTTPException(422,'Assigned resources are below the accepted quote. Revise the quote or assignments.')
            required=cents(inputs.get('deposit_required',300))
            paid=db.execute("SELECT coalesce(sum(amount_cents),0) FROM payments WHERE lead_id=? AND verified=1 AND purpose='Deposit'",(lead['id'],)).fetchone()[0]
            if paid<required and not owner_exception:
                raise HTTPException(422,'Verify the required deposit, or have an owner document an exception.')
            hours=(datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds()/3600
            if hours+1e-8<result['service_high']+result['transport_hours']:
                raise HTTPException(422,'Reserve the full high-end service duration plus base travel. Shorten only by revising the estimate.')
        driver_count=sum(one(db,'SELECT driver_verified FROM employees WHERE id=?',(rid,))['driver_verified'] for rid in crew)
        if driver_count<len(trucks):
            raise HTTPException(422,'Assign a verified driver for every truck. Driver qualifications are not inferred from the workbook.')

    @app.post('/api/jobs')
    def add_job(data:dict,u=Depends(auth)):
        require(u,{'owner','sales','dispatch'})
        with transaction(path) as db:
            lead=get_lead(db,data.get('lead_id',''))
            start,end,crew,trucks=check_job_resources(db,data)
            status=data.get('status','Tentative')
            if status not in ('Tentative','Confirmed'):raise HTTPException(422,'New jobs begin as Tentative or Confirmed.')
            qid=data.get('quote_id') or None
            q=one(db,'SELECT * FROM quotes WHERE id=? AND lead_id=?',(qid,lead['id'])) if qid else None
            if qid and not q:raise HTTPException(422,'Quote does not belong to this client.')
            exception=bounded(data.get('exception_reason',''),'Exception reason',3000)
            if status=='Confirmed':
                check_confirmation(db,lead,q,crew,trucks,start,end,exception,u,data.get('capacity_reviewed'))
            stops=data.get('stops',[])
            if not isinstance(stops,list) or len(stops)>30:raise HTTPException(422,'Invalid stop list.')
            stops=[bounded(x,'Stop',1000) for x in stops]
            rec=dict(id=uid('JOB'),lead_id=lead['id'],quote_id=qid,
                title=bounded(data.get('title') or lead['client_name'],'Job title',200),
                phase=bounded(data.get('phase','Moving'),'Phase',60),start_at=start,end_at=end,status=status,
                stops_json=dumps(stops),notes=bounded(data.get('notes',''),'Crew notes'),
                capacity_reviewed=int(data.get('capacity_reviewed') is True),exception_reason=exception,
                created_by=u['id'],created_at=now())
            try:insert(db,'jobs',rec)
            except sqlite3.IntegrityError:raise HTTPException(409,'This quote already has that job phase. Review the existing job.')
            for typ,ids in [('employee',crew),('vehicle',trucks)]:
                for rid in ids:insert(db,'assignments',dict(job_id=rec['id'],resource_type=typ,resource_id=rid))
            audit(db,u['id'],'Pilot job created','jobs',rec['id'],{'status':status,'crew':crew,'trucks':trucks})
        return rec

    @app.post('/api/jobs/{jid}/confirm')
    def confirm_job(jid:str,data:dict,u=Depends(auth)):
        require(u,{'owner','sales','dispatch'})
        with transaction(path) as db:
            j=visible_job(db,jid,u)
            if j['status']!='Tentative':raise HTTPException(409,'Only a tentative job can be confirmed.')
            if data.get('version')!=j['version']:raise HTTPException(409,'Job changed. Refresh before confirming.')
            # Normal confirmation is intentionally one click. CrownOps records the actor and
            # timestamp automatically; notes are reserved for actual exceptions.
            note=bounded(data.get('note',''),'Dispatch review',3000)
            a=rows(db,'SELECT * FROM assignments WHERE job_id=?',(jid,))
            d={'start_at':j['start_at'],'end_at':j['end_at'],
               'crew_ids':[x['resource_id'] for x in a if x['resource_type']=='employee'],
               'truck_ids':[x['resource_id'] for x in a if x['resource_type']=='vehicle']}
            start,end,crew,trucks=check_job_resources(db,d,jid)
            lead=get_lead(db,j['lead_id'])
            q=one(db,'SELECT * FROM quotes WHERE id=?',(j['quote_id'],)) if j['quote_id'] else None
            check_confirmation(db,lead,q,crew,trucks,start,end,j['exception_reason'],u,True)
            db.execute("UPDATE jobs SET status='Confirmed',capacity_reviewed=1,version=version+1 WHERE id=?",(jid,))
            audit(db,u['id'],'Pilot job confirmed','jobs',jid,{'note':note,'live_schedule_changed':False,'confirmation_mode':'one-click'})
        return {'ok':True,'live_schedule_changed':False}

    @app.patch('/api/jobs/{jid}')
    def change_job(jid:str,data:dict,u=Depends(auth)):
        with transaction(path) as db:
            j=visible_job(db,jid,u)
            if data.get('version')!=j['version']:raise HTTPException(409,'Job changed. Refresh before saving.')
            status=data.get('status',j['status'])
            allowed={'Tentative':{'Cancelled'},'Confirmed':{'In progress','Cancelled'},'In progress':{'Completed','Cancelled'},'Completed':set(),'Cancelled':set()}
            if status!=j['status'] and status not in allowed[j['status']]:
                raise HTTPException(422,'Invalid status transition. Create a reviewed booking rather than bypass approval.')
            if status=='In progress' and j['status']=='Confirmed':
                from .security import check_preuse
                check_preuse(db,jid)
            if u['role']=='crew' and status not in ('In progress','Completed'):
                raise HTTPException(403,'Crew can only start or complete assigned confirmed jobs.')
            if u['role'] not in ('owner','sales','dispatch','crew'):raise HTTPException(403,'Dispatch access required.')
            db.execute("UPDATE outbound_messages SET status='Cancelled',error='Job updated; review reminder timing' WHERE job_id=? AND category='Preparation' AND status IN ('Draft','Approved')",(jid,))
            # Rescheduling is dispatch-only; recheck both workers and vehicles atomically.
            start,end=j['start_at'],j['end_at']
            if 'start_at' in data or 'end_at' in data:
                require(u,{'owner','sales','dispatch'})
                if j['status'] in ('Completed','Cancelled'):raise HTTPException(422,'Closed jobs cannot be rescheduled.')
                assignments=rows(db,'SELECT * FROM assignments WHERE job_id=?',(jid,))
                d={'start_at':data.get('start_at',start),'end_at':data.get('end_at',end),
                   'crew_ids':[x['resource_id'] for x in assignments if x['resource_type']=='employee'],
                   'truck_ids':[x['resource_id'] for x in assignments if x['resource_type']=='vehicle']}
                start,end,crew,trucks=check_job_resources(db,d,jid)
                if j['status']=='Confirmed':
                    lead=get_lead(db,j['lead_id'])
                    q=one(db,'SELECT * FROM quotes WHERE id=?',(j['quote_id'],)) if j['quote_id'] else None
                    check_confirmation(db,lead,q,crew,trucks,start,end,j['exception_reason'],u,True)
            db.execute('UPDATE jobs SET status=?,start_at=?,end_at=?,version=version+1 WHERE id=?',(status,start,end,jid))
            audit(db,u['id'],'Job updated','jobs',jid,{'status':status,'start_at':start,'end_at':end})
        return {'ok':True}

    @app.put('/api/jobs/{jid}/plan')
    def edit_plan(jid:str,data:dict,u=Depends(auth)):
        require(u,{'owner','sales','dispatch'})
        with transaction(path) as db:
            j=visible_job(db,jid,u)
            if j['status'] in ('Completed','Cancelled'):raise HTTPException(409,'Closed jobs retain their plan. Use a return-visit job if needed.')
            if data.get('version')!=j['version']:raise HTTPException(409,'Job changed. Refresh before editing.')
            note=bounded(data.get('reason',''),'Planning change reason',3000)
            if len(note)<8:raise HTTPException(422,'Explain the schedule or assignment change.')
            start,end,crew,trucks=check_job_resources(db,data,jid)
            qid=data.get('quote_id') or None
            q=one(db,'SELECT * FROM quotes WHERE id=? AND lead_id=?',(qid,j['lead_id'])) if qid else None
            if qid and not q:raise HTTPException(422,'Estimate must belong to this move project.')
            if qid!=j['quote_id'] and one(db,'SELECT id FROM job_invoices WHERE job_id=?',(jid,)):raise HTTPException(409,'Invoice terms already exist. Have management reconcile billing before changing the accepted estimate link.')
            exception=bounded(data.get('exception_reason',j['exception_reason']),'Manager exception',3000)
            if exception!=j['exception_reason'] and u['role']!='owner':raise HTTPException(403,'Only management can change a booking exception.')
            if j['status'] in ('Confirmed','In progress'):
                check_confirmation(db,get_lead(db,j['lead_id']),q,crew,trucks,start,end,exception,u,data.get('capacity_reviewed'))
            stops=data.get('stops',[])
            if not isinstance(stops,list) or len(stops)>30:raise HTTPException(422,'Invalid stop list.')
            stops=[bounded(v,'Stop address',1000) for v in stops]
            notes=bounded(data.get('notes',j['notes']),'Instructions',12000)
            title=bounded(data.get('title',j['title']),'Title',300)
            db.execute('UPDATE jobs SET title=?,quote_id=?,start_at=?,end_at=?,notes=?,exception_reason=?,version=version+1 WHERE id=?',(title,qid,start,end,notes,exception,jid))
            db.execute('DELETE FROM assignments WHERE job_id=?',(jid,))
            for typ,ids in [('employee',crew),('vehicle',trucks)]:
                for rid in ids:insert(db,'assignments',dict(job_id=jid,resource_type=typ,resource_id=rid))
            db.execute('UPDATE jobs SET stops_json=? WHERE id=?',(dumps(stops),jid))
            db.execute("UPDATE outbound_messages SET status='Cancelled',error='Plan changed; review reminders' WHERE job_id=? AND category='Preparation' AND status IN ('Draft','Approved')",(jid,))
            audit(db,u['id'],'Job plan revised','jobs',jid,{'reason':note,'start_at':start,'end_at':end,'crew':crew,'trucks':trucks})
        return {'ok':True,'status_unchanged':j['status']}

    @app.get('/api/employees')
    def employees(u=Depends(auth)):
        with closing(connect(path)) as db:
            result=rows(db,'SELECT * FROM employees ORDER BY name')
        for e in result:
            e.pop('raw_json',None)
            if u['role'] not in ('owner','accounting'):e.pop('hourly_cents',None)
        if u['role']=='crew':result=[e for e in result if e['id']==u.get('employee_id')]
        return result

    @app.post('/api/employees/{eid}/verify-driver')
    def verify_driver(eid:str,data:dict,u=Depends(auth)):
        require(u,{'owner'})
        note=bounded(data.get('note',''),'Qualification review',2000)
        if not note:raise HTTPException(422,'Document the qualification and authorization check.')
        with transaction(path) as db:
            db.execute('UPDATE employees SET driver_verified=? WHERE id=?',(int(data.get('verified') is True),eid))
            audit(db,u['id'],'Driver authorization reviewed','employees',eid,{'note':note,'verified':data.get('verified') is True})
        return {'ok':True}

    @app.get('/api/vehicles')
    def vehicles(u=Depends(auth)):
        require(u,READERS)
        with closing(connect(path)) as db:return rows(db,'SELECT * FROM vehicles ORDER BY name')

    @app.post('/api/vehicles')
    def add_vehicle(data:dict,u=Depends(auth)):
        require(u,{'owner','sales','dispatch'})
        name=bounded(data.get('name',''),'Truck name',150)
        if not name:raise HTTPException(422,'Name is required.')
        color=str(data.get('color_hex') or '#3B82F6').upper()
        if not re.fullmatch(r'#[0-9A-F]{6}',color):raise HTTPException(422,'Choose a valid truck color.')
        with transaction(path) as db:
            order=db.execute('SELECT coalesce(max(display_order),0)+1 FROM vehicles').fetchone()[0]
            rec=dict(id=uid('TRUCK'),name=name,active=int(data.get('active') is True),notes=bounded(data.get('notes',''),'Notes'),color_hex=color,display_order=order)
            insert(db,'vehicles',rec);audit(db,u['id'],'Vehicle added','vehicles',rec['id'],{'color_hex':color})
        return rec

    @app.put('/api/vehicles/{vid}')
    def update_vehicle(vid:str,data:dict,u=Depends(auth)):
        require(u,{'owner','sales','dispatch'})
        name=bounded(data.get('name',''),'Truck name',150)
        color=str(data.get('color_hex') or '').upper()
        if not name:raise HTTPException(422,'Truck name is required.')
        if not re.fullmatch(r'#[0-9A-F]{6}',color):raise HTTPException(422,'Choose a valid truck color.')
        with transaction(path) as db:
            v=one(db,'SELECT * FROM vehicles WHERE id=?',(vid,))
            if not v:raise HTTPException(404,'Truck not found.')
            db.execute('UPDATE vehicles SET name=?,color_hex=?,version=version+1 WHERE id=?',(name,color,vid))
            audit(db,u['id'],'Truck name/color updated','vehicles',vid,{'name':name,'color_hex':color})
        return {'ok':True}

    @app.post('/api/vehicles/{vid}/activate')
    def activate_vehicle(vid:str,data:dict,u=Depends(auth)):
        require(u,{'owner','sales','dispatch'})
        note=bounded(data.get('note',''),'Fleet review',2000)
        if not note:raise HTTPException(422,'Add a note confirming the vehicle is valid and available.')
        with transaction(path) as db:
            if one(db,"SELECT id FROM inspections WHERE vehicle_id=? AND severity='Major' AND cleared_at IS NULL",(vid,)):raise HTTPException(409,'Repair and clear the major defect before activating this truck.')
            db.execute('UPDATE vehicles SET active=1,notes=?,version=version+1 WHERE id=?',(note,vid))
            audit(db,u['id'],'Vehicle activated for pilot','vehicles',vid,{'note':note})
        return {'ok':True}

    @app.get('/api/payments')
    def payments(u=Depends(auth)):
        require(u,READERS)
        with closing(connect(path)) as db:
            return rows(db,'SELECT p.*,l.client_name FROM payments p JOIN leads l ON l.id=p.lead_id ORDER BY p.created_at DESC')

    @app.post('/api/payments')
    def add_payment(data:dict,u=Depends(auth)):
        require(u,{'owner','sales','accounting','dispatch'})
        verified=data.get('verified') is True
        if verified:require(u,{'owner','accounting'})
        try:amount=cents(number(data.get('amount'),'Payment',.01,1000000))
        except ValueError as e:raise HTTPException(422,str(e))
        reference=bounded(data.get('reference',''),'Payment reference',1000)
        if not reference:raise HTTPException(422,'A payment reference or receipt number is required.')
        if data.get('method') not in ('Interac','Credit card','Cash','Cheque','Other'):
            raise HTTPException(422,'Choose a payment method.')
        purpose=data.get('purpose','Deposit')
        if purpose not in ('Deposit','Final payment'):raise HTTPException(422,'Choose Deposit or Final payment.')
        with transaction(path) as db:
            lead=get_lead(db,data.get('lead_id',''))
            if purpose=='Deposit' and one(db,"SELECT id FROM deposit_links WHERE lead_id=? AND status IN ('Creating','Active','Unknown','Cancelling')",(lead['id'],)):raise HTTPException(409,'Reconcile or close the active card-deposit checkout before recording another deposit method.')
            if one(db,'SELECT id FROM payments WHERE lead_id=? AND reference=? AND amount_cents=?',(lead['id'],reference,amount)):
                raise HTTPException(409,'A matching payment reference and amount already exist. Review the ledger.')
            p=dict(id=uid('PAY'),lead_id=lead['id'],amount_cents=amount,method=data['method'],purpose=purpose,
                reference=reference,verified=int(verified),recorded_by=u['id'],created_at=now())
            insert(db,'payments',p);audit(db,u['id'],'Payment recorded','payments',p['id'],{'verified':verified,'amount_cents':amount})
        return p

    @app.post('/api/payments/{pid}/verify')
    def verify_payment(pid:str,u=Depends(auth)):
        require(u,{'owner','accounting'})
        with transaction(path) as db:
            p=one(db,'SELECT * FROM payments WHERE id=?',(pid,))
            if not p:raise HTTPException(404,'Payment not found.')
            if one(db,'SELECT id FROM field_collections WHERE payment_id=?',(pid,)):raise HTTPException(409,'Verify this collection from its invoice so allocation is recorded together.')
            if p['reverses_id']:raise HTTPException(422,'Verify the original and reversal together through accounting review.')
            db.execute('UPDATE payments SET verified=1 WHERE id=? OR reverses_id=?',(pid,pid))
            audit(db,u['id'],'Payment receipt verified','payments',pid)
        return {'ok':True}

    @app.post('/api/payments/{pid}/reverse')
    def reverse_payment(pid:str,data:dict,u=Depends(auth)):
        require(u,{'owner','accounting'})
        note=bounded(data.get('note',''),'Reversal reason',1000)
        if not note:raise HTTPException(422,'A reversal reason is required.')
        with transaction(path) as db:
            p=one(db,'SELECT * FROM payments WHERE id=?',(pid,))
            if not p or p['reverses_id'] or p['amount_cents']<=0:raise HTTPException(422,'Select an original positive payment.')
            if one(db,'SELECT id FROM payments WHERE reverses_id=?',(pid,)):raise HTTPException(409,'Already reversed.')
            r=dict(id=uid('PAY'),lead_id=p['lead_id'],amount_cents=-p['amount_cents'],method=p['method'],purpose=p['purpose'],
                reference='Reversal: '+note,verified=p['verified'],reverses_id=pid,recorded_by=u['id'],created_at=now())
            insert(db,'payments',r);audit(db,u['id'],'Payment reversed, not deleted','payments',r['id'],{'original_id':pid})
        return r

    @app.get('/api/times')
    def times(u=Depends(auth)):
        with closing(connect(path)) as db:
            clause=' WHERE t.employee_id=?' if u['role']=='crew' else ''
            args=(u.get('employee_id') or '',) if u['role']=='crew' else ()
            result=rows(db,'SELECT t.*,e.name AS employee_name,e.hourly_cents,j.title AS job_title FROM times t JOIN employees e ON e.id=t.employee_id JOIN jobs j ON j.id=t.job_id'+clause+' ORDER BY t.created_at DESC',args)
        for r in result:
            if u['role'] not in ('owner','sales','accounting'):r.pop('hourly_cents',None)
        return result

    @app.post('/api/times')
    def add_time(data:dict,u=Depends(auth)):
        require(u,{'owner','dispatch','crew'})
        eid=data.get('employee_id') or u.get('employee_id')
        if u['role']=='crew' and eid!=u.get('employee_id'):raise HTTPException(403,'You can submit only your own time.')
        try:
            service=number(data.get('service_hours'),'Service hours',0,24)
            transport=number(data.get('transport_hours',0),'Transport hours',0,24)
            if service+transport<=0 or service+transport>24:raise ValueError('Submit 0 to 24 actual hours for this job record. Multi-day shifts need separate job phases.')
            sm,tm=service*60,transport*60
            if sm!=sm.to_integral() or tm!=tm.to_integral():raise ValueError('Time must resolve to whole minutes.')
        except ValueError as e:raise HTTPException(422,str(e))
        with transaction(path) as db:
            j=visible_job(db,data.get('job_id',''),u)
            if j['status'] not in ('In progress','Completed'):raise HTTPException(422,'Time can only be submitted for an in-progress or completed job.')
            if not one(db,"SELECT job_id FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(j['id'],eid)):
                raise HTTPException(422,'Employee is not assigned to this job.')
            rec=dict(id=uid('TIME'),job_id=j['id'],employee_id=eid,service_minutes=int(sm),transport_minutes=int(tm),
                note=bounded(data.get('note',''),'Time notes'),submitted_by=u['id'],created_at=now())
            try:insert(db,'times',rec)
            except sqlite3.IntegrityError:raise HTTPException(409,'A time entry already exists. Have an owner review it; do not duplicate hours.')
            audit(db,u['id'],'Time submitted for review','times',rec['id'])
        return rec

    @app.post('/api/times/{tid}/approve')
    def approve_time(tid:str,u=Depends(auth)):
        require(u,{'owner','accounting'})
        with transaction(path) as db:
            t=one(db,'SELECT t.id,j.status FROM times t JOIN jobs j ON j.id=t.job_id WHERE t.id=?',(tid,))
            if not t or t['status']!='Completed':raise HTTPException(422,'Complete the job before approving time.')
            db.execute("UPDATE times SET status='Approved',approved_by=? WHERE id=?",(u['id'],tid))
            audit(db,u['id'],'Time approved; payroll not processed','times',tid)
        return {'ok':True,'payroll_processed':False}

    @app.get('/api/inventory')
    def stock(u=Depends(auth)):
        require(u,READERS)
        with closing(connect(path)) as db:items=rows(db,'SELECT * FROM inventory ORDER BY category,name')
        for item in items:
            item.pop('raw_json',None)
            if u['role'] not in ('owner','sales','accounting'):item.pop('cost_cents',None)
        return items

    @app.post('/api/inventory/{iid}/movement')
    def stock_move(iid:str,data:dict,u=Depends(auth)):
        require(u,{'owner','sales','dispatch'})
        try:amount=float(number(data.get('quantity'),'Quantity change',-100000,100000))
        except ValueError as e:raise HTTPException(422,str(e))
        reason=bounded(data.get('reason',''),'Reason',1000)
        if not amount or not reason:raise HTTPException(422,'Enter a nonzero quantity change and a reason.')
        with transaction(path) as db:
            item=one(db,'SELECT * FROM inventory WHERE id=?',(iid,))
            if not item:raise HTTPException(404,'Item not found.')
            if item['quantity']+amount<0:raise HTTPException(422,'Not enough stock. Reconcile the physical quantity first.')
            job=data.get('job_id') or None
            if job and not one(db,'SELECT id FROM jobs WHERE id=?',(job,)):raise HTTPException(422,'Job not found.')
            insert(db,'stock_movements',dict(id=uid('STOCK'),item_id=iid,quantity=amount,reason=reason,job_id=job,author=u['id'],created_at=now()))
            db.execute('UPDATE inventory SET quantity=quantity+?,version=version+1 WHERE id=?',(amount,iid))
            audit(db,u['id'],'Inventory movement','inventory',iid,{'quantity':amount,'reason':reason})
        return {'ok':True}

    @app.get('/api/imports')
    def imports(u=Depends(auth)):
        require(u,{'owner','accounting'})
        with closing(connect(path)) as db:out=rows(db,'SELECT * FROM imports ORDER BY created_at DESC')
        for r in out:r['report']=json.loads(r.pop('report_json'))
        return out

    @app.post('/api/imports/workbook')
    def upload_workbook(file:UploadFile=File(...),u=Depends(auth)):
        require(u,{'owner'})
        if not (file.filename or '').lower().endswith('.xlsx'):raise HTTPException(422,'Use the original .xlsx workbook.')
        content=file.file.read(12_000_001)
        try:return import_workbook(path,content,Path(file.filename).name,u['id'])
        except ValueError as e:raise HTTPException(422,str(e))
        except Exception:raise HTTPException(422,'Workbook could not be imported. No partial import was committed. Check its format.')

    @app.get('/api/audit')
    def history(u=Depends(auth)):
        require(u,{'owner'})
        with closing(connect(path)) as db:
            return rows(db,'SELECT a.*,u.name AS actor_name FROM audit a LEFT JOIN users u ON u.id=a.actor ORDER BY a.id DESC LIMIT 200')

    @app.get('/api/integrations')
    def integrations(u=Depends(auth)):
        require(u,QUOTERS)
        with closing(connect(path)) as db:
            source=rows(db,"SELECT payload_json FROM source_rows WHERE sheet='Automation Control' ORDER BY id DESC LIMIT 9")
            events=rows(db,'SELECT id,event_type,object_id,status,created_at FROM integration_events ORDER BY created_at DESC LIMIT 100')
            usage=rows(db,'SELECT id,model,status,input_tokens,cached_tokens,output_tokens,reserved_tokens,error,created_at FROM ai_runs ORDER BY created_at DESC LIMIT 100')
        return {'ai_configured':bool(os.environ.get('OPENAI_API_KEY') and os.environ.get('OPENAI_MODEL')),
             'ai_model':os.environ.get('OPENAI_MODEL','Not configured'),
             'ai_monthly_token_limit':int(os.environ.get('AI_MONTHLY_TOKEN_LIMIT','250000')),
             'intake_configured':len(os.environ.get('CROWNOPS_WEBHOOK_SECRET',''))>=32,
             'gmail':'Not connected in this pilot','quickbooks':'Export queue only; not connected',
             'schedule':'Separate pilot; Carl live schedule is not connected','events':events,'ai_runs':usage,
             'source_controls':[json.loads(r['payload_json']) for r in source]}

    @app.get('/api/integrations/outbox/{eid}')
    def event_export(eid:str,u=Depends(auth)):
        require(u,QUOTERS)
        with closing(connect(path)) as db:e=one(db,'SELECT * FROM integration_events WHERE id=?',(eid,))
        if not e:raise HTTPException(404,'Queue item not found.')
        return Response(json.dumps(json.loads(e['payload_json']),indent=2),media_type='application/json',
                        headers={'Content-Disposition':f'attachment; filename="{eid}.json"'})

    @app.post('/api/ai/analyze')
    def analyze(data:dict,u=Depends(auth)):
        require(u,QUOTERS)
        from .platform_common import external_gate
        external_gate()
        if not os.environ.get('OPENAI_API_KEY') or not os.environ.get('OPENAI_MODEL'):
            raise HTTPException(503,'AI is not connected in this pilot. Add OPENAI_API_KEY and OPENAI_MODEL on the server. No tokens were used.')
        body=bounded(data.get('text',''),'Client message',14000)
        if not body:raise HTTPException(422,'Paste a client message first.')
        if data.get('consent_to_process') is not True:
            raise HTTPException(422,'Confirm authorization to process this selected client message with the configured AI provider.')
        rid=bounded(data.get('request_id',''),'Request ID',120)
        if not re.fullmatch(r'[a-zA-Z0-9_-]{12,120}',rid):raise HTTPException(422,'Use a unique request identifier.')
        language=bounded(data.get('language','English'),'Language',20)
        lid=data.get('lead_id') or None
        context={};approved=None;model=os.environ['OPENAI_MODEL']
        with transaction(path) as db:
            if lid:
                lead=get_lead(db,lid)
                # Do not send the workbook, call history, payroll, or unrelated clients.
                context={k:str(lead.get(k,''))[:1000] for k in ai.FIELDS}
                q=one(db,"SELECT * FROM quotes WHERE lead_id=? AND status IN ('Approved','Accepted') ORDER BY revision DESC LIMIT 1",(lid,))
                if q and q['lead_version']==lead['version']:approved=json.loads(q['result_json'])
            content_hash=sha(dumps({'body':body,'language':language,'lead':lid,'context':context,'quote':approved,'model':model}))
            previous=one(db,'SELECT * FROM ai_runs WHERE id=?',(rid,))
            if previous:
                if previous['request_hash']!=content_hash or previous['user_id']!=u['id']:
                    raise HTTPException(409,'Request ID was already used for another request.')
                if previous['status']=='Completed':
                    return {'result':json.loads(previous['result_json']),'usage':{k:previous[k] for k in ('input_tokens','cached_tokens','output_tokens')},'cached_result':True}
                raise HTTPException(409,'This request already exists or is unresolved. Review the AI usage log before retrying.')
            month=now()[:7]
            since=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(timespec='seconds')
            if db.execute('SELECT count(*) FROM ai_runs WHERE user_id=? AND created_at>?',(u['id'],since)).fetchone()[0]>=4:
                raise HTTPException(429,'Limit: four AI requests per minute per user.')
            # Conservative reservation uses bytes rather than an understated word estimate.
            reserve=len(dumps({'text':body,'context':context,'quote':approved}).encode())+len(ai.SYSTEM.encode())+len(dumps(ai.SCHEMA).encode())+6000
            used=db.execute("SELECT coalesce(sum(CASE WHEN status='Completed' THEN input_tokens+output_tokens ELSE reserved_tokens END),0) FROM ai_runs WHERE substr(created_at,1,7)=?",(month,)).fetchone()[0]
            if used+reserve>int(os.environ.get('AI_MONTHLY_TOKEN_LIMIT','250000')):
                raise HTTPException(429,'Monthly AI token budget reached or reserved. An owner must review usage before increasing it.')
            insert(db,'ai_runs',dict(id=rid,request_hash=content_hash,user_id=u['id'],lead_id=lid,model=model,
                status='Running',reserved_tokens=reserve,created_at=now()))
        try:
            result,usage=ai.invoke(body,language,context,approved)
            it=int(usage.get('input_tokens',0));ot=int(usage.get('output_tokens',0));ct=int(usage.get('input_tokens_details',{}).get('cached_tokens',0))
            with transaction(path) as db:
                db.execute("UPDATE ai_runs SET status='Completed',input_tokens=?,cached_tokens=?,output_tokens=?,result_json=? WHERE id=?",(it,ct,ot,dumps(result),rid))
                audit(db,u['id'],'AI suggestion generated; not applied or sent','ai_runs',rid,{'input_tokens':it,'output_tokens':ot})
            return {'result':result,'usage':{'input_tokens':it,'cached_tokens':ct,'output_tokens':ot},'applied':False,'sent':False}
        except Exception as exc:
            error=str(exc) if isinstance(exc,RuntimeError) else 'AI request failed or timed out. Check server connectivity. No client changes or messages were made.'
            with transaction(path) as db:
                db.execute("UPDATE ai_runs SET status='Error',error=? WHERE id=?",(error[:500],rid))
            raise HTTPException(502,error)

    @app.post('/api/webhooks/intake')
    async def intake(request:Request):
        secret=os.environ.get('CROWNOPS_WEBHOOK_SECRET','')
        if len(secret)<32:raise HTTPException(503,'Intake webhook is not configured.')
        content=await request.body()
        if len(content)>100000:raise HTTPException(413,'Intake payload too large.')
        timestamp=request.headers.get('x-crown-timestamp','')
        try:
            if abs(time.time()-int(timestamp))>300:raise ValueError()
        except ValueError:raise HTTPException(401,'Invalid or expired webhook timestamp.')
        signature=hmac.new(secret.encode(),timestamp.encode()+b'.'+content,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,request.headers.get('x-crown-signature','')):
            raise HTTPException(401,'Invalid webhook signature.')
        try:data=json.loads(content)
        except Exception:raise HTTPException(422,'Invalid JSON.')
        event_id=bounded(data.get('event_id',''),'Event ID',180)
        if not event_id:raise HTTPException(422,'A stable source event_id is required.')
        bh=hashlib.sha256(content).hexdigest()
        with transaction(path) as db:
            previous=one(db,'SELECT * FROM intake_events WHERE event_id=?',(event_id,))
            if previous:
                if previous['body_hash']!=bh:raise HTTPException(409,'Event ID reused with different content.')
                return {'lead_id':previous['lead_id'],'duplicate':True,'messages_sent':0}
            l=new_lead(db,data.get('lead',{}),'signed-intake',external_id='webhook:'+event_id)
            insert(db,'intake_events',dict(event_id=event_id,body_hash=bh,lead_id=l['id'],created_at=now()))
        return {'lead_id':l['id'],'duplicate':False,'messages_sent':0}

    @app.get('/api/schema')
    def schema(u=Depends(auth)):
        require(u,{'owner'});return app.openapi()

    @app.get('/')
    def index():return FileResponse(ROOT/'app'/'static'/'index.html')

    @app.get('/manifest.webmanifest')
    def manifest():return FileResponse(ROOT/'app'/'static'/'manifest.webmanifest',media_type='application/manifest+json')

    @app.get('/sw.js')
    def sw():return FileResponse(ROOT/'app'/'static'/'sw.js',media_type='application/javascript',headers={'Service-Worker-Allowed':'/'})

    from .invoices import register_invoices
    register_invoices(app,path,auth,visible_job,require)

    from .schedule_routes import register_schedule_imports
    register_schedule_imports(app,path,auth)

    from .portal import register_portal
    from .operations import register_operations
    from .workflows import register_workflows
    from .providers import register_providers
    from .security import register_security, validate_deployment
    validate_deployment(public_url,secure)
    register_portal(app,path,auth,public_url,secure)
    register_operations(app,path,auth)
    register_workflows(app,path,auth,new_lead,check_job_resources,public_url)
    register_providers(app,path,auth,public_url)
    register_security(app,path,auth,public_url,PASSWORDS)
    from .extensions import register_extensions
    register_extensions(app,path,auth,public_url)
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    host=urlparse(public_url).hostname or 'localhost'
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=[host,'127.0.0.1','localhost']+(['testserver','crownops.test'] if os.getenv('CROWNOPS_ENV','test')!='production' else []))

    from .payroll_adjustments import register_payroll_adjustments
    register_payroll_adjustments(app,path,auth)
    from .review_features import register_review_features, seed_review_defaults
    seed_review_defaults(path)
    register_review_features(app,path,auth,public_url)
    from .ux_features import register_ux_features, seed_ux_defaults
    seed_ux_defaults(path)
    register_ux_features(app,path,auth)
    from .final_features import register_final_features
    register_final_features(app,path,auth)
    app.mount('/static',StaticFiles(directory=ROOT/'app'/'static'),name='static')
    return app
