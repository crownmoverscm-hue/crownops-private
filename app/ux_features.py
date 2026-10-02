"""CrownOps v5.3 UX helpers.

This module is intentionally additive: it does not send email, move money, or
change QuickBooks. It provides faster read models and simple manager workflows
for the private review build.
"""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json, secrets, re
from fastapi import Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

from .store import connect, transaction, rows, one, insert, audit, uid, now
from .platform_common import require_role, OFFICE, MANAGERS, text, settings, put_setting, invoice_current

TORONTO=ZoneInfo('America/Toronto')
PRIVATE_HR={'owner','accounting'}


def _local_date(value):
    try:
        return datetime.fromisoformat(value).astimezone(TORONTO).date().isoformat()
    except Exception:
        return str(value or '')[:10]


def _date_arg(value, label):
    try:return date.fromisoformat(str(value))
    except Exception:raise HTTPException(422,f'Enter a valid {label}.')


def _profile_seed(db,e):
    old=one(db,'SELECT employee_id FROM employee_directory_profiles WHERE employee_id=?',(e['id'],))
    if old:return
    suffix=re.sub(r'\D','',e['id'])[-2:] or '00'
    insert(db,'employee_directory_profiles',dict(
        employee_id=e['id'],email='',phone=f'514-555-01{suffix.zfill(2)}',home_address='',
        emergency_name='Emergency contact to confirm',emergency_phone='',start_date='',notes='',
        team_lead=0,updated_by='review-seed',updated_at=now()))


def seed_ux_defaults(path):
    with transaction(path) as db:
        for e in rows(db,'SELECT * FROM employees ORDER BY name'):_profile_seed(db,e)
        if one(db,"SELECT employee_id FROM employee_directory_profiles WHERE employee_id='DEMO-EMP-1'"):
            db.execute("UPDATE employee_directory_profiles SET team_lead=1 WHERE employee_id='DEMO-EMP-1'")
        if settings(db,'company_communications') is None:
            put_setting(db,'company_communications',{
                'connected_mailbox':'crownmovers.cm@gmail.com',
                'send_as':'info@crownmovers.ca',
                'reply_to':'info@crownmovers.ca',
                'default_from':'info@crownmovers.ca',
                'secondary_inbox':'crownmovers.cm@gmail.com',
                'client_email_rule':'Use the paid Gmail mailbox for storage and send client mail as the Crown Movers domain address when the alias is verified.',
                'approved':False,
                'note':'Review build default. Connect and verify both mailboxes before live use.'
            })


def _job_readiness(db,j):
    l=one(db,'SELECT * FROM leads WHERE id=?',(j['lead_id'],)) or {}
    crew=rows(db,"SELECT resource_id FROM assignments WHERE job_id=? AND resource_type='employee'",(j['id'],))
    trucks=rows(db,"SELECT resource_id FROM assignments WHERE job_id=? AND resource_type='vehicle'",(j['id'],))
    missing=[]
    if not l.get('phone'):missing.append('client phone')
    if not l.get('pickup'):missing.append('pickup address')
    if not l.get('dropoff'):missing.append('destination address')
    if not crew:missing.append('assigned crew')
    if not trucks and j['phase'] not in ('Packing','Unpacking'):missing.append('assigned truck')
    if j.get('status')=='Tentative':missing.append('job not confirmed')
    if j.get('quote_id'):
        q=one(db,'SELECT * FROM quotes WHERE id=?',(j['quote_id'],))
        if not q or q['status']!='Accepted':
            missing.append('accepted quote')
        else:
            try:
                qi=json.loads(q['inputs_json']); required=round(float(qi.get('deposit_required',300) or 0)*100)
            except Exception:
                required=30000
            paid=db.execute("SELECT coalesce(sum(amount_cents),0) FROM payments WHERE lead_id=? AND verified=1 AND purpose='Deposit'",(j['lead_id'],)).fetchone()[0]
            if required and paid<required:missing.append('deposit not verified')
    elif j.get('status')!='Cancelled':
        missing.append('quote not linked')
    day=_local_date(j['start_at'])
    for r in rows(db,"""SELECT e.name FROM assignments a JOIN employees e ON e.id=a.resource_id
      JOIN time_off_requests o ON o.employee_id=e.id AND o.status='Approved'
      WHERE a.job_id=? AND a.resource_type='employee' AND o.start_date<=? AND o.end_date>=?""",(j['id'],day,day)):
        missing.append(f"{r['name']} is approved OFF")
    return {'ready':not missing,'missing':missing}


def _project_lifecycle(db,lid):
    q=one(db,"SELECT * FROM quotes WHERE lead_id=? ORDER BY revision DESC LIMIT 1",(lid,))
    j=one(db,"SELECT * FROM jobs WHERE lead_id=? ORDER BY start_at DESC LIMIT 1",(lid,))
    inv=None
    if j:inv=one(db,'SELECT * FROM job_invoices WHERE job_id=?',(j['id'],))
    steps=['Inquiry','Quote','Accepted','Scheduled','In service','Completed','Paid']
    idx=0
    if q:idx=max(idx,1)
    if q and q['status']=='Accepted':idx=max(idx,2)
    if j and j['status'] in ('Tentative','Confirmed','In progress','Completed'):idx=max(idx,3)
    if j and j['status']=='In progress':idx=max(idx,4)
    if j and j['status']=='Completed':idx=max(idx,5)
    balance=None
    if inv and inv['status']=='Issued':
        try:
            _,calc=invoice_current(db,inv['id']);balance=calc['balance_cents']
            if balance<=0:idx=6
        except Exception:pass
    return {'steps':steps,'current_index':idx,'current':steps[idx],'balance_cents':balance}


def _timeline(db,lid):
    items=[]
    def add(ts,kind,title,detail='',status=''):
        if ts:items.append({'at':ts,'kind':kind,'title':title,'detail':detail,'status':status})
    for x in rows(db,'SELECT * FROM messages WHERE lead_id=?',(lid,)):add(x['created_at'],'message',x['kind'],x['subject'] or x['body'][:220])
    for x in rows(db,'SELECT * FROM tasks WHERE lead_id=?',(lid,)):add(x['created_at'],'task',x['title'],'Due '+(x['due_date'] or 'not set'),x['status'])
    for x in rows(db,'SELECT * FROM quotes WHERE lead_id=?',(lid,)):add(x['created_at'],'quote','Estimate revision '+str(x['revision']),'',x['status'])
    for x in rows(db,'SELECT * FROM jobs WHERE lead_id=?',(lid,)):add(x['created_at'],'job',x['title']+' - '+x['phase'],_local_date(x['start_at']),x['status'])
    for x in rows(db,'SELECT * FROM payments WHERE lead_id=?',(lid,)):add(x['created_at'],'payment',x['purpose'],f"CA${x['amount_cents']/100:,.2f} - {x['method']}",'Verified' if x['verified'] else 'Pending verification')
    for x in rows(db,'SELECT * FROM client_changes WHERE lead_id=?',(lid,)):add(x['created_at'],'client change','Client portal update','',x['status'])
    for x in rows(db,'SELECT * FROM document_approvals WHERE lead_id=?',(lid,)):add(x['created_at'],'document','Client acknowledgement','',x['status'])
    for x in rows(db,'SELECT * FROM calls WHERE lead_id=?',(lid,)):add(x['created_at'],'call','Call / intake event','')
    for x in rows(db,"""SELECT i.*,j.title,r.issued_at FROM job_invoices i JOIN jobs j ON j.id=i.job_id
      LEFT JOIN invoice_revisions r ON r.invoice_id=i.id AND r.revision=i.revision WHERE j.lead_id=?""",(lid,)):
        add(x.get('issued_at') or x['created_at'],'invoice',x['number'],x['title'],x['status'])
    return sorted(items,key=lambda x:x['at'],reverse=True)


def register_ux_features(app,path,auth):
    @app.get('/api/ux/today')
    def today_view(u=Depends(auth)):
        require_role(u,OFFICE)
        today=datetime.now(TORONTO).date().isoformat()
        with closing(connect(path)) as db:
            jobs_all=rows(db,'SELECT * FROM jobs WHERE status!=\'Cancelled\' ORDER BY start_at')
            jobs=[];attention=[]
            for j in jobs_all:
                if _local_date(j['start_at'])==today:
                    ready=_job_readiness(db,j);j['readiness']=ready
                    j['crew_names']=[r['name'] for r in rows(db,"SELECT e.name FROM assignments a JOIN employees e ON e.id=a.resource_id WHERE a.job_id=? AND a.resource_type='employee' ORDER BY e.name",(j['id'],))]
                    jobs.append(j)
                    if not ready['ready']:attention.append({'type':'job','id':j['id'],'title':j['title']+' needs attention','detail':', '.join(ready['missing']),'priority':'High'})
            pending_off=rows(db,"""SELECT o.id,o.start_date,o.end_date,e.name employee_name FROM time_off_requests o JOIN employees e ON e.id=o.employee_id WHERE o.status='Pending' ORDER BY o.requested_at""")
            for r in pending_off:attention.append({'type':'timeoff','id':r['id'],'title':r['employee_name']+' requested time off','detail':r['start_date']+' to '+r['end_date'],'priority':'Normal'})
            due=rows(db,"SELECT t.*,l.client_name FROM tasks t LEFT JOIN leads l ON l.id=t.lead_id WHERE t.status!='Done' AND t.historical=0 AND (t.due_date='' OR t.due_date<=?) ORDER BY t.due_date",(today,))
            for r in due[:20]:attention.append({'type':'task','id':r['id'],'lead_id':r['lead_id'],'title':r['title'],'detail':r.get('client_name') or 'Internal task','priority':'Normal'})
            pending_changes=rows(db,"SELECT c.id,c.lead_id,l.client_name,c.created_at FROM client_changes c JOIN leads l ON l.id=c.lead_id WHERE c.status='Pending' ORDER BY c.created_at")
            for r in pending_changes:attention.append({'type':'client-change','id':r['id'],'lead_id':r['lead_id'],'title':r['client_name']+' updated their portal','detail':'Review before changing accepted scope.','priority':'Normal'})
            unverified=rows(db,"SELECT p.*,l.client_name FROM payments p JOIN leads l ON l.id=p.lead_id WHERE p.verified=0 AND p.amount_cents>0 ORDER BY p.created_at")
            for r in unverified:attention.append({'type':'payment','id':r['id'],'lead_id':r['lead_id'],'title':'Payment needs verification','detail':f"{r['client_name']} - CA${r['amount_cents']/100:,.2f}",'priority':'High'})
            # Calls and voicemails belong in CRM calls/follow-up tasks, not in the Today
            # exception queue. Keep only unmatched payment notices here for human review.
            gmail_payment=rows(db,"SELECT * FROM integration_events WHERE event_type IN ('gmail_bank_payment','gmail_wix_payment') AND status='Needs review' ORDER BY created_at DESC")
            for r in gmail_payment[:20]:
                p=json.loads(r['payload_json']).get('parsed',{})
                detail=(p.get('payer','')+' -             for r in maint:attention.append({'type':'fleet','id':r['id'],'title':r['vehicle_name']+' maintenance due','detail':r['title']+' - '+r['due_date'],'priority':'High'})
            stats={
              'jobs_today':len(jobs),'attention':len(attention),'pending_time_off':len(pending_off),
              'unverified_payments':len(unverified)+len(gmail_payment),'open_followups':len(due),'pending_client_changes':len(pending_changes)
            }
        attention.sort(key=lambda x:(0 if x['priority']=='High' else 1,x['title']))
        return {'date':today,'stats':stats,'jobs':jobs,'attention':attention[:60]}

    @app.get('/api/ux/search')
    def global_search(q:str='',u=Depends(auth)):
        require_role(u,OFFICE)
        q=(q or '').strip()
        if len(q)<2:return []
        like='%'+q.lower()+'%'
        with closing(connect(path)) as db:
            out=[]
            for r in rows(db,"SELECT id,client_name,email,phone,pickup,dropoff,move_date FROM leads WHERE lower(client_name||' '||email||' '||phone||' '||pickup||' '||dropoff) LIKE ? LIMIT 12",(like,)):
                out.append({'type':'client','id':r['id'],'title':r['client_name'],'detail':' · '.join(x for x in (r['phone'],r['move_date'],r['pickup']) if x)})
            for r in rows(db,"SELECT id,lead_id,title,phase,start_at,status FROM jobs WHERE lower(title||' '||phase||' '||id) LIKE ? LIMIT 8",(like,)):
                out.append({'type':'job','id':r['id'],'lead_id':r['lead_id'],'title':r['title'],'detail':r['phase']+' · '+_local_date(r['start_at'])+' · '+r['status']})
            for r in rows(db,"SELECT i.id,i.number,i.status,j.lead_id,j.title FROM job_invoices i JOIN jobs j ON j.id=i.job_id WHERE lower(i.number||' '||j.title) LIKE ? LIMIT 8",(like,)):
                out.append({'type':'invoice','id':r['id'],'lead_id':r['lead_id'],'title':r['number'],'detail':r['title']+' · '+r['status']})
        return out[:25]

    @app.get('/api/ux/project/{lid}')
    def project_overview(lid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            l=one(db,'SELECT * FROM leads WHERE id=?',(lid,))
            if not l:raise HTTPException(404,'Client project not found.')
            jobs=rows(db,'SELECT * FROM jobs WHERE lead_id=? ORDER BY start_at',(lid,))
            for j in jobs:j['readiness']=_job_readiness(db,j)
            return {'lifecycle':_project_lifecycle(db,lid),'timeline':_timeline(db,lid),'jobs':jobs}

    @app.get('/api/ux/jobs/{jid}/readiness')
    def readiness(jid:str,u=Depends(auth)):
        require_role(u,OFFICE|{'crew'})
        with closing(connect(path)) as db:
            j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
            if not j:raise HTTPException(404,'Job not found.')
            if u['role']=='crew' and not one(db,"SELECT 1 FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id') or '')):
                raise HTTPException(403,'Assigned team only.')
            return _job_readiness(db,j)

    @app.get('/api/ux/jobs/{jid}/crew-hours')
    def crew_hours(jid:str,u=Depends(auth)):
        if u['role'] not in OFFICE|{'crew'}:raise HTTPException(403,'Crown staff only.')
        with closing(connect(path)) as db:
            j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
            if not j:raise HTTPException(404,'Job not found.')
            if u['role']=='crew' and not one(db,"SELECT 1 FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id') or '')):
                raise HTTPException(403,'Assigned team only.')
            team=rows(db,"""SELECT e.id,e.name,e.role,t.id time_id,t.service_minutes,t.transport_minutes,t.note,t.status
              FROM assignments a JOIN employees e ON e.id=a.resource_id AND a.resource_type='employee'
              LEFT JOIN times t ON t.job_id=a.job_id AND t.employee_id=e.id WHERE a.job_id=? ORDER BY e.name""",(jid,))
            inv=one(db,'SELECT actual_json FROM job_invoices WHERE job_id=?',(jid,))
            paid=one(db,'SELECT paid_transport_minutes FROM paid_work_terms WHERE job_id=?',(jid,))
        suggested=None
        if inv:
            try:suggested=float(json.loads(inv['actual_json']).get('service_hours') or 0)+(float(paid['paid_transport_minutes'])/60 if paid and paid['paid_transport_minutes'] is not None else 0)
            except Exception:suggested=None
        for e in team:e['total_hours']=round((int(e['service_minutes'] or 0)+int(e['transport_minutes'] or 0))/60,2) if e['time_id'] else None
        return {'job':{'id':j['id'],'title':j['title'],'status':j['status']},'employees':team,'suggested_total_hours':suggested}

    @app.post('/api/ux/jobs/{jid}/crew-hours')
    def crew_hours_save(jid:str,data:dict,u=Depends(auth)):
        manager=u['role'] in {'owner','accounting','dispatch'}
        with closing(connect(path)) as checkdb:
            is_lead=bool(u['role']=='crew' and u.get('employee_id') and one(checkdb,'SELECT team_lead FROM employee_directory_profiles WHERE employee_id=? AND team_lead=1',(u['employee_id'],)))
        if not manager and not is_lead:raise HTTPException(403,'Only managers or assigned Team Leads can submit team hours.')
        selected=data.get('employee_ids') or []
        if not isinstance(selected,list) or not selected or len(set(selected))!=len(selected):raise HTTPException(422,'Select the employees receiving the team hours.')
        try:common=float(data.get('team_hours'))
        except Exception:raise HTTPException(422,'Enter the team hours.')
        if common<0 or common>24 or round(common*60)!=common*60:raise HTTPException(422,'Use valid hours that resolve to whole minutes.')
        exceptions=data.get('exceptions') or {}
        if not isinstance(exceptions,dict):raise HTTPException(422,'Invalid individual adjustments.')
        note=text(data.get('note',''),'Optional team note',2000)
        with transaction(path) as db:
            j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
            if not j:raise HTTPException(404,'Job not found.')
            if j['status'] not in ('In progress','Completed'):raise HTTPException(409,'Enter team hours after the job has started.')
            if is_lead and not one(db,"SELECT 1 FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id'))):raise HTTPException(403,'Team Leads can enter hours only for jobs they are assigned to.')
            assigned={r['resource_id'] for r in rows(db,"SELECT resource_id FROM assignments WHERE job_id=? AND resource_type='employee'",(jid,))}
            if set(selected)-assigned:raise HTTPException(422,'Selected employees must be assigned to this job.')
            changed=[]
            for eid in selected:
                hrs=common
                if eid in exceptions:
                    try:hrs=float(exceptions[eid].get('total_hours'))
                    except Exception:raise HTTPException(422,'Review the individual hours.')
                    if hrs<0 or hrs>24 or round(hrs*60)!=hrs*60:raise HTTPException(422,'Individual hours must resolve to whole minutes.')
                existing=one(db,'SELECT * FROM times WHERE job_id=? AND employee_id=?',(jid,eid))
                if existing and existing['status']=='Approved' and not manager:raise HTTPException(409,'Approved payroll hours can only be corrected by a payroll manager.')
                minutes=round(hrs*60)
                individual_note=text((exceptions.get(eid) or {}).get('note',''),'Optional adjustment note',1000) or note
                if existing:
                    new_status='Submitted' if existing['status']!='Approved' else 'Approved'
                    approved_by=existing.get('approved_by') if new_status=='Approved' else None
                    db.execute("UPDATE times SET service_minutes=?,transport_minutes=0,note=?,status=?,submitted_by=?,approved_by=? WHERE id=?",(minutes,individual_note,new_status,u['id'],approved_by,existing['id']))
                else:
                    insert(db,'times',dict(id=uid('TIME'),job_id=jid,employee_id=eid,service_minutes=minutes,transport_minutes=0,note=individual_note,status='Submitted',submitted_by=u['id'],created_at=now()))
                changed.append({'employee_id':eid,'total_minutes':minutes})
            audit(db,u['id'],'Crew hours entered in one batch','times',jid,{'entries':changed})
        return {'ok':True,'submitted':len(changed)}

    @app.get('/api/ux/employee-directory')
    def employee_directory(start:str='',end:str='',u=Depends(auth)):
        # Everyone gets the internal contact directory; only payroll/management gets private HR/pay fields.
        if u['role'] not in OFFICE|{'crew'}:raise HTTPException(403,'Directory access is restricted to Crown staff.')
        end_d=_date_arg(end,'end date') if end else date.today(); start_d=_date_arg(start,'start date') if start else end_d-timedelta(days=13)
        if end_d<start_d or (end_d-start_d).days>366:raise HTTPException(422,'Choose a payroll range up to one year.')
        private=u['role'] in PRIVATE_HR
        with transaction(path) as db:
            for e in rows(db,'SELECT * FROM employees'):_profile_seed(db,e)
            employees=rows(db,"""SELECT e.*,p.email,p.phone,p.home_address,p.emergency_name,p.emergency_phone,p.start_date,p.notes,
                p.license_original_name,p.license_mime,p.team_lead FROM employees e LEFT JOIN employee_directory_profiles p ON p.employee_id=e.id ORDER BY e.name""")
            times=rows(db,"""SELECT t.*,j.start_at FROM times t JOIN jobs j ON j.id=t.job_id WHERE j.status!='Cancelled'""")
        by={e['id']:{'minutes':0,'jobs':set(),'pending':0} for e in employees}
        for t in times:
            day=_local_date(t['start_at'])
            if start_d.isoformat()<=day<=end_d.isoformat() and t['employee_id'] in by:
                by[t['employee_id']]['minutes']+=int(t['service_minutes'] or 0)+int(t['transport_minutes'] or 0)
                by[t['employee_id']]['jobs'].add(t['job_id'])
                if t['status']!='Approved':by[t['employee_id']]['pending']+=1
        result=[]
        for e in employees:
            base={'id':e['id'],'full_name':e['name'],'phone':e.get('phone') or '',
                  'emergency_contact':{'name':e.get('emergency_name') or '', 'phone':e.get('emergency_phone') or ''},
                  'role':e['role'],'active':bool(e['active'])}
            if private:
                base.update({'email':e.get('email') or '','home_address':e.get('home_address') or '',
                  'start_date':e.get('start_date') or '','driver_verified':bool(e['driver_verified']),
                  'hourly_cents':e['hourly_cents'],'license_uploaded':bool(e.get('license_original_name')),
                  'license_name':e.get('license_original_name') or '','team_lead':bool(e.get('team_lead')),'notes':e.get('notes') or '',
                  'current_hours':round(by[e['id']]['minutes']/60,2),'current_jobs':len(by[e['id']]['jobs']),
                  'pending_time_entries':by[e['id']]['pending']})
            result.append(base)
        return {'start':start_d.isoformat(),'end':end_d.isoformat(),'private':private,'employees':result}

    @app.patch('/api/ux/employee-directory/{eid}')
    def employee_update(eid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        with transaction(path) as db:
            emp=one(db,'SELECT * FROM employees WHERE id=?',(eid,))
            if not emp:raise HTTPException(404,'Employee not found.')
            _profile_seed(db,emp)
            rate=data.get('hourly_rate')
            if rate is not None:
                try:rate_cents=round(float(rate)*100)
                except Exception:raise HTTPException(422,'Enter a valid hourly pay rate.')
                if rate_cents<0 or rate_cents>100000:raise HTTPException(422,'Review the hourly pay rate.')
                db.execute('UPDATE employees SET hourly_cents=?,role=?,active=?,driver_verified=? WHERE id=?',(rate_cents,text(data.get('role',emp['role']),'Role',120),int(data.get('active',bool(emp['active']))),int(data.get('driver_verified',bool(emp['driver_verified']))),eid))
            db.execute("""UPDATE employee_directory_profiles SET email=?,phone=?,home_address=?,emergency_name=?,emergency_phone=?,start_date=?,team_lead=?,notes=?,updated_by=?,updated_at=? WHERE employee_id=?""",
              (text(data.get('email',''),'Email',240),text(data.get('phone',''),'Phone',80),text(data.get('home_address',''),'Home address',500),
               text(data.get('emergency_name',''),'Emergency contact',180),text(data.get('emergency_phone',''),'Emergency phone',80),
               text(data.get('start_date',''),'Start date',20),int(bool(data.get('team_lead'))),text(data.get('notes',''),'Notes',3000),u['id'],now(),eid))
            audit(db,u['id'],'Employee profile updated','employees',eid)
        return {'ok':True}

    @app.post('/api/ux/employee-directory/{eid}/license')
    async def employee_license(eid:str,file:UploadFile=File(...),u=Depends(auth)):
        require_role(u,MANAGERS)
        allowed={'image/jpeg':'.jpg','image/png':'.png','application/pdf':'.pdf'}
        mime=(file.content_type or '').lower()
        if mime not in allowed:raise HTTPException(422,'Upload a JPG, PNG, or PDF licence file.')
        content=await file.read(8*1024*1024+1)
        if len(content)>8*1024*1024:raise HTTPException(413,'Licence file must be 8 MB or smaller.')
        original=text(file.filename or 'license','Filename',240)
        folder=Path(path).parent/'employee_uploads';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        stored=secrets.token_hex(18)+allowed[mime];(folder/stored).write_bytes(content)
        with transaction(path) as db:
            emp=one(db,'SELECT * FROM employees WHERE id=?',(eid,))
            if not emp:raise HTTPException(404,'Employee not found.')
            _profile_seed(db,emp);old=one(db,'SELECT license_stored_name FROM employee_directory_profiles WHERE employee_id=?',(eid,))
            db.execute('UPDATE employee_directory_profiles SET license_original_name=?,license_stored_name=?,license_mime=?,updated_by=?,updated_at=? WHERE employee_id=?',(original,stored,mime,u['id'],now(),eid))
            audit(db,u['id'],'Driver licence file uploaded','employees',eid,{'filename':original,'mime':mime})
        if old and old.get('license_stored_name'):
            try:(folder/old['license_stored_name']).unlink(missing_ok=True)
            except Exception:pass
        return {'ok':True,'name':original}

    @app.get('/api/ux/employee-directory/{eid}/license')
    def employee_license_get(eid:str,u=Depends(auth)):
        if u['role'] not in PRIVATE_HR:raise HTTPException(403,'Licence files are manager/payroll-private.')
        with closing(connect(path)) as db:p=one(db,'SELECT * FROM employee_directory_profiles WHERE employee_id=?',(eid,))
        if not p or not p['license_stored_name']:raise HTTPException(404,'No licence file uploaded.')
        f=Path(path).parent/'employee_uploads'/p['license_stored_name']
        if not f.exists():raise HTTPException(404,'Licence file is missing from storage.')
        return FileResponse(f,media_type=p['license_mime'],filename=p['license_original_name'])

    @app.get('/api/ux/payroll-summary')
    def payroll_summary(start:str='',end:str='',u=Depends(auth)):
        if u['role'] not in OFFICE:raise HTTPException(403,'Payroll summary is restricted to office staff.')
        end_d=_date_arg(end,'end date') if end else date.today();start_d=_date_arg(start,'start date') if start else end_d-timedelta(days=13)
        if end_d<start_d or (end_d-start_d).days>366:raise HTTPException(422,'Choose a payroll range up to one year.')
        private=u['role'] in PRIVATE_HR
        with closing(connect(path)) as db:
            emps=rows(db,'SELECT * FROM employees WHERE active=1 ORDER BY name')
            times=rows(db,"""SELECT t.*,j.title,j.start_at FROM times t JOIN jobs j ON j.id=t.job_id WHERE j.status!='Cancelled' ORDER BY j.start_at""")
        details={e['id']:[] for e in emps}
        for t in times:
            day=_local_date(t['start_at'])
            if start_d.isoformat()<=day<=end_d.isoformat() and t['employee_id'] in details:
                mins=int(t['service_minutes'] or 0)+int(t['transport_minutes'] or 0)
                details[t['employee_id']].append({'time_id':t['id'],'job_id':t['job_id'],'job_title':t['title'],'date':day,'hours':round(mins/60,2),'status':t['status']})
        out=[]
        for e in emps:
            d=details[e['id']];hours=round(sum(x['hours'] for x in d),2);pending=sum(x['status']!='Approved' for x in d)
            r={'employee_id':e['id'],'employee_name':e['name'],'jobs':len({x['job_id'] for x in d}),'hours':hours,'pending_entries':pending,'status':'Ready' if d and not pending else ('Needs review' if d else 'No hours'),'details':d}
            if private:r.update({'hourly_cents':e['hourly_cents'],'gross_cents':round(hours*e['hourly_cents'])})
            out.append(r)
        return {'start':start_d.isoformat(),'end':end_d.isoformat(),'private':private,'employees':out,'total_hours':round(sum(x['hours'] for x in out),2)}

    @app.post('/api/ux/payroll-summary/approve')
    def payroll_approve(data:dict,u=Depends(auth)):
        if u['role'] not in PRIVATE_HR:raise HTTPException(403,'Only payroll managers can approve hours.')
        start=_date_arg(data.get('start'),'start date').isoformat();end=_date_arg(data.get('end'),'end date').isoformat()
        with transaction(path) as db:
            items=rows(db,"SELECT t.id,j.start_at FROM times t JOIN jobs j ON j.id=t.job_id WHERE t.status!='Approved' AND j.status!='Cancelled'")
            ids=[x['id'] for x in items if start<=_local_date(x['start_at'])<=end]
            for tid in ids:db.execute("UPDATE times SET status='Approved',approved_by=? WHERE id=?",(u['id'],tid))
            audit(db,u['id'],'Payroll range hours approved','times',start+'..'+end,{'count':len(ids)})
        return {'ok':True,'approved':len(ids)}


    @app.patch('/api/ux/payroll-time/{tid}')
    def payroll_time_edit(tid:str,data:dict,u=Depends(auth)):
        if u['role'] not in PRIVATE_HR:raise HTTPException(403,'Only payroll managers can correct payroll hours.')
        try:hours=float(data.get('hours'))
        except Exception:raise HTTPException(422,'Enter valid total hours.')
        if hours<0 or hours>24 or round(hours*60)!=hours*60:raise HTTPException(422,'Hours must resolve to whole minutes.')
        with transaction(path) as db:
            t=one(db,'SELECT * FROM times WHERE id=?',(tid,))
            if not t:raise HTTPException(404,'Time entry not found.')
            before=int(t['service_minutes'] or 0)+int(t['transport_minutes'] or 0)
            db.execute('UPDATE times SET service_minutes=?,transport_minutes=0 WHERE id=?',(round(hours*60),tid))
            audit(db,u['id'],'Payroll hours corrected','times',tid,{'before_minutes':before,'after_minutes':round(hours*60)})
        return {'ok':True}

    @app.get('/api/calls')
    def calls_feed(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            items=rows(db,"""SELECT c.*,l.client_name FROM calls c
              LEFT JOIN leads l ON l.id=c.lead_id
              ORDER BY c.created_at DESC LIMIT 100""")
        for item in items:
            raw=item.pop('payload_json','{}')
            try:item['details']=json.loads(raw or '{}')
            except Exception:item['details']={}
        return items

    @app.get('/api/ux/inbox')
    def inbox(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            return rows(db,"""SELECT m.*,l.client_name,c.claimed_by,c.claimed_at,c.resolved_at,u2.name claimed_name
              FROM messages m JOIN leads l ON l.id=m.lead_id
              LEFT JOIN inbox_claims c ON c.message_id=m.id LEFT JOIN users u2 ON u2.id=c.claimed_by
              WHERE lower(m.kind) LIKE '%incoming%' OR lower(m.kind) LIKE '%email%'
              ORDER BY m.created_at DESC LIMIT 100""")

    @app.post('/api/ux/inbox/{mid}/claim')
    def inbox_claim(mid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            m=one(db,'SELECT id,lead_id FROM messages WHERE id=?',(mid,))
            if not m:raise HTTPException(404,'Message not found.')
            lead=one(db,'SELECT id,assigned_to FROM leads WHERE id=?',(m['lead_id'],))
            if lead and lead.get('assigned_to') not in ('',u['id'],u['name']):
                raise HTTPException(409,'This client is already assigned to another team member.')
            old=one(db,'SELECT * FROM inbox_claims WHERE message_id=?',(mid,))
            if old and old.get('claimed_by')!=u['id'] and not old.get('resolved_at'):raise HTTPException(409,'This email is already claimed by another team member.')
            if old:db.execute('UPDATE inbox_claims SET claimed_by=?,claimed_at=?,resolved_at=NULL WHERE message_id=?',(u['id'],now(),mid))
            else:insert(db,'inbox_claims',dict(message_id=mid,claimed_by=u['id'],claimed_at=now(),resolved_at=None))
            if lead and not lead.get('assigned_to'):
                db.execute('UPDATE leads SET assigned_to=?,version=version+1,updated_at=? WHERE id=?',(u['id'],now(),lead['id']))
                db.execute("UPDATE tasks SET owner=? WHERE lead_id=? AND status!='Done' AND (owner='' OR owner IS NULL)",(u['id'],lead['id']))
            audit(db,u['id'],'Client email claimed and lead assigned','messages',mid,{'lead_id':m['lead_id'],'open_followups_assigned':True})
        return {'ok':True,'lead_id':m['lead_id']}

    @app.post('/api/ux/inbox/{mid}/resolve')
    def inbox_resolve(mid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            c=one(db,'SELECT * FROM inbox_claims WHERE message_id=?',(mid,))
            if not c:raise HTTPException(409,'Claim the email before marking it answered.')
            if c['claimed_by']!=u['id'] and u['role'] not in MANAGERS:raise HTTPException(403,'Only the claimant or a manager can mark this answered.')
            db.execute('UPDATE inbox_claims SET resolved_at=? WHERE message_id=?',(now(),mid));audit(db,u['id'],'Client email marked answered','messages',mid)
        return {'ok':True}

    @app.get('/api/ux/company-communications')
    def company_comms(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:return settings(db,'company_communications',{})

    @app.patch('/api/ux/company-communications')
    def company_comms_update(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        connected=text(data.get('connected_mailbox',''),'Connected mailbox',240).lower();send_as=text(data.get('send_as',''),'Send-as address',240).lower();reply_to=text(data.get('reply_to',''),'Reply-to address',240).lower()
        if '@' not in connected or '@' not in send_as or '@' not in reply_to:raise HTTPException(422,'Enter valid company email addresses.')
        cfg={'connected_mailbox':connected,'send_as':send_as,'reply_to':reply_to,'default_from':send_as,'secondary_inbox':connected,'client_email_rule':'Store mail in the connected Gmail mailbox and send as the Crown Movers domain alias after Google verifies the alias.','approved':bool(data.get('approved')),'note':text(data.get('note',''),'Note',2000)}
        with transaction(path) as db:put_setting(db,'company_communications',cfg);audit(db,u['id'],'Company communication defaults updated','settings','company_communications')
        return cfg
+str(p.get('amount',''))).strip(' -')
                attention.append({'type':'gmail','id':r['id'],'title':'Gmail payment notice needs review','detail':detail,'priority':'High'})
            maint=rows(db,"SELECT m.*,v.name vehicle_name FROM maintenance m JOIN vehicles v ON v.id=m.vehicle_id WHERE m.status='Open' AND m.due_date!='' AND m.due_date<=? ORDER BY m.due_date",(today,))
            for r in maint:attention.append({'type':'fleet','id':r['id'],'title':r['vehicle_name']+' maintenance due','detail':r['title']+' - '+r['due_date'],'priority':'High'})
            stats={
              'jobs_today':len(jobs),'attention':len(attention),'pending_time_off':len(pending_off),
              'unverified_payments':len(unverified)+len(gmail_payment),'open_followups':len(due)+len(gmail_calls),'pending_client_changes':len(pending_changes)
            }
        attention.sort(key=lambda x:(0 if x['priority']=='High' else 1,x['title']))
        return {'date':today,'stats':stats,'jobs':jobs,'attention':attention[:60]}

    @app.get('/api/ux/search')
    def global_search(q:str='',u=Depends(auth)):
        require_role(u,OFFICE)
        q=(q or '').strip()
        if len(q)<2:return []
        like='%'+q.lower()+'%'
        with closing(connect(path)) as db:
            out=[]
            for r in rows(db,"SELECT id,client_name,email,phone,pickup,dropoff,move_date FROM leads WHERE lower(client_name||' '||email||' '||phone||' '||pickup||' '||dropoff) LIKE ? LIMIT 12",(like,)):
                out.append({'type':'client','id':r['id'],'title':r['client_name'],'detail':' · '.join(x for x in (r['phone'],r['move_date'],r['pickup']) if x)})
            for r in rows(db,"SELECT id,lead_id,title,phase,start_at,status FROM jobs WHERE lower(title||' '||phase||' '||id) LIKE ? LIMIT 8",(like,)):
                out.append({'type':'job','id':r['id'],'lead_id':r['lead_id'],'title':r['title'],'detail':r['phase']+' · '+_local_date(r['start_at'])+' · '+r['status']})
            for r in rows(db,"SELECT i.id,i.number,i.status,j.lead_id,j.title FROM job_invoices i JOIN jobs j ON j.id=i.job_id WHERE lower(i.number||' '||j.title) LIKE ? LIMIT 8",(like,)):
                out.append({'type':'invoice','id':r['id'],'lead_id':r['lead_id'],'title':r['number'],'detail':r['title']+' · '+r['status']})
        return out[:25]

    @app.get('/api/ux/project/{lid}')
    def project_overview(lid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            l=one(db,'SELECT * FROM leads WHERE id=?',(lid,))
            if not l:raise HTTPException(404,'Client project not found.')
            jobs=rows(db,'SELECT * FROM jobs WHERE lead_id=? ORDER BY start_at',(lid,))
            for j in jobs:j['readiness']=_job_readiness(db,j)
            return {'lifecycle':_project_lifecycle(db,lid),'timeline':_timeline(db,lid),'jobs':jobs}

    @app.get('/api/ux/jobs/{jid}/readiness')
    def readiness(jid:str,u=Depends(auth)):
        require_role(u,OFFICE|{'crew'})
        with closing(connect(path)) as db:
            j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
            if not j:raise HTTPException(404,'Job not found.')
            if u['role']=='crew' and not one(db,"SELECT 1 FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id') or '')):
                raise HTTPException(403,'Assigned team only.')
            return _job_readiness(db,j)

    @app.get('/api/ux/jobs/{jid}/crew-hours')
    def crew_hours(jid:str,u=Depends(auth)):
        if u['role'] not in OFFICE|{'crew'}:raise HTTPException(403,'Crown staff only.')
        with closing(connect(path)) as db:
            j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
            if not j:raise HTTPException(404,'Job not found.')
            if u['role']=='crew' and not one(db,"SELECT 1 FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id') or '')):
                raise HTTPException(403,'Assigned team only.')
            team=rows(db,"""SELECT e.id,e.name,e.role,t.id time_id,t.service_minutes,t.transport_minutes,t.note,t.status
              FROM assignments a JOIN employees e ON e.id=a.resource_id AND a.resource_type='employee'
              LEFT JOIN times t ON t.job_id=a.job_id AND t.employee_id=e.id WHERE a.job_id=? ORDER BY e.name""",(jid,))
            inv=one(db,'SELECT actual_json FROM job_invoices WHERE job_id=?',(jid,))
            paid=one(db,'SELECT paid_transport_minutes FROM paid_work_terms WHERE job_id=?',(jid,))
        suggested=None
        if inv:
            try:suggested=float(json.loads(inv['actual_json']).get('service_hours') or 0)+(float(paid['paid_transport_minutes'])/60 if paid and paid['paid_transport_minutes'] is not None else 0)
            except Exception:suggested=None
        for e in team:e['total_hours']=round((int(e['service_minutes'] or 0)+int(e['transport_minutes'] or 0))/60,2) if e['time_id'] else None
        return {'job':{'id':j['id'],'title':j['title'],'status':j['status']},'employees':team,'suggested_total_hours':suggested}

    @app.post('/api/ux/jobs/{jid}/crew-hours')
    def crew_hours_save(jid:str,data:dict,u=Depends(auth)):
        manager=u['role'] in {'owner','accounting','dispatch'}
        with closing(connect(path)) as checkdb:
            is_lead=bool(u['role']=='crew' and u.get('employee_id') and one(checkdb,'SELECT team_lead FROM employee_directory_profiles WHERE employee_id=? AND team_lead=1',(u['employee_id'],)))
        if not manager and not is_lead:raise HTTPException(403,'Only managers or assigned Team Leads can submit team hours.')
        selected=data.get('employee_ids') or []
        if not isinstance(selected,list) or not selected or len(set(selected))!=len(selected):raise HTTPException(422,'Select the employees receiving the team hours.')
        try:common=float(data.get('team_hours'))
        except Exception:raise HTTPException(422,'Enter the team hours.')
        if common<0 or common>24 or round(common*60)!=common*60:raise HTTPException(422,'Use valid hours that resolve to whole minutes.')
        exceptions=data.get('exceptions') or {}
        if not isinstance(exceptions,dict):raise HTTPException(422,'Invalid individual adjustments.')
        note=text(data.get('note',''),'Optional team note',2000)
        with transaction(path) as db:
            j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
            if not j:raise HTTPException(404,'Job not found.')
            if j['status'] not in ('In progress','Completed'):raise HTTPException(409,'Enter team hours after the job has started.')
            if is_lead and not one(db,"SELECT 1 FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id'))):raise HTTPException(403,'Team Leads can enter hours only for jobs they are assigned to.')
            assigned={r['resource_id'] for r in rows(db,"SELECT resource_id FROM assignments WHERE job_id=? AND resource_type='employee'",(jid,))}
            if set(selected)-assigned:raise HTTPException(422,'Selected employees must be assigned to this job.')
            changed=[]
            for eid in selected:
                hrs=common
                if eid in exceptions:
                    try:hrs=float(exceptions[eid].get('total_hours'))
                    except Exception:raise HTTPException(422,'Review the individual hours.')
                    if hrs<0 or hrs>24 or round(hrs*60)!=hrs*60:raise HTTPException(422,'Individual hours must resolve to whole minutes.')
                existing=one(db,'SELECT * FROM times WHERE job_id=? AND employee_id=?',(jid,eid))
                if existing and existing['status']=='Approved' and not manager:raise HTTPException(409,'Approved payroll hours can only be corrected by a payroll manager.')
                minutes=round(hrs*60)
                individual_note=text((exceptions.get(eid) or {}).get('note',''),'Optional adjustment note',1000) or note
                if existing:
                    new_status='Submitted' if existing['status']!='Approved' else 'Approved'
                    approved_by=existing.get('approved_by') if new_status=='Approved' else None
                    db.execute("UPDATE times SET service_minutes=?,transport_minutes=0,note=?,status=?,submitted_by=?,approved_by=? WHERE id=?",(minutes,individual_note,new_status,u['id'],approved_by,existing['id']))
                else:
                    insert(db,'times',dict(id=uid('TIME'),job_id=jid,employee_id=eid,service_minutes=minutes,transport_minutes=0,note=individual_note,status='Submitted',submitted_by=u['id'],created_at=now()))
                changed.append({'employee_id':eid,'total_minutes':minutes})
            audit(db,u['id'],'Crew hours entered in one batch','times',jid,{'entries':changed})
        return {'ok':True,'submitted':len(changed)}

    @app.get('/api/ux/employee-directory')
    def employee_directory(start:str='',end:str='',u=Depends(auth)):
        # Everyone gets the internal contact directory; only payroll/management gets private HR/pay fields.
        if u['role'] not in OFFICE|{'crew'}:raise HTTPException(403,'Directory access is restricted to Crown staff.')
        end_d=_date_arg(end,'end date') if end else date.today(); start_d=_date_arg(start,'start date') if start else end_d-timedelta(days=13)
        if end_d<start_d or (end_d-start_d).days>366:raise HTTPException(422,'Choose a payroll range up to one year.')
        private=u['role'] in PRIVATE_HR
        with transaction(path) as db:
            for e in rows(db,'SELECT * FROM employees'):_profile_seed(db,e)
            employees=rows(db,"""SELECT e.*,p.email,p.phone,p.home_address,p.emergency_name,p.emergency_phone,p.start_date,p.notes,
                p.license_original_name,p.license_mime,p.team_lead FROM employees e LEFT JOIN employee_directory_profiles p ON p.employee_id=e.id ORDER BY e.name""")
            times=rows(db,"""SELECT t.*,j.start_at FROM times t JOIN jobs j ON j.id=t.job_id WHERE j.status!='Cancelled'""")
        by={e['id']:{'minutes':0,'jobs':set(),'pending':0} for e in employees}
        for t in times:
            day=_local_date(t['start_at'])
            if start_d.isoformat()<=day<=end_d.isoformat() and t['employee_id'] in by:
                by[t['employee_id']]['minutes']+=int(t['service_minutes'] or 0)+int(t['transport_minutes'] or 0)
                by[t['employee_id']]['jobs'].add(t['job_id'])
                if t['status']!='Approved':by[t['employee_id']]['pending']+=1
        result=[]
        for e in employees:
            base={'id':e['id'],'full_name':e['name'],'phone':e.get('phone') or '',
                  'emergency_contact':{'name':e.get('emergency_name') or '', 'phone':e.get('emergency_phone') or ''},
                  'role':e['role'],'active':bool(e['active'])}
            if private:
                base.update({'email':e.get('email') or '','home_address':e.get('home_address') or '',
                  'start_date':e.get('start_date') or '','driver_verified':bool(e['driver_verified']),
                  'hourly_cents':e['hourly_cents'],'license_uploaded':bool(e.get('license_original_name')),
                  'license_name':e.get('license_original_name') or '','team_lead':bool(e.get('team_lead')),'notes':e.get('notes') or '',
                  'current_hours':round(by[e['id']]['minutes']/60,2),'current_jobs':len(by[e['id']]['jobs']),
                  'pending_time_entries':by[e['id']]['pending']})
            result.append(base)
        return {'start':start_d.isoformat(),'end':end_d.isoformat(),'private':private,'employees':result}

    @app.patch('/api/ux/employee-directory/{eid}')
    def employee_update(eid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        with transaction(path) as db:
            emp=one(db,'SELECT * FROM employees WHERE id=?',(eid,))
            if not emp:raise HTTPException(404,'Employee not found.')
            _profile_seed(db,emp)
            rate=data.get('hourly_rate')
            if rate is not None:
                try:rate_cents=round(float(rate)*100)
                except Exception:raise HTTPException(422,'Enter a valid hourly pay rate.')
                if rate_cents<0 or rate_cents>100000:raise HTTPException(422,'Review the hourly pay rate.')
                db.execute('UPDATE employees SET hourly_cents=?,role=?,active=?,driver_verified=? WHERE id=?',(rate_cents,text(data.get('role',emp['role']),'Role',120),int(data.get('active',bool(emp['active']))),int(data.get('driver_verified',bool(emp['driver_verified']))),eid))
            db.execute("""UPDATE employee_directory_profiles SET email=?,phone=?,home_address=?,emergency_name=?,emergency_phone=?,start_date=?,team_lead=?,notes=?,updated_by=?,updated_at=? WHERE employee_id=?""",
              (text(data.get('email',''),'Email',240),text(data.get('phone',''),'Phone',80),text(data.get('home_address',''),'Home address',500),
               text(data.get('emergency_name',''),'Emergency contact',180),text(data.get('emergency_phone',''),'Emergency phone',80),
               text(data.get('start_date',''),'Start date',20),int(bool(data.get('team_lead'))),text(data.get('notes',''),'Notes',3000),u['id'],now(),eid))
            audit(db,u['id'],'Employee profile updated','employees',eid)
        return {'ok':True}

    @app.post('/api/ux/employee-directory/{eid}/license')
    async def employee_license(eid:str,file:UploadFile=File(...),u=Depends(auth)):
        require_role(u,MANAGERS)
        allowed={'image/jpeg':'.jpg','image/png':'.png','application/pdf':'.pdf'}
        mime=(file.content_type or '').lower()
        if mime not in allowed:raise HTTPException(422,'Upload a JPG, PNG, or PDF licence file.')
        content=await file.read(8*1024*1024+1)
        if len(content)>8*1024*1024:raise HTTPException(413,'Licence file must be 8 MB or smaller.')
        original=text(file.filename or 'license','Filename',240)
        folder=Path(path).parent/'employee_uploads';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        stored=secrets.token_hex(18)+allowed[mime];(folder/stored).write_bytes(content)
        with transaction(path) as db:
            emp=one(db,'SELECT * FROM employees WHERE id=?',(eid,))
            if not emp:raise HTTPException(404,'Employee not found.')
            _profile_seed(db,emp);old=one(db,'SELECT license_stored_name FROM employee_directory_profiles WHERE employee_id=?',(eid,))
            db.execute('UPDATE employee_directory_profiles SET license_original_name=?,license_stored_name=?,license_mime=?,updated_by=?,updated_at=? WHERE employee_id=?',(original,stored,mime,u['id'],now(),eid))
            audit(db,u['id'],'Driver licence file uploaded','employees',eid,{'filename':original,'mime':mime})
        if old and old.get('license_stored_name'):
            try:(folder/old['license_stored_name']).unlink(missing_ok=True)
            except Exception:pass
        return {'ok':True,'name':original}

    @app.get('/api/ux/employee-directory/{eid}/license')
    def employee_license_get(eid:str,u=Depends(auth)):
        if u['role'] not in PRIVATE_HR:raise HTTPException(403,'Licence files are manager/payroll-private.')
        with closing(connect(path)) as db:p=one(db,'SELECT * FROM employee_directory_profiles WHERE employee_id=?',(eid,))
        if not p or not p['license_stored_name']:raise HTTPException(404,'No licence file uploaded.')
        f=Path(path).parent/'employee_uploads'/p['license_stored_name']
        if not f.exists():raise HTTPException(404,'Licence file is missing from storage.')
        return FileResponse(f,media_type=p['license_mime'],filename=p['license_original_name'])

    @app.get('/api/ux/payroll-summary')
    def payroll_summary(start:str='',end:str='',u=Depends(auth)):
        if u['role'] not in OFFICE:raise HTTPException(403,'Payroll summary is restricted to office staff.')
        end_d=_date_arg(end,'end date') if end else date.today();start_d=_date_arg(start,'start date') if start else end_d-timedelta(days=13)
        if end_d<start_d or (end_d-start_d).days>366:raise HTTPException(422,'Choose a payroll range up to one year.')
        private=u['role'] in PRIVATE_HR
        with closing(connect(path)) as db:
            emps=rows(db,'SELECT * FROM employees WHERE active=1 ORDER BY name')
            times=rows(db,"""SELECT t.*,j.title,j.start_at FROM times t JOIN jobs j ON j.id=t.job_id WHERE j.status!='Cancelled' ORDER BY j.start_at""")
        details={e['id']:[] for e in emps}
        for t in times:
            day=_local_date(t['start_at'])
            if start_d.isoformat()<=day<=end_d.isoformat() and t['employee_id'] in details:
                mins=int(t['service_minutes'] or 0)+int(t['transport_minutes'] or 0)
                details[t['employee_id']].append({'time_id':t['id'],'job_id':t['job_id'],'job_title':t['title'],'date':day,'hours':round(mins/60,2),'status':t['status']})
        out=[]
        for e in emps:
            d=details[e['id']];hours=round(sum(x['hours'] for x in d),2);pending=sum(x['status']!='Approved' for x in d)
            r={'employee_id':e['id'],'employee_name':e['name'],'jobs':len({x['job_id'] for x in d}),'hours':hours,'pending_entries':pending,'status':'Ready' if d and not pending else ('Needs review' if d else 'No hours'),'details':d}
            if private:r.update({'hourly_cents':e['hourly_cents'],'gross_cents':round(hours*e['hourly_cents'])})
            out.append(r)
        return {'start':start_d.isoformat(),'end':end_d.isoformat(),'private':private,'employees':out,'total_hours':round(sum(x['hours'] for x in out),2)}

    @app.post('/api/ux/payroll-summary/approve')
    def payroll_approve(data:dict,u=Depends(auth)):
        if u['role'] not in PRIVATE_HR:raise HTTPException(403,'Only payroll managers can approve hours.')
        start=_date_arg(data.get('start'),'start date').isoformat();end=_date_arg(data.get('end'),'end date').isoformat()
        with transaction(path) as db:
            items=rows(db,"SELECT t.id,j.start_at FROM times t JOIN jobs j ON j.id=t.job_id WHERE t.status!='Approved' AND j.status!='Cancelled'")
            ids=[x['id'] for x in items if start<=_local_date(x['start_at'])<=end]
            for tid in ids:db.execute("UPDATE times SET status='Approved',approved_by=? WHERE id=?",(u['id'],tid))
            audit(db,u['id'],'Payroll range hours approved','times',start+'..'+end,{'count':len(ids)})
        return {'ok':True,'approved':len(ids)}


    @app.patch('/api/ux/payroll-time/{tid}')
    def payroll_time_edit(tid:str,data:dict,u=Depends(auth)):
        if u['role'] not in PRIVATE_HR:raise HTTPException(403,'Only payroll managers can correct payroll hours.')
        try:hours=float(data.get('hours'))
        except Exception:raise HTTPException(422,'Enter valid total hours.')
        if hours<0 or hours>24 or round(hours*60)!=hours*60:raise HTTPException(422,'Hours must resolve to whole minutes.')
        with transaction(path) as db:
            t=one(db,'SELECT * FROM times WHERE id=?',(tid,))
            if not t:raise HTTPException(404,'Time entry not found.')
            before=int(t['service_minutes'] or 0)+int(t['transport_minutes'] or 0)
            db.execute('UPDATE times SET service_minutes=?,transport_minutes=0 WHERE id=?',(round(hours*60),tid))
            audit(db,u['id'],'Payroll hours corrected','times',tid,{'before_minutes':before,'after_minutes':round(hours*60)})
        return {'ok':True}

    @app.get('/api/calls')
    def calls_feed(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            items=rows(db,"""SELECT c.*,l.client_name FROM calls c
              LEFT JOIN leads l ON l.id=c.lead_id
              ORDER BY c.created_at DESC LIMIT 100""")
        for item in items:
            raw=item.pop('payload_json','{}')
            try:item['details']=json.loads(raw or '{}')
            except Exception:item['details']={}
        return items

    @app.get('/api/ux/inbox')
    def inbox(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            return rows(db,"""SELECT m.*,l.client_name,c.claimed_by,c.claimed_at,c.resolved_at,u2.name claimed_name
              FROM messages m JOIN leads l ON l.id=m.lead_id
              LEFT JOIN inbox_claims c ON c.message_id=m.id LEFT JOIN users u2 ON u2.id=c.claimed_by
              WHERE lower(m.kind) LIKE '%incoming%' OR lower(m.kind) LIKE '%email%'
              ORDER BY m.created_at DESC LIMIT 100""")

    @app.post('/api/ux/inbox/{mid}/claim')
    def inbox_claim(mid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            m=one(db,'SELECT id,lead_id FROM messages WHERE id=?',(mid,))
            if not m:raise HTTPException(404,'Message not found.')
            lead=one(db,'SELECT id,assigned_to FROM leads WHERE id=?',(m['lead_id'],))
            if lead and lead.get('assigned_to') not in ('',u['id'],u['name']):
                raise HTTPException(409,'This client is already assigned to another team member.')
            old=one(db,'SELECT * FROM inbox_claims WHERE message_id=?',(mid,))
            if old and old.get('claimed_by')!=u['id'] and not old.get('resolved_at'):raise HTTPException(409,'This email is already claimed by another team member.')
            if old:db.execute('UPDATE inbox_claims SET claimed_by=?,claimed_at=?,resolved_at=NULL WHERE message_id=?',(u['id'],now(),mid))
            else:insert(db,'inbox_claims',dict(message_id=mid,claimed_by=u['id'],claimed_at=now(),resolved_at=None))
            if lead and not lead.get('assigned_to'):
                db.execute('UPDATE leads SET assigned_to=?,version=version+1,updated_at=? WHERE id=?',(u['id'],now(),lead['id']))
                db.execute("UPDATE tasks SET owner=? WHERE lead_id=? AND status!='Done' AND (owner='' OR owner IS NULL)",(u['id'],lead['id']))
            audit(db,u['id'],'Client email claimed and lead assigned','messages',mid,{'lead_id':m['lead_id'],'open_followups_assigned':True})
        return {'ok':True,'lead_id':m['lead_id']}

    @app.post('/api/ux/inbox/{mid}/resolve')
    def inbox_resolve(mid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            c=one(db,'SELECT * FROM inbox_claims WHERE message_id=?',(mid,))
            if not c:raise HTTPException(409,'Claim the email before marking it answered.')
            if c['claimed_by']!=u['id'] and u['role'] not in MANAGERS:raise HTTPException(403,'Only the claimant or a manager can mark this answered.')
            db.execute('UPDATE inbox_claims SET resolved_at=? WHERE message_id=?',(now(),mid));audit(db,u['id'],'Client email marked answered','messages',mid)
        return {'ok':True}

    @app.get('/api/ux/company-communications')
    def company_comms(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:return settings(db,'company_communications',{})

    @app.patch('/api/ux/company-communications')
    def company_comms_update(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        connected=text(data.get('connected_mailbox',''),'Connected mailbox',240).lower();send_as=text(data.get('send_as',''),'Send-as address',240).lower();reply_to=text(data.get('reply_to',''),'Reply-to address',240).lower()
        if '@' not in connected or '@' not in send_as or '@' not in reply_to:raise HTTPException(422,'Enter valid company email addresses.')
        cfg={'connected_mailbox':connected,'send_as':send_as,'reply_to':reply_to,'default_from':send_as,'secondary_inbox':connected,'client_email_rule':'Store mail in the connected Gmail mailbox and send as the Crown Movers domain alias after Google verifies the alias.','approved':bool(data.get('approved')),'note':text(data.get('note',''),'Note',2000)}
        with transaction(path) as db:put_setting(db,'company_communications',cfg);audit(db,u['id'],'Company communication defaults updated','settings','company_communications')
        return cfg
+str(p.get('amount',''))).strip(' -')
                attention.append({'type':'gmail','id':r['id'],'title':'Gmail payment notice needs review','detail':detail,'priority':'High'})
            maint=rows(db,"SELECT m.*,v.name vehicle_name FROM maintenance m JOIN vehicles v ON v.id=m.vehicle_id WHERE m.status='Open' AND m.due_date!='' AND m.due_date<=? ORDER BY m.due_date",(today,))
            for r in maint:attention.append({'type':'fleet','id':r['id'],'title':r['vehicle_name']+' maintenance due','detail':r['title']+' - '+r['due_date'],'priority':'High'})
            stats={
              'jobs_today':len(jobs),'attention':len(attention),'pending_time_off':len(pending_off),
              'unverified_payments':len(unverified)+len(gmail_payment),'open_followups':len(due),'pending_client_changes':len(pending_changes)
            }
        attention.sort(key=lambda x:(0 if x['priority']=='High' else 1,x['title']))
        return {'date':today,'stats':stats,'jobs':jobs,'attention':attention[:60]}

    @app.get('/api/ux/search')
    def global_search(q:str='',u=Depends(auth)):
        require_role(u,OFFICE)
        q=(q or '').strip()
        if len(q)<2:return []
        like='%'+q.lower()+'%'
        with closing(connect(path)) as db:
            out=[]
            for r in rows(db,"SELECT id,client_name,email,phone,pickup,dropoff,move_date FROM leads WHERE lower(client_name||' '||email||' '||phone||' '||pickup||' '||dropoff) LIKE ? LIMIT 12",(like,)):
                out.append({'type':'client','id':r['id'],'title':r['client_name'],'detail':' · '.join(x for x in (r['phone'],r['move_date'],r['pickup']) if x)})
            for r in rows(db,"SELECT id,lead_id,title,phase,start_at,status FROM jobs WHERE lower(title||' '||phase||' '||id) LIKE ? LIMIT 8",(like,)):
                out.append({'type':'job','id':r['id'],'lead_id':r['lead_id'],'title':r['title'],'detail':r['phase']+' · '+_local_date(r['start_at'])+' · '+r['status']})
            for r in rows(db,"SELECT i.id,i.number,i.status,j.lead_id,j.title FROM job_invoices i JOIN jobs j ON j.id=i.job_id WHERE lower(i.number||' '||j.title) LIKE ? LIMIT 8",(like,)):
                out.append({'type':'invoice','id':r['id'],'lead_id':r['lead_id'],'title':r['number'],'detail':r['title']+' · '+r['status']})
        return out[:25]

    @app.get('/api/ux/project/{lid}')
    def project_overview(lid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            l=one(db,'SELECT * FROM leads WHERE id=?',(lid,))
            if not l:raise HTTPException(404,'Client project not found.')
            jobs=rows(db,'SELECT * FROM jobs WHERE lead_id=? ORDER BY start_at',(lid,))
            for j in jobs:j['readiness']=_job_readiness(db,j)
            return {'lifecycle':_project_lifecycle(db,lid),'timeline':_timeline(db,lid),'jobs':jobs}

    @app.get('/api/ux/jobs/{jid}/readiness')
    def readiness(jid:str,u=Depends(auth)):
        require_role(u,OFFICE|{'crew'})
        with closing(connect(path)) as db:
            j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
            if not j:raise HTTPException(404,'Job not found.')
            if u['role']=='crew' and not one(db,"SELECT 1 FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id') or '')):
                raise HTTPException(403,'Assigned team only.')
            return _job_readiness(db,j)

    @app.get('/api/ux/jobs/{jid}/crew-hours')
    def crew_hours(jid:str,u=Depends(auth)):
        if u['role'] not in OFFICE|{'crew'}:raise HTTPException(403,'Crown staff only.')
        with closing(connect(path)) as db:
            j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
            if not j:raise HTTPException(404,'Job not found.')
            if u['role']=='crew' and not one(db,"SELECT 1 FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id') or '')):
                raise HTTPException(403,'Assigned team only.')
            team=rows(db,"""SELECT e.id,e.name,e.role,t.id time_id,t.service_minutes,t.transport_minutes,t.note,t.status
              FROM assignments a JOIN employees e ON e.id=a.resource_id AND a.resource_type='employee'
              LEFT JOIN times t ON t.job_id=a.job_id AND t.employee_id=e.id WHERE a.job_id=? ORDER BY e.name""",(jid,))
            inv=one(db,'SELECT actual_json FROM job_invoices WHERE job_id=?',(jid,))
            paid=one(db,'SELECT paid_transport_minutes FROM paid_work_terms WHERE job_id=?',(jid,))
        suggested=None
        if inv:
            try:suggested=float(json.loads(inv['actual_json']).get('service_hours') or 0)+(float(paid['paid_transport_minutes'])/60 if paid and paid['paid_transport_minutes'] is not None else 0)
            except Exception:suggested=None
        for e in team:e['total_hours']=round((int(e['service_minutes'] or 0)+int(e['transport_minutes'] or 0))/60,2) if e['time_id'] else None
        return {'job':{'id':j['id'],'title':j['title'],'status':j['status']},'employees':team,'suggested_total_hours':suggested}

    @app.post('/api/ux/jobs/{jid}/crew-hours')
    def crew_hours_save(jid:str,data:dict,u=Depends(auth)):
        manager=u['role'] in {'owner','accounting','dispatch'}
        with closing(connect(path)) as checkdb:
            is_lead=bool(u['role']=='crew' and u.get('employee_id') and one(checkdb,'SELECT team_lead FROM employee_directory_profiles WHERE employee_id=? AND team_lead=1',(u['employee_id'],)))
        if not manager and not is_lead:raise HTTPException(403,'Only managers or assigned Team Leads can submit team hours.')
        selected=data.get('employee_ids') or []
        if not isinstance(selected,list) or not selected or len(set(selected))!=len(selected):raise HTTPException(422,'Select the employees receiving the team hours.')
        try:common=float(data.get('team_hours'))
        except Exception:raise HTTPException(422,'Enter the team hours.')
        if common<0 or common>24 or round(common*60)!=common*60:raise HTTPException(422,'Use valid hours that resolve to whole minutes.')
        exceptions=data.get('exceptions') or {}
        if not isinstance(exceptions,dict):raise HTTPException(422,'Invalid individual adjustments.')
        note=text(data.get('note',''),'Optional team note',2000)
        with transaction(path) as db:
            j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
            if not j:raise HTTPException(404,'Job not found.')
            if j['status'] not in ('In progress','Completed'):raise HTTPException(409,'Enter team hours after the job has started.')
            if is_lead and not one(db,"SELECT 1 FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id'))):raise HTTPException(403,'Team Leads can enter hours only for jobs they are assigned to.')
            assigned={r['resource_id'] for r in rows(db,"SELECT resource_id FROM assignments WHERE job_id=? AND resource_type='employee'",(jid,))}
            if set(selected)-assigned:raise HTTPException(422,'Selected employees must be assigned to this job.')
            changed=[]
            for eid in selected:
                hrs=common
                if eid in exceptions:
                    try:hrs=float(exceptions[eid].get('total_hours'))
                    except Exception:raise HTTPException(422,'Review the individual hours.')
                    if hrs<0 or hrs>24 or round(hrs*60)!=hrs*60:raise HTTPException(422,'Individual hours must resolve to whole minutes.')
                existing=one(db,'SELECT * FROM times WHERE job_id=? AND employee_id=?',(jid,eid))
                if existing and existing['status']=='Approved' and not manager:raise HTTPException(409,'Approved payroll hours can only be corrected by a payroll manager.')
                minutes=round(hrs*60)
                individual_note=text((exceptions.get(eid) or {}).get('note',''),'Optional adjustment note',1000) or note
                if existing:
                    new_status='Submitted' if existing['status']!='Approved' else 'Approved'
                    approved_by=existing.get('approved_by') if new_status=='Approved' else None
                    db.execute("UPDATE times SET service_minutes=?,transport_minutes=0,note=?,status=?,submitted_by=?,approved_by=? WHERE id=?",(minutes,individual_note,new_status,u['id'],approved_by,existing['id']))
                else:
                    insert(db,'times',dict(id=uid('TIME'),job_id=jid,employee_id=eid,service_minutes=minutes,transport_minutes=0,note=individual_note,status='Submitted',submitted_by=u['id'],created_at=now()))
                changed.append({'employee_id':eid,'total_minutes':minutes})
            audit(db,u['id'],'Crew hours entered in one batch','times',jid,{'entries':changed})
        return {'ok':True,'submitted':len(changed)}

    @app.get('/api/ux/employee-directory')
    def employee_directory(start:str='',end:str='',u=Depends(auth)):
        # Everyone gets the internal contact directory; only payroll/management gets private HR/pay fields.
        if u['role'] not in OFFICE|{'crew'}:raise HTTPException(403,'Directory access is restricted to Crown staff.')
        end_d=_date_arg(end,'end date') if end else date.today(); start_d=_date_arg(start,'start date') if start else end_d-timedelta(days=13)
        if end_d<start_d or (end_d-start_d).days>366:raise HTTPException(422,'Choose a payroll range up to one year.')
        private=u['role'] in PRIVATE_HR
        with transaction(path) as db:
            for e in rows(db,'SELECT * FROM employees'):_profile_seed(db,e)
            employees=rows(db,"""SELECT e.*,p.email,p.phone,p.home_address,p.emergency_name,p.emergency_phone,p.start_date,p.notes,
                p.license_original_name,p.license_mime,p.team_lead FROM employees e LEFT JOIN employee_directory_profiles p ON p.employee_id=e.id ORDER BY e.name""")
            times=rows(db,"""SELECT t.*,j.start_at FROM times t JOIN jobs j ON j.id=t.job_id WHERE j.status!='Cancelled'""")
        by={e['id']:{'minutes':0,'jobs':set(),'pending':0} for e in employees}
        for t in times:
            day=_local_date(t['start_at'])
            if start_d.isoformat()<=day<=end_d.isoformat() and t['employee_id'] in by:
                by[t['employee_id']]['minutes']+=int(t['service_minutes'] or 0)+int(t['transport_minutes'] or 0)
                by[t['employee_id']]['jobs'].add(t['job_id'])
                if t['status']!='Approved':by[t['employee_id']]['pending']+=1
        result=[]
        for e in employees:
            base={'id':e['id'],'full_name':e['name'],'phone':e.get('phone') or '',
                  'emergency_contact':{'name':e.get('emergency_name') or '', 'phone':e.get('emergency_phone') or ''},
                  'role':e['role'],'active':bool(e['active'])}
            if private:
                base.update({'email':e.get('email') or '','home_address':e.get('home_address') or '',
                  'start_date':e.get('start_date') or '','driver_verified':bool(e['driver_verified']),
                  'hourly_cents':e['hourly_cents'],'license_uploaded':bool(e.get('license_original_name')),
                  'license_name':e.get('license_original_name') or '','team_lead':bool(e.get('team_lead')),'notes':e.get('notes') or '',
                  'current_hours':round(by[e['id']]['minutes']/60,2),'current_jobs':len(by[e['id']]['jobs']),
                  'pending_time_entries':by[e['id']]['pending']})
            result.append(base)
        return {'start':start_d.isoformat(),'end':end_d.isoformat(),'private':private,'employees':result}

    @app.patch('/api/ux/employee-directory/{eid}')
    def employee_update(eid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        with transaction(path) as db:
            emp=one(db,'SELECT * FROM employees WHERE id=?',(eid,))
            if not emp:raise HTTPException(404,'Employee not found.')
            _profile_seed(db,emp)
            rate=data.get('hourly_rate')
            if rate is not None:
                try:rate_cents=round(float(rate)*100)
                except Exception:raise HTTPException(422,'Enter a valid hourly pay rate.')
                if rate_cents<0 or rate_cents>100000:raise HTTPException(422,'Review the hourly pay rate.')
                db.execute('UPDATE employees SET hourly_cents=?,role=?,active=?,driver_verified=? WHERE id=?',(rate_cents,text(data.get('role',emp['role']),'Role',120),int(data.get('active',bool(emp['active']))),int(data.get('driver_verified',bool(emp['driver_verified']))),eid))
            db.execute("""UPDATE employee_directory_profiles SET email=?,phone=?,home_address=?,emergency_name=?,emergency_phone=?,start_date=?,team_lead=?,notes=?,updated_by=?,updated_at=? WHERE employee_id=?""",
              (text(data.get('email',''),'Email',240),text(data.get('phone',''),'Phone',80),text(data.get('home_address',''),'Home address',500),
               text(data.get('emergency_name',''),'Emergency contact',180),text(data.get('emergency_phone',''),'Emergency phone',80),
               text(data.get('start_date',''),'Start date',20),int(bool(data.get('team_lead'))),text(data.get('notes',''),'Notes',3000),u['id'],now(),eid))
            audit(db,u['id'],'Employee profile updated','employees',eid)
        return {'ok':True}

    @app.post('/api/ux/employee-directory/{eid}/license')
    async def employee_license(eid:str,file:UploadFile=File(...),u=Depends(auth)):
        require_role(u,MANAGERS)
        allowed={'image/jpeg':'.jpg','image/png':'.png','application/pdf':'.pdf'}
        mime=(file.content_type or '').lower()
        if mime not in allowed:raise HTTPException(422,'Upload a JPG, PNG, or PDF licence file.')
        content=await file.read(8*1024*1024+1)
        if len(content)>8*1024*1024:raise HTTPException(413,'Licence file must be 8 MB or smaller.')
        original=text(file.filename or 'license','Filename',240)
        folder=Path(path).parent/'employee_uploads';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        stored=secrets.token_hex(18)+allowed[mime];(folder/stored).write_bytes(content)
        with transaction(path) as db:
            emp=one(db,'SELECT * FROM employees WHERE id=?',(eid,))
            if not emp:raise HTTPException(404,'Employee not found.')
            _profile_seed(db,emp);old=one(db,'SELECT license_stored_name FROM employee_directory_profiles WHERE employee_id=?',(eid,))
            db.execute('UPDATE employee_directory_profiles SET license_original_name=?,license_stored_name=?,license_mime=?,updated_by=?,updated_at=? WHERE employee_id=?',(original,stored,mime,u['id'],now(),eid))
            audit(db,u['id'],'Driver licence file uploaded','employees',eid,{'filename':original,'mime':mime})
        if old and old.get('license_stored_name'):
            try:(folder/old['license_stored_name']).unlink(missing_ok=True)
            except Exception:pass
        return {'ok':True,'name':original}

    @app.get('/api/ux/employee-directory/{eid}/license')
    def employee_license_get(eid:str,u=Depends(auth)):
        if u['role'] not in PRIVATE_HR:raise HTTPException(403,'Licence files are manager/payroll-private.')
        with closing(connect(path)) as db:p=one(db,'SELECT * FROM employee_directory_profiles WHERE employee_id=?',(eid,))
        if not p or not p['license_stored_name']:raise HTTPException(404,'No licence file uploaded.')
        f=Path(path).parent/'employee_uploads'/p['license_stored_name']
        if not f.exists():raise HTTPException(404,'Licence file is missing from storage.')
        return FileResponse(f,media_type=p['license_mime'],filename=p['license_original_name'])

    @app.get('/api/ux/payroll-summary')
    def payroll_summary(start:str='',end:str='',u=Depends(auth)):
        if u['role'] not in OFFICE:raise HTTPException(403,'Payroll summary is restricted to office staff.')
        end_d=_date_arg(end,'end date') if end else date.today();start_d=_date_arg(start,'start date') if start else end_d-timedelta(days=13)
        if end_d<start_d or (end_d-start_d).days>366:raise HTTPException(422,'Choose a payroll range up to one year.')
        private=u['role'] in PRIVATE_HR
        with closing(connect(path)) as db:
            emps=rows(db,'SELECT * FROM employees WHERE active=1 ORDER BY name')
            times=rows(db,"""SELECT t.*,j.title,j.start_at FROM times t JOIN jobs j ON j.id=t.job_id WHERE j.status!='Cancelled' ORDER BY j.start_at""")
        details={e['id']:[] for e in emps}
        for t in times:
            day=_local_date(t['start_at'])
            if start_d.isoformat()<=day<=end_d.isoformat() and t['employee_id'] in details:
                mins=int(t['service_minutes'] or 0)+int(t['transport_minutes'] or 0)
                details[t['employee_id']].append({'time_id':t['id'],'job_id':t['job_id'],'job_title':t['title'],'date':day,'hours':round(mins/60,2),'status':t['status']})
        out=[]
        for e in emps:
            d=details[e['id']];hours=round(sum(x['hours'] for x in d),2);pending=sum(x['status']!='Approved' for x in d)
            r={'employee_id':e['id'],'employee_name':e['name'],'jobs':len({x['job_id'] for x in d}),'hours':hours,'pending_entries':pending,'status':'Ready' if d and not pending else ('Needs review' if d else 'No hours'),'details':d}
            if private:r.update({'hourly_cents':e['hourly_cents'],'gross_cents':round(hours*e['hourly_cents'])})
            out.append(r)
        return {'start':start_d.isoformat(),'end':end_d.isoformat(),'private':private,'employees':out,'total_hours':round(sum(x['hours'] for x in out),2)}

    @app.post('/api/ux/payroll-summary/approve')
    def payroll_approve(data:dict,u=Depends(auth)):
        if u['role'] not in PRIVATE_HR:raise HTTPException(403,'Only payroll managers can approve hours.')
        start=_date_arg(data.get('start'),'start date').isoformat();end=_date_arg(data.get('end'),'end date').isoformat()
        with transaction(path) as db:
            items=rows(db,"SELECT t.id,j.start_at FROM times t JOIN jobs j ON j.id=t.job_id WHERE t.status!='Approved' AND j.status!='Cancelled'")
            ids=[x['id'] for x in items if start<=_local_date(x['start_at'])<=end]
            for tid in ids:db.execute("UPDATE times SET status='Approved',approved_by=? WHERE id=?",(u['id'],tid))
            audit(db,u['id'],'Payroll range hours approved','times',start+'..'+end,{'count':len(ids)})
        return {'ok':True,'approved':len(ids)}


    @app.patch('/api/ux/payroll-time/{tid}')
    def payroll_time_edit(tid:str,data:dict,u=Depends(auth)):
        if u['role'] not in PRIVATE_HR:raise HTTPException(403,'Only payroll managers can correct payroll hours.')
        try:hours=float(data.get('hours'))
        except Exception:raise HTTPException(422,'Enter valid total hours.')
        if hours<0 or hours>24 or round(hours*60)!=hours*60:raise HTTPException(422,'Hours must resolve to whole minutes.')
        with transaction(path) as db:
            t=one(db,'SELECT * FROM times WHERE id=?',(tid,))
            if not t:raise HTTPException(404,'Time entry not found.')
            before=int(t['service_minutes'] or 0)+int(t['transport_minutes'] or 0)
            db.execute('UPDATE times SET service_minutes=?,transport_minutes=0 WHERE id=?',(round(hours*60),tid))
            audit(db,u['id'],'Payroll hours corrected','times',tid,{'before_minutes':before,'after_minutes':round(hours*60)})
        return {'ok':True}

    @app.get('/api/calls')
    def calls_feed(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            items=rows(db,"""SELECT c.*,l.client_name FROM calls c
              LEFT JOIN leads l ON l.id=c.lead_id
              ORDER BY c.created_at DESC LIMIT 100""")
        for item in items:
            raw=item.pop('payload_json','{}')
            try:item['details']=json.loads(raw or '{}')
            except Exception:item['details']={}
        return items

    @app.get('/api/ux/inbox')
    def inbox(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            return rows(db,"""SELECT m.*,l.client_name,c.claimed_by,c.claimed_at,c.resolved_at,u2.name claimed_name
              FROM messages m JOIN leads l ON l.id=m.lead_id
              LEFT JOIN inbox_claims c ON c.message_id=m.id LEFT JOIN users u2 ON u2.id=c.claimed_by
              WHERE lower(m.kind) LIKE '%incoming%' OR lower(m.kind) LIKE '%email%'
              ORDER BY m.created_at DESC LIMIT 100""")

    @app.post('/api/ux/inbox/{mid}/claim')
    def inbox_claim(mid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            m=one(db,'SELECT id,lead_id FROM messages WHERE id=?',(mid,))
            if not m:raise HTTPException(404,'Message not found.')
            lead=one(db,'SELECT id,assigned_to FROM leads WHERE id=?',(m['lead_id'],))
            if lead and lead.get('assigned_to') not in ('',u['id'],u['name']):
                raise HTTPException(409,'This client is already assigned to another team member.')
            old=one(db,'SELECT * FROM inbox_claims WHERE message_id=?',(mid,))
            if old and old.get('claimed_by')!=u['id'] and not old.get('resolved_at'):raise HTTPException(409,'This email is already claimed by another team member.')
            if old:db.execute('UPDATE inbox_claims SET claimed_by=?,claimed_at=?,resolved_at=NULL WHERE message_id=?',(u['id'],now(),mid))
            else:insert(db,'inbox_claims',dict(message_id=mid,claimed_by=u['id'],claimed_at=now(),resolved_at=None))
            if lead and not lead.get('assigned_to'):
                db.execute('UPDATE leads SET assigned_to=?,version=version+1,updated_at=? WHERE id=?',(u['id'],now(),lead['id']))
                db.execute("UPDATE tasks SET owner=? WHERE lead_id=? AND status!='Done' AND (owner='' OR owner IS NULL)",(u['id'],lead['id']))
            audit(db,u['id'],'Client email claimed and lead assigned','messages',mid,{'lead_id':m['lead_id'],'open_followups_assigned':True})
        return {'ok':True,'lead_id':m['lead_id']}

    @app.post('/api/ux/inbox/{mid}/resolve')
    def inbox_resolve(mid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            c=one(db,'SELECT * FROM inbox_claims WHERE message_id=?',(mid,))
            if not c:raise HTTPException(409,'Claim the email before marking it answered.')
            if c['claimed_by']!=u['id'] and u['role'] not in MANAGERS:raise HTTPException(403,'Only the claimant or a manager can mark this answered.')
            db.execute('UPDATE inbox_claims SET resolved_at=? WHERE message_id=?',(now(),mid));audit(db,u['id'],'Client email marked answered','messages',mid)
        return {'ok':True}

    @app.get('/api/ux/company-communications')
    def company_comms(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:return settings(db,'company_communications',{})

    @app.patch('/api/ux/company-communications')
    def company_comms_update(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        connected=text(data.get('connected_mailbox',''),'Connected mailbox',240).lower();send_as=text(data.get('send_as',''),'Send-as address',240).lower();reply_to=text(data.get('reply_to',''),'Reply-to address',240).lower()
        if '@' not in connected or '@' not in send_as or '@' not in reply_to:raise HTTPException(422,'Enter valid company email addresses.')
        cfg={'connected_mailbox':connected,'send_as':send_as,'reply_to':reply_to,'default_from':send_as,'secondary_inbox':connected,'client_email_rule':'Store mail in the connected Gmail mailbox and send as the Crown Movers domain alias after Google verifies the alias.','approved':bool(data.get('approved')),'note':text(data.get('note',''),'Note',2000)}
        with transaction(path) as db:put_setting(db,'company_communications',cfg);audit(db,u['id'],'Company communication defaults updated','settings','company_communications')
        return cfg
+str(p.get('amount',''))).strip(' -')
                attention.append({'type':'gmail','id':r['id'],'title':'Gmail payment notice needs review','detail':detail,'priority':'High'})
            maint=rows(db,"SELECT m.*,v.name vehicle_name FROM maintenance m JOIN vehicles v ON v.id=m.vehicle_id WHERE m.status='Open' AND m.due_date!='' AND m.due_date<=? ORDER BY m.due_date",(today,))
            for r in maint:attention.append({'type':'fleet','id':r['id'],'title':r['vehicle_name']+' maintenance due','detail':r['title']+' - '+r['due_date'],'priority':'High'})
            stats={
              'jobs_today':len(jobs),'attention':len(attention),'pending_time_off':len(pending_off),
              'unverified_payments':len(unverified)+len(gmail_payment),'open_followups':len(due)+len(gmail_calls),'pending_client_changes':len(pending_changes)
            }
        attention.sort(key=lambda x:(0 if x['priority']=='High' else 1,x['title']))
        return {'date':today,'stats':stats,'jobs':jobs,'attention':attention[:60]}

    @app.get('/api/ux/search')
    def global_search(q:str='',u=Depends(auth)):
        require_role(u,OFFICE)
        q=(q or '').strip()
        if len(q)<2:return []
        like='%'+q.lower()+'%'
        with closing(connect(path)) as db:
            out=[]
            for r in rows(db,"SELECT id,client_name,email,phone,pickup,dropoff,move_date FROM leads WHERE lower(client_name||' '||email||' '||phone||' '||pickup||' '||dropoff) LIKE ? LIMIT 12",(like,)):
                out.append({'type':'client','id':r['id'],'title':r['client_name'],'detail':' · '.join(x for x in (r['phone'],r['move_date'],r['pickup']) if x)})
            for r in rows(db,"SELECT id,lead_id,title,phase,start_at,status FROM jobs WHERE lower(title||' '||phase||' '||id) LIKE ? LIMIT 8",(like,)):
                out.append({'type':'job','id':r['id'],'lead_id':r['lead_id'],'title':r['title'],'detail':r['phase']+' · '+_local_date(r['start_at'])+' · '+r['status']})
            for r in rows(db,"SELECT i.id,i.number,i.status,j.lead_id,j.title FROM job_invoices i JOIN jobs j ON j.id=i.job_id WHERE lower(i.number||' '||j.title) LIKE ? LIMIT 8",(like,)):
                out.append({'type':'invoice','id':r['id'],'lead_id':r['lead_id'],'title':r['number'],'detail':r['title']+' · '+r['status']})
        return out[:25]

    @app.get('/api/ux/project/{lid}')
    def project_overview(lid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            l=one(db,'SELECT * FROM leads WHERE id=?',(lid,))
            if not l:raise HTTPException(404,'Client project not found.')
            jobs=rows(db,'SELECT * FROM jobs WHERE lead_id=? ORDER BY start_at',(lid,))
            for j in jobs:j['readiness']=_job_readiness(db,j)
            return {'lifecycle':_project_lifecycle(db,lid),'timeline':_timeline(db,lid),'jobs':jobs}

    @app.get('/api/ux/jobs/{jid}/readiness')
    def readiness(jid:str,u=Depends(auth)):
        require_role(u,OFFICE|{'crew'})
        with closing(connect(path)) as db:
            j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
            if not j:raise HTTPException(404,'Job not found.')
            if u['role']=='crew' and not one(db,"SELECT 1 FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id') or '')):
                raise HTTPException(403,'Assigned team only.')
            return _job_readiness(db,j)

    @app.get('/api/ux/jobs/{jid}/crew-hours')
    def crew_hours(jid:str,u=Depends(auth)):
        if u['role'] not in OFFICE|{'crew'}:raise HTTPException(403,'Crown staff only.')
        with closing(connect(path)) as db:
            j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
            if not j:raise HTTPException(404,'Job not found.')
            if u['role']=='crew' and not one(db,"SELECT 1 FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id') or '')):
                raise HTTPException(403,'Assigned team only.')
            team=rows(db,"""SELECT e.id,e.name,e.role,t.id time_id,t.service_minutes,t.transport_minutes,t.note,t.status
              FROM assignments a JOIN employees e ON e.id=a.resource_id AND a.resource_type='employee'
              LEFT JOIN times t ON t.job_id=a.job_id AND t.employee_id=e.id WHERE a.job_id=? ORDER BY e.name""",(jid,))
            inv=one(db,'SELECT actual_json FROM job_invoices WHERE job_id=?',(jid,))
            paid=one(db,'SELECT paid_transport_minutes FROM paid_work_terms WHERE job_id=?',(jid,))
        suggested=None
        if inv:
            try:suggested=float(json.loads(inv['actual_json']).get('service_hours') or 0)+(float(paid['paid_transport_minutes'])/60 if paid and paid['paid_transport_minutes'] is not None else 0)
            except Exception:suggested=None
        for e in team:e['total_hours']=round((int(e['service_minutes'] or 0)+int(e['transport_minutes'] or 0))/60,2) if e['time_id'] else None
        return {'job':{'id':j['id'],'title':j['title'],'status':j['status']},'employees':team,'suggested_total_hours':suggested}

    @app.post('/api/ux/jobs/{jid}/crew-hours')
    def crew_hours_save(jid:str,data:dict,u=Depends(auth)):
        manager=u['role'] in {'owner','accounting','dispatch'}
        with closing(connect(path)) as checkdb:
            is_lead=bool(u['role']=='crew' and u.get('employee_id') and one(checkdb,'SELECT team_lead FROM employee_directory_profiles WHERE employee_id=? AND team_lead=1',(u['employee_id'],)))
        if not manager and not is_lead:raise HTTPException(403,'Only managers or assigned Team Leads can submit team hours.')
        selected=data.get('employee_ids') or []
        if not isinstance(selected,list) or not selected or len(set(selected))!=len(selected):raise HTTPException(422,'Select the employees receiving the team hours.')
        try:common=float(data.get('team_hours'))
        except Exception:raise HTTPException(422,'Enter the team hours.')
        if common<0 or common>24 or round(common*60)!=common*60:raise HTTPException(422,'Use valid hours that resolve to whole minutes.')
        exceptions=data.get('exceptions') or {}
        if not isinstance(exceptions,dict):raise HTTPException(422,'Invalid individual adjustments.')
        note=text(data.get('note',''),'Optional team note',2000)
        with transaction(path) as db:
            j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
            if not j:raise HTTPException(404,'Job not found.')
            if j['status'] not in ('In progress','Completed'):raise HTTPException(409,'Enter team hours after the job has started.')
            if is_lead and not one(db,"SELECT 1 FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id'))):raise HTTPException(403,'Team Leads can enter hours only for jobs they are assigned to.')
            assigned={r['resource_id'] for r in rows(db,"SELECT resource_id FROM assignments WHERE job_id=? AND resource_type='employee'",(jid,))}
            if set(selected)-assigned:raise HTTPException(422,'Selected employees must be assigned to this job.')
            changed=[]
            for eid in selected:
                hrs=common
                if eid in exceptions:
                    try:hrs=float(exceptions[eid].get('total_hours'))
                    except Exception:raise HTTPException(422,'Review the individual hours.')
                    if hrs<0 or hrs>24 or round(hrs*60)!=hrs*60:raise HTTPException(422,'Individual hours must resolve to whole minutes.')
                existing=one(db,'SELECT * FROM times WHERE job_id=? AND employee_id=?',(jid,eid))
                if existing and existing['status']=='Approved' and not manager:raise HTTPException(409,'Approved payroll hours can only be corrected by a payroll manager.')
                minutes=round(hrs*60)
                individual_note=text((exceptions.get(eid) or {}).get('note',''),'Optional adjustment note',1000) or note
                if existing:
                    new_status='Submitted' if existing['status']!='Approved' else 'Approved'
                    approved_by=existing.get('approved_by') if new_status=='Approved' else None
                    db.execute("UPDATE times SET service_minutes=?,transport_minutes=0,note=?,status=?,submitted_by=?,approved_by=? WHERE id=?",(minutes,individual_note,new_status,u['id'],approved_by,existing['id']))
                else:
                    insert(db,'times',dict(id=uid('TIME'),job_id=jid,employee_id=eid,service_minutes=minutes,transport_minutes=0,note=individual_note,status='Submitted',submitted_by=u['id'],created_at=now()))
                changed.append({'employee_id':eid,'total_minutes':minutes})
            audit(db,u['id'],'Crew hours entered in one batch','times',jid,{'entries':changed})
        return {'ok':True,'submitted':len(changed)}

    @app.get('/api/ux/employee-directory')
    def employee_directory(start:str='',end:str='',u=Depends(auth)):
        # Everyone gets the internal contact directory; only payroll/management gets private HR/pay fields.
        if u['role'] not in OFFICE|{'crew'}:raise HTTPException(403,'Directory access is restricted to Crown staff.')
        end_d=_date_arg(end,'end date') if end else date.today(); start_d=_date_arg(start,'start date') if start else end_d-timedelta(days=13)
        if end_d<start_d or (end_d-start_d).days>366:raise HTTPException(422,'Choose a payroll range up to one year.')
        private=u['role'] in PRIVATE_HR
        with transaction(path) as db:
            for e in rows(db,'SELECT * FROM employees'):_profile_seed(db,e)
            employees=rows(db,"""SELECT e.*,p.email,p.phone,p.home_address,p.emergency_name,p.emergency_phone,p.start_date,p.notes,
                p.license_original_name,p.license_mime,p.team_lead FROM employees e LEFT JOIN employee_directory_profiles p ON p.employee_id=e.id ORDER BY e.name""")
            times=rows(db,"""SELECT t.*,j.start_at FROM times t JOIN jobs j ON j.id=t.job_id WHERE j.status!='Cancelled'""")
        by={e['id']:{'minutes':0,'jobs':set(),'pending':0} for e in employees}
        for t in times:
            day=_local_date(t['start_at'])
            if start_d.isoformat()<=day<=end_d.isoformat() and t['employee_id'] in by:
                by[t['employee_id']]['minutes']+=int(t['service_minutes'] or 0)+int(t['transport_minutes'] or 0)
                by[t['employee_id']]['jobs'].add(t['job_id'])
                if t['status']!='Approved':by[t['employee_id']]['pending']+=1
        result=[]
        for e in employees:
            base={'id':e['id'],'full_name':e['name'],'phone':e.get('phone') or '',
                  'emergency_contact':{'name':e.get('emergency_name') or '', 'phone':e.get('emergency_phone') or ''},
                  'role':e['role'],'active':bool(e['active'])}
            if private:
                base.update({'email':e.get('email') or '','home_address':e.get('home_address') or '',
                  'start_date':e.get('start_date') or '','driver_verified':bool(e['driver_verified']),
                  'hourly_cents':e['hourly_cents'],'license_uploaded':bool(e.get('license_original_name')),
                  'license_name':e.get('license_original_name') or '','team_lead':bool(e.get('team_lead')),'notes':e.get('notes') or '',
                  'current_hours':round(by[e['id']]['minutes']/60,2),'current_jobs':len(by[e['id']]['jobs']),
                  'pending_time_entries':by[e['id']]['pending']})
            result.append(base)
        return {'start':start_d.isoformat(),'end':end_d.isoformat(),'private':private,'employees':result}

    @app.patch('/api/ux/employee-directory/{eid}')
    def employee_update(eid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        with transaction(path) as db:
            emp=one(db,'SELECT * FROM employees WHERE id=?',(eid,))
            if not emp:raise HTTPException(404,'Employee not found.')
            _profile_seed(db,emp)
            rate=data.get('hourly_rate')
            if rate is not None:
                try:rate_cents=round(float(rate)*100)
                except Exception:raise HTTPException(422,'Enter a valid hourly pay rate.')
                if rate_cents<0 or rate_cents>100000:raise HTTPException(422,'Review the hourly pay rate.')
                db.execute('UPDATE employees SET hourly_cents=?,role=?,active=?,driver_verified=? WHERE id=?',(rate_cents,text(data.get('role',emp['role']),'Role',120),int(data.get('active',bool(emp['active']))),int(data.get('driver_verified',bool(emp['driver_verified']))),eid))
            db.execute("""UPDATE employee_directory_profiles SET email=?,phone=?,home_address=?,emergency_name=?,emergency_phone=?,start_date=?,team_lead=?,notes=?,updated_by=?,updated_at=? WHERE employee_id=?""",
              (text(data.get('email',''),'Email',240),text(data.get('phone',''),'Phone',80),text(data.get('home_address',''),'Home address',500),
               text(data.get('emergency_name',''),'Emergency contact',180),text(data.get('emergency_phone',''),'Emergency phone',80),
               text(data.get('start_date',''),'Start date',20),int(bool(data.get('team_lead'))),text(data.get('notes',''),'Notes',3000),u['id'],now(),eid))
            audit(db,u['id'],'Employee profile updated','employees',eid)
        return {'ok':True}

    @app.post('/api/ux/employee-directory/{eid}/license')
    async def employee_license(eid:str,file:UploadFile=File(...),u=Depends(auth)):
        require_role(u,MANAGERS)
        allowed={'image/jpeg':'.jpg','image/png':'.png','application/pdf':'.pdf'}
        mime=(file.content_type or '').lower()
        if mime not in allowed:raise HTTPException(422,'Upload a JPG, PNG, or PDF licence file.')
        content=await file.read(8*1024*1024+1)
        if len(content)>8*1024*1024:raise HTTPException(413,'Licence file must be 8 MB or smaller.')
        original=text(file.filename or 'license','Filename',240)
        folder=Path(path).parent/'employee_uploads';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        stored=secrets.token_hex(18)+allowed[mime];(folder/stored).write_bytes(content)
        with transaction(path) as db:
            emp=one(db,'SELECT * FROM employees WHERE id=?',(eid,))
            if not emp:raise HTTPException(404,'Employee not found.')
            _profile_seed(db,emp);old=one(db,'SELECT license_stored_name FROM employee_directory_profiles WHERE employee_id=?',(eid,))
            db.execute('UPDATE employee_directory_profiles SET license_original_name=?,license_stored_name=?,license_mime=?,updated_by=?,updated_at=? WHERE employee_id=?',(original,stored,mime,u['id'],now(),eid))
            audit(db,u['id'],'Driver licence file uploaded','employees',eid,{'filename':original,'mime':mime})
        if old and old.get('license_stored_name'):
            try:(folder/old['license_stored_name']).unlink(missing_ok=True)
            except Exception:pass
        return {'ok':True,'name':original}

    @app.get('/api/ux/employee-directory/{eid}/license')
    def employee_license_get(eid:str,u=Depends(auth)):
        if u['role'] not in PRIVATE_HR:raise HTTPException(403,'Licence files are manager/payroll-private.')
        with closing(connect(path)) as db:p=one(db,'SELECT * FROM employee_directory_profiles WHERE employee_id=?',(eid,))
        if not p or not p['license_stored_name']:raise HTTPException(404,'No licence file uploaded.')
        f=Path(path).parent/'employee_uploads'/p['license_stored_name']
        if not f.exists():raise HTTPException(404,'Licence file is missing from storage.')
        return FileResponse(f,media_type=p['license_mime'],filename=p['license_original_name'])

    @app.get('/api/ux/payroll-summary')
    def payroll_summary(start:str='',end:str='',u=Depends(auth)):
        if u['role'] not in OFFICE:raise HTTPException(403,'Payroll summary is restricted to office staff.')
        end_d=_date_arg(end,'end date') if end else date.today();start_d=_date_arg(start,'start date') if start else end_d-timedelta(days=13)
        if end_d<start_d or (end_d-start_d).days>366:raise HTTPException(422,'Choose a payroll range up to one year.')
        private=u['role'] in PRIVATE_HR
        with closing(connect(path)) as db:
            emps=rows(db,'SELECT * FROM employees WHERE active=1 ORDER BY name')
            times=rows(db,"""SELECT t.*,j.title,j.start_at FROM times t JOIN jobs j ON j.id=t.job_id WHERE j.status!='Cancelled' ORDER BY j.start_at""")
        details={e['id']:[] for e in emps}
        for t in times:
            day=_local_date(t['start_at'])
            if start_d.isoformat()<=day<=end_d.isoformat() and t['employee_id'] in details:
                mins=int(t['service_minutes'] or 0)+int(t['transport_minutes'] or 0)
                details[t['employee_id']].append({'time_id':t['id'],'job_id':t['job_id'],'job_title':t['title'],'date':day,'hours':round(mins/60,2),'status':t['status']})
        out=[]
        for e in emps:
            d=details[e['id']];hours=round(sum(x['hours'] for x in d),2);pending=sum(x['status']!='Approved' for x in d)
            r={'employee_id':e['id'],'employee_name':e['name'],'jobs':len({x['job_id'] for x in d}),'hours':hours,'pending_entries':pending,'status':'Ready' if d and not pending else ('Needs review' if d else 'No hours'),'details':d}
            if private:r.update({'hourly_cents':e['hourly_cents'],'gross_cents':round(hours*e['hourly_cents'])})
            out.append(r)
        return {'start':start_d.isoformat(),'end':end_d.isoformat(),'private':private,'employees':out,'total_hours':round(sum(x['hours'] for x in out),2)}

    @app.post('/api/ux/payroll-summary/approve')
    def payroll_approve(data:dict,u=Depends(auth)):
        if u['role'] not in PRIVATE_HR:raise HTTPException(403,'Only payroll managers can approve hours.')
        start=_date_arg(data.get('start'),'start date').isoformat();end=_date_arg(data.get('end'),'end date').isoformat()
        with transaction(path) as db:
            items=rows(db,"SELECT t.id,j.start_at FROM times t JOIN jobs j ON j.id=t.job_id WHERE t.status!='Approved' AND j.status!='Cancelled'")
            ids=[x['id'] for x in items if start<=_local_date(x['start_at'])<=end]
            for tid in ids:db.execute("UPDATE times SET status='Approved',approved_by=? WHERE id=?",(u['id'],tid))
            audit(db,u['id'],'Payroll range hours approved','times',start+'..'+end,{'count':len(ids)})
        return {'ok':True,'approved':len(ids)}


    @app.patch('/api/ux/payroll-time/{tid}')
    def payroll_time_edit(tid:str,data:dict,u=Depends(auth)):
        if u['role'] not in PRIVATE_HR:raise HTTPException(403,'Only payroll managers can correct payroll hours.')
        try:hours=float(data.get('hours'))
        except Exception:raise HTTPException(422,'Enter valid total hours.')
        if hours<0 or hours>24 or round(hours*60)!=hours*60:raise HTTPException(422,'Hours must resolve to whole minutes.')
        with transaction(path) as db:
            t=one(db,'SELECT * FROM times WHERE id=?',(tid,))
            if not t:raise HTTPException(404,'Time entry not found.')
            before=int(t['service_minutes'] or 0)+int(t['transport_minutes'] or 0)
            db.execute('UPDATE times SET service_minutes=?,transport_minutes=0 WHERE id=?',(round(hours*60),tid))
            audit(db,u['id'],'Payroll hours corrected','times',tid,{'before_minutes':before,'after_minutes':round(hours*60)})
        return {'ok':True}

    @app.get('/api/calls')
    def calls_feed(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            items=rows(db,"""SELECT c.*,l.client_name FROM calls c
              LEFT JOIN leads l ON l.id=c.lead_id
              ORDER BY c.created_at DESC LIMIT 100""")
        for item in items:
            raw=item.pop('payload_json','{}')
            try:item['details']=json.loads(raw or '{}')
            except Exception:item['details']={}
        return items

    @app.get('/api/ux/inbox')
    def inbox(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            return rows(db,"""SELECT m.*,l.client_name,c.claimed_by,c.claimed_at,c.resolved_at,u2.name claimed_name
              FROM messages m JOIN leads l ON l.id=m.lead_id
              LEFT JOIN inbox_claims c ON c.message_id=m.id LEFT JOIN users u2 ON u2.id=c.claimed_by
              WHERE lower(m.kind) LIKE '%incoming%' OR lower(m.kind) LIKE '%email%'
              ORDER BY m.created_at DESC LIMIT 100""")

    @app.post('/api/ux/inbox/{mid}/claim')
    def inbox_claim(mid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            m=one(db,'SELECT id,lead_id FROM messages WHERE id=?',(mid,))
            if not m:raise HTTPException(404,'Message not found.')
            lead=one(db,'SELECT id,assigned_to FROM leads WHERE id=?',(m['lead_id'],))
            if lead and lead.get('assigned_to') not in ('',u['id'],u['name']):
                raise HTTPException(409,'This client is already assigned to another team member.')
            old=one(db,'SELECT * FROM inbox_claims WHERE message_id=?',(mid,))
            if old and old.get('claimed_by')!=u['id'] and not old.get('resolved_at'):raise HTTPException(409,'This email is already claimed by another team member.')
            if old:db.execute('UPDATE inbox_claims SET claimed_by=?,claimed_at=?,resolved_at=NULL WHERE message_id=?',(u['id'],now(),mid))
            else:insert(db,'inbox_claims',dict(message_id=mid,claimed_by=u['id'],claimed_at=now(),resolved_at=None))
            if lead and not lead.get('assigned_to'):
                db.execute('UPDATE leads SET assigned_to=?,version=version+1,updated_at=? WHERE id=?',(u['id'],now(),lead['id']))
                db.execute("UPDATE tasks SET owner=? WHERE lead_id=? AND status!='Done' AND (owner='' OR owner IS NULL)",(u['id'],lead['id']))
            audit(db,u['id'],'Client email claimed and lead assigned','messages',mid,{'lead_id':m['lead_id'],'open_followups_assigned':True})
        return {'ok':True,'lead_id':m['lead_id']}

    @app.post('/api/ux/inbox/{mid}/resolve')
    def inbox_resolve(mid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            c=one(db,'SELECT * FROM inbox_claims WHERE message_id=?',(mid,))
            if not c:raise HTTPException(409,'Claim the email before marking it answered.')
            if c['claimed_by']!=u['id'] and u['role'] not in MANAGERS:raise HTTPException(403,'Only the claimant or a manager can mark this answered.')
            db.execute('UPDATE inbox_claims SET resolved_at=? WHERE message_id=?',(now(),mid));audit(db,u['id'],'Client email marked answered','messages',mid)
        return {'ok':True}

    @app.get('/api/ux/company-communications')
    def company_comms(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:return settings(db,'company_communications',{})

    @app.patch('/api/ux/company-communications')
    def company_comms_update(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        connected=text(data.get('connected_mailbox',''),'Connected mailbox',240).lower();send_as=text(data.get('send_as',''),'Send-as address',240).lower();reply_to=text(data.get('reply_to',''),'Reply-to address',240).lower()
        if '@' not in connected or '@' not in send_as or '@' not in reply_to:raise HTTPException(422,'Enter valid company email addresses.')
        cfg={'connected_mailbox':connected,'send_as':send_as,'reply_to':reply_to,'default_from':send_as,'secondary_inbox':connected,'client_email_rule':'Store mail in the connected Gmail mailbox and send as the Crown Movers domain alias after Google verifies the alias.','approved':bool(data.get('approved')),'note':text(data.get('note',''),'Note',2000)}
        with transaction(path) as db:put_setting(db,'company_communications',cfg);audit(db,u['id'],'Company communication defaults updated','settings','company_communications')
        return cfg
