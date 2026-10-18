"""Private invoice access, scoped to one issued revision and its billing recipient."""
from contextlib import closing
from datetime import datetime,timezone,timedelta
from pathlib import Path
import hmac,json,re,secrets
from fastapi import BackgroundTasks,Depends,HTTPException,Request
from fastapi.responses import FileResponse,HTMLResponse,JSONResponse
from .store import connect,transaction,one,insert,audit,now
from .platform_common import stamp_hash,invoice_current,queue_message,live_enabled

COOKIE='crownops_invoice'


def invoice_access_link(db,inv,recipient,actor,base):
    raw=secrets.token_urlsafe(40)
    insert(db,'invoice_access_invites',dict(token_hash=stamp_hash(raw),invoice_id=inv['id'],revision=inv['revision'],recipient=recipient.lower(),expires_at=(datetime.now(timezone.utc)+timedelta(days=14)).isoformat(timespec='seconds'),created_by=actor,created_at=now()))
    return base.rstrip('/')+'/invoice-access#access='+raw


def scoped_invoice(db,session):
    inv,calc=invoice_current(db,session['invoice_id'])
    if inv['status']!='Issued' or inv['revision']!=session['revision']:raise HTTPException(409,'This invoice has been updated. Please ask Crown Movers for the current invoice link.')
    saved=one(db,'SELECT document_json FROM invoice_revisions WHERE invoice_id=? AND revision=?',(inv['id'],inv['revision']))
    if not saved:raise HTTPException(409,'The issued invoice is unavailable. Please contact Crown Movers.')
    doc=json.loads(saved['document_json'])
    lead=one(db,'SELECT l.email,l.language FROM leads l JOIN jobs j ON j.lead_id=l.id WHERE j.id=?',(inv['job_id'],))
    approved={str(doc['terms']['customer'].get('email') or '').lower(),lead['email'].lower()}
    if session['recipient'].lower() not in approved:raise HTTPException(401,'This invoice link is no longer valid. Ask Crown Movers for a new link.')
    return inv,calc,doc


def register_invoice_access(app,path,public_url,secure):
    def invoice_auth(request:Request):
        raw=request.cookies.get(COOKIE,'')
        with closing(connect(path)) as db:
            session=one(db,'SELECT * FROM invoice_access_sessions WHERE token_hash=? AND expires_at>?',(stamp_hash(raw),now())) if raw else None
            if not session:raise HTTPException(401,'Your invoice access has expired. Use the private link in your invoice email or call Crown Movers.')
            scoped_invoice(db,session)
        if request.method not in ('GET','HEAD','OPTIONS') and not hmac.compare_digest(session['csrf'],request.headers.get('x-csrf-token','')):raise HTTPException(403,'Reopen your invoice before continuing.')
        return session

    @app.get('/invoice-access')
    def invoice_page():return FileResponse(Path(__file__).parent/'static'/'invoice_access.html')

    def send_access(mid):
        from .providers import process_mail
        try:process_mail(path,mid)
        except Exception:
            # Public recovery never discloses account/provider details.
            return

    @app.post('/api/invoice-access/request-link')
    def request_access(data:dict,request:Request,background:BackgroundTasks):
        reply={'ok':True,'message':'If this invoice link is eligible, a fresh private link will be emailed to its saved recipient. For help, call 514-606-4030.'}
        if data.get('website'):return reply
        raw=data.get('token','')
        if not isinstance(raw,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,150}',raw):return reply
        with transaction(path) as db:
            invite=one(db,'SELECT * FROM invoice_access_invites WHERE token_hash=?',(stamp_hash(raw),))
            from .account_access import recovery_rate_limit
            if not recovery_rate_limit(db,request,email=invite['recipient'] if invite else '',purpose='invoice-link'):return reply
            if not invite:return reply
            inv,_=invoice_current(db,invite['invoice_id'])
            if inv['status']!='Issued':return reply
            current={**invite,'revision':inv['revision']}
            try:_,_,doc=scoped_invoice(db,current)
            except HTTPException:return reply
            lead=one(db,'SELECT l.* FROM leads l JOIN jobs j ON j.lead_id=l.id WHERE j.id=?',(inv['job_id'],))
            if not lead or lead['notifications_suppressed']:return reply
            bucket=int(datetime.now(timezone.utc).timestamp())//600
            key='invoice-access:'+inv['id']+':'+str(inv['revision'])+':'+stamp_hash(invite['recipient']+str(bucket))[:16]
            if one(db,'SELECT id FROM outbound_messages WHERE event_key=?',(key,)):return reply
            link=invoice_access_link(db,inv,invite['recipient'],'invoice-access-request',public_url)
            fr=str(doc.get('language','EN')).lower().startswith('fr')
            subject='Votre accès à la facture Crown Movers' if fr else 'Your Crown Movers invoice access'
            body=(f"Bonjour,\n\nVoici votre nouvel accès privé à la facture {inv['number']}.\n\nConsulter ma facture :\n{link}\n\nCe lien donne accès à cette facture seulement. Une question? Appelez le 514-606-4030.\n\nMerci,\nL’équipe Crown Movers" if fr else f"Hi,\n\nHere is your fresh private access to invoice {inv['number']}.\n\nOpen my invoice:\n{link}\n\nThis link provides access to this invoice only. Questions? Call 514-606-4030.\n\nThank you,\nThe Crown Movers team")
            msg=queue_message(db,lead['id'],subject,body,'Invoice access','invoice-access-request',key,jid=inv['job_id'])
            db.execute('UPDATE outbound_messages SET recipient=?,source_version=NULL WHERE id=?',(invite['recipient'],msg['id']))
            if live_enabled():
                db.execute("UPDATE outbound_messages SET status='Approved',approved_by='invoice-access-request' WHERE id=?",(msg['id'],))
                background.add_task(send_access,msg['id'])
            audit(db,'invoice-recipient:'+invite['recipient'],'Fresh invoice link requested','job_invoices',inv['id'],{'revision':inv['revision']})
        return reply

    @app.post('/api/invoice-access/exchange')
    def exchange(data:dict,request:Request):
        raw=data.get('token','')
        if not isinstance(raw,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,150}',raw):raise HTTPException(401,'This invoice link is invalid or expired. Please contact Crown Movers.')
        with transaction(path) as db:
            invite=one(db,'SELECT * FROM invoice_access_invites WHERE token_hash=? AND used_at IS NULL AND expires_at>?',(stamp_hash(raw),now()))
            if not invite:
                used=one(db,'SELECT * FROM invoice_access_invites WHERE token_hash=? AND expires_at>?',(stamp_hash(raw),now()))
                session=one(db,'SELECT * FROM invoice_access_sessions WHERE token_hash=? AND expires_at>?',(stamp_hash(request.cookies.get(COOKIE,'')),now()))
                if used and session and (used['invoice_id'],used['revision'],used['recipient'])==(session['invoice_id'],session['revision'],session['recipient']):
                    scoped_invoice(db,session)
                    return {'ok':True,'csrf':session['csrf'],'existing_session':True}
                raise HTTPException(401,'This invoice link is invalid or already used. Use the button below to request a fresh email link.')
            scoped_invoice(db,invite)
            token=secrets.token_urlsafe(40);csrf=secrets.token_urlsafe(24)
            insert(db,'invoice_access_sessions',dict(token_hash=stamp_hash(token),invoice_id=invite['invoice_id'],revision=invite['revision'],recipient=invite['recipient'],csrf=csrf,expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(timespec='seconds')))
            db.execute('UPDATE invoice_access_invites SET used_at=? WHERE token_hash=?',(now(),invite['token_hash']))
            audit(db,'invoice-recipient:'+invite['recipient'],'Private invoice opened','job_invoices',invite['invoice_id'],{'revision':invite['revision']})
        response=JSONResponse({'ok':True,'csrf':csrf})
        response.set_cookie(COOKIE,token,max_age=86400,httponly=True,secure=secure,samesite='lax',path='/api/invoice-access')
        return response

    @app.get('/api/invoice-access')
    def view(c=Depends(invoice_auth)):
        from .invoice_payments import quickbooks_payment_state
        with closing(connect(path)) as db:inv,calc,doc=scoped_invoice(db,c)
        state=quickbooks_payment_state(path,inv['id'])
        with closing(connect(path)) as db:
            current,_,_=scoped_invoice(db,c)
            if current['version']!=inv['version'] or state.get('version')!=inv['version'] or state.get('revision')!=inv['revision']:raise HTTPException(409,'Your invoice or payment record changed. Refresh to view the current invoice.')
        return {'number':inv['number'],'revision':inv['revision'],'language':doc.get('language','EN'),'service_date':doc['service_date'],
            'customer':doc['terms']['customer']['name'],'issuer':doc['terms']['issuer'],'calculation':calc,'issued_at':doc['issued_at'],
            'payment_state':state,'csrf':c['csrf']}

    @app.get('/api/invoice-access/document')
    def document(c=Depends(invoice_auth)):
        from .invoices import invoice_html
        with closing(connect(path)) as db:inv,_,doc=scoped_invoice(db,c)
        return HTMLResponse(invoice_html(doc),headers={'Content-Disposition':'inline; filename="'+inv['number']+'-r'+str(inv['revision'])+'.html"'})

    @app.get('/api/invoice-access/payment-link')
    def payment_link(c=Depends(invoice_auth)):
        from .invoice_payments import checked_quickbooks_link
        with closing(connect(path)) as db:inv,_,_=scoped_invoice(db,c)
        result=checked_quickbooks_link(path,inv['id'],None,invoice_scope=inv['id'])
        with closing(connect(path)) as db:
            current,_,_=scoped_invoice(db,c)
            if current['version']!=inv['version'] or result.get('version')!=inv['version'] or result.get('revision')!=c['revision']:raise HTTPException(409,'Your invoice changed. Reopen the current invoice before paying.')
        return result

    @app.post('/api/invoice-access/logout')
    def logout(request:Request,c=Depends(invoice_auth)):
        with transaction(path) as db:db.execute('DELETE FROM invoice_access_sessions WHERE token_hash=?',(stamp_hash(request.cookies.get(COOKIE,'')),))
        response=JSONResponse({'ok':True});response.delete_cookie(COOKIE,path='/api/invoice-access');return response
