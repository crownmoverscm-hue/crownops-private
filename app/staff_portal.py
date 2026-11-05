"""Personal staff portal: explicit availability, approved cover and private documents."""
from contextlib import closing
from datetime import datetime, timedelta, timezone, time, date
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import re
from fastapi import Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from .store import connect, transaction, rows, one, insert, now, uid, dumps, audit
from .staff_records import schedule_identity
from .platform_common import MANAGERS, require_role, text, business_day
from .payroll_periods import default_range
from .business_zone import BUSINESS_ZONE, zone_name

ET = BUSINESS_ZONE


def employee(db, user):
    eid = schedule_identity(db, user)['id']
    e = one(db, 'SELECT * FROM employees WHERE id=? AND active=1', (eid,)) if eid else None
    if not e:
        raise HTTPException(409, 'Ask a manager to link your account to an active employee record.')
    return e


def instant(value):
    d = datetime.fromisoformat(value)
    return (d if d.tzinfo else d.replace(tzinfo=ET)).astimezone(timezone.utc)


def availability(db, eid):
    r = one(db, 'SELECT * FROM staff_availability WHERE employee_id=?', (eid,))
    return {'version': r['version'], **json.loads(r['payload_json'])} if r else {'version': 0, 'weekly': [], 'exceptions': []}


def minute(value):
    if value == '24:00': return 1440
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise HTTPException(422, 'Use a valid time in HH:MM format.')
    h, m = map(int, value.split(':'))
    return h * 60 + m


def validate_availability(data):
    weekly, exceptions = data.get('weekly'), data.get('exceptions', [])
    if not isinstance(weekly, list) or len(weekly) > 28 or not isinstance(exceptions, list) or len(exceptions) > 100:
        raise HTTPException(422, 'Choose up to 28 weekly windows and 100 date exceptions.')
    def window(w):
        if not isinstance(w, dict) or set(w) - {'start', 'end', 'day'}:
            raise HTTPException(422, 'Invalid availability window.')
        if minute(w.get('start')) >= minute(w.get('end')):
            raise HTTPException(422, 'End must follow start. Split overnight availability across two days.')
        return {'start': w['start'], 'end': w['end']}
    clean = []
    for w in weekly:
        v = window(w)
        if type(w.get('day')) is not int or not 0 <= w['day'] <= 6:
            raise HTTPException(422, 'Choose a weekday from Monday to Sunday.')
        clean.append({'day': w['day'], **v})
    dates, overrides = set(), []
    for e in exceptions:
        try:
            day = date.fromisoformat(e['date']).isoformat()
            if day in dates or not isinstance(e['windows'], list) or len(e['windows']) > 4: raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, 'Use one exception per date with up to four time windows.')
        dates.add(day)
        overrides.append({'date': day, 'windows': [window(w) for w in e['windows']]})
    return {'weekly': clean, 'exceptions': overrides}


def explicitly_available(db, eid, start, end):
    """Every minute must be covered by affirmative availability, in local time."""
    if instant(end) <= instant(start): return False
    cfg = availability(db, eid)
    overrides = {e['date']: e['windows'] for e in cfg['exceptions']}
    a, b = instant(start).astimezone(ET), instant(end).astimezone(ET)
    day = a.date()
    while day <= b.date():
        lo = a.hour * 60 + a.minute + a.second / 60 if day == a.date() else 0
        hi = b.hour * 60 + b.minute + b.second / 60 if day == b.date() else 1440
        if hi > lo:
            windows = overrides.get(day.isoformat(), [w for w in cfg['weekly'] if w['day'] == day.weekday()])
            cursor = lo
            for w in sorted(windows, key=lambda w: minute(w['start'])):
                left, right = minute(w['start']), minute(w['end'])
                if left <= cursor: cursor = max(cursor, right)
            if cursor < hi: return False
        day += timedelta(days=1)
    return True


def eligible(db, job, source_id, target_id):
    source = one(db, 'SELECT * FROM employees WHERE id=?', (source_id,))
    target = one(db, 'SELECT * FROM employees WHERE id=? AND active=1', (target_id,))
    if not source or not target or source_id == target_id: return False
    if not one(db, 'SELECT id FROM users WHERE employee_id=? AND active=1', (target_id,)): return False
    if not explicitly_available(db, target_id, job['start_at'], job['end_at']): return False
    a, b = instant(job['start_at']), instant(job['end_at'])
    first, last = a.astimezone(ET).date().isoformat(), (b-timedelta(microseconds=1)).astimezone(ET).date().isoformat()
    if one(db, "SELECT id FROM time_off_requests WHERE employee_id=? AND status='Approved' AND start_date<=? AND end_date>=?", (target_id, last, first)): return False
    for other in rows(db, """SELECT j.* FROM assignments a JOIN jobs j ON j.id=a.job_id
        WHERE a.resource_type='employee' AND a.resource_id=? AND j.status!='Cancelled'""", (target_id,)):
        if instant(other['start_at']) < b and instant(other['end_at']) > a: return False
    if source['driver_verified']:
        if not target['driver_verified']: return False
        p = one(db, 'SELECT license_expiry FROM staff_profiles WHERE employee_id=?', (target_id,))
        if p and p['license_expiry'] and p['license_expiry'] < last: return False
    lead = one(db, 'SELECT team_lead FROM employee_directory_profiles WHERE employee_id=?', (source_id,))
    if lead and lead['team_lead'] and not one(db, 'SELECT employee_id FROM employee_directory_profiles WHERE employee_id=? AND team_lead=1', (target_id,)): return False
    return True


def cover_job(db, jid, eid):
    job = one(db, 'SELECT * FROM jobs WHERE id=?', (jid,))
    if not job or not one(db, "SELECT 1 FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?", (jid, eid)):
        raise HTTPException(403, 'Choose an assignment on your own schedule.')
    if job['status'] not in ('Tentative', 'Confirmed') or instant(job['start_at']) <= datetime.now(timezone.utc):
        raise HTTPException(409, 'This assignment has already started or ended. Contact a manager directly.')
    return job


def notify(db, users, title, kind, object_id):
    for user_id in set(users):
        insert(db, 'staff_portal_notifications', dict(id=uid('NOTICE'), user_id=user_id, title=title, kind=kind, object_id=object_id, created_at=now()))


def employee_users(db, eid):
    return [u['id'] for u in rows(db, 'SELECT id FROM users WHERE employee_id=? AND active=1', (eid,))]


def managers(db):
    return [u['id'] for u in rows(db, "SELECT id FROM users WHERE role IN ('owner','manager') AND active=1")]


def request_list(db, user, eid):
    result = []
    condition = '' if user['role'] in MANAGERS else '''WHERE r.source_employee=? OR EXISTS
        (SELECT 1 FROM staff_cover_recipients c WHERE c.request_id=r.id AND c.employee_id=?)'''
    for r in rows(db, """SELECT r.*,j.title,j.start_at,j.end_at,j.version current_job_version,j.status job_status,
        e.name source_name,t.name target_name FROM staff_cover_requests r JOIN jobs j ON j.id=r.job_id
        JOIN employees e ON e.id=r.source_employee LEFT JOIN employees t ON t.id=r.target_employee
        """+condition+" ORDER BY r.created_at DESC LIMIT 300", () if user['role'] in MANAGERS else (eid,eid)):
        invited = bool(eid and one(db, 'SELECT 1 FROM staff_cover_recipients WHERE request_id=? AND employee_id=?', (r['id'], eid)))
        own = eid == r['source_employee']
        if not (own or invited or user['role'] in MANAGERS): continue
        active = r['status'] in ('Awaiting coworker', 'Awaiting manager')
        stale = active and (r['job_version'] != r['current_job_version'] or instant(r['start_at']) <= datetime.now(timezone.utc) or r['job_status'] not in ('Tentative', 'Confirmed'))
        r.update(stale=stale, can_accept=invited and not own and not stale and r['status']=='Awaiting coworker',
                 can_cancel=own and active, can_review=user['role'] in MANAGERS and active and (not eid or eid not in (r['source_employee'],r['target_employee'])) and user['id']!=r['requested_by'])
        result.append(r)
    return result


def publish_document(db, user, title, body, recipients, acknowledge, filename='', blob=None):
    require_role(user, {'owner'})
    title = text(title, 'Document title', 180).strip()
    body = text(body, 'Document text', 100000).strip()
    if not title or (not body and not blob): raise HTTPException(422, 'Add a title and document text or a file.')
    active = {u['id'] for u in rows(db, 'SELECT id FROM users WHERE active=1')}
    if recipients == 'all': audience = active
    elif isinstance(recipients, list) and all(isinstance(x,str) for x in recipients): audience = set(recipients)
    else: raise HTTPException(422, 'Choose staff recipients or all active staff.')
    if not audience or audience-active: raise HTTPException(422, 'Select active staff accounts.')
    did = uid('SDOC')
    insert(db, 'staff_documents', dict(id=did,title=title,body=body,filename=filename,content=blob,
        requires_ack=int(bool(acknowledge)),created_by=user['id'],created_at=now()))
    for user_id in audience: insert(db, 'staff_document_recipients',dict(document_id=did,user_id=user_id))
    notify(db,audience,'New document: '+title,'document',did)
    audit(db,user['id'],'Staff document published','staff_documents',did,{'recipients':sorted(audience),'requires_ack':bool(acknowledge)})
    return {'id':did,'recipients':len(audience)}


def register_staff_portal(app, path, auth):
    @app.get('/api/my-portal')
    def home(u=Depends(auth)):
        with closing(connect(path)) as db:
            identity=schedule_identity(db,u);eid=identity['id']
            profile=one(db,'SELECT phone,email FROM employee_directory_profiles WHERE employee_id=?',(eid,)) or {'phone':'','email':''}
            period=default_range(db);hours=[];today=business_day()
            if eid and period:
                for t in rows(db,"SELECT t.*,j.title,j.start_at FROM times t JOIN jobs j ON j.id=t.job_id WHERE t.employee_id=? AND j.status!='Cancelled' ORDER BY j.start_at",(eid,)):
                    day=business_day(t['start_at'])
                    if period['start']<=day<=min(period['end'],today):
                        hours.append({'date':day,'job':t['title'],'minutes':t['service_minutes']+t['transport_minutes'],'status':t['status']})
            jobs=[]
            if eid:
                for j in rows(db,"SELECT j.id,j.title,j.start_at,j.end_at,j.status FROM jobs j JOIN assignments a ON a.job_id=j.id WHERE a.resource_type='employee' AND a.resource_id=? AND j.status NOT IN ('Cancelled','Completed') ORDER BY j.start_at",(eid,)):
                    if instant(j['end_at'])>=datetime.now(timezone.utc): jobs.append(j)
            docs=rows(db,"""SELECT d.id,d.title,d.created_at,d.requires_ack,d.filename,r.read_at,r.ack_at
                FROM staff_documents d JOIN staff_document_recipients r ON r.document_id=d.id
                WHERE r.user_id=? ORDER BY d.created_at DESC,d.id DESC""",(u['id'],))
            notices=rows(db,'SELECT * FROM staff_portal_notifications WHERE user_id=? AND read_at IS NULL ORDER BY created_at DESC,id DESC LIMIT 100',(u['id'],))
            published=rows(db,'SELECT id,title,created_at FROM staff_documents WHERE created_by=? ORDER BY created_at DESC',(u['id'],)) if u['role']=='owner' else []
            help_requests=rows(db,'SELECT r.*,u.name FROM staff_help_requests r JOIN users u ON u.id=r.user_id'+('' if u['role'] in MANAGERS else ' WHERE r.user_id=?')+' ORDER BY created_at DESC LIMIT 100',() if u['role'] in MANAGERS else (u['id'],))
            off=rows(db,'SELECT id,start_date,end_date,status,review_note FROM time_off_requests WHERE employee_id=? ORDER BY start_date DESC LIMIT 50',(eid,)) if eid else []
            return {'identity':identity,'profile':profile,'period':period,'as_of':today,'hours':hours,'time_off':off,'published_documents':published,'help_requests':help_requests,
                    'total_minutes':sum(t['minutes'] for t in hours),'approved_minutes':sum(t['minutes'] for t in hours if t['status']=='Approved'),
                    'jobs':jobs,'availability':availability(db,eid) if eid else None,'documents':docs,
                    'requests':request_list(db,u,eid),'notifications':notices,'can_publish':u['role']=='owner','can_manage':u['role'] in MANAGERS}

    @app.get('/api/my-portal/notifications')
    def notifications(u=Depends(auth)):
        with closing(connect(path)) as db:
            return {'unread':one(db,'SELECT COUNT(*) n FROM staff_portal_notifications WHERE user_id=? AND read_at IS NULL',(u['id'],))['n']}

    @app.post('/api/my-portal/notifications/{nid}/read')
    def read_notice(nid:str,u=Depends(auth)):
        with transaction(path) as db:db.execute('UPDATE staff_portal_notifications SET read_at=? WHERE id=? AND user_id=?',(now(),nid,u['id']))
        return {'ok':True}

    @app.post('/api/my-portal/contact-manager')
    def contact_manager(data:dict,u=Depends(auth)):
        subject=text(data.get('subject',''),'Subject',180).strip();body=text(data.get('body',''),'Message',3000).strip()
        if not subject or not body:raise HTTPException(422,'Add a subject and message.')
        with transaction(path) as db:
            rid=uid('HELP');insert(db,'staff_help_requests',dict(id=rid,user_id=u['id'],subject=subject,body=body,created_at=now()))
            notify(db,managers(db),u['name']+': '+subject,'message',rid)
            audit(db,u['id'],'Manager contacted through My Portal','staff_help_requests',rid)
        return {'id':rid}

    @app.post('/api/my-portal/messages/{rid}/reply')
    def reply_manager(rid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS);reply=text(data.get('reply',''),'Reply',3000).strip()
        if not reply:raise HTTPException(422,'Write a reply.')
        with transaction(path) as db:
            r=one(db,'SELECT * FROM staff_help_requests WHERE id=?',(rid,))
            if not r:raise HTTPException(404,'Message not found.')
            if r['replied_at']:raise HTTPException(409,'This message already has a reply. Refresh My Portal.')
            db.execute('UPDATE staff_help_requests SET reply=?,replied_at=?,replied_by=? WHERE id=?',(reply,now(),u['id'],rid))
            notify(db,[r['user_id']],'Manager replied: '+r['subject'],'message',rid)
            audit(db,u['id'],'Manager replied in My Portal','staff_help_requests',rid)
        return {'ok':True}

    @app.post('/api/my-portal/time-off')
    def personal_time_off(data:dict,u=Depends(auth)):
        from .review_features import _date, _timeoff_conflicts, _notify
        a=_date(data.get('start_date'),'First day');b=_date(data.get('end_date'),'Last day')
        if b<a or (b-a).days>60:raise HTTPException(422,'Choose up to 60 days in date order.')
        note=text(data.get('note',''),'Private note to management',2000)
        with transaction(path) as db:
            e=employee(db,u)
            if _timeoff_conflicts(db,e['id'],a.isoformat(),b.isoformat()):raise HTTPException(409,'You already have a request for those dates.')
            rid=uid('OFF');insert(db,'time_off_requests',dict(id=rid,employee_id=e['id'],start_date=a.isoformat(),end_date=b.isoformat(),status='Pending',note=note,requested_by=u['id'],requested_at=now()))
            _notify(db,'time_off',rid,e['name']+' requested time off.')
            notify(db,managers(db),e['name']+' requested time off','time_off',rid)
            audit(db,u['id'],'Time off requested','time_off_requests',rid)
        return {'id':rid}

    @app.put('/api/my-portal/availability')
    def save_availability(data:dict,u=Depends(auth)):
        if set(data)-{'weekly','exceptions','version'}:raise HTTPException(422,'Unknown availability field.')
        clean=validate_availability(data)
        with transaction(path) as db:
            eid=employee(db,u)['id'];old=availability(db,eid)
            if type(data.get('version')) is not int or data['version']!=old['version']:raise HTTPException(409,'Availability changed. Reload before saving.')
            db.execute('INSERT INTO staff_availability(employee_id,payload_json,version,updated_at) VALUES(?,?,?,?) ON CONFLICT(employee_id) DO UPDATE SET payload_json=excluded.payload_json,version=excluded.version,updated_at=excluded.updated_at',(eid,dumps(clean),old['version']+1,now()))
            audit(db,u['id'],'Personal availability updated; assignments unchanged','employees',eid)
        return {'ok':True}

    @app.patch('/api/my-portal/profile')
    def save_profile(data:dict,u=Depends(auth)):
        if set(data)-{'phone','email'}:raise HTTPException(422,'Only contact phone and contact email can be updated here.')
        phone=text(data.get('phone',''),'Phone',80).strip();email=text(data.get('email',''),'Contact email',240).strip().lower()
        if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):raise HTTPException(422,'Enter a valid contact email.')
        with transaction(path) as db:
            eid=employee(db,u)['id']
            # Preserve an existing exact-email match before changing directory contact data.
            if not u.get('employee_id'):db.execute('UPDATE users SET employee_id=? WHERE id=?',(eid,u['id']))
            db.execute('INSERT OR IGNORE INTO employee_directory_profiles(employee_id) VALUES(?)',(eid,))
            db.execute('UPDATE employee_directory_profiles SET phone=?,email=?,updated_by=?,updated_at=? WHERE employee_id=?',(phone,email,u['id'],now(),eid))
            audit(db,u['id'],'Own contact information updated; sign-in unchanged','employees',eid)
        return {'ok':True}

    @app.get('/api/my-portal/jobs/{jid}/replacements')
    def replacements(jid:str,u=Depends(auth)):
        with closing(connect(path)) as db:
            eid=employee(db,u)['id'];job=cover_job(db,jid,eid)
            options=[{'id':e['id'],'name':e['name']} for e in rows(db,'SELECT id,name FROM employees WHERE active=1 ORDER BY name') if eligible(db,job,eid,e['id'])]
            return {'job':{k:job[k] for k in ('id','title','start_at','end_at','version')},'employees':options}

    @app.post('/api/my-portal/jobs/{jid}/give-away')
    def give_away(jid:str,data:dict,u=Depends(auth)):
        ids=data.get('employee_ids');message=text(data.get('message',''),'Message to coworkers',1000).strip()
        if not isinstance(ids,list) or not ids or len(ids)>100 or not all(isinstance(i,str) for i in ids):raise HTTPException(422,'Select at least one available coworker.')
        with transaction(path) as db:
            eid=employee(db,u)['id'];job=cover_job(db,jid,eid)
            if data.get('version')!=job['version']:raise HTTPException(409,'This assignment changed. Reopen Give Shift Away.')
            if one(db,"SELECT id FROM staff_cover_requests WHERE job_id=? AND source_employee=? AND status IN ('Awaiting coworker','Awaiting manager')",(jid,eid)):raise HTTPException(409,'A coverage request is already open. Cancel it before sending another.')
            if not all(eligible(db,job,eid,t) for t in ids):raise HTTPException(409,'A selected coworker is no longer eligible. Refresh the list.')
            rid=uid('COVER');insert(db,'staff_cover_requests',dict(id=rid,job_id=jid,job_version=job['version'],source_employee=eid,requested_by=u['id'],message=message,created_at=now()))
            for target in set(ids):
                insert(db,'staff_cover_recipients',dict(request_id=rid,employee_id=target))
                notify(db,employee_users(db,target),'Can you cover '+job['title']+'?','coverage',rid)
            notify(db,managers(db),'Coverage requested: '+job['title'],'coverage',rid)
            audit(db,u['id'],'Shift coverage requested; assignment unchanged','staff_cover_requests',rid)
        return {'id':rid}

    @app.post('/api/my-portal/requests/{rid}/accept')
    def accept_cover(rid:str,u=Depends(auth)):
        with transaction(path) as db:
            eid=employee(db,u)['id'];r=one(db,'SELECT * FROM staff_cover_requests WHERE id=?',(rid,))
            if not r or not one(db,'SELECT 1 FROM staff_cover_recipients WHERE request_id=? AND employee_id=?',(rid,eid)):raise HTTPException(403,'This request was not sent to you.')
            if r['status']!='Awaiting coworker':raise HTTPException(409,'This request already has a response or is closed.')
            job=cover_job(db,r['job_id'],r['source_employee'])
            if job['version']!=r['job_version'] or not eligible(db,job,r['source_employee'],eid):raise HTTPException(409,'The assignment or your availability changed. Ask a manager to review.')
            db.execute("UPDATE staff_cover_requests SET status='Awaiting manager',target_employee=? WHERE id=?",(eid,rid))
            notify(db,managers(db)+employee_users(db,r['source_employee']),'Replacement accepted; manager approval needed: '+job['title'],'coverage',rid)
            audit(db,u['id'],'Replacement accepted; assignment unchanged','staff_cover_requests',rid)
        return {'ok':True}

    @app.post('/api/my-portal/requests/{rid}/review')
    def review_cover(rid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS);decision=data.get('decision');note=text(data.get('note',''),'Manager note',1000)
        if decision not in ('Approved','Declined'):raise HTTPException(422,'Choose Approved or Declined.')
        with transaction(path) as db:
            r=one(db,'SELECT * FROM staff_cover_requests WHERE id=?',(rid,))
            if not r or r['status'] not in ('Awaiting coworker','Awaiting manager'):raise HTTPException(409,'This request is closed.')
            eid=schedule_identity(db,u)['id']
            if u['id']==r['requested_by'] or (eid and eid in (r['source_employee'],r['target_employee'])):raise HTTPException(403,'Another manager or owner must review your own request.')
            if decision=='Approved':
                if r['status']!='Awaiting manager':raise HTTPException(409,'A coworker must accept first.')
                job=cover_job(db,r['job_id'],r['source_employee'])
                if job['version']!=r['job_version'] or not eligible(db,job,r['source_employee'],r['target_employee']):raise HTTPException(409,'The assignment or availability changed. Decline this request and arrange new coverage.')
                db.execute("UPDATE assignments SET resource_id=? WHERE job_id=? AND resource_type='employee' AND resource_id=?",(r['target_employee'],r['job_id'],r['source_employee']))
                db.execute('UPDATE jobs SET version=version+1 WHERE id=?',(r['job_id'],))
                audit(db,u['id'],'Approved shift replacement','jobs',r['job_id'],{'request':rid,'from':r['source_employee'],'to':r['target_employee']})
            db.execute('UPDATE staff_cover_requests SET status=?,reviewed_by=?,reviewed_at=?,review_note=? WHERE id=?',(decision,u['id'],now(),note,rid))
            recipients=[x['employee_id'] for x in rows(db,'SELECT employee_id FROM staff_cover_recipients WHERE request_id=?',(rid,))]+[r['source_employee']]
            notify(db,[v for e in recipients for v in employee_users(db,e)],'Coverage '+decision.lower(),'coverage',rid)
        return {'ok':True,'status':decision}

    @app.post('/api/my-portal/requests/{rid}/cancel')
    def cancel_cover(rid:str,u=Depends(auth)):
        with transaction(path) as db:
            eid=employee(db,u)['id'];r=one(db,'SELECT * FROM staff_cover_requests WHERE id=?',(rid,))
            if not r or r['source_employee']!=eid:raise HTTPException(403,'Cancel only your own request.')
            if r['status'] not in ('Awaiting coworker','Awaiting manager'):raise HTTPException(409,'This request is already closed.')
            db.execute("UPDATE staff_cover_requests SET status='Cancelled' WHERE id=?",(rid,))
            recipients=[x['employee_id'] for x in rows(db,'SELECT employee_id FROM staff_cover_recipients WHERE request_id=?',(rid,))]
            notify(db,managers(db)+[v for e in recipients for v in employee_users(db,e)],'Coverage request cancelled','coverage',rid)
            audit(db,u['id'],'Coverage request cancelled','staff_cover_requests',rid)
        return {'ok':True}

    @app.get('/api/my-portal/document-recipients')
    def document_recipients(u=Depends(auth)):
        require_role(u,{'owner'})
        with closing(connect(path)) as db:return rows(db,'SELECT id,name,role FROM users WHERE active=1 ORDER BY name')

    @app.post('/api/my-portal/documents')
    def new_document(data:dict,u=Depends(auth)):
        with transaction(path) as db:return publish_document(db,u,data.get('title',''),data.get('body',''),data.get('recipients'),data.get('requires_ack',True))

    @app.post('/api/my-portal/documents/upload')
    async def upload_document(title:str=Form(...),recipients:str=Form(...),requires_ack:bool=Form(True),file:UploadFile=File(...),u=Depends(auth)):
        require_role(u,{'owner'})
        filename=Path(file.filename or 'document').name[:180];suffix=Path(filename).suffix.lower()
        blob=await file.read(10*1024*1024+1)
        if not blob or len(blob)>10*1024*1024:raise HTTPException(422,'Choose a non-empty document up to 10 MB.')
        signatures={'.pdf':b'%PDF-', '.png':b'\x89PNG\r\n\x1a\n', '.jpg':b'\xff\xd8\xff', '.jpeg':b'\xff\xd8\xff', '.docx':b'PK\x03\x04'}
        if suffix=='.txt':
            try:blob.decode('utf-8')
            except UnicodeDecodeError:raise HTTPException(422,'Text documents must use UTF-8.')
        elif suffix not in signatures or not blob.startswith(signatures[suffix]):raise HTTPException(422,'Use a PDF, DOCX, JPG, PNG or plain-text document.')
        try:audience=json.loads(recipients)
        except (ValueError,TypeError):raise HTTPException(422,'Choose document recipients.')
        with transaction(path) as db:return publish_document(db,u,title,'',audience,requires_ack,filename,blob)

    def accessible_doc(db,did,u):
        d=one(db,'SELECT * FROM staff_documents WHERE id=?',(did,))
        r=one(db,'SELECT * FROM staff_document_recipients WHERE document_id=? AND user_id=?',(did,u['id']))
        if not d or (not r and u['role']!='owner'):raise HTTPException(404,'Document not found.')
        return d,r

    @app.get('/api/my-portal/documents/{did}')
    def get_document(did:str,u=Depends(auth)):
        with transaction(path) as db:
            d,r=accessible_doc(db,did,u);d.pop('content',None)
            if r:db.execute('UPDATE staff_document_recipients SET read_at=COALESCE(read_at,?) WHERE document_id=? AND user_id=?',(now(),did,u['id']))
            d['ack_at']=r['ack_at'] if r else None;d['can_ack']=bool(r and d['requires_ack'])
            if u['role']=='owner':d['recipients']=rows(db,'SELECT u.name,r.read_at,r.ack_at FROM staff_document_recipients r JOIN users u ON u.id=r.user_id WHERE document_id=?',(did,))
            return d

    @app.get('/api/my-portal/documents/{did}/download')
    def download_document(did:str,u=Depends(auth)):
        from urllib.parse import quote
        with closing(connect(path)) as db:d,_=accessible_doc(db,did,u)
        content=d['content'] if d['content'] is not None else (d['title']+'\n\n'+d['body']).encode('utf-8')
        filename=d['filename'] or d['title']+'.txt'
        return Response(content,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename,safe=''),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @app.post('/api/my-portal/documents/{did}/acknowledge')
    def acknowledge(did:str,u=Depends(auth)):
        with transaction(path) as db:
            d,r=accessible_doc(db,did,u)
            if not r or not d['requires_ack'] or not r['read_at']:raise HTTPException(409,'Open your assigned document before acknowledging it.')
            db.execute('UPDATE staff_document_recipients SET ack_at=COALESCE(ack_at,?) WHERE document_id=? AND user_id=?',(now(),did,u['id']))
            audit(db,u['id'],'Staff document acknowledged','staff_documents',did)
        return {'ok':True}
