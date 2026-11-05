"""Channel follow-ups reuse tasks, preserving source IDs and independent handlers."""
from contextlib import closing
from datetime import date, datetime, timezone
import hashlib
import json
from zoneinfo import ZoneInfo
from fastapi import Depends, HTTPException
from .store import connect, transaction, rows, one, insert, uid, now, dumps, audit
from .platform_common import require_role, text, business_day
from .conversations import OFFICE, MANAGERS, conversation, entry
from .business_zone import BUSINESS_ZONE, zone_name


def obj(raw):
    try:
        value=json.loads(raw or '{}')
        return value if isinstance(value,dict) else {}
    except (ValueError,TypeError):return {}


def activity_time(value):
    """Normalize source timestamps so offsets and import times cannot reorder activity."""
    try:
        stamp=datetime.fromisoformat(str(value or '').replace('Z','+00:00'))
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=BUSINESS_ZONE)
        return stamp.astimezone(timezone.utc).isoformat()
    except (ValueError,TypeError,OverflowError):return ''


def call_status(d):
    value=str(d.get('disposition') or d.get('status') or '').lower()
    if d.get('voicemail') is True or 'voice' in value:return 'Voicemail'
    if any(x in value for x in ('miss','no answer','no_answer','unanswered','busy')):return 'Missed'
    if value in ('answered','answer','connected','completed'):return 'Answered'
    if d.get('source')=='Gmail / UniFi' and d.get('voicemail') is False:return 'Missed'
    return 'Recorded'


def task_channel(t):
    meta=obj(t['raw_json'])
    if meta.get('inbox_channel') in ('emails','forms','calls'):return meta['inbox_channel']
    title=t['title'].lower();key=t.get('legacy_id') or ''
    if meta.get('call_id') or any(x in title for x in ('call','voicemail')):return 'calls'
    if key.startswith(('gmail-email:','reply:')) or 'email' in title:return 'emails'
    if any(x in title for x in ('form','portal','upload','photo','detail','inquiry')):
        if title=='review new inquiry':
            kind=(t.get('lead_type') or '').lower()
            if kind in ('missed call','voicemail'):return 'calls'
            if kind=='email':return 'emails'
        return 'forms'
    return 'tasks'


def sync_followups(db):
    """Attach exact source IDs to existing work before creating missing follow-ups.

    Never delete an old task or reopen completed/archived work during migration.
    Each source has a stable key so repeated reads/import retries cannot duplicate it.
    """
    tasks=rows(db,'SELECT * FROM tasks');by_key={t['legacy_id']:t for t in tasks if t['legacy_id']}
    by_source={s:t for t in tasks for s in obj(t['raw_json']).get('inbox_sources',[])}
    by_call={obj(t['raw_json']).get('call_id'):t for t in tasks if obj(t['raw_json']).get('call_id')}
    initial={t['lead_id']:t for t in tasks if t['title']=='Review new inquiry'}
    leads={l['id']:l for l in rows(db,'SELECT id,external_id,stage FROM leads')}
    events={}
    for e in rows(db,"SELECT event_key,event_type,payload_json FROM integration_events WHERE event_type IN ('gmail_quote','gmail_email')"):
        p=obj(e['payload_json'])
        if p.get('message_id'):events[p['message_id']]=(e,p)
    states={}
    def add(source,lid,channel,title,at,existing=None,done=False):
        t=by_source.get(source) or existing or by_key.get('inbox:'+source)
        if not t:
            if lid not in states:states[lid]=conversation(db,lid)['status'] if lid in leads else 'Needs reply'
            done=done or states[lid]=='Resolved'
            t=dict(id=uid('TASK'),legacy_id='inbox:'+source,lead_id=lid,title=title[:300],due_date=business_day(at or now()),owner='',status='Done' if done else 'Open',historical=0,raw_json='{}',created_at=at or now())
            insert(db,'tasks',t);by_key[t['legacy_id']]=t
        if done and source.startswith(('callback:','change:')):
            db.execute("UPDATE tasks SET status='Done' WHERE id=?",(t['id'],));t['status']='Done'
        meta=obj(t['raw_json']);meta['inbox_sources']=list(dict.fromkeys(meta.get('inbox_sources',[])+[source]));meta['inbox_channel']=channel
        # Keep actual receipt time separate from task creation, assignment and deadline.
        received=activity_time(at)
        if received:meta['inbox_received_at']=max(meta.get('inbox_received_at',''),received)
        raw=dumps(meta)
        if raw!=t['raw_json']:db.execute('UPDATE tasks SET raw_json=? WHERE id=?',(raw,t['id']))
        t['raw_json']=raw;by_source[source]=t
        return t
    scheduled={r['object_id'] for r in rows(db,"SELECT DISTINCT object_id FROM audit WHERE object_type='tasks' AND action IN ('Inbox follow-up schedule','Inbox follow-up reopen')")}
    for c in rows(db,'SELECT * FROM calls'):
        d=obj(c['payload_json']);status=call_status(d)
        t=add('call:'+c['id'],c['lead_id'],'calls',{'Missed':'Return missed call','Voicemail':'Review voicemail','Answered':'Answered call','Recorded':'Review call record'}[status],activity_time(d.get('occurred_at')) or c['created_at'],by_call.get(c['id']) or by_key.get('call:'+str(d.get('event_id') or c.get('legacy_id') or '')),done=status=='Answered')
        meta=obj(t['raw_json'])
        # An answered call is activity, not an overdue task. Preserve a callback
        # explicitly scheduled/reopened by staff, including before this release.
        explicit=meta.get('inbox_followup_required',t['id'] in scheduled)
        if status=='Answered' and not explicit and not t['historical']:
            if t['status']!='Done':
                db.execute("UPDATE tasks SET status='Done' WHERE id=?",(t['id'],));t['status']='Done'
            if meta.get('inbox_followup_required') is not False:meta['inbox_answered_complete']=True
        elif status!='Answered' and meta.get('inbox_answered_complete') and not t['historical']:
            db.execute("UPDATE tasks SET status='Open' WHERE id=?",(t['id'],));t['status']='Open';meta.pop('inbox_answered_complete',None)
        raw=dumps(meta)
        if raw!=t['raw_json']:db.execute('UPDATE tasks SET raw_json=? WHERE id=?',(raw,t['id']));t['raw_json']=raw
    for m in rows(db,"SELECT m.*,c.resolved_at FROM messages m LEFT JOIN inbox_claims c ON c.message_id=m.id WHERE lower(m.kind) LIKE 'incoming%' AND lower(m.kind) NOT LIKE '%call%'"):
        channel='forms' if 'form' in m['kind'].lower() else 'emails';existing=None
        e,p=events.get(m['id'],({},{}));lid=m['lead_id']
        if e:
            channel='forms' if e['event_type']=='gmail_quote' else 'emails'
            existing=by_key.get(('quote-form:' if channel=='forms' else 'gmail-email:')+e['event_key'])
            if not existing and leads.get(lid,{}).get('external_id')==e['event_key']:existing=initial.get(lid)
        elif channel=='forms':existing=initial.get(lid)
        add('message:'+m['id'],lid,channel,('Review form: ' if channel=='forms' else 'Reply to email: ')+(m['subject'] or 'Client update'),m['created_at'],existing,done=bool(m['resolved_at']))
    for c in rows(db,'SELECT * FROM client_changes'):
        add('change:'+c['id'],c['lead_id'],'forms','Review updated moving details',c['created_at'],by_key.get(c['id']),done=c['status']!='Pending')
    for c in rows(db,'SELECT * FROM client_callbacks'):
        add('callback:'+c['id'],c['lead_id'],'calls','Client requested a callback',c['created_at'],by_key.get('callback:'+c['id']),done=c['status']=='Completed')
    for e in rows(db,"SELECT * FROM conversation_entries WHERE direction='client' OR kind='call'"):
        recorded=e['kind']=='call' and e['direction']!='client'
        add('entry:'+e['id'],e['lead_id'],'calls' if recorded else 'forms','Call outcome recorded' if recorded else 'Reply to client portal message',e['created_at'],done=recorded)


def task_contact(t,call_details,unmatched=None):
    meta=obj(t['raw_json']);ids=[k[5:] for k in meta.get('inbox_sources',[]) if k.startswith('call:')]
    if meta.get('call_id'):ids.append(meta['call_id'])
    d=next((call_details[cid] for cid in ids if cid in call_details),{})
    t['contact_phone']=d.get('phone') or t.get('phone') or ''
    t['call_status']=call_status(d) if d else ''
    if not d and t['title']=='Call outcome recorded':t['call_status']='Recorded'
    t['received_at']=meta.get('inbox_received_at') or activity_time(t.get('created_at'))
    if not t.get('client_name') and d:t['client_name']=d.get('caller') or 'Unmatched caller'
    key=str(t.get('legacy_id') or '')
    if not t.get('lead_id') and key.startswith('gmail-match:'):
        p=(unmatched or {}).get(key[12:],{});parsed=p.get('parsed') or {}
        for field in ('client_name','email','phone','move_date'):
            if not t.get(field) and isinstance(parsed.get(field),str):t[field]=parsed[field]
        t['contact_phone']=t.get('contact_phone') or t.get('phone') or ''
        t['received_at']=activity_time(p.get('received_at')) or t['received_at']
        t['matching_required']=True
    return t


def task_token(t):
    return hashlib.sha256(dumps([t.get(k) for k in ('id','lead_id','owner','status','due_date','historical','raw_json')]).encode()).hexdigest()


def get_task(db,tid):
    t=one(db,'SELECT t.*,l.client_name,l.email,l.phone,l.move_date,l.lead_type,x.client_id FROM tasks t LEFT JOIN leads l ON l.id=t.lead_id LEFT JOIN client_account_leads x ON x.lead_id=t.lead_id WHERE t.id=?',(tid,))
    if not t:raise HTTPException(404,'Follow-up not found.')
    event=one(db,'SELECT event_key,payload_json FROM integration_events WHERE event_key=?',(str(t.get('legacy_id') or '')[12:],)) if str(t.get('legacy_id') or '').startswith('gmail-match:') else None
    task_contact(t,{c['id']:obj(c['payload_json']) for c in rows(db,'SELECT id,payload_json FROM calls')},{event['event_key']:obj(event['payload_json'])} if event else {})
    person=one(db,'SELECT name FROM users WHERE id=? OR name=?',(t['owner'],t['owner']))
    t['owner_name']=person['name'] if person else t['owner']
    t['channel']=task_channel(t);t['review_token']=task_token(t)
    return t


def update_task(db,tid,data,u):
    t=get_task(db,tid)
    if data.get('review_token')!=t['review_token']:raise HTTPException(409,'This follow-up changed. Refresh before continuing.')
    if t['historical']:raise HTTPException(409,'Restore archived work before changing it.')
    current=one(db,'SELECT id FROM users WHERE (id=? OR name=?) AND active=1',(t['owner'],t['owner']))
    owner=current['id'] if current else t['owner']
    action=data.get('action');target=owner;status=t['status'];due=t['due_date'];note=text(data.get('note',''),'Note',2000)
    if action=='claim':
        if owner and owner!=u['id']:raise HTTPException(409,'This follow-up is assigned. Ask for a handover.')
        if status=='Done':raise HTTPException(409,'Reopen this follow-up before claiming it.')
        target=u['id']
    else:
        new_callback=not owner and status=='Done' and t['call_status'] in ('Answered','Recorded') and action in ('schedule','reopen')
        if owner!=u['id'] and u['role'] not in MANAGERS and not new_callback:raise HTTPException(403,'Claim this follow-up first or ask its handler to update it.')
        if new_callback:target=u['id']
        if action=='assign':
            target=str(data.get('user_id') or '')
            if target and not one(db,"SELECT id FROM users WHERE id=? AND active=1 AND role IN ('owner','manager','sales','dispatch')",(target,)):raise HTTPException(422,'Choose an active office team member.')
            if not note:raise HTTPException(422,'Add a short handover note.')
        elif action in ('done','reopen','schedule'):
            if str(t.get('legacy_id') or '').startswith('gmail-match:') and action=='done':
                if one(db,"SELECT id FROM integration_events WHERE event_key=? AND status='Needs review'",(t['legacy_id'][12:],)):raise HTTPException(409,'Resolve the incoming inquiry in Connections first.')
            status='Done' if action=='done' else 'Open'
            if action=='schedule':
                try:due=date.fromisoformat(str(data.get('due_date',''))).isoformat()
                except ValueError:raise HTTPException(422,'Choose a follow-up date.')
                if due<datetime.now(BUSINESS_ZONE).date().isoformat():raise HTTPException(422,'Choose today or a future date.')
        else:raise HTTPException(422,'Choose a follow-up action.')
    meta=obj(t['raw_json']);meta['inbox_managed']=True
    if action in ('schedule','reopen','done'):
        meta['inbox_followup_required']=action!='done';meta.pop('inbox_answered_complete',None)
    db.execute('UPDATE tasks SET owner=?,status=?,due_date=?,raw_json=? WHERE id=?',(target,status,due,dumps(meta),tid))
    if action in ('done','reopen'):
        for source in meta.get('inbox_sources',[]):
            kind,source_id=source.split(':',1)
            if kind=='callback':db.execute('UPDATE client_callbacks SET status=? WHERE id=?',('Completed' if action=='done' else 'Requested',source_id))
    if t['lead_id']:entry(db,t['lead_id'],'note',note,'Follow-up '+{'claim':'claimed','assign':'assigned','done':'completed','reopen':'reopened','schedule':'scheduled'}[action]+': '+t['title'],u['id'],uid('TASKLOG'))
    audit(db,u['id'],'Inbox follow-up '+action,'tasks',tid,{'from':owner,'to':target,'status':status,'note':note})
    return get_task(db,tid)


def call_token(c):return hashlib.sha256(dumps([c['id'],c['lead_id'],c['payload_json']]).encode()).hexdigest()


def register_inbox_tasks(app,path,auth):
    @app.get('/api/inbox-tasks')
    def list_tasks(channel:str='tasks',scope:str='team',status:str='open',q:str='',offset:int=0,order:str='newest',activity_only:bool=False,u=Depends(auth)):
        require_role(u,OFFICE)
        if channel not in ('tasks','calls','emails','forms') or scope not in ('team','mine','unclaimed') or status not in ('open','today','overdue','upcoming','completed','all') or order not in ('newest','oldest'):raise HTTPException(422,'Choose a valid Inbox filter.')
        with transaction(path) as db:
            from .sales_crm_v6 import sync_unlinked_clients
            sync_unlinked_clients(db);sync_followups(db)
            people={p['id']:p['name'] for p in rows(db,'SELECT id,name FROM users WHERE active=1')}
            call_details={c['id']:obj(c['payload_json']) for c in rows(db,'SELECT id,payload_json FROM calls')}
            unmatched={e['event_key']:obj(e['payload_json']) for e in rows(db,"SELECT event_key,payload_json FROM integration_events WHERE event_type IN ('gmail_quote','gmail_email','gmail_missed_call','gmail_voicemail')")}
            names={v:k for k,v in people.items()};day=datetime.now(BUSINESS_ZONE).date().isoformat();items=[]
            for t in rows(db,'SELECT t.*,l.client_name,l.email,l.phone,l.move_date,l.lead_type,x.client_id FROM tasks t LEFT JOIN leads l ON l.id=t.lead_id LEFT JOIN client_account_leads x ON x.lead_id=t.lead_id WHERE t.historical=0 AND COALESCE(l.archived_import,0)=0'):
                task_contact(t,call_details,unmatched)
                t['channel']=task_channel(t);owner=t['owner'] if t['owner'] in people else names.get(t['owner'],t['owner'])
                if q.strip().lower() not in ' '.join(str(t.get(k) or '') for k in ('client_name','email','phone','contact_phone','title')).lower():continue
                t['bucket']='completed' if t['status']=='Done' else 'overdue' if t['due_date'] and t['due_date']<day else 'today' if t['due_date']==day else 'upcoming'
                t.update(owner_id=owner,owner_name=people.get(owner,t['owner']),review_token=task_token(t))
                items.append(t)
            def in_scope(t,scope):
                return scope=='team' or scope=='mine' and t['owner_id']==u['id'] or scope=='unclaimed' and not t['owner_id']
            def in_status(t):
                return status=='all' or status=='open' and t['bucket']!='completed' or t['bucket']==status
            if activity_only:items=[t for t in items if t['channel'] in ('calls','emails','forms')]
            scope_counts={s:sum(in_scope(t,s) and in_status(t) for t in items if channel=='tasks' or t['channel']==channel) for s in ('team','mine','unclaimed')}
            items=[t for t in items if in_scope(t,scope)]
            counts={k:sum(t['channel']==k and t['bucket']!='completed' for t in items) for k in ('calls','emails','forms')};counts['tasks']=sum(t['bucket']!='completed' for t in items)
            selected=[t for t in items if channel=='tasks' or t['channel']==channel]
            summary={k:sum(t['bucket']==k for t in selected) for k in ('today','overdue','upcoming','completed')};summary['open']=sum(t['bucket']!='completed' for t in selected)
            selected=[t for t in selected if status=='all' or status=='open' and t['bucket']!='completed' or t['bucket']==status]
            selected.sort(key=lambda t:(t['received_at'],t['id']),reverse=order=='newest');offset=max(0,offset)
            return {'items':selected[offset:offset+30],'total':len(selected),'offset':offset,'order':order,'counts':counts,'summary':summary,'scope_counts':scope_counts}

    @app.get('/api/inbox-tasks/{tid}')
    def task_detail(tid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            sync_followups(db);t=get_task(db,tid)
            from .conversations import timeline
            keys=obj(t['raw_json']).get('inbox_sources',[])
            t['events']=[e for e in timeline(db,t['lead_id']) if e['id'] in keys] if t['lead_id'] else []
            t['call_ids']=[k[5:] for k in keys if k.startswith('call:')]
            return t

    @app.post('/api/inbox-tasks/{tid}')
    def change_task(tid:str,data:dict,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:return update_task(db,tid,data,u)

    @app.get('/api/inbox-calls')
    def call_log(q:str='',status:str='all',offset:int=0,order:str='newest',u=Depends(auth)):
        require_role(u,OFFICE)
        if status not in ('all','Answered','Missed','Voicemail','Recorded') or order not in ('newest','oldest'):raise HTTPException(422,'Choose a call status and sort order.')
        with transaction(path) as db:
            sync_followups(db);items=[]
            for c in rows(db,'SELECT c.*,l.client_name,l.move_date,x.client_id FROM calls c LEFT JOIN leads l ON l.id=c.lead_id LEFT JOIN client_account_leads x ON x.lead_id=c.lead_id'):
                d=obj(c['payload_json']);s=call_status(d)
                if status!='all' and s!=status:continue
                item=dict(id=c['id'],lead_id=c['lead_id'],client_id=c['client_id'],client_name=c['client_name'] or d.get('caller') or 'Unmatched caller',move_date=c['move_date'],status=s,phone=d.get('phone') or '',body=d.get('note') or d.get('summary') or '',created_at=c['created_at'],review_token=call_token(c),linkable=True)
                item['received_at']=activity_time(d.get('occurred_at')) or activity_time(c['created_at'])
                if q.lower() in ' '.join(str(item.get(k) or '') for k in ('client_name','phone','body')).lower():items.append(item)
            if status in ('all','Recorded'):
                for e in rows(db,"SELECT e.*,l.client_name,l.move_date FROM conversation_entries e JOIN leads l ON l.id=e.lead_id WHERE kind='call'"):
                    if q.lower() in (e['client_name']+' '+e['body']).lower():items.append(dict(id=e['id'],lead_id=e['lead_id'],client_name=e['client_name'],move_date=e['move_date'],status='Recorded',phone='',body=e['body'],created_at=e['created_at'],linkable=False))
            for item in items:item.setdefault('received_at',activity_time(item['created_at']))
            items.sort(key=lambda x:(x['received_at'],x['id']),reverse=order=='newest');offset=max(0,offset)
            return {'items':items[offset:offset+30],'total':len(items),'offset':offset}

    @app.get('/api/inbox-call/{cid}')
    def read_call(cid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            c=one(db,'SELECT c.*,l.client_name FROM calls c LEFT JOIN leads l ON l.id=c.lead_id WHERE c.id=?',(cid,))
            if not c:raise HTTPException(404,'Call not found.')
            d=obj(c['payload_json'])
            return {'id':cid,'client_name':c['client_name'],'phone':d.get('phone') or '', 'status':call_status(d),'review_token':call_token(c)}

    @app.post('/api/inbox-calls/{cid}/link')
    def link_call(cid:str,data:dict,u=Depends(auth)):
        require_role(u,OFFICE)
        if data.get('confirmed') is not True:raise HTTPException(422,'Review the client and service before linking this call.')
        with transaction(path) as db:
            c=one(db,'SELECT * FROM calls WHERE id=?',(cid,))
            if not c:raise HTTPException(404,'Call not found.')
            if data.get('review_token')!=call_token(c):raise HTTPException(409,'This call changed. Refresh before linking it.')
            target=one(db,'SELECT id,client_name FROM leads WHERE id=?',(str(data.get('lead_id') or ''),))
            if not target:raise HTTPException(422,'Select the client’s service.')
            if target['id']==c['lead_id']:return {'ok':True,'lead_id':target['id']}
            sync_followups(db)
            linked=[t for t in rows(db,'SELECT * FROM tasks') if 'call:'+cid in obj(t['raw_json']).get('inbox_sources',[])]
            for t in linked:
                if t['owner'] and t['owner'] not in (u['id'],u['name']) and u['role'] not in MANAGERS:raise HTTPException(403,'Ask this follow-up’s handler or a manager to change its client.')
                sources=obj(t['raw_json']).get('inbox_sources',[])
                if len(sources)>1:raise HTTPException(409,'This follow-up covers several records. Ask a manager to review the client matching.')
            db.execute('UPDATE calls SET lead_id=? WHERE id=?',(target['id'],cid))
            for t in linked:db.execute('UPDATE tasks SET lead_id=? WHERE id=?',(target['id'],t['id']))
            db.execute("UPDATE service_conversations SET status='Needs reply',remind_on='',revision=revision+1,updated_at=? WHERE lead_id=?",(now(),target['id']))
            db.execute("UPDATE tasks SET status='Done' WHERE legacy_id=?",('conversation-reminder:'+target['id'],))
            entry(db,target['id'],'note','Call record linked to this service.','Call linked',u['id'],uid('LINKCALL'))
            audit(db,u['id'],'Call linked to client service','calls',cid,{'from':c['lead_id'],'to':target['id']})
            return {'ok':True,'lead_id':target['id']}
