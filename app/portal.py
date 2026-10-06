"""Private client portal: one-use invites, scoped sessions, change requests and uploads."""
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib, hmac, json, os, secrets, subprocess, shlex
from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from .store import connect,transaction,rows,one,insert,audit,uid,now,dumps
from .platform_common import *

PORTAL_COOKIE='crownops_client'
CLIENT_FIELDS={'client_name','phone','pickup','dropoff','boxes','inventory','pickup_access','dropoff_access','pickup_walk','dropoff_walk','heavy_items','move_date','home_size'}

def public_client(l):return {k:l[k] for k in ('id','client_name','email','phone','pickup','dropoff','move_date','home_size','boxes','inventory','pickup_access','dropoff_access','pickup_walk','dropoff_walk','heavy_items','language','version')}

def detect(content,name):
    ext=Path(name).suffix.lower()
    if content.startswith(b'\xff\xd8\xff') and ext in ('.jpg','.jpeg'):return 'image/jpeg'
    if content.startswith(b'\x89PNG\r\n\x1a\n') and ext=='.png':return 'image/png'
    if content[:4]==b'RIFF' and content[8:12]==b'WEBP' and ext=='.webp':return 'image/webp'
    if content.startswith(b'%PDF-') and ext=='.pdf':return 'application/pdf'
    if len(content)>12 and content[4:8]==b'ftyp' and ext in ('.mp4','.mov','.m4v'):return 'video/quicktime' if ext=='.mov' else 'video/mp4'
    if content.startswith(b'\x1aE\xdf\xa3') and ext=='.webm':return 'video/webm'
    raise HTTPException(422,'Use a JPEG, PNG, WebP, PDF, MP4, MOV or WebM with matching file content.')

def safe_attachment(r):return {k:r[k] for k in ('id','lead_id','job_id','category','original_name','mime','size','scan_status','created_at','deleted_at')}

def register_portal(app,path,auth,public_url,secure):
    storage=Path(os.getenv('CROWNOPS_UPLOADS',os.getenv('CROWNOPS_UPLOAD_DIR',str(Path(path).parent/'uploads')))).resolve()
    storage.mkdir(parents=True,exist_ok=True,mode=0o700)
    def client_auth(request:Request):
        raw=request.cookies.get(PORTAL_COOKIE,'')
        with closing(connect(path)) as db:
            s=one(db,'SELECT * FROM portal_sessions WHERE token_hash=? AND expires_at>?',(stamp_hash(raw),now()))
        if not raw or not s:raise HTTPException(401,'Your private link has expired. Please ask Crown for a new link.')
        if request.method not in ('GET','HEAD','OPTIONS') and not hmac.compare_digest(request.headers.get('x-csrf-token',''),s['csrf']):
            raise HTTPException(403,'Refresh your portal session before saving.')
        return s
    app.state.client_auth=client_auth

    @app.get('/portal')
    def client_page():return FileResponse(Path(__file__).parent/'static'/'portal.html')

    @app.post('/api/leads/{lid}/portal-invite')
    def invite(lid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES)
        with transaction(path) as db:
            l=staff_read_project(db,lid,u)
            if '@' not in l['email']:raise HTTPException(422,'Confirm the client email before creating portal access.')
            raw=secrets.token_urlsafe(40)
            insert(db,'portal_invites',dict(token_hash=stamp_hash(raw),lead_id=lid,email=l['email'],expires_at=(datetime.now(timezone.utc)+timedelta(hours=48)).isoformat(timespec='seconds'),created_by=u['id'],created_at=now()))
            audit(db,u['id'],'Private portal invite created','leads',lid)
        return {'url':public_url+'/portal#access='+raw,'expires_in':'48 hours; one use','sent':False}

    @app.post('/api/leads/{lid}/portal-revoke')
    def revoke(lid:str,u=Depends(auth)):
        require_role(u,SALES)
        with transaction(path) as db:
            db.execute('UPDATE portal_invites SET revoked=1 WHERE lead_id=?',(lid,));db.execute('DELETE FROM portal_sessions WHERE lead_id=?',(lid,))
            audit(db,u['id'],'Portal access revoked','leads',lid)
        return {'ok':True}

    @app.post('/api/portal/exchange')
    def exchange(data:dict):
        token=text(data.get('token',''),'Access token',180)
        with transaction(path) as db:
            v=one(db,'SELECT * FROM portal_invites WHERE token_hash=? AND used_at IS NULL AND revoked=0 AND expires_at>?',(stamp_hash(token),now()))
            if not v:raise HTTPException(401,'Private link is invalid, already used or expired.')
            raw=secrets.token_urlsafe(40);csrf=secrets.token_urlsafe(32)
            insert(db,'portal_sessions',dict(token_hash=stamp_hash(raw),lead_id=v['lead_id'],email=v['email'],csrf=csrf,expires_at=(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat(timespec='seconds'),created_at=now()))
            db.execute('UPDATE portal_invites SET used_at=? WHERE token_hash=?',(now(),stamp_hash(token)))
            audit(db,'client:'+v['email'],'Portal accessed','leads',v['lead_id'])
        response=JSONResponse({'ok':True,'csrf':csrf});response.set_cookie(PORTAL_COOKIE,raw,httponly=True,secure=secure,samesite='lax',max_age=43200,path='/');return response

    @app.post('/api/portal/logout')
    def logout(request:Request,c=Depends(client_auth)):
        with transaction(path) as db:db.execute('DELETE FROM portal_sessions WHERE token_hash=?',(c['token_hash'],))
        r=JSONResponse({'ok':True});r.delete_cookie(PORTAL_COOKIE);return r

    @app.get('/api/portal/project')
    def project(c=Depends(client_auth)):
        with closing(connect(path)) as db:
            l=one(db,'SELECT * FROM leads WHERE id=?',(c['lead_id'],))
            quotes=[]
            for q in rows(db,"SELECT * FROM quotes WHERE lead_id=? AND status IN ('Approved','Accepted') ORDER BY revision DESC",(c['lead_id'],)):
                r=json.loads(q['result_json']);snap=json.loads(q['lead_snapshot_json'])
                quotes.append({'id':q['id'],'revision':q['revision'],'status':q['status'],'stale':q['lead_version']!=l['version'],
                    'created_at':q['created_at'],'move_date':snap.get('move_date'),'pickup':snap.get('pickup'),'dropoff':snap.get('dropoff'),
                    'result':{k:r.get(k) for k in ('hourly_rate_cents','service_low','service_high','minimum_hours','transport_hours','transport_cents','fuel_cents','materials_cents','other_cents','discount_cents','low_cents','high_cents','movers','trucks','tax_note','service_type','language','line_items','distance_km','distance_rate_cents')},'inputs':{k:json.loads(q['inputs_json']).get(k) for k in ('service_type','language','template_key','template_snapshot','limited_liability','start_window','start_time','start_window_label','move_date')},'deposit_required':json.loads(q['inputs_json']).get('deposit_required',300)})
            jobs=rows(db,"SELECT id,title,phase,start_at,end_at,status FROM jobs WHERE lead_id=? AND status!='Tentative' ORDER BY start_at",(c['lead_id'],))
            invs=[]
            for inv in rows(db,"SELECT i.* FROM job_invoices i JOIN jobs j ON j.id=i.job_id WHERE j.lead_id=? AND i.status='Issued'",(c['lead_id'],)):
                _,calc=invoice_current(db,inv['id']);invs.append({'id':inv['id'],'number':inv['number'],'revision':inv['revision'],'status':inv['status'],'calculation':calc,'received_pending_verification_cents':pending_received(db,inv['id'])})
            from .deposits import deposit_state
            for qr in quotes:
                source_q=one(db,'SELECT * FROM quotes WHERE id=?',(qr['id'],))
                qr['deposit']=deposit_state(db,c['lead_id'],source_q)
            cfg=settings(db,'media_program',{})
            terms=settings(db,'client_terms',{})
            return {'csrf':c['csrf'],'client':public_client(l),'quotes':quotes,'jobs':jobs,'invoices':invs,
                'changes':rows(db,'SELECT id,payload_json,status,created_at,review_note FROM client_changes WHERE lead_id=? ORDER BY created_at DESC',(c['lead_id'],)),
                'attachments':[safe_attachment(r) for r in rows(db,'SELECT * FROM attachments WHERE lead_id=? AND deleted_at IS NULL ORDER BY created_at DESC',(c['lead_id'],))],
                'messages':rows(db,"SELECT id,subject,body,created_at FROM outbound_messages WHERE lead_id=? AND status='Sent' ORDER BY created_at DESC LIMIT 30",(c['lead_id'],)),
                'payment_links':rows(db,"SELECT p.invoice_id,p.url,p.amount_cents FROM payment_links p JOIN job_invoices i ON i.id=p.invoice_id JOIN jobs j ON j.id=i.job_id WHERE j.lead_id=? AND p.status='Active' AND p.revision=i.revision AND i.status='Issued'",(c['lead_id'],)),
                'media_program':{'approved':cfg.get('approved',False),'release_version':cfg.get('version',''),'release_text':cfg.get('release_text',''),'channels':cfg.get('channels',[]),'amount_cents':5000},
                'terms':{k:terms.get(k) for k in ('approved','version','text')} if terms.get('approved') else {'approved':False},
                'releases':rows(db,'SELECT id,status,signed_at,withdrawn_at FROM media_releases WHERE lead_id=?',(c['lead_id'],))}

    @app.post('/api/portal/quotes/{qid}/deposit-checkout')
    def deposit_checkout(qid:str,data:dict,c=Depends(client_auth)):
        if data.get('confirm') is not True:raise HTTPException(422,'Confirm requesting this deposit checkout.')
        from .deposits import create_deposit_checkout
        return create_deposit_checkout(path,qid,c['lead_id'],'client:'+c['email'])

    @app.post('/api/portal/changes')
    def changes(data:dict,c=Depends(client_auth)):
        payload=data.get('fields',{})
        if not isinstance(payload,dict) or not payload or set(payload)-CLIENT_FIELDS:raise HTTPException(422,'Submit supported move details.')
        payload={k:text(v,k,18000 if k=='inventory' else 3000) for k,v in payload.items()}
        with transaction(path) as db:
            l=one(db,'SELECT * FROM leads WHERE id=?',(c['lead_id'],))
            if data.get('version')!=l['version']:raise HTTPException(409,'The team updated your file. Refresh before submitting.')
            rec=dict(id=uid('CHANGE'),lead_id=l['id'],payload_json=dumps(payload),base_version=l['version'],created_at=now())
            insert(db,'client_changes',rec);add_task(db,l['id'],'Review customer portal changes',l['assigned_to'],rec['id'])
            db.execute("UPDATE outbound_messages SET status='Cancelled',error='Client replied; review changes first' WHERE lead_id=? AND category='Follow-up' AND status IN ('Draft','Approved')",(l['id'],))
            audit(db,'client:'+c['email'],'Move changes submitted for review','client_changes',rec['id'])
        return {'ok':True,'status':'Pending review','message':'Your changes were submitted. Dates and accepted prices do not change until Crown reviews them.'}

    @app.get('/api/client-changes')
    def change_list(u=Depends(auth)):
        require_role(u,SALES)
        with closing(connect(path)) as db:
            return rows(db,'SELECT c.*,l.client_name FROM client_changes c JOIN leads l ON l.id=c.lead_id ORDER BY created_at DESC LIMIT 200')

    @app.post('/api/client-changes/{cid}/review')
    def review(cid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES)
        note=text(data.get('note',''),'Review note',2000)
        if not note or data.get('decision') not in ('Approve','Reject'):raise HTTPException(422,'Choose a decision and enter a review note.')
        with transaction(path) as db:
            cr=one(db,'SELECT * FROM client_changes WHERE id=?',(cid,))
            if not cr or cr['status']!='Pending':raise HTTPException(409,'Request no longer pending.')
            l=one(db,'SELECT * FROM leads WHERE id=?',(cr['lead_id'],))
            if data.get('lead_version')!=l['version']:raise HTTPException(409,'Client changed. Reload and compare before reviewing.')
            if data['decision']=='Approve':
                payload=json.loads(cr['payload_json']);columns=','.join(k+'=?' for k in payload)
                db.execute('UPDATE leads SET '+columns+',version=version+1,updated_at=? WHERE id=?',list(payload.values())+[now(),l['id']])
                add_task(db,l['id'],'Review estimate and booking after client scope change',l['assigned_to'],cid+':scope')
            db.execute('UPDATE client_changes SET status=?,reviewed_by=?,reviewed_at=?,review_note=? WHERE id=?',('Approved' if data['decision']=='Approve' else 'Rejected',u['id'],now(),note,cid))
            audit(db,u['id'],'Portal changes reviewed','client_changes',cid,{'decision':data['decision'],'note':note})
        return {'ok':True,'existing_quotes_unchanged':True}

    @app.post('/api/portal/quotes/{qid}/accept')
    def accept(qid:str,data:dict,c=Depends(client_auth)):
        if data.get('agree') is not True or not text(data.get('signed_name',''),'Name',200):raise HTTPException(422,'Read the terms and confirm your agreement.')
        with transaction(path) as db:
            terms=settings(db,'client_terms',{})
            if not terms.get('approved') or data.get('terms_version')!=terms.get('version'):raise HTTPException(409,'Approved booking terms are not available or changed. Contact Crown.')
            q=one(db,'SELECT * FROM quotes WHERE id=? AND lead_id=?',(qid,c['lead_id']))
            if not q:raise HTTPException(404,'Quote not found.')
            if q['status']=='Accepted':return {'ok':True,'already_accepted':True,'booking_confirmed':False}
            l=one(db,'SELECT * FROM leads WHERE id=?',(c['lead_id'],))
            if q['status']!='Approved' or q['lead_version']!=l['version']:raise HTTPException(409,'This estimate needs review before acceptance.')
            if one(db,"SELECT id FROM client_changes WHERE lead_id=? AND status='Pending'",(c['lead_id'],)):raise HTTPException(409,'Please wait for review of your requested changes before accepting.')
            ref=dumps({'name':data['signed_name'],'portal_email':c['email'],'terms_version':terms['version'],'terms_text':terms['text'],'accepted_at':now()})
            db.execute("UPDATE quotes SET status='Accepted',acceptance_ref=?,accepted_at=? WHERE id=?",(ref,now(),qid))
            db.execute("UPDATE leads SET stage='Accepted' WHERE id=?",(c['lead_id'],))
            # Put the accepted estimate on the working schedule using the sold arrival window.
            # It remains tentative until the deposit or a manager-approved exception confirms the booking.
            from .sales_crm_v6 import ensure_tentative_job
            job=ensure_tentative_job(db,q,l,'client:'+c['email'])
            add_task(db,c['lead_id'],'Verify deposit and assign crew/truck after acceptance',l['assigned_to'],'accepted:'+qid)
            audit(db,'client:'+c['email'],'Quote accepted in portal','quotes',qid,{'terms_version':terms['version'],'tentative_job_id':job['id'] if job else None})
        return {'ok':True,'booking_confirmed':False,'tentative_job_id':job['id'] if job else None,'message':'Estimate accepted. Your requested date is now held as tentative while Crown verifies the deposit and final booking details.'}

    def get_file(db,aid,lid=None,u=None):
        r=one(db,'SELECT * FROM attachments WHERE id=? AND deleted_at IS NULL',(aid,))
        if not r:raise HTTPException(404,'File not found.')
        if lid is not None and r['lead_id']!=lid:raise HTTPException(404,'File not found.')
        if u:staff_read_project(db,r['lead_id'],u)
        return r

    async def upload_one(file,lid,jid,category,actor):
        if not uploads_enabled():raise HTTPException(503,'Private file uploads are disabled until production malware scanning is configured.')
        if category not in ('Move photos','Inventory','Access','Testimonial','Incident'):raise HTTPException(422,'Unsupported file category.')
        name=Path((file.filename or 'upload').replace('\\','/')).name[:180]
        if any(ord(c)<32 for c in name):raise HTTPException(422,'Invalid filename.')
        limit=int(os.getenv('CROWNOPS_MAX_UPLOAD_MB','100'))*1024*1024
        key=uid('FILE');temp=storage/(key+'.upload');n=0;digest=hashlib.sha256();head=b''
        try:
            with open(temp,'xb') as out:
                while chunk:=await file.read(1024*1024):
                    n+=len(chunk)
                    if n>limit:raise HTTPException(413,'File too large. Compress or split the video before uploading.')
                    if not head:head=chunk[:32]
                    digest.update(chunk);out.write(chunk)
            if n==0:raise HTTPException(422,'File is empty.')
            mime=detect(head,name);status='Quarantined'
            if category=='Testimonial' and not mime.startswith('video/'):raise HTTPException(422,'Upload a video for a testimonial.')
            command=os.getenv('CROWNOPS_SCAN_COMMAND','').strip()
            if command:
                try:
                    from starlette.concurrency import run_in_threadpool
                    scan=await run_in_threadpool(subprocess.run,shlex.split(command)+[str(temp)],capture_output=True,timeout=120,check=False)
                    status='Clean' if scan.returncode==0 else 'Rejected'
                except (OSError,subprocess.TimeoutExpired):status='Quarantined'
            elif os.getenv('CROWNOPS_ENV','test')!='production':
                # Shared staging remains quarantined until a configured scanner runs
                # or a manager explicitly reviews the test upload. Local demos can opt
                # into previews by using the review action rather than silently trusting files.
                status='Quarantined'
            dest=storage/(key+Path(name).suffix.lower());temp.rename(dest);dest.chmod(0o600)
            with transaction(path) as db:
                total=db.execute('SELECT coalesce(sum(size),0) FROM attachments WHERE lead_id=? AND deleted_at IS NULL',(lid,)).fetchone()[0]
                if total+n>int(os.getenv('CROWNOPS_PROJECT_UPLOAD_MB','1000'))*1024*1024:
                    dest.unlink(missing_ok=True);raise HTTPException(413,'Project upload limit reached. Contact Crown.')
                r=dict(id=key,lead_id=lid,job_id=jid,category=category,original_name=name,stored_name=dest.name,mime=mime,size=n,sha256=digest.hexdigest(),scan_status=status,author=actor,created_at=now())
                insert(db,'attachments',r);audit(db,actor,'Private file uploaded','attachments',key,{'bytes':n,'scan_status':status})
                add_task(db,lid,'Review uploaded '+category.lower(),key='upload:'+key)
            return safe_attachment({**r,'deleted_at':None})
        finally:temp.unlink(missing_ok=True)

    @app.post('/api/portal/uploads')
    async def client_upload(file:UploadFile=File(...),category:str=Form('Move photos'),c=Depends(client_auth)):
        return await upload_one(file,c['lead_id'],None,category,'client:'+c['email'])

    @app.post('/api/leads/{lid}/uploads')
    async def staff_upload(lid:str,file:UploadFile=File(...),category:str=Form('Move photos'),job_id:str=Form(''),u=Depends(auth)):
        with closing(connect(path)) as db:project_write(db,lid,job_id,u)
        return await upload_one(file,lid,job_id or None,category,u['id'])

    @app.get('/api/leads/{lid}/uploads')
    def uploads(lid:str,u=Depends(auth)):
        with closing(connect(path)) as db:
            staff_read_project(db,lid,u)
            return [safe_attachment(r) for r in rows(db,'SELECT * FROM attachments WHERE lead_id=? AND deleted_at IS NULL',(lid,))]

    def deliver(r):
        if r['scan_status']!='Clean':raise HTTPException(409,'This file is quarantined or rejected. It is not available until safety review.')
        p=(storage/r['stored_name']).resolve()
        if p.parent!=storage or not p.exists():raise HTTPException(404,'Stored file unavailable.')
        return FileResponse(p,media_type=r['mime'],filename=r['original_name'],headers={'Cache-Control':'no-store'})

    @app.get('/api/portal/uploads/{aid}')
    def client_download(aid:str,c=Depends(client_auth)):
        with closing(connect(path)) as db:r=get_file(db,aid,lid=c['lead_id'])
        return deliver(r)

    @app.get('/api/uploads/{aid}')
    def staff_download(aid:str,u=Depends(auth)):
        with closing(connect(path)) as db:r=get_file(db,aid,u=u)
        return deliver(r)

    @app.post('/api/uploads/{aid}/review')
    def scan_review(aid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        if os.getenv('CROWNOPS_ENV','test')=='production' and data.get('decision')=='Clean':
            raise HTTPException(409,'Production file clearance requires the configured scanner. Re-upload after scanning is available; manual clean overrides are test-only.')
        note=text(data.get('note',''),'Review evidence',2000)
        if data.get('decision') not in ('Clean','Rejected') or not note:raise HTTPException(422,'Enter a safety-review decision and evidence.')
        with transaction(path) as db:
            get_file(db,aid,u=u);db.execute('UPDATE attachments SET scan_status=? WHERE id=?',(data['decision'],aid))
            audit(db,u['id'],'File safety status reviewed','attachments',aid,{'decision':data['decision'],'note':note})
        return {'ok':True}

    @app.post('/api/portal/consent')
    def media_consent(data:dict,c=Depends(client_auth)):
        with transaction(path) as db:
            cfg=settings(db,'media_program',{})
            if not cfg.get('approved') or data.get('version')!=cfg.get('version'):raise HTTPException(409,'The optional video program is not activated or its terms changed.')
            if data.get('agree') is not True or data.get('adult_and_authorized') is not True:raise HTTPException(422,'Confirm recording/publication permission and authority to participate.')
            channels=data.get('channels',[])
            if not isinstance(channels,list) or not channels or set(channels)-set(cfg.get('channels',[])):raise HTTPException(422,'Choose the publication channels you authorize.')
            r=get_file(db,text(data.get('attachment_id',''),'File',100),lid=c['lead_id'])
            if r['category']!='Testimonial' or not r['mime'].startswith('video/'):raise HTTPException(422,'Choose your uploaded testimonial video.')
            if one(db,"SELECT id FROM media_releases WHERE attachment_id=? AND status!='Withdrawn'",(r['id'],)):raise HTTPException(409,'Consent is already recorded for this video.')
            name=text(data.get('signed_name',''),'Signed name',200)
            if not name:raise HTTPException(422,'Enter your name.')
            release=dict(id=uid('CONSENT'),lead_id=c['lead_id'],attachment_id=r['id'],customer_name=name,email=c['email'],release_version=cfg['version'],release_text=cfg['release_text'],channels_json=dumps(channels),signed_at=now())
            insert(db,'media_releases',release);insert(db,'media_release_terms',{'release_id':release['id'],'snapshot_json':dumps({'credit_mode':cfg['credit_mode'],'amount_cents':5000,'version':cfg['version'],'channels':channels})});audit(db,'client:'+c['email'],'Optional testimonial consent recorded','media_releases',release['id'])
            add_task(db,c['lead_id'],'Review video consent and one-time $50 eligibility',key='consent:'+release['id'])
        return {'ok':True,'published':False,'credit_applied':False}

    @app.post('/api/portal/consent/{rid}/withdraw')
    def withdraw(rid:str,c=Depends(client_auth)):
        with transaction(path) as db:
            r=one(db,'SELECT * FROM media_releases WHERE id=? AND lead_id=?',(rid,c['lead_id']))
            if not r:raise HTTPException(404,'Consent record not found.')
            db.execute("UPDATE media_releases SET status='Withdrawn',withdrawn_at=? WHERE id=?",(now(),rid))
            add_task(db,c['lead_id'],'Stop future publication and review testimonial takedown',key='withdraw:'+rid)
            audit(db,'client:'+c['email'],'Consent withdrawn; earned credit not reversed','media_releases',rid)
        return {'ok':True,'message':'Future use stopped in CrownOps. The team will review any content already published. Earned credit has not been removed.'}

    @app.post('/api/portal/cases')
    def concern(data:dict,c=Depends(client_auth)):
        detail=text(data.get('detail',''),'Details',12000)
        if not detail:raise HTTPException(422,'Please describe your question or concern.')
        with transaction(path) as db:
            rec=dict(id=uid('CASE'),lead_id=c['lead_id'],type='Client concern',title=text(data.get('title','Client concern'),'Title',200),detail=detail,created_by='client:'+c['email'],created_at=now(),updated_at=now())
            insert(db,'cases',rec);add_task(db,c['lead_id'],'Respond to client concern',key=rec['id'])
        return {'ok':True,'reference':rec['id']}

    @app.get('/api/portal/invoices/{iid}/document')
    def client_invoice(iid:str,c=Depends(client_auth)):
        from .invoices import invoice_html
        from fastapi.responses import HTMLResponse
        with closing(connect(path)) as db:
            r=one(db,"SELECT i.* FROM job_invoices i JOIN jobs j ON j.id=i.job_id WHERE i.id=? AND j.lead_id=? AND i.status='Issued'",(iid,c['lead_id']))
            if not r:raise HTTPException(404,'Final invoice is not available.')
            doc=json.loads(one(db,'SELECT document_json FROM invoice_revisions WHERE invoice_id=? AND revision=?',(iid,r['revision']))['document_json'])
            return HTMLResponse(invoice_html(doc),headers={'Content-Disposition':f'attachment; filename="{r["number"]}.html"','Cache-Control':'no-store'})
