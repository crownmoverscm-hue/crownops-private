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
from .staff_records import can_view_pay
from . import worked_hours
from .job_planning import can_plan_jobs
from .platform_common import require_role, OFFICE, MANAGERS, text, settings, put_setting, invoice_current
from .operations import hos_dispatch_status
from .crm_backlog import callback_backlog_preview, archive_callbacks, restore_callback
from .employee_contacts import register_employee_contacts, PAY_PENDING

TORONTO=ZoneInfo('America/Toronto')
PRIVATE_HR={'owner','manager'}


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
        employee_id=e['id'],email='',phone=f'514-555-01{suffix.zfill(2)}' if e['id'].startswith('DEMO-') else '',home_address='',
        emergency_name='Emergency contact to confirm' if e['id'].startswith('DEMO-') else '',emergency_phone='',start_date='',notes='',
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
    from .security import job_resource_blockers,check_preuse
    resource_missing=job_resource_blockers(db,j)
    missing.extend(resource_missing)
    if crew and trucks and not resource_missing:
        try:check_preuse(db,j['id'])
        except HTTPException as e:missing.append(str(e.detail))
    if j.get('status')=='Tentative' or (j.get('status')=='Confirmed' and (j.get('plan_review_required') or not j.get('capacity_reviewed'))):missing.append('Save the completed job plan')
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
    hos=hos_dispatch_status(db,j['id'])
    if hos.get('required') and not hos.get('ready'):
        missing.extend(['HOS: '+x for x in hos.get('blockers',[])[:3]])
    day=_local_date(j['start_at'])
    for r in rows(db,"""SELECT e.name FROM assignments a JOIN employees e ON e.id=a.resource_id
      JOIN time_off_requests o ON o.employee_id=e.id AND o.status='Approved'
      WHERE a.job_id=? AND a.resource_type='employee' AND o.start_date<=? AND o.end_date>=?""",(j['id'],day,day)):
        missing.append(f"{r['name']} is approved OFF")
    missing=list(dict.fromkeys(missing))
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
    outcome_titles={'done':'Action completed','waiting':'Waiting on client','followup':'Follow-up scheduled',
      'quote_sent':'Quote sent','lost':'Opportunity closed as lost'}
    for x in rows(db,'SELECT * FROM action_outcomes WHERE lead_id=?',(lid,)):
        detail=' · '.join(v for v in ('Due '+x['followup_date'] if x['followup_date'] else '',x['note']) if v)
        add(x['created_at'],'outcome',outcome_titles.get(x['outcome'],x['outcome']),detail)
    for x in rows(db,"""SELECT i.*,j.title,r.issued_at FROM job_invoices i JOIN jobs j ON j.id=i.job_id
      LEFT JOIN invoice_revisions r ON r.invoice_id=i.id AND r.revision=i.revision WHERE j.lead_id=?""",(lid,)):
        add(x.get('issued_at') or x['created_at'],'invoice',x['number'],x['title'],x['status'])
    return sorted(items,key=lambda x:(x['at'],x['kind']=='outcome'),reverse=True)



def _action_reference(db,kind,action_id):
    kind=str(kind or '').strip()
    action_id=str(action_id or '').strip()
    if kind=='task':
        r=one(db,'SELECT id,lead_id,title,legacy_id,raw_json FROM tasks WHERE id=?',(action_id,))
        if not r:raise HTTPException(404,'Follow-up not found.')
        return {'kind':kind,'id':action_id,'lead_id':r.get('lead_id') or '','label':r.get('title') or 'Follow-up','inbox_managed':bool(json.loads(r.get('raw_json') or '{}').get('inbox_managed')),'conversation':str(r.get('legacy_id') or '').startswith('conversation-reminder:')}
    if kind=='email':
        r=one(db,'SELECT id,lead_id,subject FROM messages WHERE id=?',(action_id,))
        if not r:raise HTTPException(404,'Email not found.')
        return {'kind':kind,'id':action_id,'lead_id':r.get('lead_id') or '','label':r.get('subject') or 'Client email'}
    if kind=='payment':
        r=one(db,'SELECT id,lead_id,purpose FROM payments WHERE id=?',(action_id,))
        if not r:raise HTTPException(404,'Payment action not found.')
        return {'kind':kind,'id':action_id,'lead_id':'','label':r.get('purpose') or 'Payment verification'}
    if kind=='client-change':
        r=one(db,'SELECT id,lead_id FROM client_changes WHERE id=?',(action_id,))
        if not r:raise HTTPException(404,'Client change not found.')
        return {'kind':kind,'id':action_id,'lead_id':'','label':'Client portal update'}
    if kind=='job':
        r=one(db,'SELECT id,title FROM jobs WHERE id=?',(action_id,))
        if not r:raise HTTPException(404,'Job action not found.')
        return {'kind':kind,'id':action_id,'lead_id':'','label':r.get('title') or 'Job readiness'}
    if kind=='timeoff':
        r=one(db,'SELECT id FROM time_off_requests WHERE id=?',(action_id,))
        if not r:raise HTTPException(404,'Time-off action not found.')
        return {'kind':kind,'id':action_id,'lead_id':'','label':'Time-off request'}
    if kind=='fleet':
        r=one(db,'SELECT id,title FROM maintenance WHERE id=?',(action_id,))
        if not r:raise HTTPException(404,'Fleet action not found.')
        return {'kind':kind,'id':action_id,'lead_id':'','label':r.get('title') or 'Fleet maintenance'}
    if kind=='gmail-payment-batch':
        if action_id!='gmail-payment-review':raise HTTPException(404,'Payment review action not found.')
        exists=one(db,"""SELECT id FROM integration_events WHERE event_type IN ('gmail_bank_payment','gmail_wix_payment')
          AND status='Needs review' LIMIT 1""")
        if not exists:raise HTTPException(409,'There are no Gmail payment notices waiting for review.')
        return {'kind':kind,'id':action_id,'lead_id':'','label':'Gmail payment review'}
    raise HTTPException(422,'This action type cannot be assigned.')

def _action_user(db,user_id):
    user=one(db,'SELECT id,name,email,role,active FROM users WHERE id=? AND active=1',(user_id,))
    if not user or user.get('role') not in OFFICE:raise HTTPException(422,'Choose an active office team member.')
    return user

def _sync_client_owner(db,lead_id,target_id,actor,reason,strict=False):
    lead=one(db,'SELECT id,client_name,assigned_to FROM leads WHERE id=?',(lead_id,))
    if not lead:raise HTTPException(404,'Client project not found.')
    target=_action_user(db,target_id) if target_id else None
    current=str(lead.get('assigned_to') or '')
    if strict and current not in ('',target_id,target.get('name') if target else ''):
        raise HTTPException(409,'This client is already assigned to another team member.')
    if strict:
        task_conflict=one(db,"""SELECT id,owner FROM tasks WHERE lead_id=? AND status!='Done' AND historical=0
          AND COALESCE(legacy_id,'') NOT LIKE 'conversation-reminder:%' AND COALESCE(json_extract(raw_json,'$.inbox_managed'),0)=0 AND owner!='' AND owner NOT IN (?,?) LIMIT 1""",(lead_id,target_id,target.get('name') if target else ''))
        if task_conflict:raise HTTPException(409,'An open follow-up is already assigned to another team member.')
    db.execute('UPDATE leads SET assigned_to=?,updated_at=? WHERE id=?',(target_id,now(),lead_id))
    db.execute("UPDATE tasks SET owner=? WHERE lead_id=? AND status!='Done' AND historical=0 AND COALESCE(legacy_id,'') NOT LIKE 'conversation-reminder:%' AND COALESCE(json_extract(raw_json,'$.inbox_managed'),0)=0",(target_id,lead_id))
    audit(db,actor,reason,'leads',lead_id,{'from':current,'to':target_id,'open_followups_aligned':True,'unanswered_email_aligned':False})
    return {'lead_id':lead_id,'assigned_to':target_id,'assigned_name':target.get('name') if target else ''}

def _assign_generic_action(db,ref,target_id,actor,reason,strict=False):
    target=_action_user(db,target_id) if target_id else None
    current=one(db,'SELECT * FROM action_assignments WHERE action_type=? AND action_id=?',(ref['kind'],ref['id']))
    if strict and current and current.get('assigned_to')!=target_id:
        raise HTTPException(409,'This action is already claimed by another team member.')
    if target_id:
        db.execute("""INSERT INTO action_assignments(action_type,action_id,assigned_to,assigned_by,assigned_at)
          VALUES(?,?,?,?,?) ON CONFLICT(action_type,action_id) DO UPDATE SET
          assigned_to=excluded.assigned_to,assigned_by=excluded.assigned_by,assigned_at=excluded.assigned_at""",
          (ref['kind'],ref['id'],target_id,actor,now()))
    else:
        db.execute('DELETE FROM action_assignments WHERE action_type=? AND action_id=?',(ref['kind'],ref['id']))
    audit(db,actor,reason,'action_assignments',ref['kind']+':'+ref['id'],
      {'from':current.get('assigned_to') if current else '','to':target_id,'label':ref.get('label') or ''})
    return {'assigned_to':target_id,'assigned_name':target.get('name') if target else ''}


def _json_dict(value):
    try:
        data=json.loads(value or '{}')
        return data if isinstance(data,dict) else {}
    except Exception:
        return {}

def _action_workspace_data(db,ref):
    lead_id=str(ref.get('lead_id') or '')
    if not lead_id:raise HTTPException(422,'This action uses its dedicated operational screen.')
    lead=one(db,'SELECT * FROM leads WHERE id=?',(lead_id,))
    if not lead:raise HTTPException(404,'Client project not found.')
    owner=None
    if lead.get('assigned_to'):
        owner=one(db,'SELECT id,name,email,role FROM users WHERE id=? OR name=? LIMIT 1',(lead['assigned_to'],lead['assigned_to']))
    action={}
    if ref['kind']=='task':
        task=one(db,'SELECT * FROM tasks WHERE id=?',(ref['id'],))
        if not task:raise HTTPException(404,'Follow-up not found.')
        meta=_json_dict(task.get('raw_json'))
        action={'kind':'task','id':task['id'],'title':task['title'],'status':task['status'],'conversation':ref.get('conversation',False),
          'due_date':task.get('due_date') or '','created_at':task['created_at'],'detail':'','historical':bool(task['historical'])}
        call_id=str(meta.get('call_id') or '')
        if call_id:
            call=one(db,'SELECT * FROM calls WHERE id=?',(call_id,))
            if call:
                detail=_json_dict(call.get('payload_json'))
                action['call']={
                  'id':call['id'],'phone':detail.get('phone') or detail.get('Phone') or '',
                  'caller':detail.get('caller') or detail.get('Caller') or lead.get('client_name') or '',
                  'occurred_at':detail.get('occurred_at') or call['created_at'] or detail.get('call_time') or '',
                  'subject':detail.get('subject') or '',
                  'voicemail':bool(detail.get('voicemail')),
                  'attachments':detail.get('attachments') or []
                }
                action['detail']='Voicemail' if action['call']['voicemail'] else 'Missed call'
    elif ref['kind']=='email':
        msg=one(db,'SELECT * FROM messages WHERE id=?',(ref['id'],))
        if not msg:raise HTTPException(404,'Email not found.')
        claim=one(db,'SELECT * FROM inbox_claims WHERE message_id=?',(msg['id'],))
        action={'kind':'email','id':msg['id'],'title':msg.get('subject') or 'Client email',
          'status':'Answered' if claim and claim.get('resolved_at') else 'Unanswered',
          'created_at':msg['created_at'],'body':msg.get('body') or '','message_kind':msg.get('kind') or '',
          'claimed_by':claim.get('claimed_by') if claim else '','resolved_at':claim.get('resolved_at') if claim else ''}
    else:
        raise HTTPException(422,'Open this action from its dedicated operational screen.')
    open_tasks=rows(db,"""SELECT id,title,due_date,owner,status,created_at FROM tasks
      WHERE lead_id=? AND status!='Done' AND historical=0 ORDER BY CASE WHEN due_date='' THEN 1 ELSE 0 END,due_date,created_at""",(lead_id,))
    latest_quote=one(db,'SELECT id,revision,status,created_at FROM quotes WHERE lead_id=? ORDER BY revision DESC LIMIT 1',(lead_id,))
    history=_timeline(db,lead_id)[:12]
    client={k:lead.get(k,'') for k in ('id','client_name','email','phone','move_date','pickup','dropoff','stage','assigned_to','priority','language')}
    return {'action':action,'client':client,'owner':owner,'open_tasks':open_tasks,
      'latest_quote':latest_quote,'history':history,
      'outcomes':[] if action['status'] in ('Done','Answered') or action.get('historical') else ['done','waiting','followup','quote_sent','lost']}

def _complete_source_action(db,ref,actor):
    if ref['kind']=='task':
        db.execute("UPDATE tasks SET status='Done' WHERE id=?",(ref['id'],))
    elif ref['kind']=='email':
        claim=one(db,'SELECT * FROM inbox_claims WHERE message_id=?',(ref['id'],))
        if claim:
            db.execute('UPDATE inbox_claims SET resolved_at=?,claimed_by=? WHERE message_id=?',(now(),actor,ref['id']))
        else:
            insert(db,'inbox_claims',dict(message_id=ref['id'],claimed_by=actor,claimed_at=now(),resolved_at=now()))

def _record_action_outcome(db,ref,lead_id,outcome,note,followup_task_id,followup_date,actor):
    insert(db,'action_outcomes',dict(id=uid('OUTCOME'),action_type=ref['kind'],action_id=ref['id'],
      lead_id=lead_id,outcome=outcome,note=note,followup_task_id=followup_task_id or None,
      followup_date=followup_date or '',actor=actor,created_at=now()))

def register_ux_features(app,path,auth):
    register_employee_contacts(app,path,auth)
    from .company_ops import register_company_ops
    register_company_ops(app,path,auth)
    @app.get('/api/ux/callback-backlog')
    def callback_backlog(before:str='',u=Depends(auth)):
        require_role(u,MANAGERS)
        with closing(connect(path)) as db:
            return callback_backlog_preview(db,before or datetime.now(TORONTO).date().isoformat())

    @app.post('/api/ux/callback-backlog/archive')
    def archive_callback_backlog(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        with transaction(path) as db:
            return archive_callbacks(db,data,u['id'])

    @app.post('/api/ux/callback-backlog/{task_id}/restore')
    def restore_callback_backlog(task_id:str,u=Depends(auth)):
        require_role(u,MANAGERS)
        with transaction(path) as db:
            return restore_callback(db,task_id,u['id'])

    @app.get('/api/ux/today')
    def today_view(view:str='',u=Depends(auth)):
        require_role(u,OFFICE)
        today=datetime.now(TORONTO).date().isoformat()
        selected=(view or ('team' if u.get('role') in MANAGERS else 'mine')).strip().lower()
        if selected not in ('unclaimed','mine','team'):raise HTTPException(422,'Choose Unclaimed, Mine or Team.')
        with closing(connect(path)) as db:
            people={r['id']:r['name'] for r in rows(db,"SELECT id,name FROM users WHERE active=1")}
            generic_owners={(r['action_type'],r['action_id']):r['assigned_to'] for r in rows(db,'SELECT action_type,action_id,assigned_to FROM action_assignments')}
            jobs_all=rows(db,'SELECT * FROM jobs WHERE status!=\'Cancelled\' ORDER BY start_at')
            jobs=[];attention=[]
            def add_item(item,owner=''):
                owner=str(owner or generic_owners.get((item.get('type'),item.get('id')),'') or '').strip()
                item['owner']=owner
                item['owner_name']=people.get(owner,owner) if owner else ''
                item['ownership']='mine' if owner and owner in (u.get('id'),u.get('name')) else ('unclaimed' if not owner else 'other')
                attention.append(item)
            for j in jobs_all:
                if _local_date(j['start_at'])==today:
                    ready=_job_readiness(db,j);j['readiness']=ready
                    j['crew_names']=[r['name'] for r in rows(db,"SELECT e.name FROM assignments a JOIN employees e ON e.id=a.resource_id WHERE a.job_id=? AND a.resource_type='employee' ORDER BY e.name",(j['id'],))]
                    jobs.append(j)
                    if not ready['ready']:
                        add_item({'type':'job','id':j['id'],'title':j['title']+' needs attention','detail':', '.join(ready['missing']),'priority':'High'})
            pending_off=rows(db,"""SELECT o.id,o.start_date,o.end_date,e.name employee_name FROM time_off_requests o JOIN employees e ON e.id=o.employee_id WHERE o.status='Pending' ORDER BY o.requested_at""")
            for r in pending_off:
                add_item({'type':'timeoff','id':r['id'],'title':r['employee_name']+' requested time off','detail':r['start_date']+' to '+r['end_date'],'priority':'Normal'})
            due=rows(db,"""SELECT t.*,l.client_name,l.assigned_to FROM tasks t
              LEFT JOIN leads l ON l.id=t.lead_id
              WHERE t.status!='Done' AND t.historical=0 AND (t.due_date='' OR t.due_date<=?)
              ORDER BY CASE WHEN t.due_date='' THEN 1 ELSE 0 END,t.due_date,t.created_at""",(today,))
            for r in due:
                add_item({'type':'task','id':r['id'],'lead_id':r['lead_id'],'title':r['title'],
                  'detail':r.get('client_name') or 'Internal task','priority':'Normal'},r.get('owner') or r.get('assigned_to'))
            pending_changes=rows(db,"""SELECT c.id,c.lead_id,l.client_name,l.assigned_to,c.created_at
              FROM client_changes c JOIN leads l ON l.id=c.lead_id
              WHERE c.status='Pending' ORDER BY c.created_at""")
            for r in pending_changes:
                add_item({'type':'client-change','id':r['id'],'lead_id':r['lead_id'],'title':r['client_name']+' updated their portal',
                  'detail':'Review before changing accepted scope.','priority':'Normal'})
            unverified=rows(db,"""SELECT p.*,l.client_name,l.assigned_to FROM payments p JOIN leads l ON l.id=p.lead_id
              WHERE p.verified=0 AND p.amount_cents>0 ORDER BY p.created_at""")
            for r in unverified:
                add_item({'type':'payment','id':r['id'],'lead_id':r['lead_id'],'title':'Payment needs verification',
                  'detail':f"{r['client_name']} - CA${r['amount_cents']/100:,.2f}",'priority':'High'})
            gmail_payment=rows(db,"""SELECT * FROM integration_events
              WHERE event_type IN ('gmail_bank_payment','gmail_wix_payment') AND status='Needs review'
              ORDER BY created_at DESC""")
            if gmail_payment:
                add_item({'type':'gmail-payment-batch','id':'gmail-payment-review','title':'Review Gmail payment notices',
                  'detail':f"{len(gmail_payment)} notice{'s' if len(gmail_payment)!=1 else ''} waiting in Connections",
                  'priority':'High'})
            maint=rows(db,"SELECT m.*,v.name vehicle_name FROM maintenance m JOIN vehicles v ON v.id=m.vehicle_id WHERE m.status='Open' AND m.due_date!='' AND m.due_date<=? ORDER BY m.due_date",(today,))
            for r in maint:
                add_item({'type':'fleet','id':r['id'],'title':r['vehicle_name']+' maintenance due','detail':r['title']+' - '+r['due_date'],'priority':'High'})

            def matches(item,scope):
                if scope=='team':return True
                if scope=='unclaimed':return item['ownership']=='unclaimed'
                return item['ownership']=='mine'

            view_counts={scope:sum(1 for item in attention if matches(item,scope)) for scope in ('unclaimed','mine','team')}
            filtered=[item for item in attention if matches(item,selected)]
            filtered.sort(key=lambda x:(0 if x['priority']=='High' else 1,0 if x['ownership']=='mine' else 1,x['title']))
            stats={
              'jobs_today':len(jobs),'attention':len(filtered),'pending_time_off':sum(1 for x in filtered if x['type']=='timeoff'),
              'unverified_payments':sum(1 for x in filtered if x['type'] in ('payment','gmail-payment-batch')),
              'open_followups':sum(1 for x in filtered if x['type']=='task'),
              'pending_client_changes':sum(1 for x in filtered if x['type']=='client-change')
            }
        return {'date':today,'view':selected,'view_counts':view_counts,'stats':stats,'jobs':jobs,'attention':filtered}

    @app.get('/api/ux/action-assignees')
    def action_assignees(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            return rows(db,"SELECT id,name,email,role FROM users WHERE active=1 AND role IN ('owner','manager','sales','dispatch','accounting') ORDER BY name")

    @app.post('/api/ux/actions/{kind}/{action_id}/claim')
    def action_claim(kind:str,action_id:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            ref=_action_reference(db,kind,action_id)
            if ref.get('inbox_managed'):raise HTTPException(409,'Open Tasks in Inbox to update this independently assigned follow-up.')
            if ref.get('conversation'):raise HTTPException(409,'Open the conversation in Inbox to update its reminder or handler.')
            if kind=='email':
                from .conversations import claim_conversation
                return {'ok':True,**claim_conversation(db,ref['lead_id'],u)}
            if ref.get('lead_id'):
                return {'ok':True,**_sync_client_owner(db,ref['lead_id'],u['id'],u['id'],'Action claimed',strict=True)}
            return {'ok':True,**_assign_generic_action(db,ref,u['id'],u['id'],'Action claimed',strict=True)}

    @app.post('/api/ux/actions/{kind}/{action_id}/assign')
    def action_assign(kind:str,action_id:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        target_id=str(data.get('user_id') or '').strip()
        with transaction(path) as db:
            ref=_action_reference(db,kind,action_id)
            if ref.get('inbox_managed'):raise HTTPException(409,'Open Tasks in Inbox to update this independently assigned follow-up.')
            if ref.get('conversation'):raise HTTPException(409,'Open the conversation in Inbox to update its reminder or handler.')
            if kind=='email':raise HTTPException(409,'Use Inbox to hand over this conversation with a note.')
            if ref.get('lead_id'):
                return {'ok':True,**_sync_client_owner(db,ref['lead_id'],target_id,u['id'],'Action reassigned')}
            return {'ok':True,**_assign_generic_action(db,ref,target_id,u['id'],'Action reassigned')}
    @app.get('/api/ux/actions/{kind}/{action_id}/workspace')
    def action_workspace(kind:str,action_id:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            ref=_action_reference(db,kind,action_id)
            return _action_workspace_data(db,ref)

    @app.post('/api/ux/actions/{kind}/{action_id}/outcome')
    def action_outcome(kind:str,action_id:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','manager','sales','dispatch'})
        outcome=str(data.get('outcome') or '').strip().lower()
        allowed={'done','waiting','followup','quote_sent','lost'}
        if outcome not in allowed:raise HTTPException(422,'Choose a valid action outcome.')
        note=str(data.get('note') or '').strip()
        if len(note)>3000:raise HTTPException(422,'Keep the outcome note under 3,000 characters.')
        followup_date=str(data.get('followup_date') or '').strip()
        followup_title=str(data.get('followup_title') or '').strip()
        if len(followup_title)>300:raise HTTPException(422,'Keep the follow-up title under 300 characters.')
        with transaction(path) as db:
            ref=_action_reference(db,kind,action_id)
            if ref.get('inbox_managed'):raise HTTPException(409,'Open Tasks in Inbox to update this independently assigned follow-up.')
            if ref.get('conversation'):raise HTTPException(409,'Open the conversation in Inbox to update its reminder or handler.')
            if kind=='email':raise HTTPException(409,'Use Inbox to record this conversation outcome.')
            if not ref.get('lead_id'):raise HTTPException(422,'Use the dedicated operational screen for this action.')
            lead=one(db,'SELECT * FROM leads WHERE id=?',(ref['lead_id'],))
            if not lead:raise HTTPException(404,'Client project not found.')
            current=str(lead.get('assigned_to') or '')
            if current:
                current_user=one(db,'SELECT id,name FROM users WHERE id=? OR name=? LIMIT 1',(current,current))
                current_id=current_user.get('id') if current_user else current
                if current_id!=u['id'] and u['role'] not in MANAGERS:
                    raise HTTPException(403,'This client is assigned to another team member.')
            else:
                _sync_client_owner(db,lead['id'],u['id'],u['id'],'Action claimed during outcome',strict=True)
            if ref['kind']=='task':
                source=one(db,'SELECT status,historical FROM tasks WHERE id=?',(ref['id'],))
                if source['status']=='Done' or source['historical']:
                    raise HTTPException(409,'This action is completed or archived. Refresh the queue.')
            elif ref['kind']=='email':
                claim=one(db,'SELECT resolved_at FROM inbox_claims WHERE message_id=?',(ref['id'],))
                if claim and claim.get('resolved_at'):
                    raise HTTPException(409,'This email is already resolved. Refresh the queue.')
            else:
                raise HTTPException(422,'Use the dedicated operational screen for this action.')
            next_task_id=None
            if outcome=='followup':
                if not followup_date:raise HTTPException(422,'Choose the follow-up date.')
                due=_date_arg(followup_date,'follow-up date')
                today_local=datetime.now(TORONTO).date()
                if due<today_local:raise HTTPException(422,'Follow-up date cannot be in the past.')
                next_task_id=uid('TASK')
                title=followup_title or ('Follow up with '+(lead.get('client_name') or 'client'))
                owner=lead.get('assigned_to') or u['id']
                if owner not in (u['id'],u.get('name')) and u['role'] not in MANAGERS:
                    owner=u['id']
                insert(db,'tasks',dict(id=next_task_id,legacy_id=None,lead_id=lead['id'],title=title,
                  due_date=followup_date,owner=owner,status='Open',historical=0,raw_json='{}',created_at=now()))
            _complete_source_action(db,ref,u['id'])
            if outcome=='waiting':
                db.execute("UPDATE leads SET stage='Waiting on client',updated_at=? WHERE id=?",(now(),lead['id']))
            elif outcome=='quote_sent':
                db.execute("UPDATE leads SET stage='Quote Sent',updated_at=? WHERE id=?",(now(),lead['id']))
            elif outcome=='lost':
                from .conversations import conversation,save_state
                c=conversation(db,lead['id']);save_state(db,c,u['id'],status='Resolved',remind_on='')
                db.execute("UPDATE leads SET stage='Lost',version=version+1,updated_at=? WHERE id=?",(now(),lead['id']))
                db.execute("UPDATE tasks SET status='Done' WHERE lead_id=? AND status!='Done' AND historical=0",(lead['id'],))
                unanswered=rows(db,"""SELECT m.id FROM messages m LEFT JOIN inbox_claims c ON c.message_id=m.id
                  WHERE m.lead_id=? AND (lower(m.kind) LIKE '%incoming%' OR lower(m.kind) LIKE '%email%')
                    AND c.resolved_at IS NULL""",(lead['id'],))
                for message in unanswered:
                    _complete_source_action(db,{'kind':'email','id':message['id']},u['id'])
            _record_action_outcome(db,ref,lead['id'],outcome,note,next_task_id,followup_date,u['id'])
            audit(db,u['id'],'Action outcome recorded','action_outcomes',ref['kind']+':'+ref['id'],
              {'outcome':outcome,'lead_id':lead['id'],'followup_task_id':next_task_id,'followup_date':followup_date,'note':note})
        return {'ok':True,'outcome':outcome,'followup_task_id':next_task_id}

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
            team=rows(db,"""SELECT e.id,e.name,e.role,t.id time_id,t.service_minutes,t.transport_minutes,t.note,t.status,t.version
              FROM assignments a JOIN employees e ON e.id=a.resource_id AND a.resource_type='employee'
              LEFT JOIN times t ON t.job_id=a.job_id AND t.employee_id=e.id WHERE a.job_id=? ORDER BY e.name""",(jid,))
            inv=one(db,'SELECT actual_json FROM job_invoices WHERE job_id=?',(jid,))
            paid=one(db,'SELECT paid_transport_minutes FROM paid_work_terms WHERE job_id=?',(jid,))
            is_lead=bool(u['role']=='crew' and one(db,'SELECT 1 FROM employee_directory_profiles WHERE employee_id=? AND team_lead=1',(u.get('employee_id'),)))
        suggested=None
        if inv:
            try:suggested=float(json.loads(inv['actual_json']).get('service_hours') or 0)+(float(paid['paid_transport_minutes'])/60 if paid and paid['paid_transport_minutes'] is not None else 0)
            except Exception:suggested=None
        own_only=u['role']=='crew' and not is_lead
        if own_only:team=[e for e in team if e['id']==u.get('employee_id')]
        for e in team:e['total_hours']=(int(e['service_minutes'] or 0)+int(e['transport_minutes'] or 0))/60 if e['time_id'] else None
        return {'own_only':own_only,'job':{'id':j['id'],'title':j['title'],'status':j['status']},'employees':team,'suggested_total_hours':suggested}

    @app.post('/api/ux/jobs/{jid}/crew-hours')
    def crew_hours_save(jid:str,data:dict,u=Depends(auth)):
        manager=u['role'] in {'owner','manager','accounting','dispatch'}
        with closing(connect(path)) as checkdb:
            is_lead=bool(u['role']=='crew' and u.get('employee_id') and one(checkdb,'SELECT team_lead FROM employee_directory_profiles WHERE employee_id=? AND team_lead=1',(u['employee_id'],)))
        own_only=u['role']=='crew' and not is_lead
        if not manager and not is_lead and not own_only:raise HTTPException(403,'Only managers, assigned Team Leads or the employee can submit hours.')
        selected=data.get('employee_ids') or []
        if not isinstance(selected,list) or not selected or not all(isinstance(x,str) for x in selected) or len(set(selected))!=len(selected):raise HTTPException(422,'Select the employees receiving the team hours.')
        if own_only and selected!=[u.get('employee_id')]:raise HTTPException(403,'Crew members can submit only their own hours.')
        common=worked_hours.hour_minutes(data.get('team_hours'))
        versions=data.get('versions')
        if versions is not None and not isinstance(versions,dict):raise HTTPException(422,'Reload the hours form.')
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
                minutes=common
                if eid in exceptions:
                    if not isinstance(exceptions[eid],dict):raise HTTPException(422,'Review the individual hours.')
                    minutes=worked_hours.hour_minutes(exceptions[eid].get('total_hours'))
                existing=one(db,'SELECT * FROM times WHERE job_id=? AND employee_id=?',(jid,eid))
                if versions is not None and versions.get(eid)!=(existing['version'] if existing else None):raise HTTPException(409,'These hours changed. Reopen the form before saving.')
                individual_note=text((exceptions.get(eid) or {}).get('note',''),'Optional adjustment note',1000) or note or (existing['note'] if existing else '')
                if existing and existing['status']=='Approved' and not manager:
                    if minutes!=existing['service_minutes']+existing['transport_minutes'] or individual_note!=existing['note']:raise HTTPException(409,'Approved payroll hours can only be corrected by a payroll manager.')
                    continue
                if existing:
                    if existing['service_minutes']+existing['transport_minutes']!=minutes or existing['note']!=individual_note:
                        db.execute("UPDATE times SET service_minutes=?,transport_minutes=0,note=?,status='Submitted',submitted_by=?,approved_by=NULL,approved_hourly_cents=NULL,approved_at=NULL,version=version+1 WHERE id=?",(minutes,individual_note,u['id'],existing['id']))
                        audit(db,u['id'],'Hours changed; approval reset','times',existing['id'],{'before_minutes':existing['service_minutes']+existing['transport_minutes'],'after_minutes':minutes,'before_status':existing['status']})
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
            can_plan_jobs_for_directory=can_plan_jobs(db,u)
            service_rows=rows(db,"""SELECT a.resource_id employee_id,j.id,j.title,j.start_at,j.end_at,j.status FROM assignments a JOIN jobs j ON j.id=a.job_id WHERE a.resource_type='employee' AND j.status NOT IN ('Completed','Cancelled') ORDER BY j.start_at""")
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
            if can_view_pay(u,e['id']):base.update(hourly_cents=e['hourly_cents'],pay_rate_pending=bool(json.loads(e.get('raw_json') or '{}').get(PAY_PENDING)),can_view_pay=True)
            if can_plan_jobs_for_directory or u.get('employee_id')==e['id']:
                base['assigned_services']=[r for r in service_rows if r['employee_id']==e['id']]
            if private:
                base.update({'email':e.get('email') or '','home_address':e.get('home_address') or '',
                  'start_date':e.get('start_date') or '','driver_verified':bool(e['driver_verified']),
                  'hourly_cents':e['hourly_cents'],'license_uploaded':bool(e.get('license_original_name')),
                  'pay_rate_pending':bool(json.loads(e.get('raw_json') or '{}').get(PAY_PENDING)),
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
            raw=json.loads(emp.get('raw_json') or '{}')
            if rate not in (None,''):
                try:rate_cents=round(float(rate)*100)
                except Exception:raise HTTPException(422,'Enter a valid hourly pay rate.')
                if rate_cents<0 or rate_cents>100000:raise HTTPException(422,'Review the hourly pay rate.')
                db.execute('UPDATE employees SET hourly_cents=?,role=?,active=?,driver_verified=? WHERE id=?',(rate_cents,text(data.get('role',emp['role']),'Role',120),int(data.get('active',bool(emp['active']))),int(data.get('driver_verified',bool(emp['driver_verified']))),eid))
                if raw.pop(PAY_PENDING,False):
                    db.execute('UPDATE employees SET raw_json=? WHERE id=?',(json.dumps(raw),eid))
            db.execute('UPDATE employees SET role=?,active=?,driver_verified=? WHERE id=?',(text(data.get('role',emp['role']),'Role',120),int(data.get('active',bool(emp['active']))),int(data.get('driver_verified',bool(emp['driver_verified']))),eid))
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
        if u['role'] not in OFFICE|{'crew'}:raise HTTPException(403,'Payroll summary is restricted to staff.')
        with closing(connect(path)) as db:return worked_hours.summary(db,u,start,end)

    @app.get('/api/ux/payroll-hours/export.csv')
    def payroll_hours_export(start:str='',end:str='',u=Depends(auth)):
        start,end=worked_hours.date_range(start,end)
        with transaction(path) as db:return worked_hours.export_csv(db,u,start,end)

    @app.post('/api/ux/payroll-summary/approve')
    def payroll_approve(data:dict,u=Depends(auth)):
        if u['role'] not in {'owner','manager','accounting'}:raise HTTPException(403,'Only payroll managers can approve hours.')
        start,end=worked_hours.date_range(data.get('start'),data.get('end'))
        with transaction(path) as db:
            before=worked_hours.summary(db,u,start,end)
            if data.get('review_token') is not None and data['review_token']!=before['review_token']:raise HTTPException(409,'Hours or assignments changed. Reload payroll and review them before approving.')
            items=rows(db,"SELECT t.id,j.start_at FROM times t JOIN jobs j ON j.id=t.job_id WHERE (t.status!='Approved' OR t.approved_hourly_cents IS NULL) AND j.status='Completed'")
            ids=[x['id'] for x in items if start<=_local_date(x['start_at'])<=end]
            for tid in ids:worked_hours.approve_entry(db,tid,u['id'])
            report=worked_hours.summary(db,u,start,end)
            audit(db,u['id'],'Completed service hours approved','times',start+'..'+end,{'count':len(ids)})
        return {'ok':True,'approved':len(ids),'pending_entries':report['pending_entries'],'missing_entries':report['missing_entries']}

    @app.patch('/api/ux/payroll-time/{tid}')
    def payroll_time_edit(tid:str,data:dict,u=Depends(auth)):
        if u['role'] not in {'owner','manager','accounting'}:raise HTTPException(403,'Only payroll managers can correct payroll hours.')
        minutes=worked_hours.hour_minutes(data.get('hours'))
        with transaction(path) as db:
            t=one(db,'SELECT * FROM times WHERE id=?',(tid,))
            if not t:raise HTTPException(404,'Time entry not found.')
            if 'version' in data and data['version']!=t['version']:raise HTTPException(409,'These hours changed. Reopen the details before saving.')
            before=t['service_minutes']+t['transport_minutes']
            if before!=minutes:
                db.execute("UPDATE times SET service_minutes=?,transport_minutes=0,status='Submitted',approved_by=NULL,approved_hourly_cents=NULL,approved_at=NULL,version=version+1 WHERE id=?",(minutes,tid))
                audit(db,u['id'],'Payroll hours corrected; approval reset','times',tid,{'before_minutes':before,'after_minutes':minutes,'before_status':t['status']})
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
            ref=_action_reference(db,'email',mid)
            from .conversations import claim_conversation
            result=claim_conversation(db,ref['lead_id'],u)
        return {'ok':True,**result}

    @app.post('/api/ux/inbox/{mid}/resolve')
    def inbox_resolve(mid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        raise HTTPException(409,'Open Inbox, review the latest updates and mark the conversation resolved.')

    @app.get('/api/ux/company-communications')
    def company_comms(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:return settings(db,'company_communications',{})

    @app.patch('/api/ux/company-communications')
    def company_comms_update(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        connected=text(data.get('connected_mailbox',''),'Connected mailbox',240).lower();send_as=text(data.get('send_as',''),'Send-as address',240).lower();reply_to=text(data.get('reply_to',''),'Reply-to address',240).lower()
        if any(not __import__('re').fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) for email in (connected,send_as,reply_to)):raise HTTPException(422,'Enter valid company email addresses.')
        cfg={'connected_mailbox':connected,'send_as':send_as,'reply_to':reply_to,'default_from':send_as,'secondary_inbox':connected,'client_email_rule':'Store mail in the connected Gmail mailbox and send as the Crown Movers domain alias after Google verifies the alias.','approved':data.get('approved') is True,'note':text(data.get('note',''),'Note',2000)}
        with transaction(path) as db:put_setting(db,'company_communications',cfg);audit(db,u['id'],'Company communication defaults updated','settings','company_communications')
        return cfg
