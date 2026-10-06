"""Fleet, daily rates, reviewed payroll drafts and client aftercare."""
from contextlib import closing
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_HALF_UP
import csv, io, json, os
from fastapi import Depends, HTTPException
from fastapi.responses import Response
from .store import connect,transaction,rows,one,insert,audit,uid,now,dumps
from .pricing import number,cents
from .platform_common import *


def prefill_payroll(db,inv,doc,actor):
    """Invoice actuals propose hours, never release pay or replace locked entries."""
    terms=one(db,'SELECT * FROM paid_work_terms WHERE job_id=?',(inv['job_id'],))
    transport=terms['paid_transport_minutes'] if terms else None
    service=int((Decimal(str(doc['calculation']['actual_service_hours']))*60).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    team=rows(db,"SELECT e.* FROM employees e JOIN assignments a ON a.resource_id=e.id AND a.resource_type='employee' WHERE a.job_id=?",(inv['job_id'],))
    for e in team:
        if one(db,'SELECT id FROM payroll_drafts WHERE job_id=? AND employee_id=? AND invoice_revision=?',(inv['job_id'],e['id'],inv['revision'])):continue
        old=one(db,"SELECT * FROM payroll_drafts WHERE job_id=? AND employee_id=? AND status IN ('Approved','Locked') ORDER BY invoice_revision DESC LIMIT 1",(inv['job_id'],e['id']))
        recorded=one(db,'SELECT * FROM times WHERE job_id=? AND employee_id=?',(inv['job_id'],e['id']))
        sm=recorded['service_minutes'] if recorded else service
        tm=recorded['transport_minutes'] if recorded else transport
        state='Adjustment review' if old else 'Needs review'
        note=('Invoice revision changed. Earlier approved pay is preserved; reconcile differences.' if old else 'Proposed from recorded employee time.' if recorded else 'Proposed from invoice ACTUAL service hours. Confirm each worker and paid travel; discounts/minimums do not change worked time.')
        insert(db,'payroll_drafts',dict(id=uid('PAYROLL'),job_id=inv['job_id'],employee_id=e['id'],invoice_id=inv['id'],invoice_revision=inv['revision'],service_minutes=sm,transport_minutes=tm,hourly_cents=e['hourly_cents'],status=state,note=note,created_at=now()))
    audit(db,actor,'Payroll drafts prepared; no payroll payout','job_invoices',inv['id'],{'invoice_revision':inv['revision']})


def fleet_warnings(db,v):
    p=one(db,'SELECT * FROM fleet_profiles WHERE vehicle_id=?',(v['id'],)) or {}
    warnings=[]
    if not v['active']:warnings.append('Unavailable')
    if p.get('next_service_date') and p['next_service_date']<=now()[:10]:warnings.append('Maintenance due')
    if p.get('next_service_km') is not None and p.get('odometer_km',0)>=p['next_service_km']:warnings.append('Mileage service due')
    if p.get('inspection_due') and p['inspection_due']<now()[:10]:warnings.append('Inspection overdue')
    if one(db,"SELECT id FROM inspections WHERE vehicle_id=? AND severity='Major' AND cleared_at IS NULL",(v['id'],)):warnings.append('OPEN MAJOR DEFECT')
    latest=one(db,'SELECT * FROM inspections WHERE vehicle_id=? ORDER BY checked_at DESC LIMIT 1',(v['id'],))
    if p.get('check_required'):
        if not latest or datetime.fromisoformat(latest['checked_at'])<datetime.now(timezone.utc)-timedelta(hours=p.get('inspection_interval_hours',24)):warnings.append('Pre-use check required')
    return p,warnings,latest


def check_fleet_dispatch(db,trucks,crew,start):
    for vid in trucks:
        if one(db,"SELECT id FROM inspections WHERE vehicle_id=? AND severity='Major' AND cleared_at IS NULL",(vid,)):raise HTTPException(409,'Truck has an uncleared major defect.')
        p=one(db,'SELECT * FROM fleet_profiles WHERE vehicle_id=?',(vid,))
        if p and p['inspection_due'] and p['inspection_due']<start[:10]:raise HTTPException(409,'Truck inspection expires before this job. Update inspection evidence first.')
    for eid in crew:
        p=one(db,'SELECT * FROM staff_profiles WHERE employee_id=?',(eid,))
        if p and p['license_expiry'] and p['license_expiry']<start[:10]:
            e=one(db,'SELECT driver_verified FROM employees WHERE id=?',(eid,))
            if e and e['driver_verified']:raise HTTPException(409,'An assigned driver license expires before this job.')


def register_operations(app,path,auth):
    @app.get('/api/platform/settings')
    def get_settings(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            result={k:settings(db,k,{}) for k in ('billing_policy','client_terms','media_program','payroll_policy','automation_policy','deposit_policy')}
        return result

    @app.post('/api/platform/settings/{key}')
    def set_settings(key:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        if key not in ('billing_policy','client_terms','media_program','payroll_policy','automation_policy','deposit_policy'):raise HTTPException(422,'Unknown settings group.')
        if key in ('client_terms','media_program'):
            if not text(data.get('version',''),'Version',100):raise HTTPException(422,'A version is required.')
            field='release_text' if key=='media_program' else 'text'
            if len(text(data.get(field,''),'Approved terms',20000))<50:raise HTTPException(422,'Supply reviewed terms before activation.')
        if key=='media_program':
            if data.get('credit_mode') not in ('before_tax','after_tax'):raise HTTPException(422,'Choose whether the $50 credit is before or after taxes.')
            if not isinstance(data.get('channels'),list) or not data['channels'] or set(data['channels'])-{'Website','Social posts','Advertising'}:raise HTTPException(422,'Choose permitted promotional channels. Public review incentives are not supported.')
            data['amount_cents']=5000
        if key=='payroll_policy':
            try:
                d=date.fromisoformat(data.get('period_anchor',''))
                if d.weekday()!=3:raise ValueError()
            except ValueError:raise HTTPException(422,'Choose a Thursday as the approved biweekly period anchor.')
            data['period_days']=14
        if key=='billing_policy':
            from .invoices import validate_terms
            candidate={**data.get('terms',{}),'customer':{'name':'Policy validation'}}
            try:validate_terms(candidate)
            except (ValueError,KeyError,TypeError) as ex:raise HTTPException(422,str(ex))
        if key=='automation_policy':
            allowed={'preparation','invoice_followup','review_request','fleet_tasks'}
            if set(data)-allowed-{'review_url','enabled'}:raise HTTPException(422,'Unknown automation setting.')
            if data.get('review_url') and not str(data['review_url']).startswith('https://'):raise HTTPException(422,'Use a secure review URL.')
        if key=='deposit_policy':
            allowed={'enabled','recipient_name','recipient_email','auto_deposit','reference_note','approved','review_note'}
            if set(data)-allowed:raise HTTPException(422,'Unknown deposit setting.')
            email=text(data.get('recipient_email',''),'Interac recipient email',320)
            name=text(data.get('recipient_name',''),'Interac recipient name',200)
            note=text(data.get('reference_note',''),'Interac reference note',500)
            if data.get('enabled') or data.get('approved'):
                if not name:raise HTTPException(422,'Enter the Interac recipient name.')
                if not email or '@' not in email or '.' not in email.rsplit('@',1)[-1]:raise HTTPException(422,'Enter a valid Interac recipient email.')
            data={**data,'recipient_name':name,'recipient_email':email.lower(),'reference_note':note,'auto_deposit':bool(data.get('auto_deposit',True))}
        if data.get('approved') is True and not data.get('review_note'):raise HTTPException(422,'Record who reviewed these operational terms and rules.')
        with transaction(path) as db:
            put_setting(db,key,{**data,'updated_by':u['id'],'updated_at':now()});audit(db,u['id'],'Configuration updated','settings',key,{'approved':data.get('approved',False)})
        return {'ok':True}

    @app.get('/api/daily-rates')
    def daily_rates(u=Depends(auth)):
        require_role(u,SALES)
        with closing(connect(path)) as db:return rows(db,'SELECT * FROM daily_rates ORDER BY service_date DESC,movers,version DESC LIMIT 1000')

    @app.post('/api/daily-rates')
    def daily_rate(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        try:d=date.fromisoformat(data.get('service_date',''));n=int(number(data.get('movers'),'Crew',2,6));rate=cents(number(data.get('hourly_rate'),'Rate',1,5000))
        except (TypeError,ValueError) as ex:raise HTTPException(422,str(ex))
        if Decimal(str(data.get('movers')))!=n:raise HTTPException(422,'Crew size must be a whole number.')
        note=text(data.get('note',''),'Approval note',2000)
        if not note:raise HTTPException(422,'Explain the daily override.')
        with transaction(path) as db:
            v=db.execute('SELECT coalesce(max(version),0)+1 FROM daily_rates WHERE service_date=? AND movers=?',(d.isoformat(),n)).fetchone()[0]
            rec=dict(id=uid('RATE'),service_date=d.isoformat(),movers=n,hourly_cents=rate,version=v,approved_by=u['id'],note=note,created_at=now());insert(db,'daily_rates',rec)
            audit(db,u['id'],'Daily rate version published','daily_rates',rec['id'],{'existing_quotes_changed':False})
        return rec

    @app.post('/api/pricing-versions')
    def new_rates(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        from .pricing import seasonal_rate
        rates=data.get('rates');config=data.get('settings')
        if not isinstance(rates,list) or not isinstance(config,dict):raise HTTPException(422,'Supply the full seasonal table and explicit settings.')
        try:
            d=date(2024,1,1)
            while d.year==2024:
                for n in range(2,7):seasonal_rate(rates,d.isoformat(),n)
                d+=timedelta(days=1)
        except (ValueError,KeyError,TypeError) as ex:raise HTTPException(422,'Rate table invalid: '+str(ex))
        rec=dict(id=uid('PRICE'),label=text(data.get('label','New pricing'),'Label',200),rates_json=dumps(rates),settings_json=dumps(config),created_at=now())
        with transaction(path) as db:insert(db,'pricing_versions',rec);audit(db,u['id'],'New rate draft saved','pricing_versions',rec['id'])
        return {'id':rec['id'],'approved':False}

    @app.get('/api/fleet')
    def fleet(u=Depends(auth)):
        with closing(connect(path)) as db:
            out=[]
            for v in rows(db,'SELECT * FROM vehicles ORDER BY name'):
                p,w,latest=fleet_warnings(db,v);pos=one(db,'SELECT * FROM positions WHERE vehicle_id=?',(v['id'],))
                if pos:
                    pos['stale']=(datetime.now(timezone.utc)-datetime.fromisoformat(pos['observed_at'])).total_seconds()>900
                out.append({**v,'profile':p,'warnings':w,'latest_check':latest,'position':pos,
                    'maintenance':rows(db,'SELECT * FROM maintenance WHERE vehicle_id=? ORDER BY created_at DESC',(v['id'],))})
            return out

    @app.put('/api/fleet/{vid}/profile')
    def fleet_profile(vid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','sales','dispatch'})
        allowed={'plate','vin','model','payload_kg','gvwr_kg','cargo_length_ft','odometer_km','next_service_km','next_service_date','inspection_due','inspection_interval_hours','check_required'}
        if set(data)-allowed:raise HTTPException(422,'Unknown vehicle fields.')
        with transaction(path) as db:
            if not one(db,'SELECT id FROM vehicles WHERE id=?',(vid,)):raise HTTPException(404,'Truck not found.')
            old=one(db,'SELECT * FROM fleet_profiles WHERE vehicle_id=?',(vid,)) or {}
            rec={k:text(data.get(k,old.get(k,'')),k,150) for k in ('plate','vin','model','next_service_date','inspection_due')}
            for k in ('next_service_date','inspection_due'):
                if rec[k]:
                    try:date.fromisoformat(rec[k])
                    except ValueError:raise HTTPException(422,'Use YYYY-MM-DD for inspection/service dates.')
            for k in ('payload_kg','gvwr_kg','cargo_length_ft','odometer_km','next_service_km'):
                val=data.get(k,old.get(k));rec[k]=float(number(val,k,0,10000000)) if val is not None and val!='' else (0 if k=='odometer_km' else None)
            if rec['odometer_km']<old.get('odometer_km',0):raise HTTPException(409,'Odometer cannot decrease. Ask management to reconcile the source.')
            rec.update(vehicle_id=vid,check_required=int(data.get('check_required',old.get('check_required',0)) is True or data.get('check_required')==1),inspection_interval_hours=int(number(data.get('inspection_interval_hours',24),'Check interval',1,168)),updated_at=now())
            db.execute('DELETE FROM fleet_profiles WHERE vehicle_id=?',(vid,));insert(db,'fleet_profiles',rec);audit(db,u['id'],'Fleet details reviewed','vehicles',vid)
        return {'ok':True}

    @app.post('/api/fleet/{vid}/inspection')
    def inspection(vid:str,data:dict,u=Depends(auth)):
        checklist=data.get('checklist',{})
        keys={'tires','lights','brakes','fluids','body','dollies_3','straps_2','blankets','paperwork','locked'}
        if not isinstance(checklist,dict) or set(checklist)!=keys or any(not isinstance(v,bool) for v in checklist.values()):raise HTTPException(422,'Complete every truck and equipment check.')
        severity=data.get('severity')
        if severity not in ('None','Minor','Major'):raise HTTPException(422,'Choose a defect classification.')
        notes=text(data.get('notes',''),'Inspection notes',5000)
        if (severity!='None' or not all(checklist.values())) and not notes:raise HTTPException(422,'Describe defects or missing equipment.')
        try:odo=float(number(data.get('odometer_km'),'Odometer',0,10000000))
        except ValueError as ex:raise HTTPException(422,str(ex))
        with transaction(path) as db:
            if not one(db,'SELECT id FROM vehicles WHERE id=?',(vid,)):raise HTTPException(404,'Truck not found.')
            jid=data.get('job_id') or None
            if u['role']=='crew':
                assigned(db,jid,u)
                if not one(db,"SELECT job_id FROM assignments WHERE job_id=? AND resource_type='vehicle' AND resource_id=?",(jid,vid)):raise HTTPException(403,'Truck is not assigned to this job.')
            elif jid:assigned(db,jid,u)
            p=one(db,'SELECT * FROM fleet_profiles WHERE vehicle_id=?',(vid,))
            if p and odo<p['odometer_km']:raise HTTPException(409,'Odometer is older than the saved reading.')
            rec=dict(id=uid('CHECK'),vehicle_id=vid,job_id=jid,checked_by=u['id'],checked_at=now(),odometer_km=odo,checklist_json=dumps(checklist),severity=severity,notes=notes)
            insert(db,'inspections',rec)
            db.execute('UPDATE fleet_profiles SET odometer_km=?,updated_at=? WHERE vehicle_id=?',(odo,now(),vid))
            if severity=='Major':db.execute('UPDATE vehicles SET active=0,version=version+1 WHERE id=?',(vid,))
            audit(db,u['id'],'Truck check recorded','inspections',rec['id'],{'severity':severity,'unavailable':severity=='Major'})
        return {**rec,'truck_blocked':severity=='Major'}

    @app.post('/api/inspections/{iid}/clear')
    def clear(iid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        note=text(data.get('repair_evidence',data.get('evidence','')),'Repair and inspection evidence',4000)
        if len(note)<12:raise HTTPException(422,'Record repair evidence and authorization before releasing the truck.')
        with transaction(path) as db:
            r=one(db,'SELECT * FROM inspections WHERE id=?',(iid,))
            if not r:raise HTTPException(404,'Inspection not found.')
            db.execute('UPDATE inspections SET cleared_by=?,cleared_at=?,clearance_note=? WHERE id=?',(u['id'],now(),note,iid))
            audit(db,u['id'],'Defect clearance recorded; activate truck separately','inspections',iid,{'evidence':note})
        return {'ok':True,'truck_automatically_activated':False}

    @app.post('/api/fleet/{vid}/maintenance')
    def maintenance(vid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','sales','dispatch'})
        title=text(data.get('title',''),'Work order',200)
        if not title:raise HTTPException(422,'Describe the work order.')
        due=text(data.get('due_date',''),'Due date',10)
        if due:
            try:date.fromisoformat(due)
            except ValueError:raise HTTPException(422,'Invalid due date.')
        with transaction(path) as db:
            if not one(db,'SELECT id FROM vehicles WHERE id=?',(vid,)):raise HTTPException(404,'Vehicle not found.')
            rec=dict(id=uid('SERVICE'),vehicle_id=vid,title=title,due_date=due,due_km=float(number(data['due_km'],'Service mileage',0,10000000)) if data.get('due_km') not in ('',None) else None,notes=text(data.get('notes','')),author=u['id'],created_at=now());insert(db,'maintenance',rec)
        return rec

    @app.post('/api/maintenance/{mid}/complete')
    def maintain_done(mid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','sales','dispatch'})
        note=text(data.get('notes',''),'Service evidence',3000)
        if not note:raise HTTPException(422,'Record the completed work or receipt reference.')
        with transaction(path) as db:
            db.execute("UPDATE maintenance SET status='Completed',completed_at=?,cost_cents=?,notes=? WHERE id=?",(now(),cents(number(data.get('cost',0),'Cost',0,100000)),note,mid))
            audit(db,u['id'],'Maintenance recorded','maintenance',mid,{'note':note})
        return {'ok':True}

    @app.get('/api/cases')
    def cases(u=Depends(auth)):
        with closing(connect(path)) as db:
            records=rows(db,'SELECT c.*,l.client_name FROM cases c JOIN leads l ON l.id=c.lead_id ORDER BY updated_at DESC')
            if u['role']=='crew':
                own={r['job_id'] for r in rows(db,"SELECT job_id FROM assignments WHERE resource_type='employee' AND resource_id=?",(u.get('employee_id'),))}
                records=[r for r in records if r['job_id'] in own]
            return records

    @app.post('/api/cases')
    def create_case(data:dict,u=Depends(auth)):
        with transaction(path) as db:
            lid=data.get('lead_id');jid=data.get('job_id') or None;project_write(db,lid,jid,u)
            rec=dict(id=uid('CASE'),lead_id=lid,job_id=jid,type=text(data.get('type','Incident'),'Type',100),title=text(data.get('title',''),'Title',200),detail=text(data.get('detail',''),'Details',12000),owner=text(data.get('owner',''),'Owner',200),created_by=u['id'],created_at=now(),updated_at=now())
            if not rec['title'] or not rec['detail']:raise HTTPException(422,'Describe the incident or return visit.')
            insert(db,'cases',rec);audit(db,u['id'],'Case opened','cases',rec['id'])
        return rec

    @app.patch('/api/cases/{cid}')
    def update_case(cid:str,data:dict,u=Depends(auth)):
        require_role(u,OFFICE)
        if data.get('status') not in ('Open','In review','Resolved'):raise HTTPException(422,'Choose a case status.')
        with transaction(path) as db:
            r=one(db,'SELECT * FROM cases WHERE id=?',(cid,))
            if not r or r['version']!=data.get('version'):raise HTTPException(409,'Case changed. Refresh it.')
            db.execute('UPDATE cases SET status=?,owner=?,resolution=?,version=version+1,updated_at=? WHERE id=?',(data['status'],text(data.get('owner',r['owner'])),text(data.get('resolution','')),now(),cid))
            audit(db,u['id'],'Case updated','cases',cid,{'status':data['status']})
        return {'ok':True}

    @app.post('/api/jobs/{jid}/paid-travel')
    def paid_travel(jid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','accounting'})
        try:m=number(data.get('transport_hours',data.get('hours')),'Paid travel hours',0,24)*60
        except ValueError as ex:raise HTTPException(422,str(ex))
        if m!=m.to_integral():raise HTTPException(422,'Use whole minutes.')
        note=text(data.get('basis',''),'Paid travel approval',2000)
        if not note:raise HTTPException(422,'Confirm the agreed paid-travel basis, not the dollar-valued transport charge.')
        with transaction(path) as db:
            assigned(db,jid,u)
            db.execute('DELETE FROM paid_work_terms WHERE job_id=?',(jid,));insert(db,'paid_work_terms',dict(job_id=jid,paid_transport_minutes=int(m),basis=note,approved_by=u['id'],approved_at=now()))
            audit(db,u['id'],'Paid travel reviewed','jobs',jid,{'minutes':int(m),'basis':note})
        return {'ok':True,'previous_payroll_unchanged':True}

    @app.get('/api/payroll-drafts')
    def payroll_list(u=Depends(auth)):
        with closing(connect(path)) as db:
            clause=' WHERE p.employee_id=?' if u['role']=='crew' else ''
            result=rows(db,'SELECT p.*,e.name AS employee_name,j.title AS job_title,j.start_at FROM payroll_drafts p JOIN employees e ON e.id=p.employee_id JOIN jobs j ON j.id=p.job_id'+clause+' ORDER BY j.start_at DESC,p.created_at DESC',(u.get('employee_id'),) if clause else ())
            cfg=settings(db,'payroll_policy',{})
        for r in result:
            r['estimated_pay_cents']=None if r['transport_minutes'] is None else int((Decimal(r['service_minutes']+r['transport_minutes']+r['extra_minutes']-r['break_minutes'])*r['hourly_cents']/60).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            r['period_start']=None
            if cfg.get('period_anchor'):
                anchor=date.fromisoformat(cfg['period_anchor']);day=datetime.fromisoformat(r['start_at']).astimezone(ZoneInfo('America/Toronto')).date()
                r['period_start']=(anchor+timedelta(days=((day-anchor).days//14)*14)).isoformat()
            if u['role']=='crew':r.pop('hourly_cents',None);r.pop('estimated_pay_cents',None)
        return result

    @app.patch('/api/payroll-drafts/{pid}')
    def adjust_draft(pid:str,data:dict,u=Depends(auth)):
        with transaction(path) as db:
            r=one(db,'SELECT * FROM payroll_drafts WHERE id=?',(pid,))
            if not r:raise HTTPException(404,'Time draft not found.')
            if u['role']=='crew' and r['employee_id']!=u.get('employee_id'):raise HTTPException(403,'Only your own time may be corrected.')
            if r['status'] in ('Approved','Locked','Adjustment review','Reconciled'):raise HTTPException(409,'Approved or adjustment records require accountant reconciliation; do not overwrite paid time.')
            if data.get('version')!=r['version']:raise HTTPException(409,'Time draft changed. Refresh first.')
            note=text(data.get('note',''),'Actual work explanation',2000)
            if not note:raise HTTPException(422,'Add an explanation.')
            values=[]
            try:
                for k in ('service_hours','transport_hours','break_hours','extra_hours'):
                    m=number(data.get(k,0),k,0,24)*60
                    if m!=m.to_integral():raise ValueError('Use whole minutes.')
                    values.append(int(m))
            except ValueError as ex:raise HTTPException(422,str(ex))
            if not 0<values[0]+values[1]+values[3]-values[2]<=1440:raise HTTPException(422,'Review total work time; multi-day work needs separate service phases.')
            db.execute('UPDATE payroll_drafts SET service_minutes=?,transport_minutes=?,break_minutes=?,extra_minutes=?,note=?,version=version+1 WHERE id=?',values+[note,pid]);audit(db,u['id'],'Payroll draft corrected','payroll_drafts',pid,{'minutes':values,'note':note})
        return {'ok':True}

    @app.post('/api/payroll-drafts/{pid}/approve')
    def approve_draft(pid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','accounting'})
        with transaction(path) as db:
            r=one(db,'SELECT * FROM payroll_drafts WHERE id=?',(pid,))
            if not r or r['version']!=data.get('version'):raise HTTPException(409,'Draft changed. Refresh first.')
            if r['status']!='Needs review' or r['transport_minutes'] is None or data.get('reviewed') is not True:raise HTTPException(422,'Review individual paid time and travel before approval; adjustment cases need separate reconciliation.')
            inv=one(db,'SELECT * FROM job_invoices WHERE id=?',(r['invoice_id'],))
            if inv['status']!='Issued' or inv['revision']!=r['invoice_revision']:raise HTTPException(409,'Invoice revision changed; use the new payroll review.')
            if one(db,"SELECT id FROM payroll_drafts WHERE job_id=? AND employee_id=? AND status IN ('Approved','Locked')",(r['job_id'],r['employee_id'])):raise HTTPException(409,'Pay already approved for this worker/job. Reconcile an adjustment instead of duplicating wages.')
            total=r['service_minutes']+r['transport_minutes']+r['extra_minutes']-r['break_minutes']
            if not 0<total<=1440 or r['hourly_cents']<=0:raise HTTPException(422,'Confirm valid paid hours and employee pay rate.')
            db.execute("UPDATE payroll_drafts SET status='Approved',approved_by=?,approved_at=?,version=version+1 WHERE id=?",(u['id'],now(),pid));audit(db,u['id'],'Individual hours approved; deductions and payout not processed','payroll_drafts',pid)
        return {'ok':True,'payroll_paid':False}

    @app.get('/api/payroll-export')
    def export(period_start:str,u=Depends(auth)):
        require_role(u,{'owner','accounting'})
        try:start=date.fromisoformat(period_start)
        except ValueError:raise HTTPException(422,'Use a valid period date.')
        with closing(connect(path)) as db:
            cfg=settings(db,'payroll_policy',{})
            if not cfg.get('approved') or not cfg.get('period_anchor'):raise HTTPException(409,'Approve the payroll period anchor first.')
            anchor=date.fromisoformat(cfg['period_anchor'])
            if (start-anchor).days%14:raise HTTPException(422,'Date is not the start of the configured biweekly period.')
            records=rows(db,"SELECT p.*,e.name,j.start_at FROM payroll_drafts p JOIN jobs j ON j.id=p.job_id JOIN employees e ON e.id=p.employee_id WHERE p.status IN ('Approved','Locked')")
        end=start+timedelta(days=13);buf=io.StringIO();w=csv.writer(buf);w.writerow(['Period start','Period end','Employee','Job ID','Service hours','Travel hours','Break hours','Extra hours','Hourly rate CAD','Straight time estimate CAD','Warning'])
        for r in records:
            day=datetime.fromisoformat(r['start_at']).astimezone(ZoneInfo('America/Toronto')).date()
            if not start<=day<=end:continue
            name=r['name'];name="'"+name if name.startswith(('=','+','-','@')) else name
            total=r['service_minutes']+r['transport_minutes']+r['extra_minutes']-r['break_minutes']
            pay=(Decimal(total)*r['hourly_cents']/6000).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            w.writerow([start,end,name,r['job_id'],r['service_minutes']/60,r['transport_minutes']/60,r['break_minutes']/60,r['extra_minutes']/60,r['hourly_cents']/100,str(pay),'Review statutory overtime, vacation, deductions and entitlements in payroll system. Not net pay.'])
        return Response(buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="payroll-review-{start}.csv"'})

    @app.get('/api/testimonials')
    def releases(u=Depends(auth)):
        with closing(connect(path)) as db:
            records=rows(db,'SELECT r.*,l.client_name,a.original_name,a.scan_status,c.invoice_id,c.amount_cents AS applied_cents FROM media_releases r JOIN leads l ON l.id=r.lead_id JOIN attachments a ON a.id=r.attachment_id LEFT JOIN project_credits c ON c.release_id=r.id ORDER BY signed_at DESC')
            if u['role']=='crew':
                lids={x['lead_id'] for x in rows(db,"SELECT j.lead_id FROM jobs j JOIN assignments a ON a.job_id=j.id WHERE a.resource_type='employee' AND a.resource_id=?",(u.get('employee_id'),))};records=[r for r in records if r['lead_id'] in lids]
            return records

    @app.post('/api/testimonials/{rid}/apply-credit')
    def credit(rid:str,data:dict,u=Depends(auth)):
        from .invoices import calculate_invoice
        with transaction(path) as db:
            r=one(db,'SELECT * FROM media_releases WHERE id=?',(rid,))
            if not r:raise HTTPException(404,'Release not found.')
            inv,calc=invoice_current(db,data.get('invoice_id'))
            j=assigned(db,inv['job_id'],u)
            if j['lead_id']!=r['lead_id']:raise HTTPException(422,'Video and invoice must belong to the same move project.')
            old=one(db,"SELECT * FROM project_credits WHERE lead_id=? AND program='testimonial'",(r['lead_id'],))
            if old:return {'ok':True,'already_applied':True,'invoice_id':old['invoice_id']}
            cfg=settings(db,'media_program',{})
            if not cfg.get('approved') or cfg.get('credit_mode') not in ('before_tax','after_tax'):raise HTTPException(409,'Management must approve the program and tax treatment first.')
            a=one(db,'SELECT * FROM attachments WHERE id=? AND deleted_at IS NULL',(r['attachment_id'],))
            if r['status']=='Withdrawn' or not a or a['scan_status']!='Clean':raise HTTPException(409,'A valid consent and safety-cleared video are required.')
            if inv['status']!='Draft' or j['status']!='Completed':raise HTTPException(409,'Apply the credit to a draft invoice after service completion.')
            terms=json.loads(inv['terms_json']);terms['testimonial_credit_cents']=5000;terms['testimonial_credit_mode']=json.loads(one(db,'SELECT snapshot_json FROM media_release_terms WHERE release_id=?',(rid,))['snapshot_json'])['credit_mode']
            if calculate_invoice(terms,json.loads(inv['actual_json']),verified_allocated(db,inv['id']))['balance_cents']<0:raise HTTPException(409,'This credit would require a refund. Ask management to reconcile it first.')
            rec=dict(id=uid('CREDIT'),lead_id=r['lead_id'],program='testimonial',release_id=rid,invoice_id=inv['id'],amount_cents=5000,state='Applied',author=u['id'],created_at=now());insert(db,'project_credits',rec)
            db.execute('UPDATE job_invoices SET terms_json=?,version=version+1,updated_at=? WHERE id=?',(dumps(terms),now(),inv['id']))
            audit(db,u['id'],'Optional testimonial participation credit applied','job_invoices',inv['id'],{'amount_cents':5000,'mode':cfg['credit_mode'],'release_id':rid,'not_a_public_review_incentive':True})
        return {'ok':True,'credit_cents':5000,'published':False}

    @app.post('/api/testimonials/{rid}/publication-review')
    def publication(rid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        if data.get('reviewed') is not True:raise HTTPException(422,'Confirm channels, consent and compensation disclosure.')
        with transaction(path) as db:
            r=one(db,'SELECT * FROM media_releases WHERE id=?',(rid,))
            if not r or r['status']=='Withdrawn':raise HTTPException(409,'Consent not available.')
            a=one(db,'SELECT * FROM attachments WHERE id=? AND deleted_at IS NULL',(r['attachment_id'],))
            if not a or a['scan_status']!='Clean':raise HTTPException(409,'File must pass safety review.')
            db.execute("UPDATE media_releases SET status='Approved for specified channels',reviewed_by=?,reviewed_at=? WHERE id=?",(u['id'],now(),rid));audit(db,u['id'],'Promotional publication approved; nothing posted','media_releases',rid)
        return {'ok':True,'published':False,'required_disclosure':'Customer received a $50 moving discount for participating in this testimonial.'}
