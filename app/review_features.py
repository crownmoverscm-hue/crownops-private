"""Review-build workflows added after field testing.

These features stay inside CrownOps' private test environment until explicitly
approved for production: time-off approvals, bulk crew time entry, annual rates,
materials management, and client acknowledgement documents.
"""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib, json, secrets
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .store import connect, transaction, rows, one, insert, audit, uid, now, dumps
from .platform_common import require_role, SALES, OFFICE, MANAGERS, text, queue_message
from .pricing import number, cents


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _date(value, label='Date'):
    try:
        return date.fromisoformat(str(value))
    except Exception:
        raise HTTPException(422, f'Enter a valid {label.lower()}.')


def _job_assigned(db, jid, u, *, write=False):
    j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
    if not j: raise HTTPException(404,'Job not found.')
    if write and u['role']=='crew':
        if not one(db,"SELECT job_id FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id') or '')):
            raise HTTPException(403,'Only the assigned team can change this job.')
    return j


def _employee_name(db,eid):
    e=one(db,'SELECT * FROM employees WHERE id=?',(eid,))
    if not e: raise HTTPException(404,'Employee not found.')
    return e


def _notify(db, kind, object_id, message):
    insert(db,'manager_notifications',dict(id=uid('NOTICE'),kind=kind,object_id=object_id,message=message,created_at=now()))


def _timeoff_conflicts(db,eid,start_date,end_date,ignore=''):
    return rows(db,"""SELECT * FROM time_off_requests
      WHERE employee_id=? AND status IN ('Pending','Approved') AND id!=?
        AND start_date<=? AND end_date>=? ORDER BY start_date""",(eid,ignore,end_date,start_date))


def _annual_conflicts(db,start,end,ignore=''):
    return rows(db,"""SELECT * FROM annual_rate_periods
      WHERE active=1 AND id!=? AND start_date<=? AND end_date>=? ORDER BY start_date""",(ignore,end,start))


def seed_review_defaults(path):
    """Seed only safe review defaults. Never sends messages or creates jobs."""
    with transaction(path) as db:
        # Mirror existing inventory into the editable material catalogue once.
        if not one(db,'SELECT id FROM material_catalog LIMIT 1'):
            for item in rows(db,'SELECT * FROM inventory ORDER BY category,name'):
                code='MAT_'+''.join(c if c.isalnum() else '_' for c in item['name'].upper()).strip('_')[:36]
                base=code; n=2
                while one(db,'SELECT id FROM material_catalog WHERE code=?',(code,)):
                    code=f'{base}_{n}';n+=1
                insert(db,'material_catalog',dict(id=uid('MAT'),code=code,name=item['name'],category=item['category'],sell_cents=item['sell_cents'],cost_cents=item['cost_cents'],quantity=item['quantity'],minimum=item['minimum'],location=item['location'],taxable=1,active=1,updated_by='migration',updated_at=now()))
        # Build an editable current/next-year rate calendar from the approved source snapshot once.
        if not one(db,'SELECT id FROM annual_rate_periods LIMIT 1'):
            pv=one(db,'SELECT * FROM pricing_versions WHERE approved=1 ORDER BY created_at DESC LIMIT 1') or one(db,'SELECT * FROM pricing_versions ORDER BY created_at DESC LIMIT 1')
            if pv:
                source=json.loads(pv['rates_json'])
                base_year=date.today().year
                for year in (base_year,base_year+1):
                    for row in source:
                        sm,sd,em,ed=int(row['Start Month']),int(row['Start Day']),int(row['End Month']),int(row['End Day'])
                        # Cross-year low season is split into Jan-Mar and Nov-Dec for a clean yearly calendar.
                        ranges=[]
                        if (sm,sd)>(em,ed):
                            ranges=[(date(year,1,1),date(year,em,ed)),(date(year,sm,sd),date(year,12,31))]
                        else:
                            ranges=[(date(year,sm,sd),date(year,em,ed))]
                        for st,en in ranges:
                            insert(db,'annual_rate_periods',dict(id=uid('RATEPERIOD'),label=str(row['Season / Date Range']),start_date=st.isoformat(),end_date=en.isoformat(),
                                rate_2_cents=cents(row['2 movers']),rate_3_cents=cents(row['3 movers']),rate_4_cents=cents(row['4 movers']),rate_5_cents=cents(row['5 movers']),rate_6_cents=cents(row['6 movers']),
                                active=int(bool(pv['approved']) and year==base_year),version=1,
                                note='Copied pricing for review only; next-year values require separate owner approval.',approved_by='migration',created_at=now()))
        # Provide test-only document shells so the workflow can be reviewed before legal wording is approved.
        if not one(db,'SELECT id FROM document_templates LIMIT 1'):
            samples=[
                ('Scope / information difference acknowledgement','Additional work',
                 'TEST TEMPLATE - LEGAL REVIEW REQUIRED BEFORE LIVE USE.\n\nThe information available when the estimate was prepared differs from the conditions found for this service. The job-specific details below describe the difference. Additional work may increase the time and final charges under the rates and terms already accepted for the move. By approving, the client confirms the described difference and authorizes Crown Movers to continue with the additional work described below.'),
                ('Special handling / liability acknowledgement','Liability',
                 'TEST TEMPLATE - LEGAL REVIEW REQUIRED BEFORE LIVE USE.\n\nThe client has requested work or handling that requires a separate acknowledgement. The job-specific details below describe the item, condition, access issue, or requested method. By approving, the client confirms that the described condition was reviewed before the team proceeds. Replace this test wording with lawyer-approved language before production.'),
                ('Existing condition acknowledgement','Condition',
                 'TEST TEMPLATE - LEGAL REVIEW REQUIRED BEFORE LIVE USE.\n\nThe team identified an existing condition before continuing the service. The job-specific details below describe what was observed. By approving, the client confirms that the condition was disclosed and documented before the related work continued. Replace this test wording with lawyer-approved language before production.')]
            for name_,cat,body in samples:
                insert(db,'document_templates',dict(id=uid('DOC'),name=name_,category=cat,body=body,active=1,manager_only=0,note='Fictional/test wording only; legal review required.',created_by='review-seed',created_at=now(),updated_at=now()))


def register_review_features(app,path,auth,public_url):
    # ---------- Time off ----------
    @app.get('/api/time-off')
    def time_off_list(u=Depends(auth)):
        with closing(connect(path)) as db:
            if u['role']=='crew':
                eid=u.get('employee_id') or ''
                items=rows(db,"""SELECT r.*,e.name AS employee_name FROM time_off_requests r
                   JOIN employees e ON e.id=r.employee_id WHERE r.employee_id=? ORDER BY r.start_date DESC,r.requested_at DESC""",(eid,))
            else:
                require_role(u,OFFICE)
                items=rows(db,"""SELECT r.*,e.name AS employee_name FROM time_off_requests r
                   JOIN employees e ON e.id=r.employee_id ORDER BY r.start_date,r.employee_id""")
        return items

    @app.post('/api/time-off')
    def time_off_request(data:dict,u=Depends(auth)):
        if u['role'] not in ('crew','owner','manager'): raise HTTPException(403,'Only employees or management can submit a time-off request.')
        eid=u.get('employee_id') if u['role']=='crew' else data.get('employee_id')
        if not eid: raise HTTPException(422,'Choose the employee requesting time off.')
        start=_date(data.get('start_date'),'Start date'); end=_date(data.get('end_date'),'End date')
        if end<start: raise HTTPException(422,'End date cannot be before start date.')
        if (end-start).days>60: raise HTTPException(422,'Submit longer absences as separate requests for review.')
        note=text(data.get('note',''),'Request note',2000)
        with transaction(path) as db:
            emp=_employee_name(db,eid)
            conflicts=_timeoff_conflicts(db,eid,start.isoformat(),end.isoformat())
            if conflicts: raise HTTPException(409,'This employee already has a pending or approved request overlapping those dates.')
            rid=uid('OFF')
            insert(db,'time_off_requests',dict(id=rid,employee_id=eid,start_date=start.isoformat(),end_date=end.isoformat(),status='Pending',note=note,requested_by=u['id'],requested_at=now()))
            _notify(db,'time_off',rid,f"{emp['name']} requested OFF {start.isoformat()} to {end.isoformat()}.")
            audit(db,u['id'],'Time off requested','time_off_requests',rid,{'employee_id':eid,'start':start.isoformat(),'end':end.isoformat()})
        return {'id':rid,'status':'Pending','manager_notified':True}

    @app.post('/api/time-off/{rid}/review')
    def time_off_review(rid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        decision=data.get('decision')
        if decision not in ('Approved','Declined'): raise HTTPException(422,'Choose Approve or Decline.')
        note=text(data.get('note',''),'Manager review note',2000)
        with transaction(path) as db:
            r=one(db,'SELECT * FROM time_off_requests WHERE id=?',(rid,))
            if not r or r['status']!='Pending': raise HTTPException(409,'Only a pending request can be reviewed.')
            from .staff_records import schedule_identity
            if schedule_identity(db,u)['id']==r['employee_id']:
                raise HTTPException(403,'Another manager or owner must review this request.')
            db.execute('UPDATE time_off_requests SET status=?,reviewed_by=?,reviewed_at=?,review_note=? WHERE id=?',(decision,u['id'],now(),note,rid))
            emp=_employee_name(db,r['employee_id'])
            if decision=='Approved':
                # Detect existing assignments using Montreal service dates so approval never silently conflicts with a job.
                from zoneinfo import ZoneInfo
                assigned_jobs=rows(db,"""SELECT j.id,j.title,j.start_at,j.end_at FROM assignments a JOIN jobs j ON j.id=a.job_id
                    WHERE a.resource_type='employee' AND a.resource_id=? AND j.status!='Cancelled'""",(r['employee_id'],))
                conflicts=[]
                for job in assigned_jobs:
                    from .staff_portal import instant
                    first=instant(job['start_at']).astimezone(ZoneInfo('America/Toronto')).date().isoformat()
                    last=(instant(job['end_at'])-timedelta(microseconds=1)).astimezone(ZoneInfo('America/Toronto')).date().isoformat()
                    if first<=r['end_date'] and last>=r['start_date']:conflicts.append(job)
                if conflicts:
                    raise HTTPException(409,'This employee is already assigned during the requested dates. Reassign those jobs before approving OFF.')
            audit(db,u['id'],'Time off '+decision.lower(),'time_off_requests',rid,{'employee_id':r['employee_id'],'note':note})
            from .staff_portal import notify, employee_users
            notify(db,employee_users(db,r['employee_id']), 'Time off '+decision.lower(), 'time_off', rid)
        return {'ok':True,'status':decision}

    @app.post('/api/time-off/{rid}/cancel')
    def time_off_cancel(rid:str,data:dict,u=Depends(auth)):
        if u['role']!='crew': raise HTTPException(403,'The employee must cancel their own approved OFF request.')
        with transaction(path) as db:
            r=one(db,'SELECT * FROM time_off_requests WHERE id=?',(rid,))
            if not r or r['employee_id']!=u.get('employee_id'): raise HTTPException(403,'You can cancel only your own time-off request.')
            if r['status'] not in ('Pending','Approved'): raise HTTPException(409,'This request is not active.')
            prior=r['status']
            db.execute("UPDATE time_off_requests SET status='Cancelled',cancelled_at=? WHERE id=?",(now(),rid))
            emp=_employee_name(db,r['employee_id'])
            _notify(db,'time_off_cancelled',rid,f"{emp['name']} cancelled {prior.lower()} OFF {r['start_date']} to {r['end_date']} and is available again.")
            audit(db,u['id'],'Time off cancelled by employee','time_off_requests',rid,{'prior_status':prior})
        return {'ok':True,'status':'Cancelled','manager_notified':True}

    @app.get('/api/manager-notifications')
    def notifications(u=Depends(auth)):
        require_role(u,MANAGERS)
        with closing(connect(path)) as db:return rows(db,"SELECT * FROM manager_notifications ORDER BY created_at DESC LIMIT 200")

    @app.post('/api/manager-notifications/{nid}/read')
    def read_notice(nid:str,u=Depends(auth)):
        require_role(u,MANAGERS)
        with transaction(path) as db:
            db.execute("UPDATE manager_notifications SET status='Read',read_at=? WHERE id=?",(now(),nid))
        return {'ok':True}

    # ---------- Bulk team hours ----------
    @app.get('/api/jobs/{jid}/team-hours')
    def team_hours(jid:str,u=Depends(auth)):
        with closing(connect(path)) as db:
            j=_job_assigned(db,jid,u,write=False)
            team=rows(db,"""SELECT e.id,e.name,e.role,e.driver_verified,t.id AS time_id,t.service_minutes,t.transport_minutes,t.note,t.status
              FROM assignments a JOIN employees e ON e.id=a.resource_id AND a.resource_type='employee'
              LEFT JOIN times t ON t.job_id=a.job_id AND t.employee_id=e.id
              WHERE a.job_id=? ORDER BY e.name""",(jid,))
            inv=one(db,'SELECT actual_json FROM job_invoices WHERE job_id=?',(jid,))
            paid=one(db,'SELECT paid_transport_minutes FROM paid_work_terms WHERE job_id=?',(jid,))
        actual=json.loads(inv['actual_json']) if inv else {}
        return {'job':{'id':j['id'],'title':j['title'],'status':j['status']},'employees':team,
                'invoice_service_hours':actual.get('service_hours'),'approved_transport_hours':None if not paid else paid['paid_transport_minutes']/60}

    @app.post('/api/jobs/{jid}/team-hours')
    def team_hours_save(jid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','manager','dispatch'})
        selected=data.get('employee_ids')
        if not isinstance(selected,list) or not selected: raise HTTPException(422,'Select at least one assigned employee.')
        if len(set(selected))!=len(selected) or len(selected)>30: raise HTTPException(422,'Invalid employee selection.')
        try:
            common_s=number(data.get('service_hours'),'Service hours',0,24);common_t=number(data.get('transport_hours',0),'Transport hours',0,24)
            if common_s+common_t<=0 or common_s+common_t>24: raise ValueError('Review the common service and transport hours.')
            if common_s*60!=(common_s*60).to_integral() or common_t*60!=(common_t*60).to_integral(): raise ValueError('Hours must resolve to whole minutes.')
        except ValueError as ex: raise HTTPException(422,str(ex))
        exceptions=data.get('exceptions',{}) or {}
        if not isinstance(exceptions,dict): raise HTTPException(422,'Invalid individual adjustments.')
        with transaction(path) as db:
            j=_job_assigned(db,jid,u,write=True)
            if j['status'] not in ('In progress','Completed'): raise HTTPException(409,'Record team hours only after the job has started.')
            assigned_ids={r['resource_id'] for r in rows(db,"SELECT resource_id FROM assignments WHERE job_id=? AND resource_type='employee'",(jid,))}
            if set(selected)-assigned_ids: raise HTTPException(422,'Every selected employee must be assigned to this job.')
            changed=[]
            for eid in selected:
                s,t=common_s,common_t; note=text(data.get('note',''),'Team time note',2000)
                if eid in exceptions:
                    ex=exceptions[eid]
                    try:
                        s=number(ex.get('service_hours'),'Individual service hours',0,24);t=number(ex.get('transport_hours',0),'Individual transport hours',0,24)
                        if s+t<=0 or s+t>24 or s*60!=(s*60).to_integral() or t*60!=(t*60).to_integral():raise ValueError('Review individual hours; use whole minutes.')
                    except ValueError as err: raise HTTPException(422,str(err))
                    enote=text(ex.get('note',''),'Individual adjustment reason',2000)
                    if s!=common_s or t!=common_t:
                        if not enote: raise HTTPException(422,'Explain why an individual employee differs from the team hours.')
                        note=enote
                existing=one(db,'SELECT * FROM times WHERE job_id=? AND employee_id=?',(jid,eid))
                if existing and existing['status']=='Approved': raise HTTPException(409,'Approved time cannot be overwritten. Reconcile it as a payroll adjustment.')
                sm,tm=int(s*60),int(t*60)
                if existing:
                    db.execute("UPDATE times SET service_minutes=?,transport_minutes=?,note=?,status='Submitted',submitted_by=?,approved_by=NULL WHERE id=?",(sm,tm,note,u['id'],existing['id']))
                    tid=existing['id']
                else:
                    tid=uid('TIME');insert(db,'times',dict(id=tid,job_id=jid,employee_id=eid,service_minutes=sm,transport_minutes=tm,note=note,status='Submitted',submitted_by=u['id'],created_at=now()))
                changed.append({'employee_id':eid,'service_minutes':sm,'transport_minutes':tm})
            audit(db,u['id'],'Team hours submitted in one batch','times',jid,{'entries':changed})
        return {'ok':True,'submitted':len(changed),'payroll_processed':False}

    @app.post('/api/jobs/{jid}/team-hours/approve')
    def team_hours_approve(jid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','manager','accounting'})
        if data.get('reviewed') is not True: raise HTTPException(422,'Confirm you reviewed the individual employee hours.')
        with transaction(path) as db:
            j=_job_assigned(db,jid,u,write=False)
            if j['status']!='Completed': raise HTTPException(409,'Complete the service before approving employee hours.')
            pending=rows(db,"SELECT * FROM times WHERE job_id=? AND status!='Approved'",(jid,))
            if not pending: return {'ok':True,'approved':0}
            for r in pending: db.execute("UPDATE times SET status='Approved',approved_by=? WHERE id=?",(u['id'],r['id']))
            audit(db,u['id'],'Team hours approved','times',jid,{'count':len(pending)})
        return {'ok':True,'approved':len(pending),'payroll_processed':False}

    # ---------- Annual pricing ----------
    @app.get('/api/annual-rates')
    def annual_rates(year:int|None=None,u=Depends(auth)):
        require_role(u,SALES)
        with closing(connect(path)) as db:
            if year:
                start=f'{year:04d}-01-01';end=f'{year:04d}-12-31'
                return rows(db,'SELECT * FROM annual_rate_periods WHERE end_date>=? AND start_date<=? ORDER BY start_date,version',(start,end))
            return rows(db,'SELECT * FROM annual_rate_periods ORDER BY start_date,version')

    @app.post('/api/annual-rates')
    def annual_rate_create(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        start=_date(data.get('start_date'),'Start date');end=_date(data.get('end_date'),'End date')
        if end<start: raise HTTPException(422,'End date cannot be before start date.')
        label=text(data.get('label',''),'Rate period label',160)
        if not label: raise HTTPException(422,'Name this rate period.')
        note=text(data.get('note',''),'Approval note',2000)
        if not note: raise HTTPException(422,'Add a management approval note.')
        rates={}
        try:
            for n in range(2,7): rates[n]=cents(number(data.get(f'rate_{n}'),f'{n}-mover rate',1,5000))
        except ValueError as ex: raise HTTPException(422,str(ex))
        with transaction(path) as db:
            conflicts=_annual_conflicts(db,start.isoformat(),end.isoformat())
            if conflicts: raise HTTPException(409,'This period overlaps another active annual rate period. Edit/deactivate that period first.')
            rid=uid('RATEPERIOD')
            insert(db,'annual_rate_periods',dict(id=rid,label=label,start_date=start.isoformat(),end_date=end.isoformat(),rate_2_cents=rates[2],rate_3_cents=rates[3],rate_4_cents=rates[4],rate_5_cents=rates[5],rate_6_cents=rates[6],active=1,version=1,note=note,approved_by=u['id'],created_at=now()))
            audit(db,u['id'],'Annual rate period created','annual_rate_periods',rid,{'start':start.isoformat(),'end':end.isoformat(),'rates':rates})
        return {'id':rid,'version':1}

    @app.patch('/api/annual-rates/{rid}')
    def annual_rate_update(rid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        with transaction(path) as db:
            current=one(db,'SELECT * FROM annual_rate_periods WHERE id=?',(rid,))
            if not current: raise HTTPException(404,'Rate period not found.')
            if int(data.get('version',0))!=current['version']: raise HTTPException(409,'Rate period changed. Refresh first.')
            start=_date(data.get('start_date',current['start_date']),'Start date');end=_date(data.get('end_date',current['end_date']),'End date')
            if end<start: raise HTTPException(422,'End date cannot be before start date.')
            active=1 if data.get('active',bool(current['active'])) else 0
            if active and _annual_conflicts(db,start.isoformat(),end.isoformat(),rid): raise HTTPException(409,'This period overlaps another active annual rate period.')
            values=[]
            try:
                for n in range(2,7): values.append(cents(number(data.get(f'rate_{n}',current[f'rate_{n}_cents']/100),f'{n}-mover rate',1,5000)))
            except ValueError as ex: raise HTTPException(422,str(ex))
            note=text(data.get('note',current['note']),'Approval note',2000)
            if not note: raise HTTPException(422,'Add a management approval note.')
            label=text(data.get('label',current['label']),'Rate period label',160)
            db.execute('''UPDATE annual_rate_periods SET label=?,start_date=?,end_date=?,rate_2_cents=?,rate_3_cents=?,rate_4_cents=?,rate_5_cents=?,rate_6_cents=?,active=?,version=version+1,note=?,approved_by=? WHERE id=?''',
                       (label,start.isoformat(),end.isoformat(),*values,active,note,u['id'],rid))
            audit(db,u['id'],'Annual rate period updated','annual_rate_periods',rid,{'active':active,'start':start.isoformat(),'end':end.isoformat()})
        return {'ok':True}

    # ---------- Materials ----------
    @app.get('/api/materials')
    def materials(u=Depends(auth)):
        if u['role']=='crew': raise HTTPException(403,'Material catalogue editing is an office function.')
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            result=rows(db,'SELECT * FROM material_catalog ORDER BY active DESC,category,name')
        if u['role'] not in ('owner','manager'):
            for r in result:r.pop('cost_cents',None)
        return result

    @app.post('/api/materials')
    def material_create(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        code=text(data.get('code',''),'Material code',60).upper().replace(' ','_')
        name_=text(data.get('name',''),'Material name',160);category=text(data.get('category','Moving material'),'Category',120)
        if not code or not all(c.isalnum() or c in '_-' for c in code):raise HTTPException(422,'Use a simple unique material code.')
        if not name_:raise HTTPException(422,'Enter the material name.')
        try:
            sell=cents(number(data.get('sell_price',0),'Selling price',0,10000));cost=cents(number(data.get('cost_price',0),'Cost',0,10000))
            qty=float(number(data.get('quantity',0),'Quantity',0,100000));minimum=float(number(data.get('minimum',0),'Minimum',0,100000))
        except ValueError as ex: raise HTTPException(422,str(ex))
        with transaction(path) as db:
            if one(db,'SELECT id FROM material_catalog WHERE code=?',(code,)):raise HTTPException(409,'That material code already exists.')
            mid=uid('MAT');insert(db,'material_catalog',dict(id=mid,code=code,name=name_,category=category,sell_cents=sell,cost_cents=cost,quantity=qty,minimum=minimum,location=text(data.get('location','Warehouse'),'Location',160),taxable=int(data.get('taxable') is True),active=1,version=1,updated_by=u['id'],updated_at=now()))
            audit(db,u['id'],'Material created','material_catalog',mid,{'code':code,'sell_cents':sell})
        return {'id':mid}

    @app.patch('/api/materials/{mid}')
    def material_update(mid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        with transaction(path) as db:
            r=one(db,'SELECT * FROM material_catalog WHERE id=?',(mid,))
            if not r:raise HTTPException(404,'Material not found.')
            if int(data.get('version',0))!=r['version']:raise HTTPException(409,'Material changed. Refresh first.')
            try:
                sell=cents(number(data.get('sell_price',r['sell_cents']/100),'Selling price',0,10000));cost=cents(number(data.get('cost_price',r['cost_cents']/100),'Cost',0,10000))
                qty=float(number(data.get('quantity',r['quantity']),'Quantity',0,100000));minimum=float(number(data.get('minimum',r['minimum']),'Minimum',0,100000))
            except ValueError as ex:raise HTTPException(422,str(ex))
            active=1 if data.get('active',bool(r['active'])) else 0
            db.execute('''UPDATE material_catalog SET name=?,category=?,sell_cents=?,cost_cents=?,quantity=?,minimum=?,location=?,taxable=?,active=?,version=version+1,updated_by=?,updated_at=? WHERE id=?''',
                (text(data.get('name',r['name']),'Material name',160),text(data.get('category',r['category']),'Category',120),sell,cost,qty,minimum,text(data.get('location',r['location']),'Location',160),int(data.get('taxable',bool(r['taxable']))),active,u['id'],now(),mid))
            audit(db,u['id'],'Material updated','material_catalog',mid,{'sell_cents':sell,'active':active})
        return {'ok':True}

    # ---------- Documents & approvals ----------
    @app.get('/api/document-templates')
    def templates(u=Depends(auth)):
        with closing(connect(path)) as db:
            if u['role']=='crew':
                return rows(db,'SELECT * FROM document_templates WHERE active=1 AND manager_only=0 ORDER BY category,name')
            require_role(u,OFFICE)
            return rows(db,'SELECT * FROM document_templates ORDER BY active DESC,category,name')

    @app.post('/api/document-templates')
    def template_create(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        name_=text(data.get('name',''),'Template name',180);body=text(data.get('body',''),'Template wording',20000)
        if not name_ or len(body)<50:raise HTTPException(422,'Enter a template name and complete reviewed wording.')
        with transaction(path) as db:
            tid=uid('DOC');insert(db,'document_templates',dict(id=tid,name=name_,category=text(data.get('category','Acknowledgement'),'Category',120),version=1,body=body,active=1,manager_only=int(data.get('manager_only') is True),note=text(data.get('note',''),'Template note',2000),created_by=u['id'],created_at=now(),updated_at=now()))
            audit(db,u['id'],'Document template created','document_templates',tid)
        return {'id':tid}

    @app.patch('/api/document-templates/{tid}')
    def template_update(tid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        with transaction(path) as db:
            r=one(db,'SELECT * FROM document_templates WHERE id=?',(tid,))
            if not r:raise HTTPException(404,'Template not found.')
            if int(data.get('version',0))!=r['version']:raise HTTPException(409,'Template changed. Refresh first.')
            body=text(data.get('body',r['body']),'Template wording',20000)
            if len(body)<50:raise HTTPException(422,'Complete the reviewed wording before saving.')
            db.execute('''UPDATE document_templates SET name=?,category=?,body=?,active=?,manager_only=?,note=?,version=version+1,updated_at=? WHERE id=?''',
                (text(data.get('name',r['name']),'Template name',180),text(data.get('category',r['category']),'Category',120),body,int(data.get('active',bool(r['active']))),int(data.get('manager_only',bool(r['manager_only']))),text(data.get('note',r['note']),'Template note',2000),now(),tid))
            audit(db,u['id'],'Document template updated','document_templates',tid,{'prior_version':r['version']})
        return {'ok':True}

    @app.get('/api/document-approvals')
    def approvals(u=Depends(auth)):
        with closing(connect(path)) as db:
            base='''SELECT a.*,t.name AS template_name,l.client_name,j.title AS job_title FROM document_approvals a
                    JOIN document_templates t ON t.id=a.template_id JOIN leads l ON l.id=a.lead_id LEFT JOIN jobs j ON j.id=a.job_id'''
            if u['role']=='crew':
                return rows(db,base+" WHERE a.job_id IN (SELECT job_id FROM assignments WHERE resource_type='employee' AND resource_id=?) ORDER BY a.created_at DESC",(u.get('employee_id') or '',))
            require_role(u,OFFICE)
            return rows(db,base+' ORDER BY a.created_at DESC')

    @app.post('/api/document-approvals')
    def approval_prepare(data:dict,u=Depends(auth)):
        if u['role'] not in OFFICE|{'crew'}:raise HTTPException(403,'Your role cannot prepare client acknowledgements.')
        tid=data.get('template_id');jid=data.get('job_id') or None;lid=data.get('lead_id')
        method=data.get('delivery_method','Copy link')
        if method not in ('Email','Text','Copy link'):raise HTTPException(422,'Choose Email, Text, or Copy link.')
        details=text(data.get('details',''),'Job-specific details',6000)
        if not details:raise HTTPException(422,'Describe the specific condition or extra work the client is acknowledging.')
        with transaction(path) as db:
            tmpl=one(db,'SELECT * FROM document_templates WHERE id=? AND active=1',(tid,))
            if not tmpl:raise HTTPException(404,'Active template not found.')
            if os.getenv('CROWNOPS_ENV','test')=='production' and 'TEST TEMPLATE - LEGAL REVIEW REQUIRED BEFORE LIVE USE.' in tmpl['body']:
                raise HTTPException(409,'This acknowledgement still contains test wording. Replace it with reviewed production wording before use.')
            if u['role']=='crew' and tmpl['manager_only']:raise HTTPException(403,'This template is management-only.')
            if jid:
                j=_job_assigned(db,jid,u,write=(u['role']=='crew'))
                if not lid:lid=j['lead_id']
                if j['lead_id']!=lid:raise HTTPException(422,'Job and client project do not match.')
            if not lid:raise HTTPException(422,'Choose a client project.')
            lead=one(db,'SELECT * FROM leads WHERE id=?',(lid,))
            if not lead:raise HTTPException(404,'Client not found.')
            recipient=text(data.get('recipient') or (lead['email'] if method=='Email' else lead['phone']),'Recipient',260)
            if not recipient:raise HTTPException(422,'Enter the client email or phone for this approval request.')
            rendered=tmpl['body']+'\n\nJOB-SPECIFIC DETAILS\n'+details
            raw=secrets.token_urlsafe(36);aid=uid('APPROVAL');exp=(datetime.now(timezone.utc)+timedelta(days=7)).isoformat(timespec='seconds')
            insert(db,'document_approvals',dict(id=aid,template_id=tmpl['id'],template_version=tmpl['version'],lead_id=lid,job_id=jid,rendered_body=rendered,recipient=recipient,delivery_method=method,status='Prepared',token_hash=_token_hash(raw),expires_at=exp,created_by=u['id'],created_at=now()))
            link=public_url.rstrip('/')+'/approval#token='+raw
            if method=='Email':
                queue_message(db,lid,'Approval requested | Crown Movers',f'Please review and approve the attached service acknowledgement using this private link:\n\n{link}\n\nNothing is approved until you submit the acknowledgement.', 'Document approval',u['id'],'document-approval:'+aid,jid)
            audit(db,u['id'],'Client approval request prepared','document_approvals',aid,{'template_id':tid,'method':method,'job_id':jid})
        sms=f'Crown Movers: please review and approve this service acknowledgement before we continue: {link}'
        return {'id':aid,'url':link,'email_draft_created':method=='Email','text_message':sms,'sent':False,'expires_at':exp}

    @app.post('/api/document-approvals/{aid}/cancel')
    def approval_cancel(aid:str,u=Depends(auth)):
        with transaction(path) as db:
            r=one(db,'SELECT * FROM document_approvals WHERE id=?',(aid,))
            if not r:raise HTTPException(404,'Approval request not found.')
            if u['role']=='crew':_job_assigned(db,r['job_id'],u,write=True)
            elif u['role'] not in OFFICE:raise HTTPException(403,'Your role cannot cancel this request.')
            if r['status'] not in ('Prepared',):raise HTTPException(409,'This request can no longer be cancelled.')
            db.execute("UPDATE document_approvals SET status='Cancelled' WHERE id=?",(aid,));audit(db,u['id'],'Client approval cancelled','document_approvals',aid)
        return {'ok':True}

    @app.get('/approval')
    def approval_page():
        from pathlib import Path
        root=Path(__file__).resolve().parent.parent
        return FileResponse(root/'app'/'static'/'approval.html')

    @app.get('/api/public/document-approval')
    def public_approval(token:str):
        th=_token_hash(token)
        with closing(connect(path)) as db:
            r=one(db,'''SELECT a.*,t.name AS template_name,l.client_name,j.title AS job_title FROM document_approvals a
               JOIN document_templates t ON t.id=a.template_id JOIN leads l ON l.id=a.lead_id LEFT JOIN jobs j ON j.id=a.job_id WHERE a.token_hash=?''',(th,))
        if not r:raise HTTPException(404,'This approval link is invalid.')
        if r['status']=='Prepared' and r['expires_at']<now():
            with transaction(path) as db:db.execute("UPDATE document_approvals SET status='Expired' WHERE id=? AND status='Prepared'",(r['id'],))
            r['status']='Expired'
        return {k:r[k] for k in ('id','template_name','template_version','client_name','job_title','rendered_body','status','expires_at','signed_name','response_note')}

    @app.post('/api/public/document-approval')
    def public_approval_submit(data:dict):
        token=data.get('token','');decision=data.get('decision')
        if decision not in ('Approved','Declined'):raise HTTPException(422,'Choose Approve or Decline.')
        signed=text(data.get('signed_name',''),'Full name',180)
        if decision=='Approved' and not signed:raise HTTPException(422,'Enter your full name to approve.')
        note=text(data.get('response_note',''),'Response note',2000)
        th=_token_hash(token)
        with transaction(path) as db:
            r=one(db,'SELECT * FROM document_approvals WHERE token_hash=?',(th,))
            if not r:raise HTTPException(404,'This approval link is invalid.')
            if r['status']!='Prepared':raise HTTPException(409,'This approval request is already closed.')
            if r['expires_at']<now():
                db.execute("UPDATE document_approvals SET status='Expired' WHERE id=?",(r['id'],));raise HTTPException(410,'This approval link has expired. Ask Crown Movers for a new one.')
            if decision=='Approved':db.execute("UPDATE document_approvals SET status='Approved',approved_at=?,signed_name=?,response_note=? WHERE id=?",(now(),signed,note,r['id']))
            else:db.execute("UPDATE document_approvals SET status='Declined',declined_at=?,signed_name=?,response_note=? WHERE id=?",(now(),signed,note,r['id']))
            audit(db,'client:'+r['lead_id'],'Client document '+decision.lower(),'document_approvals',r['id'],{'signed_name':signed})
        return {'ok':True,'status':decision}
