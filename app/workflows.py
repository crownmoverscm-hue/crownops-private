"""End-to-end workflow glue: intake, reviewed imports, invoices, collection, outbox."""
from contextlib import closing
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
from html import escape
import hashlib,hmac,json,os,re,secrets,time
from fastapi import Depends,HTTPException,Request
from fastapi.responses import FileResponse,HTMLResponse
from .store import connect,transaction,rows,one,insert,audit,uid,now,dumps
from .platform_common import *
from .pricing import number,cents,calculate_with_overrides


def normalize_phone(x):
    n=re.sub(r'\D','',str(x));return n[-10:] if len(n)==11 and n.startswith('1') else n


def ingest_event(db,data,actor,new_lead):
    eid=text(data.get('event_id',''),'Event ID',180)
    if not eid:raise HTTPException(422,'A stable source event ID is required.')
    digest=hashlib.sha256(dumps(data).encode()).hexdigest()
    prior=one(db,'SELECT * FROM bridge_events WHERE event_id=?',(eid,))
    if prior:
        if prior['body_hash']!=digest:raise HTTPException(409,'Event ID already used for different data.')
        return {**json.loads(prior['result_json']),'duplicate':True}
    kind=data.get('kind')
    if kind not in ('form','call','email','gps','employment','spam'):raise HTTPException(422,'Unsupported source event kind.')
    result={'duplicate':False,'lead_id':None,'messages_sent':0}
    if kind=='gps':
        vid=text(data.get('vehicle_id',''),'Vehicle ID',100)
        if not one(db,'SELECT id FROM vehicles WHERE id=?',(vid,)):raise HTTPException(422,'GPS vehicle ID is not mapped to the fleet.')
        try:
            lat=float(number(data.get('latitude'),'Latitude',-90,90));lon=float(number(data.get('longitude'),'Longitude',-180,180));observed=datetime.fromisoformat(data.get('observed_at',''))
            if observed.tzinfo is None or observed>datetime.now(timezone.utc)+timedelta(minutes=5):raise ValueError('Invalid GPS time')
        except (TypeError,ValueError):raise HTTPException(422,'Use a valid position and offset-aware observation time.')
        old=one(db,'SELECT * FROM positions WHERE vehicle_id=?',(vid,))
        if not old or observed>datetime.fromisoformat(old['observed_at']):
            db.execute('INSERT INTO positions(vehicle_id,latitude,longitude,observed_at,source,received_at) VALUES(?,?,?,?,?,?) ON CONFLICT(vehicle_id) DO UPDATE SET latitude=excluded.latitude,longitude=excluded.longitude,observed_at=excluded.observed_at,source=excluded.source,received_at=excluded.received_at',(vid,lat,lon,observed.astimezone(timezone.utc).isoformat(),text(data.get('source','Telematics'),'Source',100),now()))
        result['position_recorded']=True
    elif kind not in ('employment','spam'):
        lead_data=data.get('lead',{})
        if not isinstance(lead_data,dict):raise HTTPException(422,'Lead must be an object.')
        email=str(lead_data.get('email','')).strip().lower();phone=normalize_phone(lead_data.get('phone',''))
        target=None
        supplied=data.get('lead_id')
        if supplied:
            target=one(db,'SELECT * FROM leads WHERE id=?',(supplied,))
            if not target:raise HTTPException(422,'Explicit project ID not found.')
            if email and email!=target['email'].lower():raise HTTPException(422,'Sender does not match project email.')
        elif kind in ('call','email'):
            candidates=rows(db,'SELECT * FROM leads WHERE email=? COLLATE NOCASE',(email,)) if email else []
            if not candidates and phone:
                candidates=[r for r in rows(db,"SELECT * FROM leads WHERE phone!=''") if normalize_phone(r['phone'])==phone]
            if len(candidates)==1:target=candidates[0]
            if len(candidates)>1:result['matching_requires_review']=True
        # A form represents a new project unless an explicit trusted project ID is supplied.
        if not target:
            fields={k:v for k,v in lead_data.items() if k in {'client_name','email','phone','move_date','pickup','dropoff','home_size','boxes','pickup_access','dropoff_access','pickup_walk','dropoff_walk','inventory','heavy_items','language','photo_link'}}
            fields.setdefault('client_name','Unknown caller' if kind=='call' else 'New inquiry')
            call_disposition=str(data.get('disposition') or '').strip().lower()
            intake_type={'form':'Form','call':('Voicemail' if 'voice' in call_disposition else 'Missed Call'),'email':'Email'}[kind]
            fields.update(source=text(data.get('source','Connected intake'),'Source',100),lead_type=intake_type,stage='New Lead')
            target=new_lead(db,fields,actor,external_id='bridge:'+eid,suppressed=0 if data.get('permission_to_contact') is True else 1)
        lid=target['id'];result['lead_id']=lid
        body=text(data.get('body',''),'Message',20000)
        if kind=='call':
            call_data={'source':data.get('source'),'phone':lead_data.get('phone'),'disposition':data.get('disposition','Missed'),'duration_seconds':data.get('duration_seconds'), 'note':body,'event_id':eid,'occurred_at':data.get('occurred_at',now())}
            insert(db,'calls',dict(id=uid('CALL'),legacy_id=eid,lead_id=lid,payload_json=dumps(call_data),created_at=now()))
            add_task(db,lid,'Return / review voicemail' if 'voice' in str(call_data['disposition']).lower() else 'Return / review client call',key='call:'+eid)
        if body:
            insert(db,'messages',dict(id=uid('MSG'),lead_id=lid,kind='Incoming '+kind,subject=text(data.get('subject',''),'Subject',300),body=body,language=target.get('language','English'),author=actor,created_at=now()))
        if kind in ('call','email'):
            db.execute("UPDATE outbound_messages SET status='Cancelled',error='Client replied; review before following up' WHERE lead_id=? AND category='Follow-up' AND status IN ('Draft','Approved')",(lid,));add_task(db,lid,'Read new client message / call',key='reply:'+eid)
    insert(db,'bridge_events',dict(event_id=eid,body_hash=digest,kind=kind,result_json=dumps(result),created_at=now()))
    audit(db,actor,'Intake event captured','bridge_events',eid,{'kind':kind,'lead_id':result.get('lead_id')})
    return result


def register_workflows(app,path,auth,new_lead,check_job_resources,public_url):
    @app.get('/request')
    def request_page():return FileResponse(Path(__file__).parent/'static'/'request.html')

    @app.post('/api/request')
    def public_request(data:dict,request:Request):
        if data.get('website'):return {'ok':True} # honeypot; do not store bot content
        if data.get('permission_to_contact') is not True:raise HTTPException(422,'Confirm permission for Crown to contact you about this request.')
        l=data.get('lead',{})
        if not isinstance(l,dict) or not l.get('client_name') or not l.get('email') or not l.get('phone'):raise HTTPException(422,'Name, email and phone are required.')
        rid=text(data.get('request_id',''),'Request ID',120)
        if not re.fullmatch(r'[A-Za-z0-9_-]{12,120}',rid):raise HTTPException(422,'Reload the form to create a valid submission ID.')
        ip=stamp_hash(request.client.host if request.client else 'local');since=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(timespec='seconds')
        with transaction(path) as db:
            if db.execute("SELECT count(*) FROM audit WHERE actor=? AND action='Intake event captured' AND created_at>?",('public:'+ip,since)).fetchone()[0]>=8:raise HTTPException(429,'Too many submissions. Please contact Crown directly.')
            r=ingest_event(db,{'event_id':'webform:'+rid,'kind':'form','lead':l,'source':'CrownOps EN/FR form','permission_to_contact':True,'body':text(data.get('body',''))},'public:'+ip,new_lead)
        return {'ok':True,'reference':r['lead_id'],'message':'Request received. Your move is not yet booked.'}

    @app.post('/api/bridge/events')
    async def bridge(request:Request):
        secret=os.getenv('CROWNOPS_WEBHOOK_SECRET','')
        if len(secret)<32:raise HTTPException(503,'Signed integration bridge is not configured.')
        body=await request.body()
        if len(body)>150000:raise HTTPException(413,'Event too large.')
        ts=request.headers.get('x-crown-timestamp','')
        try:
            if abs(time.time()-int(ts))>300:raise ValueError()
        except ValueError:raise HTTPException(401,'Expired event timestamp.')
        signature=hmac.new(secret.encode(),ts.encode()+b'.'+body,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,request.headers.get('x-crown-signature','')):raise HTTPException(401,'Invalid signature.')
        try:data=json.loads(body)
        except Exception:raise HTTPException(422,'Invalid event JSON.')
        if not isinstance(data,dict):raise HTTPException(422,'Event must be an object.')
        with transaction(path) as db:return ingest_event(db,data,'signed-bridge',new_lead)

    @app.get('/api/calls')
    def calls(u=Depends(auth),limit:int=100):
        require_role(u,SALES)
        with closing(connect(path)) as db:
            r=rows(db,'SELECT c.*,l.client_name,l.phone FROM calls c LEFT JOIN leads l ON l.id=c.lead_id ORDER BY c.created_at DESC LIMIT ?',(min(300,max(1,limit)),))
        for x in r:x['details']=json.loads(x.pop('payload_json'))
        return r

    @app.post('/api/leads/{lid}/communications')
    def enable_messages(lid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES)
        note=text(data.get('evidence',data.get('note','')),'Contact permission evidence',1500)
        allowed=data.get('allowed',data.get('confirmed'))
        if not isinstance(allowed,bool) or not note:raise HTTPException(422,'Record the contact permission or withdrawal and evidence.')
        with transaction(path) as db:
            staff_read_project(db,lid,u);db.execute('UPDATE leads SET notifications_suppressed=? WHERE id=?',(int(not allowed),lid))
            if not allowed:db.execute("UPDATE outbound_messages SET status='Cancelled',error='Operational contact disabled' WHERE lead_id=? AND status IN ('Draft','Approved')",(lid,))
            audit(db,u['id'],'Operational communication permission updated','leads',lid,{'allowed':allowed,'evidence':note})
        return {'ok':True,'messages_sent':0}

    @app.get('/api/outbound')
    def outbox(u=Depends(auth)):
        require_role(u,SALES)
        with closing(connect(path)) as db:return rows(db,'SELECT o.*,l.client_name FROM outbound_messages o JOIN leads l ON l.id=o.lead_id ORDER BY created_at DESC LIMIT 200')

    @app.post('/api/outbound')
    def draft(data:dict,u=Depends(auth)):
        require_role(u,SALES)
        subject=text(data.get('subject',''),'Subject',250);body=text(data.get('body',''),'Message',20000)
        if not subject or not body:raise HTTPException(422,'Subject and message are required.')
        if '\r' in subject or '\n' in subject:raise HTTPException(422,'Subject must be one line.')
        category=data.get('category','Reply')
        if category not in ('Reply','Follow-up','Preparation','Invoice','Collection','Review request'):raise HTTPException(422,'Choose an operational message category.')
        with transaction(path) as db:
            l=staff_read_project(db,data.get('lead_id'),u);r=queue_message(db,l['id'],subject,body,category,u['id'],'manual:'+uid('MESSAGE'))
            audit(db,u['id'],'Client email draft created; not sent','outbound_messages',r['id'])
        return r

    @app.post('/api/outbound/{mid}/approve')
    def approve_mail(mid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES)
        if data.get('reviewed') is not True:raise HTTPException(422,'Review recipient, dates, prices and attachments before approving.')
        with transaction(path) as db:
            r=one(db,'SELECT * FROM outbound_messages WHERE id=?',(mid,))
            if not r or r['status']!='Draft':raise HTTPException(409,'Only a current draft can be approved.')
            db.execute("UPDATE outbound_messages SET status='Approved',approved_by=? WHERE id=?",(u['id'],mid));audit(db,u['id'],'Email approved; not sent','outbound_messages',mid)
        return {'ok':True,'sent':False}

    @app.post('/api/outbound/{mid}/cancel')
    def cancel_mail(mid:str,u=Depends(auth)):
        require_role(u,SALES)
        with transaction(path) as db:
            r=one(db,'SELECT * FROM outbound_messages WHERE id=?',(mid,))
            if not r or r['status'] not in ('Draft','Approved'):raise HTTPException(409,'Only an unsent message can be cancelled.')
            db.execute("UPDATE outbound_messages SET status='Cancelled' WHERE id=?",(mid,))
        return {'ok':True}

    @app.post('/api/jobs/{jid}/invoice-from-quote')
    def prepare_invoice(jid:str,u=Depends(auth)):
        from .invoices import validate_terms
        with transaction(path) as db:
            j=assigned(db,jid,u);existing=one(db,'SELECT id FROM job_invoices WHERE job_id=?',(jid,))
            if existing:return {'id':existing['id'],'already_prepared':True}
            if j['status'] not in ('Tentative','Confirmed','In progress','Completed'):raise HTTPException(409,'This job is not available for invoice preparation.')
            q=one(db,"SELECT * FROM quotes WHERE id=? AND lead_id=? AND status='Accepted'",(j['quote_id'],j['lead_id']))
            cfg=settings(db,'billing_policy',{})
            if not q or not cfg.get('approved'):raise HTTPException(409,'An accepted quote and manager-approved billing policy are required. Management can set up reviewed manual terms otherwise.')
            l=one(db,'SELECT * FROM leads WHERE id=?',(j['lead_id'],))
            if l['version']!=q['lead_version']:raise HTTPException(409,'Client scope changed. Sales must review the accepted terms first.')
            if one(db,"SELECT id FROM client_changes WHERE lead_id=? AND status='Pending'",(j['lead_id'],)):raise HTTPException(409,'Client changes need review before billing.')
            r=json.loads(q['result_json']);base=cfg['terms'];data={**base,'customer':{'name':l['client_name'],'address':l['dropoff'],'email':l['email']},'hourly_rate':r['hourly_rate_cents']/100,'minimum_hours':r['minimum_hours'],'transport_amount':r['transport_cents']/100,'fuel_amount':r['fuel_cents']/100,'discount_amount':r['discount_cents']/100,'other_amount':r['other_cents']/100,'approval_reference':'Accepted quote '+q['id']+' revision '+str(q['revision'])+'; '+base.get('approval_reference','')}
            try:terms=validate_terms(data)
            except (TypeError,KeyError,ValueError) as ex:raise HTTPException(422,str(ex))
            iid=uid('INV');rec=dict(id=iid,job_id=jid,number=iid,status='Draft',terms_json=dumps(terms),actual_json='{}',terms_approved_by=q['approved_by'],terms_approved_at=q['approved_at'],created_by=u['id'],created_at=now(),updated_at=now());insert(db,'job_invoices',rec)
            audit(db,u['id'],'Invoice prepared from accepted quote','job_invoices',iid,{'quote_id':q['id'],'rates_changed':False})
        return {'id':iid,'ready':True}

    @app.get('/api/invoices/{iid}/collections')
    def collections(iid:str,u=Depends(auth)):
        with closing(connect(path)) as db:
            inv,calc=invoice_current(db,iid);assigned(db,inv['job_id'],u)
            recs=rows(db,'SELECT c.*,p.amount_cents,p.method,p.reference,p.verified FROM field_collections c JOIN payments p ON p.id=c.payment_id WHERE c.invoice_id=? ORDER BY p.created_at DESC',(iid,))
            links=rows(db,'SELECT id,url,status,amount_cents,revision FROM payment_links WHERE invoice_id=? ORDER BY created_at DESC',(iid,))
            return {'collections':recs,'links':links,'balance_cents':calc['balance_cents'],'pending_received_cents':pending_received(db,iid)}

    @app.post('/api/invoices/{iid}/collect')
    def collect(iid:str,data:dict,u=Depends(auth)):
        try:amount=cents(number(data.get('amount'),'Amount received',.01,1000000))
        except ValueError as ex:raise HTTPException(422,str(ex))
        method=data.get('method');reference=text(data.get('reference',''),'Receipt / reference',300);rid=text(data.get('request_id',''),'Request ID',120)
        if method not in ('Cash','Cheque','Interac','Credit card','Other') or not reference or not re.fullmatch(r'[A-Za-z0-9_-]{12,120}',rid):raise HTTPException(422,'Choose a method and enter the receipt/reference and unique request ID.')
        with transaction(path) as db:
            inv,calc=invoice_current(db,iid);j=assigned(db,inv['job_id'],u)
            old=one(db,'SELECT c.*,p.amount_cents,p.method,p.reference FROM field_collections c JOIN payments p ON p.id=c.payment_id WHERE request_id=?',(rid,))
            if old:
                if old['invoice_id']!=iid or old['amount_cents']!=amount or old['method']!=method or old['reference']!=reference:raise HTTPException(409,'Request ID already used for a different payment.')
                return {'id':old['id'],'already_recorded':True,'verified':False}
            if inv['status']!='Issued':raise HTTPException(409,'Finalize the invoice before collecting.')
            if one(db,"SELECT id FROM payment_links WHERE invoice_id=? AND status IN ('Creating','Active','Unknown','Cancelling')",(iid,)):raise HTTPException(409,'A checkout link is open. Reconcile/cancel it before recording another payment.')
            if amount>calc['balance_cents']-pending_received(db,iid):raise HTTPException(409,'Amount exceeds the balance after recorded pending payments. Check whether a teammate already collected it.')
            if one(db,'SELECT id FROM payments WHERE lead_id=? AND reference=?',(j['lead_id'],reference)):raise HTTPException(409,'Payment reference already recorded for this project.')
            pid=uid('PAY');insert(db,'payments',dict(id=pid,lead_id=j['lead_id'],amount_cents=amount,method=method,purpose='Final payment',reference=reference,verified=0,recorded_by=u['id'],created_at=now()))
            rec=dict(id=uid('RECEIPT'),invoice_id=iid,payment_id=pid,request_id=rid,received_by=u['id'],note=text(data.get('note',''),'Payment note',1500));insert(db,'field_collections',rec)
            audit(db,u['id'],'Payment received by staff; awaiting reconciliation','field_collections',rec['id'],{'amount_cents':amount,'method':method})
        return {**rec,'verified':False,'amount_cents':amount}

    @app.post('/api/collections/{cid}/verify')
    def verify_collection(cid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','accounting'})
        note=text(data.get('evidence',''),'Reconciliation evidence',2000)
        if not note:raise HTTPException(422,'Record bank/terminal/cash-handover confirmation.')
        with transaction(path) as db:
            c=one(db,'SELECT * FROM field_collections WHERE id=?',(cid,))
            if not c:raise HTTPException(404,'Collection not found.')
            pay=one(db,'SELECT * FROM payments WHERE id=?',(c['payment_id'],));inv,calc=invoice_current(db,c['invoice_id'])
            if c['verified_at']:return {'ok':True,'already_verified':True}
            if one(db,'SELECT id FROM payments WHERE reverses_id=?',(pay['id'],)):raise HTTPException(409,'Payment was reversed.')
            used=db.execute('SELECT coalesce(sum(amount_cents),0) FROM invoice_payment_allocations WHERE payment_id=?',(pay['id'],)).fetchone()[0]
            if used:raise HTTPException(409,'This payment has already been allocated. Reconcile existing allocations.')
            if pay['amount_cents']>calc['balance_cents']:raise HTTPException(409,'Invoice was reduced; review the unapplied credit before allocating.')
            db.execute('UPDATE payments SET verified=1 WHERE id=?',(pay['id'],));insert(db,'invoice_payment_allocations',dict(id=uid('ALLOC'),invoice_id=inv['id'],payment_id=pay['id'],amount_cents=pay['amount_cents'],allocated_by=u['id'],created_at=now()))
            db.execute("UPDATE field_collections SET handover_status='Reconciled',verified_by=?,verified_at=?,note=? WHERE id=?",(u['id'],now(),note,cid));db.execute('UPDATE job_invoices SET version=version+1,updated_at=? WHERE id=?',(now(),inv['id']))
            db.execute("UPDATE outbound_messages SET status='Cancelled',error='Payment reconciled; refresh balance' WHERE lead_id=? AND category='Collection' AND status IN ('Draft','Approved')",(pay['lead_id'],))
            audit(db,u['id'],'Staff collection verified and allocated','field_collections',cid,{'evidence':note})
        return {'ok':True}

    @app.get('/api/collections/{cid}/receipt')
    def receipt(cid:str,u=Depends(auth)):
        with closing(connect(path)) as db:
            r=one(db,'SELECT c.*,p.amount_cents,p.method,p.reference,p.created_at FROM field_collections c JOIN payments p ON p.id=c.payment_id WHERE c.id=?',(cid,))
            if not r:raise HTTPException(404,'Receipt not found.')
            inv,calc=invoice_current(db,r['invoice_id']);assigned(db,inv['job_id'],u)
        e=escape
        label='Reconciled receipt' if r['verified_at'] else 'Staff collection acknowledgment - awaiting reconciliation'
        html='<html><head><meta charset="utf-8"><title>Payment acknowledgment</title></head><body style="font:16px Arial;max-width:700px;margin:40px auto"><h1>Crown Movers</h1><h2>'+label+'</h2><p>Invoice '+e(inv['number'])+'</p><p>Amount received: CA$'+f'{r["amount_cents"]/100:,.2f}'+'</p><p>Method: '+e(r['method'])+'</p><p>Reference: '+e(r['reference'])+'</p><p>'+e(r['created_at'])+'</p><p>This acknowledgment records the collection. It is not proof of bank settlement while marked awaiting reconciliation.</p></body></html>'
        return HTMLResponse(html,headers={'Content-Disposition':f'attachment; filename="{cid}.html"'})

    @app.post('/api/invoices/{iid}/prepare-email')
    def invoice_email(iid:str,u=Depends(auth)):
        with transaction(path) as db:
            inv,calc=invoice_current(db,iid);j=assigned(db,inv['job_id'],u);l=one(db,'SELECT * FROM leads WHERE id=?',(j['lead_id'],))
            if inv['status']!='Issued':raise HTTPException(409,'Finalize the invoice before preparing delivery.')
            is_fr=l['language'].lower().startswith('fr')
            body=(f"Bonjour {l['client_name']},\n\nVotre facture {inv['number']} est disponible dans votre portail client.\nMontant total : {calc['total_cents']/100:.2f} $ CA.\nSolde confirme : {calc['balance_cents']/100:.2f} $ CA.\nLes paiements recemment recueillis peuvent etre en cours de verification.\n\nMerci,\nJonathan\nCrown Movers" if is_fr else f"Hi {l['client_name']},\n\nYour invoice {inv['number']} is available in your private client portal.\nInvoice total: CA${calc['total_cents']/100:.2f}.\nConfirmed balance: CA${calc['balance_cents']/100:.2f}.\nRecently collected payments may still be awaiting verification.\n\nThank you,\nJonathan\nCrown Movers")
            m=queue_message(db,l['id'],'Votre facture Crown Movers' if is_fr else 'Your Crown Movers invoice',body,'Invoice',u['id'],'invoice-mail:'+iid+':'+str(inv['revision']),jid=j['id'])
        return {'id':m['id'],'sent':False,'message':'Invoice email draft is ready for sales/manager review.'}

    @app.post('/api/employees')
    def add_employee(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        name=text(data.get('name',''),'Name',200)
        if not name:raise HTTPException(422,'Enter employee name.')
        try:rate=cents(number(data.get('hourly_rate',0),'Employee hourly rate',0,500))
        except ValueError as ex:raise HTTPException(422,str(ex))
        rec=dict(id=uid('EMP'),name=name,role=text(data.get('role','Mover'),'Work role',150),hourly_cents=rate,driver_verified=0)
        with transaction(path) as db:insert(db,'employees',rec);audit(db,u['id'],'Employee directory record created; no account yet','employees',rec['id'])
        return rec

    @app.get('/api/reports')
    def reports(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            stages=rows(db,'SELECT stage,count(*) AS count FROM leads GROUP BY stage')
            invs=rows(db,"SELECT * FROM job_invoices WHERE status='Issued'");tot=paid=0
            for inv in invs:
                _,c=invoice_current(db,inv['id']);tot+=c['total_cents'];paid+=c['deposit_cents']
            return {'pipeline':stages,'issued_invoice_total_cents':tot,'verified_allocated_payments_cents':paid,'balance_cents':tot-paid,
                'unverified_payments':db.execute('SELECT count(*) FROM payments WHERE verified=0').fetchone()[0],
                'open_cases':db.execute("SELECT count(*) FROM cases WHERE status!='Resolved'").fetchone()[0],
                'pending_portal_changes':db.execute("SELECT count(*) FROM client_changes WHERE status='Pending'").fetchone()[0],
                'quarantined_files':db.execute("SELECT count(*) FROM attachments WHERE scan_status='Quarantined' AND deleted_at IS NULL").fetchone()[0],
                'note':'Operational invoice totals, not profit, bank cash or accounting-certified revenue. Imported source bookings are excluded.'}

    @app.post('/api/schedule-imports/promote')
    def promote(data:dict,u=Depends(auth)):
        require_role(u,SALES)
        if data.get('reviewed') is not True or not text(data.get('note',''),'Source review note',3000):raise HTTPException(422,'Review source fields and explain resolved ambiguities first.')
        key=text(data.get('source_key',''),'Source key',300)
        with transaction(path) as db:
            old=one(db,'SELECT * FROM source_promotions WHERE source_key=?',(key,))
            if old:return {'job_id':old['job_id'],'lead_id':old['lead_id'],'already_promoted':True}
            source=one(db,'SELECT * FROM imported_schedule_events WHERE source_key=?',(key,))
            if not source:raise HTTPException(404,'Source event not found.')
            lid=data.get('lead_id')
            if lid:l=staff_read_project(db,lid,u)
            else:l=new_lead(db,data.get('client',{}),u['id'],external_id='schedule-review:'+key,suppressed=1)
            start,end,crew,trucks=check_job_resources(db,data)
            rec=dict(id=uid('JOB'),lead_id=l['id'],title=l['client_name'],phase=text(data.get('phase','Moving'),'Phase',80),start_at=start,end_at=end,status='Tentative',notes='Reviewed source '+key+'; '+data['note'],created_by=u['id'],created_at=now())
            insert(db,'jobs',rec)
            for typ,ids in [('employee',crew),('vehicle',trucks)]:
                for rid in ids:insert(db,'assignments',dict(job_id=rec['id'],resource_type=typ,resource_id=rid))
            insert(db,'source_promotions',dict(source_key=key,job_id=rec['id'],lead_id=l['id'],approved_by=u['id'],approved_at=now(),note=data['note']))
            audit(db,u['id'],'Source record staged as working tentative job','jobs',rec['id'],{'source_key':key,'deposits_created':0})
        return {'job_id':rec['id'],'lead_id':l['id'],'confirmed':False,'payments_created':0}
