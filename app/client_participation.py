"""Manual service offers, independent honest reviews and free promotional entry."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import calendar
import hmac
import json
import re
import secrets
from fastapi import Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from .store import connect, transaction, one, rows, insert, audit, uid, now, dumps
from .platform_common import (settings, put_setting, require_role, invoice_job, text, stamp_hash,
    business_day, queue_message, add_task)
from .photo_safety import READY_STATUSES

COOKIE='crownops_participation'
STAFF={'owner','manager','sales','crew'}
PROFILES=[{'id':'main','label':'Crown Movers — main profile','url':'https://g.page/r/CacaJ3Q-wayMEBM/review'},
          {'id':'french','label':'Crown Movers — French profile','url':'https://g.page/r/CUQdPArzzqz_EAE/review'}]
DISCLOSURE='The customer received a $50 moving discount for participating in this testimonial.'

def policy(db):
    return settings(db,'participation_policy',{'profiles':PROFILES})

def secure_google_url(value):
    value=text(value,'Google review link',2000)
    p=urlsplit(value)
    if p.scheme!='https' or p.username or p.password or p.hostname not in ('g.page','maps.app.goo.gl','www.google.com','google.com','maps.google.com'):
        raise HTTPException(422,'Use a direct HTTPS Google review link.')
    return value

def service(db,jid,u):
    require_role(u,STAFF)
    return invoice_job(db,jid,u)

def video_window(db,j):
    if j['status'] not in ('In progress','Completed'):return False
    # The offer belongs to the final-invoice preparation, before issuing/collecting.
    if one(db,"SELECT id FROM job_invoices WHERE job_id=? AND status!='Draft'",(j['id'],)):return False
    if one(db,"SELECT p.id FROM payments p JOIN field_collections a ON a.payment_id=p.id JOIN job_invoices i ON i.id=a.invoice_id WHERE i.job_id=? AND p.purpose!='Deposit'",(j['id'],)):return False
    if one(db,"SELECT p.id FROM payments p JOIN invoice_payment_allocations a ON a.payment_id=p.id JOIN job_invoices i ON i.id=a.invoice_id WHERE i.job_id=? AND p.purpose!='Deposit'",(j['id'],)):return False
    return True

def public_draw(d):
    if not d:return None
    return {**{k:d[k] for k in ('id','start_date','end_date','draw_date','prize_cents','rules_en','rules_fr','status')},'rules_hash':stamp_hash(d['rules_en']+'\n'+d['rules_fr'])}

def active_draw(db):
    today=business_day()
    return one(db,"SELECT * FROM client_draws WHERE status='Open' AND start_date<=? AND end_date>=? ORDER BY start_date DESC LIMIT 1",(today,today))

def check_participant(db,c,video=False):
    access=one(db,'SELECT * FROM participation_access WHERE session_hash=? AND expires_at>?',(c['session_hash'],now()))
    if not access:raise HTTPException(401,'This private link expired. Ask the team for a new link.')
    j=one(db,'SELECT * FROM jobs WHERE id=?',(access['job_id'],))
    if not j or j['status'] not in ('In progress','Completed'):raise HTTPException(409,'This service is not available for participation.')
    if video and (not video_window(db,j) or not settings(db,'media_program',{}).get('approved')):
        raise HTTPException(409,'The video offer is available during the service, before the final invoice is issued. Ask the team for help.')
    return j

def register_client_participation(app,path,auth,public_url,secure):
    @app.get('/participate')
    @app.get('/giveaway/{did}')
    def page(did:str=''):
        return FileResponse(Path(__file__).parent/'static'/'participate.html',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

    def client(request:Request):
        with closing(connect(path)) as db:
            c=one(db,'SELECT * FROM participation_access WHERE session_hash=? AND expires_at>?',(stamp_hash(request.cookies.get(COOKIE,'')),now()))
        if not c:raise HTTPException(401,'Open the private link the team shared with you.')
        if request.method!='GET' and not hmac.compare_digest(request.headers.get('x-csrf-token',''),c['csrf'] or ''):
            raise HTTPException(403,'Refresh this page before submitting.')
        return c

    @app.post('/api/participation/exchange')
    def exchange(data:dict):
        token=text(data.get('token',''),'Private link',160)
        with transaction(path) as db:
            c=one(db,'SELECT * FROM participation_access WHERE token_hash=? AND used_at IS NULL AND expires_at>?',(stamp_hash(token),now()))
            if not c:raise HTTPException(401,'This link was already opened or expired. Ask the team for a new link.')
            raw=secrets.token_urlsafe(40);csrf=secrets.token_urlsafe(32)
            db.execute('UPDATE participation_access SET session_hash=?,csrf=?,used_at=? WHERE token_hash=?',(stamp_hash(raw),csrf,now(),c['token_hash']))
        response=JSONResponse({'ok':True});response.set_cookie(COOKIE,raw,httponly=True,secure=secure,samesite='lax',max_age=43200,path='/')
        return response

    @app.get('/api/participation')
    def state(c=Depends(client)):
        with closing(connect(path)) as db:
            j=check_participant(db,c);l=one(db,'SELECT * FROM leads WHERE id=?',(j['lead_id'],))
            offer=one(db,'SELECT * FROM service_participation WHERE job_id=?',(j['id'],))
            cfg=settings(db,'media_program',{})
            attachment=one(db,'SELECT a.id,a.original_name,a.scan_status,m.duration_seconds FROM attachments a LEFT JOIN attachment_media m ON m.attachment_id=a.id WHERE a.id=? AND a.deleted_at IS NULL',(offer['attachment_id'],))
            release=one(db,'SELECT id,status,signed_at FROM media_releases WHERE id=?',(offer['release_id'],))
            profile=next((p for p in policy(db)['profiles'] if p['id']==offer['review_profile']),None)
            credit=one(db,"SELECT amount_cents FROM project_credits WHERE lead_id=? AND program='testimonial'",(j['lead_id'],))
            draw=active_draw(db)
            return {'csrf':c['csrf'],'client_name':l['client_name'],'email':l['email'],'language':l['language'],
                'job':{'id':j['id'],'title':j['title']},'profile':profile,'draw':public_draw(draw),
                'video_available':video_window(db,j) and bool(cfg.get('approved')) and not credit,
                'media':{k:cfg.get(k) for k in ('version','release_text','release_text_fr','channels','credit_mode')},
                'attachment':attachment,'release':release,'credit_applied':bool(credit)}

    @app.post('/api/participation/video')
    async def upload(file:UploadFile=File(...),c=Depends(client)):
        with closing(connect(path)) as db:
            j=check_participant(db,c,video=True)
            offer=one(db,'SELECT * FROM service_participation WHERE job_id=?',(j['id'],))
            if offer['release_id']:raise HTTPException(409,'Permission is already recorded for this video. Ask the team before replacing it.')
        result=await app.state.participation_upload(file,j['lead_id'],j['id'],'Testimonial','client-participation',participant=c)
        with transaction(path) as db:
            check_participant(db,c,video=True)
            offer=one(db,'SELECT * FROM service_participation WHERE job_id=?',(j['id'],))
            if offer['release_id']:raise HTTPException(409,'A video was already signed while this upload was processing. Refresh the page.')
            db.execute('UPDATE service_participation SET attachment_id=? WHERE job_id=?',(result['id'],j['id']))
        return result

    @app.post('/api/participation/consent')
    def consent(data:dict,c=Depends(client)):
        with transaction(path) as db:
            j=check_participant(db,c,video=True);cfg=settings(db,'media_program',{})
            offer=one(db,'SELECT * FROM service_participation WHERE job_id=?',(j['id'],))
            if offer['release_id']:return {'ok':True,'id':offer['release_id'],'already_recorded':True}
            if data.get('version')!=cfg.get('version'):raise HTTPException(409,'The terms changed. Refresh and read them again.')
            if data.get('agree') is not True or data.get('adult_and_authorized') is not True:raise HTTPException(422,'Confirm your permission and authority to participate.')
            name=text(data.get('signed_name',''),'Your name',200)
            channels=data.get('channels',[])
            if not name or not isinstance(channels,list) or not channels or set(channels)-set(cfg['channels']):raise HTTPException(422,'Enter your name and choose where we may use your video.')
            a=one(db,'SELECT a.*,m.duration_seconds FROM attachments a JOIN attachment_media m ON m.attachment_id=a.id WHERE a.id=? AND a.job_id=? AND a.deleted_at IS NULL',(offer['attachment_id'],j['id']))
            if not a or a['scan_status'] not in READY_STATUSES or not a['mime'].startswith('video/') or a['duration_seconds']<30:
                raise HTTPException(409,'Upload a checked video of at least 30 seconds first.')
            l=one(db,'SELECT * FROM leads WHERE id=?',(j['lead_id'],));rid=uid('CONSENT')
            release_text=cfg.get('release_text_fr') if str(data.get('language',l['language'])).lower().startswith('fr') and cfg.get('release_text_fr') else cfg['release_text']
            insert(db,'media_releases',dict(id=rid,lead_id=j['lead_id'],attachment_id=a['id'],customer_name=name,email=l['email'],release_version=cfg['version'],release_text=release_text,channels_json=dumps(channels),signed_at=now()))
            insert(db,'media_release_terms',{'release_id':rid,'snapshot_json':dumps({'credit_mode':cfg['credit_mode'],'amount_cents':5000,'version':cfg['version'],'channels':channels,'job_id':j['id'],'disclosure':DISCLOSURE})})
            db.execute('UPDATE service_participation SET release_id=? WHERE job_id=?',(rid,j['id']))
            audit(db,'client-participation','Client signed optional video permission','media_releases',rid,{'job_id':j['id']})
            add_task(db,j['lead_id'],'Apply signed in-service video credit before issuing invoice',key='participation:'+rid)
        return {'ok':True,'id':rid}

    @app.get('/api/jobs/{jid}/participation')
    def job_state(jid:str,u=Depends(auth)):
        with closing(connect(path)) as db:
            j=service(db,jid,u);l=one(db,'SELECT client_name,email,language FROM leads WHERE id=?',(j['lead_id'],))
            offer=one(db,'SELECT * FROM service_participation WHERE job_id=?',(jid,))
            cfg=settings(db,'media_program',{})
            attachment=one(db,'SELECT original_name,scan_status FROM attachments WHERE id=?',(offer['attachment_id'],)) if offer else None
            credit=one(db,"SELECT amount_cents,invoice_id FROM project_credits WHERE lead_id=? AND program='testimonial'",(j['lead_id'],))
            invoices=rows(db,'SELECT id,number,status,version FROM job_invoices WHERE job_id=? ORDER BY created_at DESC',(jid,))
            return {'job':{k:j[k] for k in ('id','title','status','version')},'client':l,'offer':offer,'attachment':attachment,'credit':credit,'invoices':invoices,
                'profiles':policy(db)['profiles'],'video_ready':bool(cfg.get('approved')),'video_window':video_window(db,j),
                'draw':public_draw(active_draw(db)),'emails':rows(db,"SELECT id,status,recipient,category,created_at FROM outbound_messages WHERE job_id=? AND event_key LIKE 'participation:%' ORDER BY created_at DESC LIMIT 10",(jid,))}

    @app.post('/api/jobs/{jid}/participation/open')
    def open_offer(jid:str,data:dict,u=Depends(auth)):
        with transaction(path) as db:
            j=service(db,jid,u)
            if j['status'] not in ('In progress','Completed'):raise HTTPException(409,'Start the service before opening client participation.')
            profile=text(data.get('profile',''),'Profile',40)
            if profile not in {p['id'] for p in policy(db)['profiles']}:raise HTTPException(422,'Choose the Google profile that handled this service.')
            old=one(db,'SELECT * FROM service_participation WHERE job_id=?',(jid,))
            if not old:
                crew=rows(db,"SELECT resource_id FROM assignments WHERE job_id=? AND resource_type='employee'",(jid,))
                insert(db,'service_participation',dict(job_id=jid,opened_by=u['id'],opened_at=now(),review_profile=profile,crew_json=dumps([x['resource_id'] for x in crew])))
            else:db.execute('UPDATE service_participation SET review_profile=? WHERE job_id=?',(profile,jid))
            raw=secrets.token_urlsafe(40)
            insert(db,'participation_access',dict(token_hash=stamp_hash(raw),job_id=jid,expires_at=(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat(timespec='seconds'),created_by=u['id']))
            audit(db,u['id'],'Opened manual client participation','jobs',jid)
        return {'url':public_url+'/participate#access='+raw,'expires_in':'12 hours; opens once','sent':False}

    @app.post('/api/jobs/{jid}/participation/credit')
    def apply_credit(jid:str,data:dict,u=Depends(auth)):
        with closing(connect(path)) as db:
            j=service(db,jid,u);offer=one(db,'SELECT * FROM service_participation WHERE job_id=?',(jid,))
            if not offer or not offer['release_id']:raise HTTPException(409,'The client needs to upload their video and confirm permission first.')
            inv=one(db,'SELECT * FROM job_invoices WHERE id=? AND job_id=?',(data.get('invoice_id'),jid))
            if not inv:raise HTTPException(422,'Choose this service’s draft invoice.')
        return app.state.apply_testimonial_credit(offer['release_id'],data,u)

    @app.post('/api/jobs/{jid}/participation/email')
    def prepare_email(jid:str,data:dict,u=Depends(auth)):
        kind=data.get('kind')
        if kind not in ('review','participation'):raise HTTPException(422,'Choose review link or client participation.')
        with transaction(path) as db:
            j=service(db,jid,u)
            if j['status'] not in ('In progress','Completed'):raise HTTPException(409,'Start the service before preparing this message.')
            l=one(db,'SELECT * FROM leads WHERE id=?',(j['lead_id'],));fr=str(l['language']).lower().startswith('fr')
            profile=next((p for p in policy(db)['profiles'] if p['id']==data.get('profile')),None)
            if not profile:raise HTTPException(422,'Choose the profile that handled this service.')
            key='participation:'+kind+':'+jid
            old=one(db,'SELECT * FROM outbound_messages WHERE event_key=?',(key,))
            if old and old['status'] in ('Sending','Sent','Unknown','Approved'):return {k:old[k] for k in ('id','status','recipient','subject','body')}
            if kind=='participation' and (not video_window(db,j) or not settings(db,'media_program',{}).get('approved')):
                raise HTTPException(409,'The video offer must be approved and the final invoice must still be a draft. You can share the independent review or free-entry link instead.')
        if kind=='participation':
            link=open_offer(jid,{'profile':profile['id']},u)['url']
            body=(f"Bonjour {l['client_name']},\n\nVoici le lien demandé pendant votre service :\n{link}\n\nVous pouvez choisir chaque option séparément. La vidéo facultative donne droit à 50 $ de rabais selon les conditions, avant l’émission de la facture finale. Aucun avis Google ni éloge n’est exigé. Lorsqu’un tirage est ouvert, son inscription est gratuite, sans achat, avis ou vidéo.\n\nMerci,\nCrown Movers" if fr else f"Hi {l['client_name']},\n\nHere is the link you requested during your service:\n{link}\n\nEach choice is independent. The optional video earns a $50 discount under the participation terms, before the final invoice is issued. No Google review or praise is required. When a draw is open, entry is free without a purchase, review or video.\n\nThank you,\nCrown Movers")
        else:
            body=(f"Bonjour {l['client_name']},\n\nMerci d’avoir choisi Crown Movers. Si vous le souhaitez, partagez un avis honnête sur votre expérience :\n{profile['url']}\n\nToute opinion est bienvenue. Aucun rabais ni accès au tirage ne dépend de votre avis.\n\nMerci,\nCrown Movers" if fr else f"Hi {l['client_name']},\n\nThank you for choosing Crown Movers. If you wish, share an honest review of your experience:\n{profile['url']}\n\nAll opinions are welcome. No discount or draw entry depends on your review.\n\nThank you,\nCrown Movers")
        with transaction(path) as db:
            service(db,jid,u)
            old=one(db,'SELECT * FROM outbound_messages WHERE event_key=?',(key,))
            if old and old['status'] in ('Draft','Cancelled','Failed'):
                db.execute("UPDATE outbound_messages SET subject=?,body=?,recipient=?,status='Draft',error='' WHERE id=?",('Merci | Thank you — Crown Movers',body,l['email'],old['id']))
                m=one(db,'SELECT * FROM outbound_messages WHERE id=?',(old['id'],))
            else:m=queue_message(db,j['lead_id'],'Merci | Thank you — Crown Movers',body,'Review request' if kind=='review' else 'Video invitation',u['id'],key,jid)
        return {**{k:m[k] for k in ('id','recipient','subject','body')},'status':m.get('status','Draft')}

    @app.post('/api/jobs/{jid}/participation/email/{mid}/send')
    def send(jid:str,mid:str,data:dict,u=Depends(auth)):
        if data.get('confirm') is not True:raise HTTPException(422,'Confirm the displayed recipient and message before sending.')
        from .providers import process_mail
        from .platform_common import external_gate
        with transaction(path) as db:
            service(db,jid,u);m=one(db,"SELECT * FROM outbound_messages WHERE id=? AND job_id=? AND event_key LIKE 'participation:%'",(mid,jid))
            if not m or data.get('recipient')!=m['recipient']:raise HTTPException(409,'Refresh and check the recipient before sending.')
            if m['status']=='Sent':return {'sent':True,'already_sent':True}
            if m['status']!='Draft':raise HTTPException(409,'Delivery needs review. Do not send another copy.')
            external_gate()
            db.execute("UPDATE outbound_messages SET status='Approved',approved_by=? WHERE id=?",(u['id'],mid))
            audit(db,u['id'],'Manually approved participation email','outbound_messages',mid)
        try:return process_mail(path,mid)
        except HTTPException:
            # A pre-send validation failure is safe to re-preview. Never reset
            # Sending/Unknown/Sent: those may already have reached the provider.
            with transaction(path) as db:db.execute("UPDATE outbound_messages SET status='Draft' WHERE id=? AND status='Approved'",(mid,))
            raise

    @app.get('/api/participation/settings')
    def get_settings(u=Depends(auth)):
        require_role(u,STAFF)
        with closing(connect(path)) as db:return {'profiles':policy(db)['profiles'],'draws':rows(db,'SELECT * FROM client_draws ORDER BY start_date DESC') if u['role']=='owner' else [],'media':settings(db,'media_program',{}) if u['role']=='owner' else None}

    @app.put('/api/participation/settings')
    def save_settings(data:dict,u=Depends(auth)):
        require_role(u,{'owner'})
        profiles=data.get('profiles',[])
        if not isinstance(profiles,list) or not 1<=len(profiles)<=5:raise HTTPException(422,'Add between one and five review profiles.')
        cleaned=[]
        for p in profiles:
            if not isinstance(p,dict):raise HTTPException(422,'Provide each profile with an ID, label and Google review URL.')
            pid=text(p.get('id',''),'Profile ID',40);label=text(p.get('label',''),'Profile label',100)
            if not re.fullmatch(r'[a-z0-9_-]+',pid) or not label or pid in {x['id'] for x in cleaned}:raise HTTPException(422,'Choose distinct profile IDs and clear labels.')
            cleaned.append({'id':pid,'label':label,'url':secure_google_url(p.get('url',''))})
        with transaction(path) as db:
            put_setting(db,'participation_policy',{'profiles':cleaned});audit(db,u['id'],'Review profile links updated','settings','participation_policy')
        return {'ok':True}

    @app.post('/api/participation/reviews')
    def verify_review(data:dict,u=Depends(auth)):
        require_role(u,{'owner','manager'})
        url=secure_google_url(data.get('review_url',''));rating=data.get('rating');note=text(data.get('note',''),'Verification evidence',2000)
        if type(rating)!=int or not 1<=rating<=5 or len(note)<10:raise HTTPException(422,'Record the actual star rating and how you matched the published review to this service.')
        with transaction(path) as db:
            j=service(db,data.get('job_id'),u)
            if j['status']!='Completed':raise HTTPException(409,'Verify a review against a completed service.')
            if one(db,'SELECT id FROM service_review_records WHERE review_url=?',(url,)):raise HTTPException(409,'This review is already recorded.')
            crew=rows(db,"SELECT resource_id FROM assignments WHERE job_id=? AND resource_type='employee'",(j['id'],))
            insert(db,'service_review_records',dict(id=uid('REVIEW'),job_id=j['id'],review_url=url,rating=rating,reviewed_by=u['id'],reviewed_at=now(),note=note,crew_json=dumps([x['resource_id'] for x in crew])))
            audit(db,u['id'],'Verified published review against service','jobs',j['id'],{'rating':rating})
        return {'ok':True}

    @app.get('/api/my-portal/client-feedback')
    def my_feedback(u=Depends(auth)):
        today=date.fromisoformat(business_day());start=date(today.year,1 if today.month<=6 else 7,1).isoformat()
        end=date(today.year,6,30).isoformat() if today.month<=6 else date(today.year,12,31).isoformat()
        lower=datetime.fromisoformat(start).replace(tzinfo=ZoneInfo('America/Toronto')).astimezone(timezone.utc).isoformat()
        upper=datetime.fromisoformat((date.fromisoformat(end)+timedelta(days=1)).isoformat()).replace(tzinfo=ZoneInfo('America/Toronto')).astimezone(timezone.utc).isoformat()
        with closing(connect(path)) as db:
            records=rows(db,'SELECT r.*,j.title FROM service_review_records r JOIN jobs j ON j.id=r.job_id WHERE r.reviewed_at>=? AND r.reviewed_at<? ORDER BY r.reviewed_at DESC',(lower,upper))
            mine=[r for r in records if u.get('employee_id') in json.loads(r['crew_json'])]
            return {'reviews':[{k:r[k] for k in ('rating','title','reviewed_at','review_url')} for r in mine],'five_star_count':sum(r['rating']==5 for r in mine),'total':len(mine),'period_start':start,'period_end':end}

    @app.get('/api/giveaways/{did}')
    def draw_info(did:str):
        with closing(connect(path)) as db:d=one(db,'SELECT * FROM client_draws WHERE id=?',(did,))
        if not d:raise HTTPException(404,'Draw not found.')
        return public_draw(d)

    @app.post('/api/giveaways/{did}/enter')
    def enter(did:str,data:dict,request:Request):
        name=text(data.get('name',''),'Name',200);email=text(data.get('email',''),'Email',254).lower()
        if not name or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise HTTPException(422,'Enter your name and a valid email.')
        if data.get('agree') is not True or data.get('eligible') is not True:raise HTTPException(422,'Read the rules and confirm that you qualify.')
        if data.get('website'):return {'ok':True,'message':'Entry received if eligible.'}
        from .account_access import recovery_rate_limit
        with transaction(path) as db:
            if not recovery_rate_limit(db,request,email,purpose='free-giveaway'):raise HTTPException(429,'Please wait before submitting again.')
            d=one(db,'SELECT * FROM client_draws WHERE id=?',(did,));today=business_day()
            if not d or d['status']!='Open' or not d['start_date']<=today<=d['end_date']:raise HTTPException(409,'This draw is not open for entries.')
            digest=stamp_hash(d['rules_en']+'\n'+d['rules_fr'])
            if data.get('rules_hash')!=digest:raise HTTPException(409,'The rules changed. Refresh and review them again.')
            if not one(db,'SELECT id FROM client_draw_entries WHERE draw_id=? AND email=?',(did,email)):
                insert(db,'client_draw_entries',dict(id=uid('ENTRY'),draw_id=did,name=name,email=email,created_at=now(),rules_hash=digest))
            # Same answer for an existing entry; no public list or email enumeration.
        return {'ok':True,'message':'Entry received, subject to the published rules. One entry per person per draw. No review or video is required.'}

    @app.post('/api/participation/draws')
    def create_draw(data:dict,u=Depends(auth)):
        require_role(u,{'owner'})
        if data.get('rules_reviewed') is not True:raise HTTPException(422,'Approve reviewed bilingual rules before opening this draw.')
        try:
            start=date.fromisoformat(data['start_date']);month=start.month+6;year=start.year+(month-1)//12;month=(month-1)%12+1
            end=date(year,month,min(start.day,calendar.monthrange(year,month)[1]))-timedelta(days=1)
            draw=date.fromisoformat(data['draw_date'])
            if draw<=end or start<date.fromisoformat(business_day()):raise ValueError()
        except (ValueError,KeyError,TypeError):raise HTTPException(422,'Choose a start date today or later and a draw date after the six-month entry period.')
        en=text(data.get('rules_en',''),'English rules',20000);fr=text(data.get('rules_fr',''),'French rules',20000)
        if min(len(en),len(fr))<100:raise HTTPException(422,'Provide the reviewed rules in both languages, including eligibility, free entry, one $500 prize, selection and claim requirements.')
        with transaction(path) as db:
            if one(db,"SELECT id FROM client_draws WHERE start_date<=? AND end_date>=?",(end.isoformat(),start.isoformat())):raise HTTPException(409,'This period overlaps an existing draw.')
            did=uid('DRAW');insert(db,'client_draws',dict(id=did,start_date=start.isoformat(),end_date=end.isoformat(),draw_date=draw.isoformat(),rules_en=en,rules_fr=fr,created_by=u['id'],created_at=now()))
            audit(db,u['id'],'Opened six-month free-entry draw','client_draws',did)
        return {'id':did,'end_date':end.isoformat(),'url':public_url+'/giveaway/'+did}

    @app.get('/api/participation/draws/{did}/entries')
    def entries(did:str,u=Depends(auth)):
        require_role(u,{'owner'})
        with closing(connect(path)) as db:return rows(db,'SELECT * FROM client_draw_entries WHERE draw_id=? ORDER BY created_at,id',(did,))

    @app.post('/api/participation/draws/{did}/status')
    def draw_status(did:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner'});status=data.get('status');note=text(data.get('note',''),'Reason',2000)
        if status not in ('Open','Paused') or len(note)<10:raise HTTPException(422,'Choose Open or Paused and record the reason.')
        with transaction(path) as db:
            d=one(db,'SELECT * FROM client_draws WHERE id=?',(did,))
            if not d or d['winner_id']:raise HTTPException(409,'Entry controls are locked after winner selection.')
            db.execute('UPDATE client_draws SET status=? WHERE id=?',(status,did))
            audit(db,u['id'],'Changed giveaway entry availability','client_draws',did,{'status':status,'reason':note})
        return {'ok':True,'status':status,'existing_entries_preserved':True}

    @app.post('/api/participation/draws/{did}/entries/{eid}/review')
    def eligibility(did:str,eid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner'});status=data.get('status');note=text(data.get('note',''),'Eligibility evidence',2000)
        if status not in ('Eligible','Ineligible') or len(note)<10:raise HTTPException(422,'Record eligibility and your evidence; review rating or video participation is not an eligibility condition.')
        with transaction(path) as db:
            d=one(db,'SELECT * FROM client_draws WHERE id=?',(did,))
            if not d or d['winner_id']:raise HTTPException(409,'Eligibility is locked after winner selection.')
            e=one(db,'SELECT * FROM client_draw_entries WHERE id=? AND draw_id=?',(eid,did))
            if not e:raise HTTPException(404,'Entry not found.')
            db.execute('UPDATE client_draw_entries SET status=?,reviewed_by=?,reviewed_at=?,review_note=? WHERE id=?',(status,u['id'],now(),note,eid))
            audit(db,u['id'],'Reviewed draw eligibility','client_draw_entries',eid,{'status':status,'note':note})
        return {'ok':True}

    @app.post('/api/participation/draws/{did}/select-winner')
    def select_winner(did:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner'})
        if data.get('confirm') is not True:raise HTTPException(422,'Confirm the final eligible roster. A draw cannot be rerun.')
        with transaction(path) as db:
            d=one(db,'SELECT * FROM client_draws WHERE id=?',(did,))
            if not d or business_day()<d['draw_date']:raise HTTPException(409,'Wait until the published draw date.')
            if d['winner_id']:raise HTTPException(409,'A provisional winner has already been selected. This draw cannot be rerun.')
            if d['status']=='Paused':raise HTTPException(409,'Resolve the pause and resume this draw before selecting a winner. Its published entry dates still apply.')
            if one(db,"SELECT id FROM client_draw_entries WHERE draw_id=? AND status='Pending verification'",(did,)):raise HTTPException(409,'Review every entry’s eligibility before drawing.')
            eligible=rows(db,"SELECT id FROM client_draw_entries WHERE draw_id=? AND status='Eligible' ORDER BY id",(did,))
            if not eligible:raise HTTPException(409,'There are no verified eligible entries.')
            ids=[e['id'] for e in eligible];winner=secrets.choice(ids);digest=stamp_hash(dumps(ids))
            db.execute("UPDATE client_draws SET winner_id=?,selected_at=?,roster_hash=?,candidate_count=?,status='Winner pending qualification' WHERE id=?",(winner,now(),digest,len(ids),did))
            audit(db,u['id'],'Randomly selected one provisional draw winner','client_draws',did,{'roster_hash':digest,'candidate_count':len(ids),'winner_id':winner})
        return {'winner_id':winner,'candidate_count':len(ids),'paid':False}

    @app.post('/api/participation/draws/{did}/award')
    def award(did:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner'});reference=text(data.get('payment_reference',''),'Payment reference',500)
        if data.get('qualification_confirmed') is not True or len(reference)<5:raise HTTPException(422,'Confirm the winner’s eligibility, required skill-testing question and prize claim, then record the actual payment reference.')
        with transaction(path) as db:
            d=one(db,'SELECT * FROM client_draws WHERE id=?',(did,))
            if not d or not d['winner_id'] or d['paid_at']:raise HTTPException(409,'This draw is not awaiting a prize payment record.')
            db.execute("UPDATE client_draws SET status='Awarded',paid_at=?,payment_reference=? WHERE id=?",(now(),reference,did));audit(db,u['id'],'Recorded $500 draw prize payment','client_draws',did,{'reference':reference})
        return {'ok':True,'money_sent':False}
