"""Shared service conversations; client aggregation never widens portal access."""
from contextlib import closing
from datetime import date, datetime, timedelta
import hashlib
import json
import re
from zoneinfo import ZoneInfo
from fastapi import Depends, HTTPException
from .store import connect, transaction, one, rows, insert, uid, now, dumps, audit
from .platform_common import require_role, staff_read_project, queue_message, text

OFFICE={'owner','manager','sales','dispatch'}
MANAGERS={'owner','manager'}
STATUSES={'Needs reply','In progress','Waiting on client','Resolved'}


def incoming_signal(db,lid):
    # Include the source ID: two updates in the same second are distinct.
    found=rows(db,"""SELECT id,created_at,COUNT(*) OVER() signal_count FROM (
      SELECT 'message:'||id id,created_at FROM messages WHERE lead_id=? AND lower(kind) LIKE 'incoming%'
      UNION ALL SELECT 'entry:'||id,created_at FROM conversation_entries WHERE lead_id=? AND direction='client'
      UNION ALL SELECT 'photo:'||id,created_at FROM attachments WHERE lead_id=? AND deleted_at IS NULL
      UNION ALL SELECT 'change:'||id,created_at FROM client_changes WHERE lead_id=?
      UNION ALL SELECT 'callback:'||id,created_at FROM client_callbacks WHERE lead_id=?
      UNION ALL SELECT 'call:'||id,created_at FROM calls WHERE lead_id=?
      UNION ALL SELECT 'link:'||id,created_at FROM saved_media_links WHERE lead_id=? AND removed_at IS NULL
    ) ORDER BY created_at DESC,id DESC LIMIT 1""",(lid,)*7)
    return dumps(found[0]) if found else ''


def conversation(db,lid):
    lead=one(db,'SELECT * FROM leads WHERE id=?',(lid,))
    if not lead:raise HTTPException(404,'Service not found.')
    r=one(db,'SELECT * FROM service_conversations WHERE lead_id=?',(lid,))
    initial=r is None
    if not r:
        legacy=rows(db,"""SELECT DISTINCT c.claimed_by FROM inbox_claims c JOIN messages m ON m.id=c.message_id
          WHERE m.lead_id=? AND c.resolved_at IS NULL""",(lid,))
        r={'lead_id':lid,'handler_id':legacy[0]['claimed_by'] if len(legacy)==1 else None,'status':'Needs reply',
           'revision':0,'seen_signal':'','remind_on':'','updated_at':lead['created_at']}
    signal=incoming_signal(db,lid)
    if initial and lead['stage'] in ('Closed','Completed','Lost') and not rows(db,"SELECT m.id FROM messages m LEFT JOIN inbox_claims c ON c.message_id=m.id WHERE m.lead_id=? AND lower(m.kind) LIKE 'incoming%' AND c.resolved_at IS NULL LIMIT 1",(lid,)) and not one(db,"SELECT id FROM client_changes WHERE lead_id=? AND status='Pending' UNION ALL SELECT id FROM client_callbacks WHERE lead_id=? AND status!='Completed' LIMIT 1",(lid,lid)):
        r['status']='Resolved';r['seen_signal']=signal
    if signal!=r['seen_signal'] and signal:r['status']='Needs reply';r['remind_on']=''
    handler=one(db,'SELECT id,name,active FROM users WHERE id=?',(r['handler_id'],)) if r['handler_id'] else None
    if handler and not handler['active']:r['handler_id']=None;handler=None
    owner=one(db,'SELECT id,name FROM users WHERE id=? OR name=?',(lead['assigned_to'],lead['assigned_to']))
    task_handlers=[t['owner'] for t in rows(db,"SELECT owner FROM tasks WHERE lead_id=? AND status!='Done' AND historical=0 AND owner!='' AND json_extract(raw_json,'$.inbox_managed')=1 ORDER BY owner",(lid,))]
    r['task_handlers']=task_handlers
    r.update(handler_name=handler['name'] if handler else '',service_owner=owner['name'] if owner else lead['assigned_to'],
             signal=signal,review_token=hashlib.sha256(dumps([lid,r['revision'],signal,r['handler_id'],lead['email'],task_handlers]).encode()).hexdigest())
    return r


def save_state(db,c,actor,**changes):
    values={k:c[k] for k in ('lead_id','handler_id','status','remind_on')}
    values.update(changes,seen_signal=c['signal'],revision=c['revision']+1,updated_at=now())
    db.execute('''INSERT INTO service_conversations(lead_id,handler_id,status,remind_on,seen_signal,revision,updated_at)
      VALUES (:lead_id,:handler_id,:status,:remind_on,:seen_signal,:revision,:updated_at)
      ON CONFLICT(lead_id) DO UPDATE SET handler_id=excluded.handler_id,status=excluded.status,
      remind_on=excluded.remind_on,seen_signal=excluded.seen_signal,revision=excluded.revision,updated_at=excluded.updated_at''',values)
    # Keep old email-only dashboards consistent during the UI transition.
    for message in rows(db,"SELECT id FROM messages WHERE lead_id=? AND (lower(kind) LIKE '%incoming%' OR lower(kind) LIKE '%email%')",(c['lead_id'],)):
        if values['handler_id']:
            db.execute("""INSERT INTO inbox_claims(message_id,claimed_by,claimed_at,resolved_at) VALUES(?,?,?,?)
              ON CONFLICT(message_id) DO UPDATE SET claimed_by=excluded.claimed_by,resolved_at=excluded.resolved_at""",
              (message['id'],values['handler_id'],now(),now() if values['status']=='Resolved' else None))
        else:db.execute('DELETE FROM inbox_claims WHERE message_id=?',(message['id'],))
    audit(db,actor,'Conversation updated' ,'service_conversations',c['lead_id'],changes)


def check_token(c,data):
    if data.get('review_token')!=c['review_token']:raise HTTPException(409,'This conversation changed. Refresh before continuing.')


def can_handle(c,u):
    if c['handler_id']!=u['id'] and u['id'] not in c.get('task_handlers',[]) and u['role'] not in MANAGERS:
        raise HTTPException(403,'Claim this conversation or ask its handler to hand it over.')


def claim_conversation(db,lid,u):
    require_role(u,OFFICE)
    c=conversation(db,lid)
    if c['handler_id'] and c['handler_id']!=u['id']:raise HTTPException(409,'This conversation is already claimed. Ask for a handover.')
    if c['handler_id']!=u['id']:
        save_state(db,c,u['id'],handler_id=u['id'],status='In progress',remind_on='')
        db.execute("UPDATE tasks SET status='Done' WHERE legacy_id=?",('conversation-reminder:'+lid,))
    return conversation(db,lid)


def safe_public_body(body):
    # Access emails may contain several service links; never republish credentials.
    return re.sub(r'https?://\S*(?:#access=|[?&]token=)\S*','[Open the related document in your private moving file]',body or '')


def timeline(db,lid,public=False):
    lead=one(db,'SELECT * FROM leads WHERE id=?',(lid,));events=[]
    def add(key,at,kind,title,body='',visibility='client',actor='',direction='update',status=''):
        if public and visibility!='client':return
        events.append(dict(id=key,at=at,kind=kind,title=title,body=safe_public_body(body) if public else body,
                           visibility=visibility,actor=actor,direction=direction,status=status,lead_id=lid,service_date=lead['move_date']))
    add('inquiry:'+lid,lead['created_at'],'inquiry','Service request received',lead['move_date'] or 'Date to confirm')
    people={r['id']:r['name'] for r in rows(db,'SELECT id,name FROM users')}
    for r in rows(db,'SELECT * FROM conversation_entries WHERE lead_id=?',(lid,)):
        add('entry:'+r['id'],r['created_at'],r['kind'],r['subject'],r['body'],r['visibility'],
            'You' if public and r['direction']=='client' else 'Crown Movers' if public else people.get(r['actor'],r['actor']),r['direction'])
    if not public:
        form_messages={r['message_id'] for r in rows(db,"SELECT json_extract(payload_json,'$.message_id') message_id FROM integration_events WHERE event_type='gmail_quote' AND json_valid(payload_json)")}
        for r in rows(db,'SELECT * FROM messages WHERE lead_id=?',(lid,)):
            kind='form' if r['id'] in form_messages or 'form' in r['kind'].lower() else 'email' if r['kind'].lower().startswith('incoming') else 'note'
            add('message:'+r['id'],r['created_at'],kind,r['subject'] or r['kind'],r['body'],'staff',people.get(r['author'],r['author']), 'client' if r['kind'].lower().startswith('incoming') else 'staff')
        for r in rows(db,'SELECT * FROM calls WHERE lead_id=?',(lid,)):
            d=json.loads(r['payload_json']);add('call:'+r['id'],r['created_at'],'call',d.get('disposition') or 'Phone call',d.get('note') or d.get('summary') or d.get('phone') or '', 'staff')
    for r in rows(db,"SELECT * FROM outbound_messages WHERE lead_id=? AND status='Sent'",(lid,)):
        if public and (r['recipient'].strip().lower()!=lead['email'].strip().lower() or r['event_key'].startswith('client-access:')):continue
        add('mail:'+r['id'],r['sent_at'] or r['created_at'],'email',r['subject'],r['body'],actor='Crown Movers' if public else people.get(r['approved_by'],'Crown Movers'),direction='staff',status='Sent')
    for r in rows(db,'SELECT * FROM client_changes WHERE lead_id=?',(lid,)):
        fields=json.loads(r['payload_json']);body='; '.join(str(k).replace('_',' ')+': '+str(v) for k,v in fields.items())
        add('change:'+r['id'],r['created_at'],'details','Moving details submitted',body,status=r['status'])
        if r['reviewed_at']:add('review:'+r['id'],r['reviewed_at'],'details','Moving details reviewed',status=r['status'])
    for r in rows(db,'SELECT * FROM attachments WHERE lead_id=? AND deleted_at IS NULL',(lid,)):
        add('file:'+r['id'],r['created_at'],'file','File received',r['original_name'],status=r['scan_status'])
    for r in rows(db,'SELECT * FROM saved_media_links WHERE lead_id=? AND removed_at IS NULL',(lid,)):
        add('link:'+r['id'],r['created_at'],'file','Photo / video folder added',r['label'])
    for r in rows(db,'SELECT * FROM client_callbacks WHERE lead_id=?',(lid,)):
        add('callback:'+r['id'],r['created_at'],'callback','Callback requested',r['preferred_time']+('\n'+r['message'] if r['message'] else ''),status=r['status'])
    if not public:
        for r in rows(db,'SELECT * FROM action_outcomes WHERE lead_id=?',(lid,)):
            add('outcome:'+r['id'],r['created_at'],'note','Follow-up: '+r['outcome'].replace('_',' '),r['note']+(' · Follow up '+r['followup_date'] if r['followup_date'] else ''),'staff',people.get(r['actor'],r['actor']))
        for r in rows(db,'SELECT * FROM quote_views WHERE lead_id=?',(lid,)):
            add('view:'+r['id'],r['viewed_at'],'estimate','Estimate opened','Opened in the private portal; this does not prove it was read.','staff')
    for r in rows(db,"SELECT * FROM quotes WHERE lead_id=? AND status='Accepted'",(lid,)):
        add('accept:'+r['id'],r['accepted_at'] or r['created_at'],'estimate','Estimate accepted','Revision '+str(r['revision']))
    for r in rows(db,"SELECT * FROM jobs WHERE lead_id=? AND status IN ('Confirmed','In progress','Completed')",(lid,)):
        add('job:'+r['id'],r['created_at'],'booking','Service schedule',r['start_at'],status=r['status'])
    for r in rows(db,"SELECT i.* FROM job_invoices i JOIN jobs j ON j.id=i.job_id WHERE j.lead_id=? AND i.status='Issued'",(lid,)):
        add('invoice:'+r['id'],r['issued_at'] or r['created_at'],'invoice','Invoice issued',r['number'])
    for r in rows(db,'SELECT * FROM payments WHERE lead_id=? AND verified=1',(lid,)):
        add('payment:'+r['id'],r['created_at'],'payment','Payment reversal recorded' if r['reverses_id'] else 'Verified payment record','CAD %.2f'%(r['amount_cents']/100))
    return sorted(events,key=lambda x:(x['at'] or '',x['id']),reverse=True)


def entry(db,lid,kind,body,subject,actor,key,visibility='staff',direction='staff'):
    prior=one(db,'SELECT * FROM conversation_entries WHERE lead_id=? AND request_key=?',(lid,key))
    payload=(kind,body,subject,actor,visibility,direction)
    if prior:
        if tuple(prior[k] for k in ('kind','body','subject','actor','visibility','direction'))!=payload:raise HTTPException(409,'This saved request has different content. Refresh before retrying.')
        return prior
    r=dict(id=uid('CONV'),lead_id=lid,kind=kind,body=body,subject=subject,actor=actor,visibility=visibility,direction=direction,request_key=key,created_at=datetime.now(ZoneInfo('UTC')).isoformat())
    insert(db,'conversation_entries',r);return r


def request_key(data):
    key=str(data.get('request_key') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{12,100}',key):raise HTTPException(422,'Refresh the form before saving.')
    return key


def mail_review_token(m,c):
    return hashlib.sha256(dumps([m['id'],m['subject'],m['body'],m['recipient'],m['status'],c['review_token']]).encode()).hexdigest()


def register_conversations(app,path,auth):
    # New client activity reopens the existing conversation and cancels its stale wait task.
    # Triggers also cover existing email/form/upload ingestion paths atomically.
    with transaction(path) as db:
        # Preserve existing claimants and closed work on migration. Every subsequent
        # inquiry starts a conversation, so client updates can reopen it atomically.
        for lead in rows(db,'SELECT id FROM leads WHERE id NOT IN (SELECT lead_id FROM service_conversations)'):
            c=conversation(db,lead['id'])
            insert(db,'service_conversations',{k:c[k] for k in ('lead_id','handler_id','status','remind_on','seen_signal','revision','updated_at')})
        db.execute("""CREATE TRIGGER IF NOT EXISTS conversation_new_lead AFTER INSERT ON leads BEGIN
          INSERT INTO service_conversations(lead_id,status,updated_at) VALUES(NEW.id,'Needs reply',NEW.created_at);
          END""")
        for table,condition in [('messages',"lower(NEW.kind) LIKE 'incoming%'"),
                ('conversation_entries',"NEW.direction='client'"),('attachments','1'),
                ('client_changes','1'),('client_callbacks','1'),('calls','1'),('saved_media_links','1')]:
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS conversation_new_{table} AFTER INSERT ON {table}
              WHEN {condition} BEGIN
              UPDATE service_conversations SET status='Needs reply',remind_on='',revision=revision+1,
                updated_at=NEW.created_at WHERE lead_id=NEW.lead_id;
              UPDATE tasks SET status='Done' WHERE legacy_id='conversation-reminder:'||NEW.lead_id;
              END""")
    @app.get('/api/inbox')
    def inbox(q:str='',scope:str='team',status:str='open',offset:int=0,u=Depends(auth)):
        require_role(u,OFFICE)
        if scope not in ('team','mine','unclaimed'):raise HTTPException(422,'Choose Mine, Team or Unclaimed.')
        if status not in STATUSES|{'open','all'}:raise HTTPException(422,'Unknown status.')
        with transaction(path) as db:
            from .sales_crm_v6 import sync_unlinked_clients
            sync_unlinked_clients(db)
            leads=rows(db,"""SELECT l.id,l.client_name,l.move_date,l.email,l.phone,l.assigned_to,l.stage,l.updated_at,
              x.client_id FROM leads l LEFT JOIN client_account_leads x ON x.lead_id=l.id
              WHERE l.archived_import=0 AND (?='' OR lower(l.client_name||' '||l.email||' '||l.phone||' '||l.pickup) LIKE ?)
              ORDER BY l.updated_at DESC,l.id""",(q.strip(),'%'+q.strip().lower()+'%'))
            result=[]
            for l in leads:
                c=conversation(db,l['id'])
                if l['stage'] in ('Lost','Closed','Completed') and not c['signal'] and not c['revision']:continue
                if scope=='mine' and c['handler_id']!=u['id']:continue
                if scope=='unclaimed' and c['handler_id']:continue
                if status=='open' and c['status']=='Resolved':continue
                if status in STATUSES and c['status']!=status:continue
                latest=one(db,"""SELECT MAX(at) at FROM (
                  SELECT created_at at FROM conversation_entries WHERE lead_id=?
                  UNION ALL SELECT created_at FROM messages WHERE lead_id=?
                  UNION ALL SELECT sent_at FROM outbound_messages WHERE lead_id=? AND status='Sent')""",(l['id'],)*3)
                signal=json.loads(c['signal']) if c['signal'] else {}
                l.update({k:c[k] for k in ('handler_id','handler_name','service_owner','status','remind_on')})
                l['last_activity']=max(l['updated_at'],signal.get('created_at',''),latest['at'] or '')
                l['new_activity']=bool(c['signal'] and c['signal']!=c.get('seen_signal'))
                result.append(l)
            result.sort(key=lambda r:(r['last_activity'],r['id']),reverse=True);offset=max(0,offset)
            return {'items':result[offset:offset+30],'total':len(result),'offset':offset}

    @app.get('/api/inbox/activity')
    def activity(kind:str='calls',q:str='',offset:int=0,u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            if kind=='calls':
                result=rows(db,"""SELECT c.id,c.lead_id,c.created_at,c.payload_json,l.client_name,l.move_date
                  FROM calls c LEFT JOIN leads l ON l.id=c.lead_id ORDER BY c.created_at DESC""")
                for r in result:
                    d=json.loads(r.pop('payload_json'));r.update(subject=d.get('disposition') or 'Phone call',body=d.get('note') or d.get('phone') or '',status=d.get('source') or 'Recorded')
                result+=rows(db,"""SELECT e.id,e.lead_id,e.created_at,e.subject,e.body,'Recorded' status,l.client_name,l.move_date
                  FROM conversation_entries e JOIN leads l ON l.id=e.lead_id WHERE e.kind='call'""")
            elif kind in ('drafts','sent'):
                result=rows(db,"""SELECT o.id,o.lead_id,o.created_at,o.subject,o.body,o.status,o.error,l.client_name,l.move_date
                  FROM outbound_messages o JOIN leads l ON l.id=o.lead_id WHERE """+('o.status=\'Sent\'' if kind=='sent' else "o.status NOT IN ('Sent','Cancelled')"))
            else:raise HTTPException(422,'Unknown activity type.')
            needle=q.strip().lower();result=[r for r in result if needle in ' '.join(str(r.get(k) or '') for k in ('client_name','subject','body','move_date')).lower()]
            result.sort(key=lambda r:(r['created_at'],r['id']),reverse=True);offset=max(0,offset)
            return {'items':result[offset:offset+30],'total':len(result),'offset':offset}

    @app.get('/api/inbox/{lid}')
    def detail(lid:str,offset:int=0,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            from .sales_crm_v6 import sync_unlinked_clients
            sync_unlinked_clients(db)
            l=staff_read_project(db,lid,u);c=conversation(db,lid);events=timeline(db,lid);offset=max(0,offset)
            link=one(db,'SELECT client_id FROM client_account_leads WHERE lead_id=?',(lid,))
            services=rows(db,'SELECT l.id,l.move_date,l.stage FROM leads l JOIN client_account_leads x ON x.lead_id=l.id WHERE x.client_id=? ORDER BY l.move_date DESC',(link['client_id'],)) if link else []
            return {'client':{k:l[k] for k in ('id','client_name','email','phone','move_date','stage')},'client_id':link['client_id'] if link else None,
                    'conversation':c,'events':events[offset:offset+40],'total':len(events),'offset':offset,'services':services}

    @app.get('/api/crm/clients/{cid}/history')
    def client_history(cid:str,offset:int=0,kind:str='all',u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            if not one(db,'SELECT id FROM client_accounts WHERE id=?',(cid,)):raise HTTPException(404,'Client not found.')
            events=[]
            for l in rows(db,'SELECT lead_id FROM client_account_leads WHERE client_id=?',(cid,)):events+=timeline(db,l['lead_id'])
            if kind not in ('all','emails','forms','calls','notes'):raise HTTPException(422,'Choose an interaction type.')
            groups={'emails':{'email'},'forms':{'inquiry','form','details','file','portal'},'calls':{'call','callback'},'notes':{'note','handoff','status'}}
            if kind!='all':events=[e for e in events if e['kind'] in groups[kind]]
            events.sort(key=lambda r:(r['at'] or '',r['id']),reverse=True);offset=max(0,offset)
            return {'items':events[offset:offset+40],'total':len(events),'offset':offset}

    @app.post('/api/inbox/{lid}/claim')
    def claim(lid:str,data:dict,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            c=conversation(db,lid);check_token(c,data);return claim_conversation(db,lid,u)

    @app.post('/api/inbox/{lid}/assign')
    def assign(lid:str,data:dict,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            c=conversation(db,lid);check_token(c,data)
            if c['handler_id']!=u['id'] and u['role'] not in MANAGERS:raise HTTPException(403,'Only the current handler or a manager can hand over the client history.')
            target=str(data.get('handler_id') or '');note=text(data.get('note',''),'Handoff note',2000)
            if not note:raise HTTPException(422,'Add a short handoff note.')
            if target and not one(db,"SELECT id FROM users WHERE id=? AND active=1 AND role IN ('owner','manager','sales','dispatch')",(target,)):
                raise HTTPException(422,'Choose an active sales or operations team member.')
            target_name=one(db,'SELECT name FROM users WHERE id=?',(target,)) if target else None
            entry(db,lid,'handoff',note,'Handed over to '+(target_name['name'] if target_name else 'Unclaimed'),u['id'],uid('HAND'))
            save_state(db,c,u['id'],handler_id=target or None)
            db.execute("UPDATE tasks SET owner=? WHERE legacy_id=? AND status='Open'",(target,'conversation-reminder:'+lid))
        return {'ok':True}

    @app.post('/api/inbox/{lid}/status')
    def status_update(lid:str,data:dict,u=Depends(auth)):
        require_role(u,OFFICE);status=data.get('status')
        if status not in STATUSES:raise HTTPException(422,'Choose a conversation status.')
        with transaction(path) as db:
            c=conversation(db,lid);check_token(c,data)
            if c['handler_id']!=u['id'] and u['role'] not in MANAGERS:raise HTTPException(403,'Update your own follow-up in Tasks. Only the client history handler or a manager can change the overall reply status.')
            remind=''
            if status=='Waiting on client':
                try:remind=date.fromisoformat(str(data.get('remind_on',''))).isoformat()
                except ValueError:raise HTTPException(422,'Choose a reminder date.')
                if remind<datetime.now(ZoneInfo('America/Toronto')).date().isoformat():raise HTTPException(422,'Choose today or a future date.')
            key='conversation-reminder:'+lid
            db.execute("UPDATE tasks SET status='Done' WHERE legacy_id=?",(key,))
            if remind:
                old=one(db,'SELECT id FROM tasks WHERE legacy_id=?',(key,))
                if old:db.execute("UPDATE tasks SET status='Open',due_date=?,owner=? WHERE id=?",(remind,c['handler_id'] or u['id'],old['id']))
                else:insert(db,'tasks',dict(id=uid('TASK'),lead_id=lid,legacy_id=key,title='Follow up on waiting conversation',due_date=remind,owner=c['handler_id'] or u['id'],created_at=now()))
            save_state(db,c,u['id'],status=status,remind_on=remind)
            entry(db,lid,'status',text(data.get('note',''),'Note',2000),status,u['id'],uid('STATE'))
        return {'ok':True}

    @app.post('/api/inbox/{lid}/entries')
    def add_entry(lid:str,data:dict,u=Depends(auth)):
        require_role(u,OFFICE);key=request_key(data);kind=data.get('kind','note')
        if kind not in ('note','call','portal'):raise HTTPException(422,'Choose an internal note, call outcome or portal reply.')
        body=text(data.get('body',''),'Message',10000)
        if not body:raise HTTPException(422,'Enter a message.')
        visible='client' if kind=='portal' or kind=='call' and data.get('share_with_client') is True else 'staff'
        with transaction(path) as db:
            c=conversation(db,lid);can_handle(c,u)
            old=one(db,'SELECT id FROM conversation_entries WHERE lead_id=? AND request_key=?',(lid,key))
            if not old:check_token(c,data)
            r=entry(db,lid,kind,body,{'note':'Internal note','call':'Phone conversation with Crown Movers','portal':'Message from Crown Movers'}[kind],u['id'],key,visible)
        return {'id':r['id'],'visibility':visible,'email_sent':False}

    @app.post('/api/inbox/{lid}/email')
    def prepare_email(lid:str,data:dict,u=Depends(auth)):
        require_role(u,OFFICE);key=request_key(data)
        subject=text(data.get('subject',''),'Subject',300);body=text(data.get('body',''),'Message',10000)
        if not body or not subject:raise HTTPException(422,'Enter a subject and message.')
        with transaction(path) as db:
            c=conversation(db,lid);can_handle(c,u)
            old=one(db,'SELECT * FROM outbound_messages WHERE event_key=?',('conversation:'+lid+':'+key,))
            if old and (old['body']!=body or old['subject']!=subject):raise HTTPException(409,'This draft already has different content.')
            if not old:check_token(c,data)
            r=queue_message(db,lid,subject,body,'Reply',u['id'],'conversation:'+lid+':'+key)
        return {'mail_id':r['id'],'sent':False}

    @app.get('/api/portal/conversation')
    def portal_history(offset:int=0,c=Depends(app.state.client_auth)):
        with closing(connect(path)) as db:
            events=timeline(db,c['lead_id'],public=True);offset=max(0,offset)
            return {'items':events[offset:offset+40],'total':len(events),'offset':offset}

    @app.post('/api/portal/conversation')
    def portal_message(data:dict,c=Depends(app.state.client_auth)):
        key=request_key(data);body=text(data.get('body',''),'Message',10000)
        if not body:raise HTTPException(422,'Enter your message.')
        with transaction(path) as db:
            old=one(db,'SELECT id FROM conversation_entries WHERE lead_id=? AND request_key=?',(c['lead_id'],key))
            if not old and db.execute("SELECT COUNT(*) FROM conversation_entries WHERE lead_id=? AND direction='client' AND created_at>?",(c['lead_id'],(datetime.now(ZoneInfo('UTC'))-timedelta(minutes=1)).isoformat())).fetchone()[0]>=10:
                raise HTTPException(429,'Please wait a minute before sending another message.')
            r=entry(db,c['lead_id'],'portal',body,'Client message', 'client',key,'client','client')
        return {'id':r['id'],'received':True}

    @app.get('/api/inbox/mail/{mid}')
    def read_mail(mid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            m=one(db,'SELECT * FROM outbound_messages WHERE id=?',(mid,))
            if not m:raise HTTPException(404,'Message not found.')
            m['conversation']=conversation(db,m['lead_id'])
            m['can_handle']=m['conversation']['handler_id']==u['id'] or u['id'] in m['conversation'].get('task_handlers',[]) or u['role'] in MANAGERS
            m['review_token']=mail_review_token(m,m['conversation'])
            return m

    @app.put('/api/inbox/mail/{mid}')
    def edit_mail(mid:str,data:dict,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            m=one(db,'SELECT * FROM outbound_messages WHERE id=?',(mid,))
            if not m:raise HTTPException(404,'Message not found.')
            c=conversation(db,m['lead_id']);can_handle(c,u)
            expected=mail_review_token(m,c)
            if data.get('review_token')!=expected:raise HTTPException(409,'This draft changed. Review the current version.')
            if m['status']!='Draft' or not m['event_key'].startswith('conversation:'):raise HTTPException(409,'Only unsent conversation replies can be edited here.')
            subject=text(data.get('subject',''),'Subject',300);body=text(data.get('body',''),'Message',10000)
            if not subject or not body:raise HTTPException(422,'Enter a subject and message.')
            db.execute('UPDATE outbound_messages SET subject=?,body=? WHERE id=?',(subject,body,mid))
            audit(db,u['id'],'Reply draft edited','outbound_messages',mid)
        return {'ok':True}

    @app.post('/api/inbox/mail/{mid}/send')
    def send_mail(mid:str,data:dict,u=Depends(auth)):
        require_role(u,OFFICE)
        if data.get('confirmed') is not True:raise HTTPException(422,'Review the recipient and message before sending.')
        with transaction(path) as db:
            m=one(db,'SELECT * FROM outbound_messages WHERE id=?',(mid,))
            if not m:raise HTTPException(404,'Message not found.')
            c=conversation(db,m['lead_id']);can_handle(c,u)
            expected=mail_review_token(m,c)
            if data.get('review_token')!=expected:raise HTTPException(409,'This email changed. Review the current draft.')
            if m['status'] not in ('Draft','Approved'):raise HTTPException(409,'This message cannot be sent again. Check its delivery status.')
            l=one(db,'SELECT email FROM leads WHERE id=?',(m['lead_id'],))
            if l['email'].strip().lower()!=m['recipient'].strip().lower():raise HTTPException(409,'The client email changed. Prepare a new message.')
            db.execute("UPDATE outbound_messages SET status='Approved',approved_by=? WHERE id=?",(u['id'],mid))
            audit(db,u['id'],'Email reviewed in shared Inbox','outbound_messages',mid)
        from .providers import process_mail
        return process_mail(path,mid)
