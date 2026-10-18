"""Explicit management review for invoice extras and uncertain email delivery."""
from contextlib import closing
from datetime import datetime,timezone,timedelta
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
import base64,hashlib,json,re
from fastapi import Depends,HTTPException
from .store import connect,transaction,one,rows,insert,uid,now,dumps,audit
from .platform_common import invoice_job,require_role,invoice_current
from .pricing import number,cents


def _text(v,label,limit=2000):
    if not isinstance(v,str) or len(v)>limit:raise HTTPException(422,label+' is too long or invalid.')
    return v.strip()


def gmail_delivery_evidence(path,message):
    """Search the deterministic Message-ID and compare Sent recipient/content."""
    from .providers import provider_token
    from .gmail_sync import _api
    token=provider_token(path,'gmail',require_external=False)['access_token']
    key='<'+message['id'].lower()+'@crownops.local>'
    found=_api(token,'messages',{'q':'in:sent rfc822msgid:'+key,'maxResults':10})
    evidence=[]
    for item in found.get('messages') or []:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',str(item.get('id',''))):raise HTTPException(502,'Email provider returned an invalid message reference.')
        remote=_api(token,'messages/'+item['id'],{'format':'raw'})
        if 'SENT' not in remote.get('labelIds',[]):continue
        raw=remote.get('raw','');mail=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
        addresses={x[1].lower() for x in getaddresses(mail.get_all('To',[]))}
        part=mail.get_body(preferencelist=('plain',)) if mail.is_multipart() else mail
        content=part.get_content() if part else ''
        normal=lambda text:str(text).replace('\r\n','\n').strip()
        if str(mail.get('Message-ID','')).strip()!=key or addresses!={message['recipient'].lower()} or str(mail.get('Subject',''))!=message['subject'] or normal(content)!=normal(message['body']):
            raise HTTPException(409,'The provider message differs from this invoice email. Check company email and contact support before retrying.')
        evidence.append({'provider_id':item['id'],'sent_at':datetime.fromtimestamp(int(remote['internalDate'])/1000,timezone.utc).isoformat(timespec='seconds')})
    if len(evidence)>1:raise HTTPException(409,'Multiple sent copies were found. Management must investigate; another email will not be sent.')
    return evidence[0] if evidence else None


def register_invoice_management(app,path,auth):
    @app.post('/api/invoices/{iid}/resolve-delivery')
    def resolve_delivery(iid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','manager'})
        if data.get('confirmed') is not True:raise HTTPException(422,'Confirm your review of the delivery evidence.')
        note=_text(data.get('note',''),'Delivery review note')
        if len(note)<10:raise HTTPException(422,'Record what you checked in company email or with the email provider.')
        with closing(connect(path)) as db:
            inv,_=invoice_current(db,iid);invoice_job(db,inv['job_id'],u)
            if (data.get('version'),data.get('revision'))!=(inv['version'],inv['revision']):raise HTTPException(409,'Reload the current invoice before reviewing its email.')
            message=one(db,'SELECT * FROM outbound_messages WHERE id=? AND event_key IN (?,?)',(data.get('message_id'),'invoice-delivery:'+iid+':'+str(inv['revision']),'invoice-mail:'+iid+':'+str(inv['revision'])))
            if not message or message['status'] not in ('Unknown','Failed','Sending'):raise HTTPException(409,'Only an unresolved or failed delivery can be reviewed here.')
        try:evidence=gmail_delivery_evidence(path,message)
        except HTTPException:raise
        except Exception:raise HTTPException(502,'Company Sent email could not be checked. The existing delivery remains unresolved; no new email was sent.')
        if not evidence:
            with closing(connect(path)) as db:attempt=one(db,"SELECT created_at FROM audit WHERE object_type='outbound_messages' AND object_id=? AND action='Email delivery attempt started' ORDER BY id DESC LIMIT 1",(message['id'],))
            if not attempt:
                with transaction(path) as db:
                    attempt=one(db,"SELECT created_at FROM audit WHERE object_type='outbound_messages' AND object_id=? AND action='Untracked invoice delivery first observed' ORDER BY id LIMIT 1",(message['id'],))
                    if not attempt:
                        observed=now()
                        audit(db,u['id'],'Untracked invoice delivery first observed','outbound_messages',message['id'],{'provider_search':'No matching sent copy found','observed_at':observed})
                        attempt={'created_at':observed}
            attempt_at=attempt['created_at']
            if data.get('confirmed_not_sent') is not True:raise HTTPException(409,'No matching sent message was found. Check company Sent email and the provider, then explicitly confirm it was not delivered before permitting a retry.')
            if datetime.fromisoformat(attempt_at)>datetime.now(timezone.utc)-timedelta(minutes=30):raise HTTPException(409,'Wait at least 30 minutes after the attempt and check company Sent email before permitting a retry. The provider may still be completing delivery.')
        with transaction(path) as db:
            current,_=invoice_current(db,iid);current_message=one(db,'SELECT * FROM outbound_messages WHERE id=?',(message['id'],))
            if current['version']!=inv['version'] or current['revision']!=inv['revision'] or current_message!=message:raise HTTPException(409,'Invoice or delivery changed during the provider check. Reload before reviewing.')
            if evidence:
                db.execute("UPDATE outbound_messages SET status='Sent',provider_id=?,sent_at=?,error='' WHERE id=?",(evidence['provider_id'],evidence['sent_at'],message['id']))
                status='Sent'
            else:
                db.execute("UPDATE outbound_messages SET status='Draft',approved_by=NULL,error='Management verified not delivered; explicit retry permitted' WHERE id=?",(message['id'],))
                status='Draft'
            audit(db,u['id'],'Invoice email delivery reconciled','outbound_messages',message['id'],{'prior_status':message['status'],'status':status,'evidence':evidence,'note':note,'confirmed_not_sent':not bool(evidence)})
        return {'ok':True,'status':status,'sent':bool(evidence),'new_email_sent':False}

    @app.get('/api/invoices/{iid}/extras')
    def extras(iid:str,u=Depends(auth)):
        with closing(connect(path)) as db:
            inv,_=invoice_current(db,iid);invoice_job(db,inv['job_id'],u)
            return rows(db,'SELECT * FROM invoice_change_orders WHERE invoice_id=? ORDER BY created_at,id',(iid,))

    @app.post('/api/invoices/{iid}/extras')
    def request_extra(iid:str,data:dict,u=Depends(auth)):
        name=_text(data.get('name',''),'Material or service',160);reason=_text(data.get('reason',''),'Reason')
        key=_text(data.get('request_id',''),'Request ID',120)
        if not name or len(reason)<5 or not re.fullmatch(r'[A-Za-z0-9_-]{12,120}',key):raise HTTPException(422,'Enter the item, why it is needed, and a valid request reference.')
        try:
            quantity=number(data.get('quantity'),'Quantity',1,10000)
            if quantity!=quantity.to_integral():raise ValueError('Quantity must be a whole number.')
            price=cents(number(data.get('unit_price',0),'Proposed unit price',0,10000))
        except ValueError as ex:raise HTTPException(422,str(ex))
        payload=dumps({'name':name,'quantity':int(quantity),'unit_cents':price,'reason':reason});digest=hashlib.sha256(payload.encode()).hexdigest()
        with transaction(path) as db:
            inv,_=invoice_current(db,iid);invoice_job(db,inv['job_id'],u)
            old=one(db,'SELECT * FROM invoice_change_orders WHERE request_key=?',(key,))
            if old:
                if old['invoice_id']!=iid or old['payload_hash']!=digest:raise HTTPException(409,'This request reference was already used for another item.')
                return old
            if inv['status']!='Draft' or data.get('version')!=inv['version']:raise HTTPException(409,'Open the current draft invoice before requesting a new item. Issued invoices need a correction revision first.')
            rec=dict(id=uid('EXTRA'),invoice_id=iid,request_key=key,payload_hash=digest,name=name,quantity=int(quantity),unit_cents=price,reason=reason,status='Requested',requested_by=u['id'],created_at=now())
            insert(db,'invoice_change_orders',rec)
            from .platform_common import add_task
            job=one(db,'SELECT lead_id FROM jobs WHERE id=?',(inv['job_id'],))
            add_task(db,job['lead_id'],'Management: review invoice extra — '+name,key='invoice-extra:'+rec['id'])
            audit(db,u['id'],'Invoice extra requested; not charged','invoice_change_orders',rec['id'],{'invoice_id':iid,'name':name})
            return rec

    @app.post('/api/invoices/{iid}/extras/{eid}/review')
    def review_extra(iid:str,eid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','manager'})
        decision=data.get('decision');agreement=_text(data.get('agreement',''),'Client agreement / review note')
        if decision not in ('Approve','Decline') or len(agreement)<10:raise HTTPException(422,'Choose Approve or Decline and record the client agreement or review reason.')
        if decision=='Approve' and data.get('client_agreed') is not True:raise HTTPException(422,'Confirm the client agreed to this item, quantity and price before charging it.')
        try:price=cents(number(data.get('unit_price',0),'Approved unit price',0,10000))
        except ValueError as ex:raise HTTPException(422,str(ex))
        with transaction(path) as db:
            inv,_=invoice_current(db,iid);invoice_job(db,inv['job_id'],u);extra=one(db,'SELECT * FROM invoice_change_orders WHERE id=? AND invoice_id=?',(eid,iid))
            if not extra:raise HTTPException(404,'Extra request not found.')
            if extra['status']!='Requested':raise HTTPException(409,'This request has already been reviewed.')
            if inv['status']!='Draft' or data.get('version')!=inv['version']:raise HTTPException(409,'Reload the current draft before reviewing its extras.')
            if decision=='Approve':
                terms=json.loads(inv['terms_json']);actual=json.loads(inv['actual_json']);code='extra_'+eid.split('-',1)[1]
                terms['catalogue'].append({'code':code,'name':extra['name'],'unit_cents':price})
                actual.setdefault('quantities',{})[code]=extra['quantity']
                terms.setdefault('approved_extras',[]).append({'id':eid,'agreement':agreement,'approved_by':u['id'],'approved_at':now()})
                from .invoices import calculate_invoice
                calculate_invoice(terms,actual)
                db.execute('UPDATE job_invoices SET terms_json=?,actual_json=?,version=version+1,updated_at=? WHERE id=?',(dumps(terms),dumps(actual),now(),iid))
            db.execute('UPDATE invoice_change_orders SET status=?,unit_cents=?,agreement=?,reviewed_by=?,reviewed_at=? WHERE id=?',('Approved' if decision=='Approve' else 'Declined',price,agreement,u['id'],now(),eid))
            db.execute("UPDATE tasks SET status='Done' WHERE legacy_id=?",('invoice-extra:'+eid,))
            audit(db,u['id'],'Invoice extra '+('approved with client agreement' if decision=='Approve' else 'declined'),'invoice_change_orders',eid,{'invoice_id':iid,'unit_cents':price,'quantity':extra['quantity'],'agreement':agreement})
        return {'ok':True,'status':'Approved' if decision=='Approve' else 'Declined'}
