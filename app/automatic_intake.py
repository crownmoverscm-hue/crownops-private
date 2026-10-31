"""Prepare local drafts using the exact same company calculator as the browser."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import json
import threading
from fastapi import Depends, HTTPException
from .store import connect, transaction, one, rows, insert, audit, uid, now, dumps
from .platform_common import settings, put_setting, require_role, SALES, MANAGERS

SYNC_LOCK=threading.Lock()

def repair_imported_forms(path,actor):
    """Fill parsing gaps only on untouched Gmail forms without estimates or bookings."""
    from .gmail_sync import _quote_fields
    from .inquiry_intake import intake_values,photo_link
    fields=('client_name','email','phone','move_date','pickup','dropoff','home_size','boxes','pickup_access','dropoff_access','pickup_walk','dropoff_walk','inventory','heavy_items','photo_link','notes')
    repaired=0
    with transaction(path) as db:
        candidates=rows(db,"""SELECT l.* FROM leads l WHERE l.source='Gmail' AND l.lead_type IN ('Form','Instant estimate')
            AND l.version=1 AND l.assigned_to='' AND l.archived_import=0 AND NOT EXISTS(SELECT 1 FROM quotes q WHERE q.lead_id=l.id)
            AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.lead_id=l.id) ORDER BY l.created_at DESC LIMIT 2000""")
        for lead in candidates:
            original=one(db,"SELECT subject,body FROM messages WHERE lead_id=? AND lower(kind)='incoming email' ORDER BY created_at LIMIT 1",(lead['id'],))
            if not original:continue
            parsed=_quote_fields(original['body'],original['subject']);changes={}
            for field in fields:
                if (not str(lead.get(field) or '').strip() or (field=='client_name' and lead[field]=='Website inquiry')) and parsed.get(field):
                    changes[field]=parsed[field]
            try:
                if changes.get('photo_link'):photo_link(changes['photo_link'])
                old_meta=json.loads(lead.get('intake_json') or '{}')
                meta=intake_values({**old_meta,**{k:v for k,v in parsed.get('intake',{}).items() if v and not old_meta.get(k)}})
            except (HTTPException,ValueError):continue
            if meta!=old_meta:changes['intake_json']=dumps(meta)
            if not changes:continue
            db.execute('UPDATE leads SET '+','.join(k+'=?' for k in changes)+',version=version+1,updated_at=? WHERE id=?',[*changes.values(),now(),lead['id']])
            # Correct a placeholder contact label without overwriting a reviewed account name.
            if changes.get('client_name'):
                db.execute("UPDATE client_accounts SET client_name=? WHERE id IN (SELECT client_id FROM client_account_leads WHERE lead_id=?) AND client_name IN ('','Website inquiry')",(changes['client_name'],lead['id']))
            for field in ('email','phone'):
                if changes.get(field):db.execute('UPDATE client_accounts SET '+field+"=? WHERE id IN (SELECT client_id FROM client_account_leads WHERE lead_id=?) AND "+field+"=''",(changes[field],lead['id']))
            prepare_draft(db,one(db,'SELECT * FROM leads WHERE id=?',(lead['id'],)),actor,blockers=parsed.get('parser_review') or ())
            audit(db,actor,'Original quote-form fields recovered','leads',lead['id'],{'fields':list(changes),'original_preserved':True})
            repaired+=1
    return repaired

def labour_suggestion(lead):
    from py_mini_racer import MiniRacer
    with MiniRacer() as engine:
        engine.eval('var window={};'+Path(__file__).with_name('static').joinpath('local_estimator.js').read_text(),timeout_sec=1,max_memory=64*1024*1024)
        return engine.call('window.CrownLocalEstimator.suggest',lead,timeout_sec=1,max_memory=64*1024*1024)

def prepare_draft(db,lead,actor,blockers=()):
    prior=one(db,'SELECT * FROM intake_drafts WHERE lead_id=?',(lead['id'],))
    if prior and prior['quote_id']:return prior
    existing=one(db,'SELECT id FROM quotes WHERE lead_id=? ORDER BY revision DESC LIMIT 1',(lead['id'],))
    if existing:return {'lead_id':lead['id'],'quote_id':existing['id'],'status':'Estimate already exists','missing':[]}
    missing=list(blockers)
    for field,label in [('client_name','client name'),('email','email'),('phone','phone'),('move_date','service date'),('pickup','pickup address'),('dropoff','destination'),('home_size','move size'),('boxes','box count'),('pickup_access','pickup access'),('dropoff_access','destination access'),('pickup_walk','pickup walking distance'),('dropoff_walk','destination walking distance')]:
        value=str(lead.get(field) or '').strip()
        if not value or 'not sure' in value.lower() or 'unknown' in value.lower():missing.append(label)
    try:
        meta=json.loads(lead.get('intake_json') or '{}')
        suggestion=labour_suggestion(lead) if meta.get('local_area')=='Yes' else {'available':False,'reason':'Confirm the service area and service type before calculating.'}
    except Exception:
        suggestion={'available':False,'reason':'Calculator guidance is unavailable. Sales can prepare this draft manually.'}
    meta=json.loads(lead.get('intake_json') or '{}')
    inputs={'lead_id':lead['id'],'lead_version':lead['version'],'move_date':lead.get('move_date',''),'service_type':lead['lead_type'] if lead['lead_type'] in ('Moving','Long Distance','Packing','Junk Removal') else 'Moving',
            'language':lead.get('language','English'),'start_window':meta.get('start_window') or 'unknown','auto_prepared':True}
    if suggestion.get('available'):
        inputs.update({k:suggestion[k] for k in ('movers','trucks','hours_low','hours_high','minimum_hours','small_afternoon_job')})
        if not suggestion.get('can_apply'):missing.append('driving time between stops')
        if suggestion.get('transport_hours') is None:missing.append('base travel allowance')
        else:inputs['transport_hours']=suggestion['transport_hours']
    else:missing.append(suggestion.get('reason') or 'service calculation')
    pricing=one(db,'SELECT * FROM pricing_versions WHERE approved=1 ORDER BY created_at DESC LIMIT 1')
    if not pricing:missing.append('approved pricing setup')
    if meta.get('start_window') not in ('morning','afternoon'):missing.append('preferred arrival window')
    status='Needs details';qid=None
    if not missing:
        from .sales_crm_v6 import calculate,quote_start_window
        lang='French' if str(inputs['language']).lower().startswith('fr') else 'English'
        template=one(db,"SELECT * FROM crm_quote_templates WHERE active=1 AND service_type='Moving' AND language=? ORDER BY template_key LIMIT 1",(lang,))
        if not template:missing.append('active service template')
        else:
            try:
                result=calculate(db,inputs)
                inputs.update(template_key=template['template_key'],template_snapshot={'template_key':template['template_key'],'name':template['name'],**json.loads(template['body_json'])},
                              deposit_required=300,client_note='',quote_title='',limited_liability='$0.60/lb per article')
                inputs['start_time'],inputs['start_window_label']=quote_start_window(inputs)
                qid=uid('QUOTE')
                insert(db,'quotes',dict(id=qid,lead_id=lead['id'],revision=1,status='Draft',inputs_json=dumps(inputs),result_json=dumps(result),pricing_id=pricing['id'],lead_snapshot_json=dumps({k:v for k,v in lead.items() if k!='raw_json'}),lead_version=lead['version'],created_by=actor,created_at=now()))
                status='Draft ready for review'
                audit(db,actor,'Estimate draft prepared automatically','quotes',qid,{'sent':False,'approved':False})
            except HTTPException as ex:missing.append(str(ex.detail))
    inputs['suggestion']=suggestion
    db.execute('INSERT INTO intake_drafts(lead_id,inputs_json,missing_json,quote_id,status,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(lead_id) DO UPDATE SET inputs_json=excluded.inputs_json,missing_json=excluded.missing_json,quote_id=excluded.quote_id,status=excluded.status,updated_at=excluded.updated_at',
        (lead['id'],dumps(inputs),dumps(list(dict.fromkeys(missing))),qid,status,now()))
    return {'lead_id':lead['id'],'quote_id':qid,'status':status,'missing':missing,'inputs':inputs}

def run_intake(path,new_lead,force=False):
    from .quote_staging import deny_mailbox
    deny_mailbox()
    if not SYNC_LOCK.acquire(blocking=False):return {'running':True}
    try:
        with transaction(path) as db:
            cfg=settings(db,'intake_sync',{'enabled':True})
            if not cfg.get('enabled',True) and not force:return {'enabled':False}
            previous=settings(db,'intake_sync_state',{})
            put_setting(db,'intake_sync_state',{**previous,'last_attempt':now(),'running':True})
        step='repair'
        repaired=repair_imported_forms(path,'automatic-intake-repair')
        from .providers import provider_token
        from .gmail_sync import sync_gmail_readonly
        step='connection'
        token=provider_token(path,'gmail',require_external=False)
        step='import'
        result=sync_gmail_readonly(path,token['access_token'],'automatic-intake',new_lead,days=14,max_per_feed=50,fluid=True,feed_names=('quote','missed_call','voicemail'))
        result['recovered_forms']=repaired
        state={'last_attempt':now(),'last_success':now() if not result['errors'] else previous.get('last_success'),'running':False,'summary':result,'enabled':True}
        with transaction(path) as db:put_setting(db,'intake_sync_state',state)
        return state
    except Exception as ex:
        step=locals().get('step','setup')
        code='gmail_connection' if step=='connection' else 'intake_import' if step=='import' else 'intake_processing'
        message='Gmail access could not be confirmed. An owner or manager should check Connections and reconnect Gmail if requested.' if step=='connection' else 'Inquiry processing did not finish. Existing client records were kept. Check the import again; management should review the server logs if this continues.'
        state={'last_attempt':now(),'last_success':locals().get('previous',{}).get('last_success'),'running':False,'error':message,'error_code':code,'enabled':True}
        import logging
        logging.getLogger('crownops').warning('Automatic intake stopped: step=%s exception=%s',step,type(ex).__name__)
        with transaction(path) as db:put_setting(db,'intake_sync_state',state)
        return state
    finally:SYNC_LOCK.release()

def register_automatic_intake(app,path,auth,new_lead):
    @app.get('/api/intake/status')
    def status(u=Depends(auth)):
        require_role(u,SALES)
        with closing(connect(path)) as db:return {**settings(db,'intake_sync_state',{}),'settings':settings(db,'intake_sync',{'enabled':True}),'can_manage':u['role'] in MANAGERS}

    @app.post('/api/intake/sync')
    def sync(u=Depends(auth)):
        require_role(u,SALES)
        return run_intake(path,new_lead,force=True)

    @app.put('/api/intake/settings')
    def configure(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        if set(data)!={'enabled'} or type(data['enabled']) is not bool:raise HTTPException(422,'Choose whether automatic inquiry import is enabled.')
        with transaction(path) as db:
            put_setting(db,'intake_sync',data);audit(db,u['id'],'Automatic intake settings updated','settings','intake_sync',data)
        return data

    @app.post('/api/intake/drafts/{lid}/prepare')
    def draft(lid:str,u=Depends(auth)):
        require_role(u,SALES)
        with transaction(path) as db:
            lead=one(db,'SELECT * FROM leads WHERE id=?',(lid,))
            if not lead:raise HTTPException(404,'Inquiry not found.')
            prepared=prepare_draft(db,lead,u['id'])
            record=one(db,'SELECT * FROM intake_drafts WHERE lead_id=?',(lid,))
        if not record:return prepared
        quote=None
        if record.get('quote_id'):
            with closing(connect(path)) as db:quote=one(db,'SELECT lead_version FROM quotes WHERE id=?',(record['quote_id'],))
            if quote and quote['lead_version']!=lead['version']:
                return {**record,'status':'Client details changed — review a new draft','inputs':json.loads(record['inputs_json']),'missing':['updated client details and estimate revision']}
        return {**record,'inputs':json.loads(record['inputs_json']),'missing':json.loads(record['missing_json'])}
