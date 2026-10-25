"""Explicit preview/apply recovery of stored form exceptions. No provider calls."""
from contextlib import closing
import hashlib
import json
from fastapi import Depends, HTTPException
from .store import connect, transaction, rows, one, dumps, now, audit
from .platform_common import require_role, MANAGERS
from .gmail_sync import _quote_fields, match_inquiry_project, _quote_inquiry


def _selection(data):
    ids=data.get('event_ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(x,str) or len(x)>180 for x in ids) or len(set(ids))!=len(ids):
        raise HTTPException(422,'Select 1–100 unique stored form exception IDs.')
    return ids


def recovery_preview(db,ids):
    items=[];snapshots=[]
    for eid in ids:
        event=one(db,"SELECT * FROM integration_events WHERE id=? AND event_type='gmail_quote'",(eid,))
        if not event:raise HTTPException(404,'Stored quote form not found: '+eid)
        try:payload=json.loads(event['payload_json'])
        except (ValueError,TypeError):payload={}
        if not isinstance(payload,dict):payload={}
        parsed=_quote_fields(payload.get('body',''),payload.get('subject',''))
        match=match_inquiry_project(db,parsed,'quote')
        failures=[]
        if not parsed['email'] and not parsed['phone']:failures.append('email or phone: no usable labelled contact field')
        if event['status']!='Needs review':failures.append('status: already resolved; no retry needed')
        if match['requires_review'] and not failures:failures.append('identity: '+match['reason'])
        items.append({'event_id':eid,'status':'Blocked' if failures else 'Ready','failing_fields':failures,
                      'parsed':parsed,'matched_service_id':(match['lead'] or {}).get('id'),
                      'existing_service_id':payload.get('lead_id')})
        snapshots.append([event,parsed,match['fingerprint']])
    fingerprint=hashlib.sha256(dumps(snapshots).encode()).hexdigest()
    return {'preview_hash':fingerprint,'items':items,'dry_run':True,'messages_sent':0,'provider_calls':0}


def register_intake_recovery(app,path,auth,new_lead):
    @app.post('/api/intake/recovery/preview')
    def preview(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        with closing(connect(path)) as db:return recovery_preview(db,_selection(data))

    @app.post('/api/intake/recovery/apply')
    def apply(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        if data.get('confirm') is not True:raise HTTPException(422,'Review the recovery preview and explicitly confirm.')
        ids=_selection(data);report=[]
        with transaction(path) as db:
            preview=recovery_preview(db,ids)
            if data.get('preview_hash')!=preview['preview_hash']:raise HTTPException(409,'The exceptions or matching services changed. Preview again.')
            for item in preview['items']:
                if item['status']=='Blocked':report.append(item);continue
                event=one(db,'SELECT * FROM integration_events WHERE id=?',(item['event_id'],))
                payload=json.loads(event['payload_json']);parsed=item['parsed']
                # Recheck after each successful item; the preceding item may
                # have created the exact client/service this item should match.
                match=match_inquiry_project(db,parsed,'quote')
                if match['requires_review']:
                    report.append({**item,'status':'Blocked','failing_fields':['identity: '+match['reason']]});continue
                db.execute('SAVEPOINT recover_form')
                try:
                    lid,created=_quote_inquiry(db,parsed,payload,u['id'],event['event_key'],new_lead,match['lead'])
                    payload['parsed']=parsed
                    payload['recovery']={'actor':u['id'],'at':now(),'parser':'visible-html-v2','created_service':created}
                    db.execute("UPDATE integration_events SET status='Imported',object_id=?,payload_json=? WHERE id=?",(lid,dumps(payload),event['id']))
                    db.execute("UPDATE tasks SET status='Done' WHERE legacy_id=? AND status='Open'",('gmail-match:'+event['event_key'],))
                    draft=one(db,'SELECT quote_id,status,missing_json FROM intake_drafts WHERE lead_id=?',(lid,)) or {}
                    client=one(db,'SELECT client_id FROM client_account_leads WHERE lead_id=?',(lid,)) or {}
                    report.append({'event_id':event['id'],'status':'Recovered','service_id':lid,'client_id':client.get('client_id'),
                                   'quote_id':draft.get('quote_id'),'draft_status':draft.get('status'),
                                   'missing':json.loads(draft.get('missing_json') or '[]')})
                    audit(db,u['id'],'Stored form exception recovered','integration_events',event['id'],{'lead_id':lid,'sent':False})
                except HTTPException as ex:
                    db.execute('ROLLBACK TO recover_form')
                    report.append({**item,'status':'Blocked','failing_fields':[str(ex.detail)]})
                finally:db.execute('RELEASE recover_form')
        return {'items':report,'dry_run':False,'messages_sent':0,'provider_calls':0}
