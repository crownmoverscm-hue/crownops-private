"""Explicitly authorized external adapters. Disabled by default; no credentials shipped.

Timeouts remain Unknown, not automatically retried for non-idempotent sending.
Provider interfaces have mocked contract tests, not live-account certification.
"""
from contextlib import closing
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from urllib.parse import urlencode, quote
import base64, hashlib, hmac, json, os, secrets, time
import requests
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from . import gmail_sync
from .store import connect,transaction,rows,one,insert,audit,uid,now,dumps
from .platform_common import *
from .pricing import cents,number

PROVIDERS={
 'gmail':{'auth':'https://accounts.google.com/o/oauth2/v2/auth','token':'https://oauth2.googleapis.com/token',
   'scope':'https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send', 'prefix':'GOOGLE'},
 'quickbooks':{'auth':'https://appcenter.intuit.com/connect/oauth2','token':'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer',
   'scope':'com.intuit.quickbooks.accounting','prefix':'QBO'}}

def token_save(db,provider,body):
    body=dict(body);body['expires_at']=time.time()+int(body.get('expires_in',3600))-60
    db.execute('INSERT INTO oauth_credentials(provider,ciphertext,updated_at) VALUES(?,?,?) ON CONFLICT(provider) DO UPDATE SET ciphertext=excluded.ciphertext,updated_at=excluded.updated_at',(provider,encrypt(body),now()))

def provider_token(path,provider,require_external=True):
    if require_external: external_gate()
    cfg=PROVIDERS[provider]
    with transaction(path) as db:
        r=one(db,'SELECT * FROM oauth_credentials WHERE provider=?',(provider,))
        if not r:raise HTTPException(503,provider+' is not connected.')
        body=decrypt(r['ciphertext'])
        if body.get('expires_at',0)>time.time():return body
        params={'grant_type':'refresh_token','refresh_token':body['refresh_token']}
        cid=os.getenv(cfg['prefix']+'_CLIENT_ID','');cs=os.getenv(cfg['prefix']+'_CLIENT_SECRET','')
        kwargs={'auth':(cid,cs)} if provider=='quickbooks' else {}
        if provider=='gmail':params.update(client_id=cid,client_secret=cs)
        try:
            r=requests.post(cfg['token'],data=params,timeout=20,**kwargs);r.raise_for_status();new=r.json()
        except requests.RequestException:raise HTTPException(502,'Connection refresh failed. Reconnect '+provider+' from Settings.')
        new.setdefault('refresh_token',body.get('refresh_token'));new['realm_id']=body.get('realm_id');token_save(db,provider,new)
        return new

def _qbo_connection(path):
    t=provider_token(path,'quickbooks',require_external=False);realm=t.get('realm_id')
    if not realm or not str(realm).isdigit():raise HTTPException(503,'QuickBooks company connection is incomplete.')
    host='https://sandbox-quickbooks.api.intuit.com' if quickbooks_environment()=='sandbox' else 'https://quickbooks.api.intuit.com'
    return t,str(realm),host

def qbo_call(path,method,resource,payload=None,key=None):
    quickbooks_gate()
    t,realm,host=_qbo_connection(path)
    params={'minorversion':os.getenv('QBO_MINOR_VERSION','75')}
    if key:params['requestid']=hashlib.sha256(key.encode()).hexdigest()[:40]
    r=requests.request(method,host+'/v3/company/'+realm+'/'+resource,json=payload,params=params,headers={'Authorization':'Bearer '+t['access_token'],'Accept':'application/json'},timeout=30)
    if not r.ok:raise RuntimeError('QuickBooks rejected this request. Review the account, item/tax mapping and provider logs. HTTP '+str(r.status_code))
    return r.json()

def qbo_read(path,resource,params=None):
    t,realm,host=_qbo_connection(path)
    q={'minorversion':os.getenv('QBO_MINOR_VERSION','75')}
    if params:q.update(params)
    r=requests.get(host+'/v3/company/'+realm+'/'+resource,params=q,headers={'Authorization':'Bearer '+t['access_token'],'Accept':'application/json'},timeout=30)
    if not r.ok:raise RuntimeError('QuickBooks read failed. Review the sandbox connection. HTTP '+str(r.status_code))
    return r.json()

def qbo_query(path,statement):
    return qbo_read(path,'query',{'query':statement}).get('QueryResponse',{})

def qbo_mapping(path,key):
    env_key={'service_item_id':'QBO_SERVICE_ITEM_ID','tax_code_id':'QBO_TAX_CODE_ID','discount_account_id':'QBO_DISCOUNT_ACCOUNT_ID','deposit_account_id':'QBO_DEPOSIT_ACCOUNT_ID'}[key]
    with closing(connect(path)) as db:saved=settings(db,'quickbooks_mappings',{}) or {}
    return str(saved.get(key) or os.getenv(env_key,'')).strip()

def qbo_customer(path,l):
    with closing(connect(path)) as db:old=one(db,"SELECT external_id FROM external_records WHERE provider='quickbooks' AND kind='Customer' AND object_id=? AND revision=0",(l['id'],))
    if old:return old['external_id']
    name=(l['client_name'][:65]+' | '+l['id'][-12:])
    body={'DisplayName':name,'PrimaryEmailAddr':{'Address':l['email']},'PrimaryPhone':{'FreeFormNumber':l['phone']}}
    r=qbo_call(path,'POST','customer',body,'customer:'+l['id'])['Customer']
    with transaction(path) as db:
        db.execute('INSERT OR IGNORE INTO external_records(provider,kind,object_id,external_id,updated_at) VALUES(?,?,?,?,?)',('quickbooks','Customer',l['id'],r['Id'],now()))
    return r['Id']

def qbo_invoice_payload(doc,customer_id,item_id,tax_id,discount_account):
    if not item_id or not tax_id:raise ValueError('Configure the reviewed QuickBooks service item and Canadian tax code IDs.')
    t=doc['terms'];r=doc['calculation']
    if t.get('testimonial_credit_mode')=='after_tax' and t.get('testimonial_credit_cents',0):raise ValueError('After-tax credits require a separate reviewed QuickBooks credit memo. No invoice was exported.')
    lines=[]
    for line in r['lines']:
        if not line['amount_cents']:continue
        lines.append({'DetailType':'SalesItemLineDetail','Amount':line['amount_cents']/100,'Description':line['description'],
             'SalesItemLineDetail':{'ItemRef':{'value':item_id},'TaxCodeRef':{'value':tax_id},'Qty':float(line['quantity']),'UnitPrice':line['unit_cents']/100}})
    discount=r['discount_cents']+(t.get('testimonial_credit_cents',0) if t.get('testimonial_credit_mode')=='before_tax' else 0)
    if discount:
        if not discount_account:raise ValueError('Configure the reviewed QuickBooks discount account ID.')
        lines.append({'DetailType':'DiscountLineDetail','Amount':discount/100,'DiscountLineDetail':{'PercentBased':False,'DiscountAccountRef':{'value':discount_account}}})
    return {'CustomerRef':{'value':customer_id},'CurrencyRef':{'value':'CAD'},'GlobalTaxCalculation':'TaxExcluded','Line':lines,
       'BillEmail':{'Address':t['customer'].get('email','')},'TxnDate':doc['service_date'],'PrivateNote':'CrownOps '+doc['id']+' revision '+str(doc['revision']),
       'CustomerMemo':{'value':t.get('approval_reference','')[:1000]},'ApplyTaxAfterDiscount':True}

def send_gmail(path,message):
    t=provider_token(path,'gmail');m=EmailMessage();m['To']=message['recipient'];m['Subject']=message['subject'];m['Message-ID']='<'+message['id'].lower()+'@crownops.local>';m.set_content(message['body'])
    if message['category']=='Invoice':
        marker=message['event_key'].split(':')
        if marker[0] in ('invoice-delivery','invoice-mail') and len(marker)==3:
            from .invoices import invoice_html
            with closing(connect(path)) as db:
                inv=one(db,'SELECT * FROM job_invoices WHERE id=?',(marker[1],))
                if not inv or inv['status']!='Issued' or inv['revision']!=int(marker[2]):raise ValueError('Invoice changed before delivery.')
                rec=one(db,'SELECT document_json FROM invoice_revisions WHERE invoice_id=? AND revision=?',(inv['id'],inv['revision']))
            doc=json.loads(rec['document_json']);m.add_attachment(invoice_html(doc).encode(),maintype='text',subtype='html',filename=inv['number']+'.html')
    raw=base64.urlsafe_b64encode(m.as_bytes()).decode()
    r=requests.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',json={'raw':raw},headers={'Authorization':'Bearer '+t['access_token']},timeout=25)
    if not r.ok:raise RuntimeError('Gmail did not confirm sending. Review the provider. HTTP '+str(r.status_code))
    return r.json()['id']

def process_mail(path,mid):
    external_gate()
    with transaction(path) as db:
        m=one(db,'SELECT * FROM outbound_messages WHERE id=?',(mid,))
        if not m or m['status']!='Approved':raise HTTPException(409,'Only a reviewed and approved message can be sent.')
        l=one(db,'SELECT * FROM leads WHERE id=?',(m['lead_id'],))
        if l['notifications_suppressed']:raise HTTPException(409,'Client communication remains suppressed. Review and activate this project first.')
        if l['email'].lower()!=m['recipient'].lower():raise HTTPException(409,'Client email changed. Prepare a new message.')
        if m['job_id'] and m['source_version']:
            j=one(db,'SELECT * FROM jobs WHERE id=?',(m['job_id'],))
            if not j or j['version']!=m['source_version'] or j['status']=='Cancelled':
                db.execute("UPDATE outbound_messages SET status='Cancelled',error='Job changed; recreate this message' WHERE id=?",(mid,))
                return {'sent':False,'reason':'Job changed; stale message cancelled'}
        if m['category']=='Collection' and m['event_key'].startswith('balance:'):
            _,invoice_id,revision,balance=m['event_key'].split(':')
            invoice,calc=invoice_current(db,invoice_id)
            if invoice['status']!='Issued' or invoice['revision']!=int(revision) or calc['balance_cents']!=int(balance) or pending_received(db,invoice_id) or one(db,"SELECT id FROM cases WHERE lead_id=? AND status!='Resolved'",(m['lead_id'],)):
                db.execute("UPDATE outbound_messages SET status='Cancelled',error='Invoice/payment/dispute changed' WHERE id=?",(mid,));return {'sent':False,'reason':'Collection state changed; stale reminder cancelled'}
        if os.getenv('CROWNOPS_ENV','test')!='production':
            allow={x.strip().lower() for x in os.getenv('CROWNOPS_TEST_EMAIL_ALLOWLIST','').split(',') if x.strip()}
            if m['recipient'].lower() not in allow:raise HTTPException(409,'Add this controlled test recipient to CROWNOPS_TEST_EMAIL_ALLOWLIST before sending.')
        db.execute("UPDATE outbound_messages SET status='Sending' WHERE id=?",(mid,))
    try:
        pid=send_gmail(path,m)
        with transaction(path) as db:
            db.execute("UPDATE outbound_messages SET status='Sent',provider_id=?,sent_at=? WHERE id=?",(pid,now(),mid));audit(db,m['approved_by'],'Client email sent','outbound_messages',mid,{'provider_id':pid})
        return {'sent':True,'provider_id':pid}
    except Exception:
        with transaction(path) as db:db.execute("UPDATE outbound_messages SET status='Unknown',error=? WHERE id=?",('Sending was not confirmed. Check Sent mail before retrying; no automatic retry.',mid))
        raise HTTPException(502,'Sending was not confirmed. Check Gmail Sent before taking further action.')

def register_providers(app,path,auth,public_url,new_lead=None):
    @app.get('/api/connections')
    def status(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            connected={r['provider']:r['updated_at'] for r in rows(db,'SELECT provider,updated_at FROM oauth_credentials')}
            operations=rows(db,'SELECT id,provider,object_id,kind,status,error,created_at,updated_at FROM provider_operations ORDER BY created_at DESC LIMIT 60')
        return {'external_actions_enabled':live_enabled(),'gmail':{'authorized_at':connected.get('gmail'),'configured':bool(os.getenv('GOOGLE_CLIENT_ID'))},
          'quickbooks':{'authorized_at':connected.get('quickbooks'),'configured':bool(os.getenv('QBO_CLIENT_ID')),'environment':os.getenv('QBO_ENV','sandbox')},
          'square':{'configured':bool(os.getenv('SQUARE_ACCESS_TOKEN') and os.getenv('SQUARE_LOCATION_ID')),'environment':os.getenv('SQUARE_ENV','sandbox')},
          'ai':{'configured':bool(os.getenv('OPENAI_API_KEY') and os.getenv('OPENAI_MODEL'))},
          'intake':{'configured':len(os.getenv('CROWNOPS_WEBHOOK_SECRET',''))>=32},'scanner':{'configured':bool(os.getenv('CROWNOPS_SCAN_COMMAND'))},
          'operations':operations,'note':'Configuration/authorization does not certify a successful end-to-end live test.'}

    @app.get('/api/gmail/review')
    def gmail_review(u=Depends(auth)):
        require_role(u,OFFICE)
        try:
            return gmail_sync.review_items(path)
        except Exception as e:
            raise HTTPException(500, 'Gmail review queue error: ' + type(e).__name__ + ': ' + str(e)[:180])

    @app.post('/api/gmail/sync-readonly')
    def gmail_sync_readonly(data:dict=None,u=Depends(auth)):
        require_role(u,MANAGERS)
        if new_lead is None: raise HTTPException(503,'Lead intake is unavailable in this build.')
        t=provider_token(path,'gmail',require_external=False)
        data=data or {}
        try:
            return gmail_sync.sync_gmail_readonly(path,t['access_token'],u['id'],new_lead,data.get('days',14),data.get('max_per_feed',30),fluid=False)
        except HTTPException:
            raise
        except requests.RequestException as e:
            status=getattr(getattr(e,'response',None),'status_code',None)
            raise HTTPException(502,'Gmail read-only sync API error' + (f' (HTTP {status})' if status else '') + '. Gmail messages were not changed.')
        except Exception as e:
            raise HTTPException(500,'Gmail read-only sync internal error: ' + type(e).__name__ + ': ' + str(e)[:180])

    @app.post('/api/gmail/sync-v8-test')
    def gmail_sync_v8_test(data:dict=None,u=Depends(auth)):
        require_role(u,MANAGERS)
        if new_lead is None: raise HTTPException(503,'Lead intake is unavailable in this build.')
        t=provider_token(path,'gmail',require_external=False)
        data=data or {}
        try:
            return gmail_sync.sync_gmail_readonly(path,t['access_token'],u['id'],new_lead,data.get('days',2),data.get('max_per_feed',20),fluid=True)
        except HTTPException:
            raise
        except requests.RequestException as e:
            status=getattr(getattr(e,'response',None),'status_code',None)
            raise HTTPException(502,'V8 intake test Gmail API error' + (f' (HTTP {status})' if status else '') + '. Gmail messages were not changed.')
        except Exception as e:
            raise HTTPException(500,'V8 intake test internal error: ' + type(e).__name__ + ': ' + str(e)[:180])

    @app.post('/api/gmail/review/{eid}/reviewed')
    def gmail_mark_reviewed(eid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with transaction(path) as db:
            ev=one(db,"SELECT * FROM integration_events WHERE id=? AND event_type LIKE 'gmail_%'",(eid,))
            if not ev:raise HTTPException(404,'Gmail review item not found.')
            db.execute("UPDATE integration_events SET status='Reviewed' WHERE id=?",(eid,));audit(db,u['id'],'Gmail review item marked reviewed','integration_events',eid)
        return {'ok':True}

    @app.post('/api/connect/{provider}')
    def start(provider:str,u=Depends(auth)):
        require_role(u,MANAGERS)
        if provider not in PROVIDERS:raise HTTPException(422,'Unknown provider.')
        secret_box();cfg=PROVIDERS[provider];cid=os.getenv(cfg['prefix']+'_CLIENT_ID','')
        if not cid:raise HTTPException(503,'Configure the app client ID and secret on the server first.')
        raw=secrets.token_urlsafe(32)
        with transaction(path) as db:insert(db,'oauth_states',dict(state_hash=stamp_hash(raw),provider=provider,user_id=u['id'],expires_at=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(timespec='seconds')))
        params={'client_id':cid,'redirect_uri':public_url+'/api/oauth/'+provider+'/callback','response_type':'code','scope':cfg['scope'],'state':raw}
        if provider=='gmail':params.update(access_type='offline',prompt='consent')
        return {'url':cfg['auth']+'?'+urlencode(params)}

    @app.get('/api/oauth/{provider}/callback')
    def callback(provider:str,request:Request):
        if provider not in PROVIDERS:raise HTTPException(422,'Unknown provider.')
        state=request.query_params.get('state','');code=request.query_params.get('code','');cfg=PROVIDERS[provider]
        if not state or not code:raise HTTPException(400,'Authorization was cancelled or incomplete.')
        with transaction(path) as db:
            s=one(db,'SELECT * FROM oauth_states WHERE state_hash=? AND provider=? AND used_at IS NULL AND expires_at>?',(stamp_hash(state),provider,now()))
            if not s:raise HTTPException(401,'Connection request expired or was already used.')
            raw=request.cookies.get('crownops_session','')
            staff=one(db,"SELECT s.user_id FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1 AND u.role='owner'",(stamp_hash(raw),now()))
            if not staff or staff['user_id']!=s['user_id']:raise HTTPException(403,'Sign in as the manager who started the connection.')
            db.execute('UPDATE oauth_states SET used_at=? WHERE state_hash=?',(now(),stamp_hash(state)))
        cid=os.getenv(cfg['prefix']+'_CLIENT_ID','');cs=os.getenv(cfg['prefix']+'_CLIENT_SECRET','');body={'grant_type':'authorization_code','code':code,'redirect_uri':public_url+'/api/oauth/'+provider+'/callback'}
        kwargs={'auth':(cid,cs)} if provider=='quickbooks' else {}
        if provider=='gmail':body.update(client_id=cid,client_secret=cs)
        try:
            r=requests.post(cfg['token'],data=body,timeout=25,**kwargs);r.raise_for_status();tokens=r.json()
            if not tokens.get('access_token') or not tokens.get('refresh_token'):raise ValueError('Missing credentials')
            if provider=='quickbooks':tokens['realm_id']=request.query_params.get('realmId','')
            with transaction(path) as db:token_save(db,provider,tokens);audit(db,s['user_id'],'Provider authorization completed','connections',provider)
        except Exception:raise HTTPException(502,'Connection exchange failed. Check provider configuration and reconnect. Tokens were not exposed.')
        return RedirectResponse('/#connections',status_code=303)

    @app.post('/api/disconnect/{provider}')
    def disconnect(provider:str,u=Depends(auth)):
        require_role(u,MANAGERS)
        with transaction(path) as db:
            db.execute('DELETE FROM oauth_credentials WHERE provider=?',(provider,));audit(db,u['id'],'Local provider credentials removed','connections',provider)
        return {'ok':True,'note':'Also revoke access in the provider account if required.'}

    @app.post('/api/outbound/{mid}/send')
    def send(mid:str,u=Depends(auth)):
        require_role(u,SALES)
        return process_mail(path,mid)

    @app.post('/api/invoices/{iid}/quickbooks')
    def sync_invoice(iid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','accounting'});external_gate()
        if data.get('confirmed',data.get('confirm')) is not True:raise HTTPException(422,'Confirm the accounting write after reviewing mappings and totals.')
        with transaction(path) as db:
            inv,calc=invoice_current(db,iid)
            if inv['status']!='Issued':raise HTTPException(409,'Finalize the current invoice revision first.')
            doc=json.loads(one(db,'SELECT document_json FROM invoice_revisions WHERE invoice_id=? AND revision=?',(iid,inv['revision']))['document_json'])
            j=one(db,'SELECT * FROM jobs WHERE id=?',(inv['job_id'],));l=one(db,'SELECT * FROM leads WHERE id=?',(j['lead_id'],))
            if os.getenv('QBO_ENV','sandbox')!='sandbox' and doc.get('test_mode',True):raise HTTPException(409,'Test invoices cannot be exported to production accounting.')
            old=one(db,"SELECT * FROM external_records WHERE provider='quickbooks' AND kind='Invoice' AND object_id=? ORDER BY revision DESC LIMIT 1",(iid,))
            if old and old['revision']==inv['revision']:return {'already_synced':True,'external_id':old['external_id'],'amount_matches':old['amount_cents']==calc['total_cents']}
            key='qbo:invoice:'+iid+':'+str(inv['revision']);op=one(db,'SELECT * FROM provider_operations WHERE id=?',(key,))
            if op and op['status'] in ('Running','Unknown','Mismatch'):raise HTTPException(409,'This export needs reconciliation before retrying. No second invoice was created.')
            db.execute('DELETE FROM provider_operations WHERE id=?',(key,));insert(db,'provider_operations',dict(id=key,provider='quickbooks',object_id=iid,kind='Invoice',status='Running',created_at=now(),updated_at=now()))
        try:
            cust=qbo_customer(path,l);payload=qbo_invoice_payload(doc,cust,os.getenv('QBO_SERVICE_ITEM_ID',''),os.getenv('QBO_TAX_CODE_ID',''),os.getenv('QBO_DISCOUNT_ACCOUNT_ID',''))
            if old:
                existing=qbo_call(path,'GET','invoice/'+quote(old['external_id']))['Invoice']
                if existing['SyncToken']!=old['sync_token']:raise ValueError('QuickBooks was edited externally. Reconcile before overwriting.')
                payload.update(Id=old['external_id'],SyncToken=old['sync_token'],sparse=False)
            r=qbo_call(path,'POST','invoice',payload,key)['Invoice'];remote=cents(r['TotalAmt'])
            with transaction(path) as db:
                insert(db,'external_records',dict(provider='quickbooks',kind='Invoice',object_id=iid,revision=inv['revision'],external_id=r['Id'],sync_token=r['SyncToken'],amount_cents=remote,updated_at=now()))
                db.execute('UPDATE provider_operations SET status=?,result_json=?,updated_at=? WHERE id=?',('Completed' if remote==calc['total_cents'] else 'Mismatch',dumps({'external_id':r['Id'],'remote_total_cents':remote,'expected_total_cents':calc['total_cents']}),now(),key))
                audit(db,u['id'],'Invoice accounting export','job_invoices',iid,{'external_id':r['Id'],'amount_matches':remote==calc['total_cents']})
            return {'external_id':r['Id'],'amount_matches':remote==calc['total_cents'],'sent':False}
        except Exception as e:
            state='Failed' if isinstance(e,ValueError) else 'Unknown'
            with transaction(path) as db:db.execute('UPDATE provider_operations SET status=?,error=?,updated_at=? WHERE id=?',(state,str(e)[:350] if isinstance(e,ValueError) else audit_safe_error(e),now(),key))
            raise HTTPException(502,str(e)[:350] if isinstance(e,ValueError) else 'QuickBooks write not confirmed. Review operation status before retrying.')

    @app.post('/api/invoices/{iid}/square-link')
    def square_link(iid:str,data:dict,u=Depends(auth)):
        external_gate()
        token=os.getenv('SQUARE_ACCESS_TOKEN','');loc=os.getenv('SQUARE_LOCATION_ID','')
        if not token or not loc:raise HTTPException(503,'Square is not configured.')
        with transaction(path) as db:
            inv,calc=invoice_current(db,iid);assigned(db,inv['job_id'],u)
            if inv['status']!='Issued':raise HTTPException(409,'Finalize the invoice before requesting payment.')
            issued_doc=json.loads(one(db,'SELECT document_json FROM invoice_revisions WHERE invoice_id=? AND revision=?',(iid,inv['revision']))['document_json'])
            if os.getenv('SQUARE_ENV','sandbox')!='sandbox' and issued_doc.get('test_mode',True):raise HTTPException(409,'A test invoice cannot create a production card charge.')
            if pending_received(db,iid):raise HTTPException(409,'A payment is already recorded but unverified. Reconcile it before charging again.')
            if calc['balance_cents']<=0:raise HTTPException(409,'No outstanding balance.')
            previous=one(db,"SELECT * FROM payment_links WHERE invoice_id=? AND status IN ('Creating','Active','Unknown','Cancelling') ORDER BY created_at DESC LIMIT 1",(iid,))
            if previous:
                if previous['revision']==inv['revision'] and previous['amount_cents']==calc['balance_cents'] and previous['status']=='Active':return {'url':previous['url'],'already_created':True}
                raise HTTPException(409,'An existing checkout needs reconciliation or cancellation before creating another.')
            key=uid('CHECKOUT');insert(db,'payment_links',dict(id=key,invoice_id=iid,revision=inv['revision'],amount_cents=calc['balance_cents'],provider='square',created_at=now()))
        host='https://connect.squareupsandbox.com' if os.getenv('SQUARE_ENV','sandbox')=='sandbox' else 'https://connect.squareup.com'
        try:
            r=requests.post(host+'/v2/online-checkout/payment-links',headers={'Authorization':'Bearer '+token,'Square-Version':os.getenv('SQUARE_API_VERSION','2026-09-16')},json={'idempotency_key':key,'quick_pay':{'name':'Crown Movers invoice '+inv['number'],'price_money':{'amount':calc['balance_cents'],'currency':'CAD'},'location_id':loc},'checkout_options':{'allow_tipping':False}},timeout=30)
            r.raise_for_status();link=r.json()['payment_link']
            with transaction(path) as db:
                db.execute("UPDATE payment_links SET status='Active',url=?,order_id=?,link_id=? WHERE id=?",(link['url'],link['order_id'],link['id'],key));audit(db,u['id'],'Square checkout created; not payment confirmation','payment_links',key)
            return {'url':link['url'],'paid':False}
        except Exception:
            with transaction(path) as db:db.execute("UPDATE payment_links SET status='Unknown' WHERE id=?",(key,))
            raise HTTPException(502,'Square link creation was not confirmed. Reconcile in Square before retrying.')

    @app.post('/api/webhooks/square')
    async def square_callback(request:Request):
        key=os.getenv('SQUARE_WEBHOOK_SIGNATURE_KEY','');url=os.getenv('SQUARE_WEBHOOK_URL','')
        if not key or not url:raise HTTPException(503,'Payment webhook not configured.')
        body=await request.body()
        if len(body)>200000:raise HTTPException(413,'Payload too large.')
        wanted=base64.b64encode(hmac.new(key.encode(),url.encode()+body,hashlib.sha256).digest()).decode()
        if not hmac.compare_digest(wanted,request.headers.get('x-square-hmacsha256-signature','')):raise HTTPException(401,'Invalid signature.')
        try:event=json.loads(body);eid='square:'+event['event_id'];p=event.get('data',{}).get('object',{}).get('payment',{})
        except Exception:raise HTTPException(422,'Invalid payment event.')
        with transaction(path) as db:
            if one(db,'SELECT event_id FROM bridge_events WHERE event_id=?',(eid,)):return {'ok':True,'duplicate':True}
            result={'applied':False}
            link=one(db,'SELECT * FROM payment_links WHERE order_id=?',(p.get('order_id'),))
            if link and p.get('status')=='COMPLETED':
                money=p.get('amount_money',{})
                inv,calc=invoice_current(db,link['invoice_id']);j=one(db,'SELECT * FROM jobs WHERE id=?',(inv['job_id'],))
                if money.get('currency')!='CAD' or money.get('amount')!=link['amount_cents'] or p.get('location_id')!=os.getenv('SQUARE_LOCATION_ID'):
                    result={'applied':False,'reason':'Payment amount/currency/location mismatch'};add_task(db,j['lead_id'],'URGENT: reconcile Square payment mismatch',key=eid)
                elif not one(db,'SELECT id FROM payments WHERE reference=?',('Square:'+p['id'],)):
                    amt=money['amount'];pid=uid('PAY');insert(db,'payments',dict(id=pid,lead_id=j['lead_id'],amount_cents=amt,method='Credit card',purpose='Final payment',reference='Square:'+p['id'],verified=1,recorded_by='square-webhook',created_at=now()))
                    allocation=min(amt,max(calc['balance_cents'],0))
                    if allocation:insert(db,'invoice_payment_allocations',dict(id=uid('ALLOC'),invoice_id=inv['id'],payment_id=pid,amount_cents=allocation,allocated_by='square-webhook',created_at=now()))
                    if amt>allocation or inv['revision']!=link['revision']:add_task(db,j['lead_id'],'Reconcile payment against corrected invoice / unapplied credit',key=eid)
                    db.execute("UPDATE payment_links SET status='Paid' WHERE id=?",(link['id'],));db.execute('UPDATE job_invoices SET version=version+1,updated_at=? WHERE id=?',(now(),inv['id']))
                    db.execute("UPDATE outbound_messages SET status='Cancelled',error='Payment received; balance review required' WHERE lead_id=? AND category='Collection' AND status IN ('Draft','Approved')",(j['lead_id'],))
                    result={'applied':True,'payment_id':pid};audit(db,'square-webhook','Verified provider payment recorded','payments',pid,{'amount_cents':amt})
            elif not link and p.get('order_id'):
                from .deposits import record_deposit_callback
                result=record_deposit_callback(db,p,eid) or result
            elif event.get('type','').startswith(('refund','dispute')):
                # Do not guess reversal allocation. Record a finance exception.
                result={'applied':False,'reason':'Refund/dispute requires finance reconciliation'}
                add_task(db,None,'URGENT: Square refund/dispute needs finance reconciliation',key=eid)
            insert(db,'bridge_events',dict(event_id=eid,body_hash=hashlib.sha256(body).hexdigest(),kind='Square payment',result_json=dumps(result),created_at=now()))
        return {'ok':True,**result}
