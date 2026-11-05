"""Staff-reviewed client details, visible estimate opens, and explainable sales priorities."""
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo
import json
from fastapi import Depends, HTTPException
from .store import connect, transaction, one, rows, insert, now, uid, dumps, audit
from .platform_common import SALES, require_role, text, staff_read_project, queue_message, add_task
from .business_zone import BUSINESS_ZONE, zone_name

DETAIL_LABELS={
    'client_name':('Name','Nom'), 'phone':('Phone','Téléphone'),
    'move_date':('Requested service date','Date souhaitée'), 'home_size':('Home / move size','Taille du déménagement'),
    'pickup':('Pickup address, unit and postal code','Adresse de départ, appartement et code postal'),
    'dropoff':('Destination, unit and postal code','Destination, appartement et code postal'),
    'boxes':('Approximate boxes and bags','Nombre approximatif de boîtes et sacs'),
    'inventory':('Furniture, inventory and other stops','Mobilier, inventaire et autres arrêts'),
    'pickup_access':('Pickup stairs, elevator and restrictions','Escaliers, ascenseur et restrictions au départ'),
    'dropoff_access':('Destination stairs, elevator and restrictions','Escaliers, ascenseur et restrictions à destination'),
    'pickup_walk':('Pickup parking / walking distance','Stationnement / distance au départ'),
    'dropoff_walk':('Destination parking / walking distance','Stationnement / distance à destination'),
    'heavy_items':('Heavy / fragile items (or none)','Articles lourds ou fragiles (ou aucun)'),
    'photos':('Photos, video or a shared folder link (optional)','Photos, vidéo ou lien de dossier partagé (facultatif)')}


def details_state(db,lead):
    request=one(db,'SELECT * FROM detail_requests WHERE lead_id=? ORDER BY created_at DESC,id DESC LIMIT 1',(lead['id'],))
    draft=one(db,'SELECT * FROM portal_detail_drafts WHERE lead_id=?',(lead['id'],))
    return {'request':{**{k:request[k] for k in ('id','status','note','created_at','submitted_at','reviewed_at')},'fields':json.loads(request['fields_json'])} if request else None,
            'draft':{**draft,'fields':json.loads(draft['fields_json']),'stale':draft['base_version']!=lead['version']} if draft else None,
            'labels':DETAIL_LABELS,
            'missing':[k for k in DETAIL_LABELS if k!='photos' and not str(lead.get(k) or '').strip()]}


def submitted_details(db,lid):
    """Close the request loop even when the client confirms already-correct details."""
    for r in rows(db,"SELECT * FROM detail_requests WHERE lead_id=? AND status='Open'",(lid,)):
        db.execute("UPDATE detail_requests SET status='Submitted',submitted_at=? WHERE id=?",(now(),r['id']))
        lead=one(db,'SELECT assigned_to FROM leads WHERE id=?',(lid,))
        add_task(db,lid,'Review client information received',lead['assigned_to'],'detail-review:'+r['id'])
    db.execute('DELETE FROM portal_detail_drafts WHERE lead_id=?',(lid,))


def quote_delivery(db,q):
    message=one(db,'SELECT status,sent_at,error FROM outbound_messages WHERE event_key=?',('quote-delivery:'+q['id']+':'+str(q['revision']),))
    qbo=one(db,'SELECT state,sent_at,error FROM qbo_estimate_deliveries WHERE quote_id=?',(q['id'],))
    if qbo:
        message={'status':'Sent' if qbo['sent_at'] else qbo['state'],'sent_at':qbo['sent_at'],'error':qbo['error']}
    views=one(db,'SELECT count(*) opens,min(viewed_at) first_viewed_at,max(viewed_at) last_viewed_at FROM quote_views WHERE quote_id=?',(q['id'],))
    state='Accepted' if q['status']=='Accepted' else 'Sent' if q['status']=='Approved' and message and message['status']=='Sent' else 'Ready' if q['status']=='Approved' else q['status']
    if qbo and qbo['state']=='Rejected' and q['status']!='Accepted':state='Declined'
    return {'state':state,'email_status':message['status'] if message else None,'sent_at':message['sent_at'] if message else None,
            'delivery_provider':'QuickBooks' if qbo else 'CrownOps','delivery_error':qbo['error'] if qbo else '',**views}


def register_sales_journey(app,path,auth,public_url):
    client_auth=app.state.client_auth

    @app.get('/api/leads/{lid}/detail-requests')
    def detail_request_state(lid:str,u=Depends(auth)):
        require_role(u,SALES)
        with closing(connect(path)) as db:
            l=staff_read_project(db,lid,u)
            state=details_state(db,l)
            # Unsaved client work is private until they submit it.
            state.pop('draft',None)
            return state

    @app.post('/api/leads/{lid}/detail-requests')
    def request_details(lid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES)
        key=text(data.get('request_key',''),'Request key',100)
        fields=data.get('fields',[]);note=text(data.get('note',''),'Client note',2000)
        if not key or not isinstance(fields,list) or not fields or any(not isinstance(k,str) or k not in DETAIL_LABELS for k in fields):raise HTTPException(422,'Choose the details to request.')
        fields=list(dict.fromkeys(fields));fingerprint=dumps({'fields':fields,'note':note})
        with transaction(path) as db:
            l=staff_read_project(db,lid,u)
            if not l['email'] or '@' not in l['email']:raise HTTPException(422,'Add the client email before preparing this request.')
            prior=one(db,'SELECT * FROM detail_requests WHERE lead_id=? AND request_key=?',(lid,key))
            if prior:
                if prior['request_json']!=fingerprint:raise HTTPException(409,'That request was already prepared with different details. Reopen the form.')
                return {'id':prior['id'],'mail_id':prior['mail_id'],'status':prior['status'],'duplicate':True,'sent':False}
            active=one(db,"SELECT * FROM detail_requests WHERE lead_id=? AND status='Open'",(lid,))
            if active:
                return {'id':active['id'],'mail_id':active['mail_id'],'status':active['status'],'duplicate':True,'sent':False}
            from .client_communications import portal_link
            rid=uid('DETAIL');link=portal_link(db,l,u['id'],public_url,tab='details');fr=str(l['language']).lower().startswith('fr')
            from .company_profile import company_profile,help_line,team_signoff
            c=company_profile(db)
            checklist='\n'.join('• '+DETAIL_LABELS[k][int(fr)] for k in fields)
            if fr:
                subject='Préparons votre service | '+c['name']
                body=f"Bonjour {l['client_name']},\n\nPour bien préparer votre devis ou votre service, pourriez-vous vérifier votre dossier privé? Les renseignements que nous avons sont déjà remplis. Ajoutez seulement ce qui manque ou a changé.\n\nÀ vérifier :\n{checklist}\n\n{link}\n\nVotre progression est enregistrée dans votre dossier privé. Cliquez sur « Transmettre mes renseignements » lorsque vous êtes prêt. Vous pouvez ajouter vos photos ou coller un lien Google Drive dans Fichiers."
            else:
                subject='Let’s prepare your service | '+c['name']
                body=f"Hi {l['client_name']},\n\nTo help us prepare your estimate or service, please check your private moving file. The details we already have are filled in. Just add anything missing or changed.\n\nPlease check:\n{checklist}\n\n{link}\n\nYour progress is saved in your private file. Click ‘Submit my details’ when you’re ready. Add photos or paste a Google Drive link on the Files tab."
            if note:body+='\n\n'+note
            body+=('\n\nUne date souhaitée reste à confirmer. Besoin d’aide? Demandez un rappel dans votre dossier ou appelez le '+c['phone']+'.' if fr else '\n\nA requested date still needs confirmation. Need a hand? Request a callback in your file or call '+c['phone']+'.')+team_signoff(c,fr)
            m=queue_message(db,lid,subject,body,'Preparation',u['id'],'details-request:'+rid)
            insert(db,'detail_requests',dict(id=rid,lead_id=lid,request_key=key,request_json=fingerprint,fields_json=dumps(fields),note=note,mail_id=m['id'],created_by=u['id'],created_at=now()))
            audit(db,u['id'],'Client information request prepared','detail_requests',rid,{'fields':fields})
            return {'id':rid,'mail_id':m['id'],'status':'Open','sent':False}

    @app.post('/api/leads/{lid}/detail-requests/{rid}/complete')
    def complete_details(lid:str,rid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES)
        with transaction(path) as db:
            staff_read_project(db,lid,u)
            r=one(db,'SELECT * FROM detail_requests WHERE id=? AND lead_id=?',(rid,lid))
            if not r:raise HTTPException(404,'Information request not found.')
            if one(db,"SELECT id FROM client_changes WHERE lead_id=? AND status='Pending'",(lid,)):raise HTTPException(409,'Review the submitted changes before completing this request.')
            db.execute("UPDATE detail_requests SET status='Reviewed',reviewed_at=? WHERE id=?",(now(),rid))
            db.execute("UPDATE tasks SET status='Done' WHERE legacy_id=?",('detail-review:'+rid,))
            if r['status']=='Open':db.execute("UPDATE outbound_messages SET status='Cancelled',error='Information request closed by staff' WHERE id=? AND status IN ('Draft','Approved')",(r['mail_id'],))
            audit(db,u['id'],'Client information request completed','detail_requests',rid)
        return {'ok':True}

    @app.get('/api/portal/details-progress')
    def client_details(c=Depends(client_auth)):
        with closing(connect(path)) as db:return details_state(db,one(db,'SELECT * FROM leads WHERE id=?',(c['lead_id'],)))

    @app.put('/api/portal/details-progress')
    def save_progress(data:dict,c=Depends(client_auth)):
        from .portal import CLIENT_FIELDS
        fields=data.get('fields')
        if not isinstance(fields,dict) or set(fields)-CLIENT_FIELDS:raise HTTPException(422,'Save supported move details.')
        fields={k:text(v,k,18000 if k=='inventory' else 3000) for k,v in fields.items()}
        with transaction(path) as db:
            l=one(db,'SELECT * FROM leads WHERE id=?',(c['lead_id'],))
            if data.get('version')!=l['version']:raise HTTPException(409,'The team updated your file. Refresh and compare before saving.')
            old=one(db,'SELECT * FROM portal_detail_drafts WHERE lead_id=?',(l['id'],))
            revision=old['revision'] if old else 0
            if data.get('revision')!=revision:raise HTTPException(409,'Your progress changed in another window. Refresh before saving.')
            saved=now()
            db.execute('INSERT INTO portal_detail_drafts(lead_id,base_version,fields_json,revision,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(lead_id) DO UPDATE SET base_version=excluded.base_version,fields_json=excluded.fields_json,revision=excluded.revision,updated_at=excluded.updated_at',(l['id'],l['version'],dumps(fields),revision+1,saved))
            return {'ok':True,'revision':revision+1,'saved_at':saved}

    @app.post('/api/portal/quotes/{qid}/view')
    def quote_view(qid:str,c=Depends(client_auth)):
        with transaction(path) as db:
            q=one(db,"SELECT * FROM quotes WHERE id=? AND lead_id=? AND status IN ('Approved','Accepted')",(qid,c['lead_id']))
            if not q:raise HTTPException(404,'Estimate not available.')
            bucket=int(datetime.now().timestamp())//1800
            exists=one(db,'SELECT id FROM quote_views WHERE quote_id=? AND session_hash=? AND time_bucket=?',(qid,c['token_hash'],bucket))
            if exists:return {'ok':True,'duplicate':True}
            insert(db,'quote_views',dict(id=uid('VIEW'),quote_id=qid,lead_id=c['lead_id'],session_hash=c['token_hash'],time_bucket=bucket,viewed_at=now()))
            l=one(db,'SELECT assigned_to FROM leads WHERE id=?',(c['lead_id'],))
            if q['status']=='Approved' and quote_delivery(db,q)['state']=='Sent':add_task(db,c['lead_id'],'Client viewed estimate — review next follow-up',l['assigned_to'],'quote-view:'+qid)
            return {'ok':True,'duplicate':False}

    @app.get('/api/sales/overview')
    def sales_overview(u=Depends(auth)):
        require_role(u,SALES)
        today=datetime.now(BUSINESS_ZONE).date().isoformat();suggestions=[]
        with closing(connect(path)) as db:
            users={r['id']:r['name'] for r in rows(db,'SELECT id,name FROM users WHERE active=1')}
            all_quotes=rows(db,'SELECT * FROM quotes ORDER BY revision DESC,created_at DESC')
            summary={q['id']:quote_delivery(db,q) for q in all_quotes}
            latest={}
            for q in all_quotes:latest.setdefault(q['lead_id'],q)
            leads=rows(db,'SELECT * FROM leads WHERE archived_import=0 ORDER BY updated_at DESC LIMIT 2000')
            assignments={l['id']:users.get(l['assigned_to'],l['assigned_to']) or 'Unassigned' for l in leads}
            tasks={}
            for r in rows(db,"SELECT t.* FROM tasks t JOIN leads l ON l.id=t.lead_id WHERE t.status='Open' AND t.historical=0 AND l.archived_import=0 ORDER BY CASE WHEN t.due_date='' THEN 1 ELSE 0 END,t.due_date,t.created_at,t.id"):
                tasks.setdefault(r['lead_id'],r)
            for l in leads:
                if l['stage'] in ('Lost','Closed'):continue
                mine=l['assigned_to'] in ('',u['id'],u['name'])
                if not mine and u['role']=='sales':continue
                pending=one(db,"SELECT id FROM client_changes WHERE lead_id=? AND status='Pending'",(l['id'],))
                callback=one(db,"SELECT id FROM client_callbacks WHERE lead_id=? AND status='Requested'",(l['id'],))
                submitted=one(db,"SELECT id FROM detail_requests WHERE lead_id=? AND status='Submitted'",(l['id'],))
                if l['stage'] in ('Completed','Booked') and not (pending or callback or submitted):continue
                task=tasks.get(l['id']);q=latest.get(l['id']);delivery=summary.get(q['id'],{}) if q else {}
                score=0;reason='';action=''
                if callback:score=100;reason='The client requested a callback.';action='Return requested call'
                elif pending or submitted:score=95;reason='Client information is waiting for review.';action='Review client details'
                elif task and task['due_date'] and task['due_date']<today:score=90;reason='Follow-up overdue since '+task['due_date']+'.';action=task['title']
                elif q and q['status']=='Accepted':score=85;reason='Estimate accepted; check the deposit and availability.';action='Check booking confirmation'
                elif delivery.get('opens',0) and q and q['status']=='Approved':score=75;reason='Estimate opened '+str(delivery['opens'])+' time(s); check prior contact before following up.';action='Review quote follow-up'
                elif task and task['due_date'] and task['due_date']<=today:score=70;reason='Follow-up due today.';action=task['title']
                elif delivery.get('state')=='Ready':score=60;reason='Estimate is ready; client email delivery has not been confirmed.';action='Review and send estimate'
                elif q and q['status']=='Draft':score=55;reason='The latest estimate is still a draft. Check the scope, rates and missing details.';action='Review draft estimate'
                elif not q:score=50;reason='No estimate yet. Check missing information first.';action='Prepare next sales step'
                if score:suggestions.append({'lead_id':l['id'],'client_name':l['client_name'],'action':action,'reason':reason,'owner':assignments[l['id']],'score':score,'task_id':task['id'] if task else None})
            suggestions.sort(key=lambda x:(-x['score'],x['client_name'].lower()))
            next_actions={lid:{'id':r['id'],'title':r['title'],'due_date':r['due_date'],'owner':users.get(r['owner'],r['owner']) or 'Unassigned'} for lid,r in tasks.items()}
        return {'date':today,'quote_delivery':summary,'owners':assignments,'next_actions':next_actions,'suggestions':suggestions[:12],'suggestion_count':len(suggestions),'method':'Rules based on callbacks, submitted details, due tasks, acceptance and visible estimate opens. Nothing is sent automatically.'}
