"""Explicitly authorized external adapters. Disabled by default; no credentials shipped.

Timeouts remain Unknown, not automatically retried for non-idempotent sending.
Provider interfaces have mocked contract tests, not live-account certification.
"""
from contextlib import closing
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from email.utils import formataddr
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
    from .core_v1 import deny_if_enabled
    deny_if_enabled("token save")
    body=dict(body);body['expires_at']=time.time()+int(body.get('expires_in',3600))-60
    db.execute('INSERT INTO oauth_credentials(provider,ciphertext,updated_at) VALUES(?,?,?) ON CONFLICT(provider) DO UPDATE SET ciphertext=excluded.ciphertext,updated_at=excluded.updated_at',(provider,encrypt(body),now()))

def provider_token(path,provider,require_external=True):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("provider token")
    if require_external: external_gate()
    cfg=PROVIDERS[provider]
    with transaction(path) as db:
        r=one(db,'SELECT * FROM oauth_credentials WHERE provider=?',(provider,))
        if not r:raise HTTPException(503,provider+' is not connected.')
        body=decrypt(r['ciphertext'])
        if body.get('expires_at',0)>time.time():return body
        if not body.get('refresh_token'):
            raise HTTPException(409,'Reconnect '+provider+' once to allow automatic renewal.')
        params={'grant_type':'refresh_token','refresh_token':body['refresh_token']}
        cid=os.getenv(cfg['prefix']+'_CLIENT_ID','');cs=os.getenv(cfg['prefix']+'_CLIENT_SECRET','')
        kwargs={'auth':(cid,cs)} if provider=='quickbooks' else {}
        if provider=='gmail':params.update(client_id=cid,client_secret=cs)
        try:
            r=requests.post(cfg['token'],data=params,timeout=15,**kwargs)
            if r.status_code in (400,401):
                try:error=r.json().get('error')
                except ValueError:error=None
                if error=='invalid_grant':raise HTTPException(409,provider+' requires permission again. An owner can reconnect it in Connections.')
            r.raise_for_status();new=r.json()
            if not new.get('access_token'):raise ValueError('Missing access token')
        except (requests.RequestException,ValueError):
            raise HTTPException(502,'Temporary '+provider+' connection problem. The connection is saved and CrownOps will retry automatically.')
        new['refresh_token']=new.get('refresh_token') or body.get('refresh_token');new['realm_id']=body.get('realm_id');token_save(db,provider,new)
        return new

def maintain_connections(path):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("maintain connections")
    """Renew existing grants without sending email or changing accounting records."""
    for provider in PROVIDERS:
        with closing(connect(path)) as db:
            if not one(db,'SELECT provider FROM oauth_credentials WHERE provider=?',(provider,)):continue
            previous=settings(db,'connection_health_'+provider,{}) or {}
        if previous.get('state')=='reconnect_required':continue
        try:
            provider_token(path,provider,require_external=False)
            health={'state':'connected','checked_at':now(),'message':'Connection renews automatically.'}
        except HTTPException as error:
            health={'state':'reconnect_required' if error.status_code==409 else 'retrying','checked_at':now(),
                    'message':'Permission must be renewed by an owner.' if error.status_code==409 else 'Temporary problem. Retrying automatically; saved connection retained.'}
        except Exception:
            health={'state':'retrying','checked_at':now(),'message':'Connection check could not finish. Retrying automatically.'}
        with transaction(path) as db:
            if one(db,'SELECT provider FROM oauth_credentials WHERE provider=?',(provider,)):
                put_setting(db,'connection_health_'+provider,health)

def _qbo_connection(path):
    t=provider_token(path,'quickbooks',require_external=False);realm=t.get('realm_id')
    if not realm or not str(realm).isdigit():raise HTTPException(503,'QuickBooks company connection is incomplete.')
    host='https://sandbox-quickbooks.api.intuit.com' if quickbooks_environment()=='sandbox' else 'https://quickbooks.api.intuit.com'
    return t,str(realm),host

def qbo_call(path,method,resource,payload=None,key=None):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("qbo call")
    quickbooks_gate()
    t,realm,host=_qbo_connection(path)
    params={'minorversion':os.getenv('QBO_MINOR_VERSION','75')}
    if key:params['requestid']=hashlib.sha256(key.encode()).hexdigest()[:40]
    r=requests.request(method,host+'/v3/company/'+realm+'/'+resource,json=payload,params=params,headers={'Authorization':'Bearer '+t['access_token'],'Accept':'application/json'},timeout=30)
    if not r.ok:raise RuntimeError('QuickBooks rejected this request. Review the account, item/tax mapping and provider logs. HTTP '+str(r.status_code))
    return r.json()

def qbo_read(path,resource,params=None):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("qbo read")
    t,realm,host=_qbo_connection(path)
    q={'minorversion':os.getenv('QBO_MINOR_VERSION','75')}
    if params:q.update(params)
    r=requests.get(host+'/v3/company/'+realm+'/'+resource,params=q,headers={'Authorization':'Bearer '+t['access_token'],'Accept':'application/json'},timeout=30)
    if not r.ok:raise RuntimeError('QuickBooks read failed. Review the sandbox connection. HTTP '+str(r.status_code))
    return r.json()

def qbo_query(path,statement):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("qbo query")
    return qbo_read(path,'query',{'query':statement}).get('QueryResponse',{})

def qbo_send_estimate(path,estimate_id,recipient):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("qbo send estimate")
    """Intuit's explicit email endpoint; never automatically retry this operation."""
    quickbooks_gate()
    if not str(estimate_id).isdigit():raise ValueError('Invalid QuickBooks estimate ID.')
    t,realm,host=_qbo_connection(path)
    response=requests.post(host+'/v3/company/'+realm+'/estimate/'+str(estimate_id)+'/send',
        params={'minorversion':os.getenv('QBO_MINOR_VERSION','75'),'sendTo':recipient},data=b'',
        headers={'Authorization':'Bearer '+t['access_token'],'Accept':'application/json','Content-Type':'application/octet-stream'},timeout=30)
    if not response.ok:raise RuntimeError('QuickBooks did not confirm estimate email sending.')
    return response.json()

def qbo_send_invoice(path,invoice_id,recipient):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("qbo send invoice")
    """Send through Intuit once; delivery uncertainty is handled by the caller."""
    quickbooks_gate()
    if not str(invoice_id).isdigit():raise ValueError('Invalid QuickBooks invoice ID.')
    t,realm,host=_qbo_connection(path)
    response=requests.post(host+'/v3/company/'+realm+'/invoice/'+str(invoice_id)+'/send',
        params={'minorversion':os.getenv('QBO_MINOR_VERSION','75'),'sendTo':recipient},data=b'',
        headers={'Authorization':'Bearer '+t['access_token'],'Accept':'application/json','Content-Type':'application/octet-stream'},timeout=30)
    if not response.ok:raise RuntimeError('QuickBooks did not confirm invoice email sending.')
    return response.json()

def qbo_mapping(path,key):
    env_key={'service_item_id':'QBO_SERVICE_ITEM_ID','tax_code_id':'QBO_TAX_CODE_ID','discount_account_id':'QBO_DISCOUNT_ACCOUNT_ID','deposit_account_id':'QBO_DEPOSIT_ACCOUNT_ID'}[key]
    mapping_key='quickbooks_mappings_'+quickbooks_environment()
    with closing(connect(path)) as db:saved=settings(db,mapping_key,{}) or {}
    return str(saved.get(key) or os.getenv(env_key,'')).strip()

def qbo_customer(path,l):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("qbo customer")
    with closing(connect(path)) as db:old=one(db,"SELECT external_id FROM external_records WHERE provider='quickbooks' AND kind='Customer' AND object_id=? AND revision=0",(l['id'],))
    if old:return old['external_id']
    name=(l['client_name'][:65]+' | '+l['id'][-12:])
    body={'DisplayName':name}
    if (l.get('email') or '').strip():body['PrimaryEmailAddr']={'Address':l['email'].strip()}
    if (l.get('phone') or '').strip():body['PrimaryPhone']={'FreeFormNumber':l['phone'].strip()}
    r=qbo_call(path,'POST','customer',body,'customer:'+l['id'])['Customer']
    with transaction(path) as db:
        db.execute('INSERT OR IGNORE INTO external_records(provider,kind,object_id,external_id,updated_at) VALUES(?,?,?,?,?)',('quickbooks','Customer',l['id'],r['Id'],now()))
    return r['Id']

def qbo_invoice_payload(doc,customer_id,item_id,tax_id,discount_account,company=None):
    from .company_profile import profile_or_legacy
    c=profile_or_legacy(company)
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
    fr=str(doc.get('language','')).lower().startswith('fr')
    memo=(f"Bonjour {t['customer'].get('name','')}, merci d’avoir choisi {c['name']}! Voici votre facture pour le service du {doc['service_date']}. Si vous avez déjà payé, ne payez pas une deuxième fois. Une question? {c['phone']}." if fr else
          f"Hi {t['customer'].get('name','')}, thank you for choosing {c['name']}! Here is your invoice for service on {doc['service_date']}. If you have already paid, please do not pay again. Questions? {c['phone']}.")
    if t.get('payment_terms'):memo+='\n'+t['payment_terms']
    if len(memo)>1000:raise ValueError('Shorten the invoice payment terms before exporting to QuickBooks; no terms were truncated.')
    payload={'CustomerRef':{'value':customer_id},'CurrencyRef':{'value':'CAD'},'GlobalTaxCalculation':'TaxExcluded','Line':lines,
       'BillEmail':{'Address':t['customer'].get('email','')},'TxnDate':doc['service_date'],'PrivateNote':'CrownOps '+doc['id']+' revision '+str(doc['revision']),
       'CustomerMemo':{'value':memo},'ApplyTaxAfterDiscount':True,'EmailStatus':'NotSet'}
    if t.get('due_date'):payload['DueDate']=t['due_date']
    return payload

def send_gmail(path,message):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("send gmail")
    t=provider_token(path,'gmail')
    profile=requests.get('https://gmail.googleapis.com/gmail/v1/users/me/profile',headers={'Authorization':'Bearer '+t['access_token']},timeout=15)
    profile.raise_for_status();sender=profile.json().get('emailAddress','')
    if not isinstance(sender,str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',sender):raise ValueError('Gmail sender identity could not be confirmed.')
    from .company_profile import company_profile
    with closing(connect(path)) as db:identity=settings(db,'company_communications',{}) or {};company=company_profile(db)
    reply_to=sender
    if identity.get('approved') is True:
        if str(identity.get('connected_mailbox','')).lower()!=sender.lower():raise ValueError('The connected mailbox changed. Management must review company email rules before sending.')
        alias=identity.get('send_as','');reply=identity.get('reply_to','')
        if any(not isinstance(value,str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value) for value in (alias,reply)):raise ValueError('Review the company sender and reply email addresses before sending.')
        sender=alias;reply_to=reply
    m=EmailMessage();m['From']=formataddr((company['name'],sender));m['Reply-To']=reply_to;m['To']=message['recipient'];m['Subject']=message['subject'];m['Message-ID']='<'+message['id'].lower()+'@crownops.local>';m.set_content(message['body'])
    from .client_communications import email_html
    m.add_alternative(email_html(message['subject'],message['body'],company),subtype='html')
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
    from .core_v1 import deny_if_enabled
    deny_if_enabled("process mail")
    external_gate()
    # Network reads and optional local receipt reconciliation happen before the
    # write lock. The final transaction rechecks the exact invoice version.
    collection_check=None
    invoice_check=None
    with closing(connect(path)) as db:
        preview=one(db,'SELECT * FROM outbound_messages WHERE id=?',(mid,))
        if preview and preview['status']=='Approved' and preview['category']=='Collection':
            marker=(preview.get('event_key') or '').split(':')
            if len(marker)==4 and marker[0]=='balance':
                try:collection_target=(marker[1],int(marker[3]))
                except ValueError:raise HTTPException(409,'Prepare a current invoice reminder before sending.')
            else:
                candidates=rows(db,"SELECT i.id FROM job_invoices i JOIN jobs j ON j.id=i.job_id WHERE j.lead_id=? AND i.status='Issued'"+(' AND j.id=?' if preview['job_id'] else ''),
                    (preview['lead_id'],preview['job_id']) if preview['job_id'] else (preview['lead_id'],))
                if len(candidates)!=1:raise HTTPException(409,'Choose one issued invoice and prepare its current balance reminder.')
                collection_target=(candidates[0]['id'],None)
    if preview and preview['status']=='Approved' and preview['category']=='Invoice':
        marker=(preview.get('event_key') or '').split(':')
        if len(marker)!=3 or marker[0] not in ('invoice-delivery','invoice-mail'):raise HTTPException(409,'Prepare the current invoice email before sending.')
        from .invoice_payments import quickbooks_payment_state
        invoice_check=quickbooks_payment_state(path,marker[1],reconcile=True,actor=preview['approved_by'])
        if invoice_check.get('provider_email_status')=='EmailSent':raise HTTPException(409,'QuickBooks already reports this invoice emailed. Review its delivery before sending another copy.')
        if invoice_check['mapped'] and (invoice_check['status'] in ('Review required','Unavailable') or invoice_check['local_balance_cents']!=invoice_check['remote_balance_cents']):
            raise HTTPException(409,invoice_check['reason'] or 'Reconcile the current payment status before sending this invoice.')
    if preview and preview['status']=='Approved' and preview['category']=='Collection':
        from .invoice_payments import collection_payment_check
        collection_check=collection_payment_check(path,*collection_target)
        if not collection_check['can_collect']:
            reason=collection_check['reason'] or 'No unpaid balance is confirmed. Review the invoice before following up.'
            with transaction(path) as db:
                db.execute("UPDATE outbound_messages SET status='Cancelled',error=? WHERE id=? AND status='Approved'",(reason,mid))
            return {'sent':False,'reason':reason,'payment_status':collection_check['status']}
    with transaction(path) as db:
        m=one(db,'SELECT * FROM outbound_messages WHERE id=?',(mid,))
        if not m or m['status']!='Approved':raise HTTPException(409,'Only a reviewed and approved message can be sent.')
        l=one(db,'SELECT * FROM leads WHERE id=?',(m['lead_id'],))
        if l['notifications_suppressed']:raise HTTPException(409,'Client communication remains suppressed. Review and activate this project first.')
        recipients={l['email'].strip().lower()}
        if m['category']=='Invoice':
            marker=(m.get('event_key') or '').split(':')
            if len(marker)==3 and one(db,'SELECT id FROM provider_operations WHERE id=?',('qbo:InvoiceEmail:'+marker[1]+':'+marker[2],)):
                raise HTTPException(409,'This invoice uses QuickBooks email delivery. Check its delivery status before another send.')
            if len(marker)!=3 or marker[0] not in ('invoice-delivery','invoice-mail'):raise HTTPException(409,'Prepare the current issued invoice email before sending.')
            inv=one(db,'SELECT i.* FROM job_invoices i JOIN jobs j ON j.id=i.job_id WHERE i.id=? AND j.lead_id=?',(marker[1],l['id']))
            if not inv or inv['status']!='Issued' or str(inv['revision'])!=marker[2]:raise HTTPException(409,'Invoice changed. Prepare the current issued invoice email.')
            if not invoice_check or inv['version']!=invoice_check['version'] or inv['revision']!=invoice_check['revision'] or (m['source_version'] and inv['version']!=m['source_version']):
                db.execute("UPDATE outbound_messages SET status='Cancelled',error='Invoice or payments changed; review the current invoice email' WHERE id=?",(mid,))
                return {'sent':False,'reason':'Invoice or payments changed; review and send the current invoice.'}
            issued=one(db,'SELECT document_json FROM invoice_revisions WHERE invoice_id=? AND revision=?',(inv['id'],inv['revision']))
            if not issued:raise HTTPException(409,'The issued invoice snapshot is unavailable.')
            billing=json.loads(issued['document_json']).get('terms',{}).get('customer',{}).get('email','')
            if isinstance(billing,str) and '@' in billing:recipients.add(billing.strip().lower())
        if m['category']=='Invoice access':
            marker=(m.get('event_key') or '').split(':')
            if len(marker)!=4 or marker[0]!='invoice-access':raise HTTPException(409,'Prepare a current private invoice access email.')
            inv=one(db,'SELECT i.* FROM job_invoices i JOIN jobs j ON j.id=i.job_id WHERE i.id=? AND j.lead_id=?',(marker[1],l['id']))
            if not inv or inv['status']!='Issued' or str(inv['revision'])!=marker[2]:raise HTTPException(409,'Invoice changed. Request a new private link.')
            issued=one(db,'SELECT document_json FROM invoice_revisions WHERE invoice_id=? AND revision=?',(inv['id'],inv['revision']))
            billing=json.loads(issued['document_json']).get('terms',{}).get('customer',{}).get('email','') if issued else ''
            if isinstance(billing,str) and '@' in billing:recipients.add(billing.strip().lower())
        if m['recipient'].strip().lower() not in recipients:raise HTTPException(409,'Client or approved billing email changed. Prepare a new message.')
        if m['event_key'].startswith('accepted-deposit:'):
            from .client_communications import deposit_request_current
            if not deposit_request_current(db,m):
                db.execute("UPDATE outbound_messages SET status='Cancelled',error='Deposit, booking or payment instructions changed; review a current request' WHERE id=?",(mid,))
                return {'sent':False,'reason':'Deposit request changed; stale message cancelled'}
        if m['event_key'].startswith('quote-delivery:'):
            _,qid,revision=m['event_key'].split(':')
            if one(db,'SELECT quote_id FROM qbo_estimate_deliveries WHERE quote_id=?',(qid,)):
                raise HTTPException(409,'This estimate uses QuickBooks delivery. Review its status before sending another email.')
            q=one(db,'SELECT * FROM quotes WHERE id=? AND lead_id=?',(qid,l['id']))
            from .client_communications import quote_scope_current,pending_scope_changes
            if not q or q['status'] not in ('Approved','Accepted') or q['revision']!=int(revision) or not quote_scope_current(db,q,l) or pending_scope_changes(db,l['id']):
                db.execute("UPDATE outbound_messages SET status='Cancelled',error='Estimate or client details changed; prepare a current message' WHERE id=?",(mid,))
                return {'sent':False,'reason':'Estimate needs review; stale delivery cancelled'}
        if (m.get('event_key') or '').startswith('booking-confirmed:'):
            from .client_communications import booking_confirmation_current
            if not booking_confirmation_current(db,m):
                db.execute("UPDATE outbound_messages SET status='Cancelled',error='Booking details changed; prepare the current confirmation' WHERE id=?",(mid,))
                return {'sent':False,'reason':'Booking details changed; stale confirmation cancelled'}
        elif m['category']!='Invoice' and m['job_id'] and m['source_version']:
            j=one(db,'SELECT * FROM jobs WHERE id=?',(m['job_id'],))
            if not j or j['version']!=m['source_version'] or j['status']=='Cancelled':
                db.execute("UPDATE outbound_messages SET status='Cancelled',error='Job changed; recreate this message' WHERE id=?",(mid,))
                return {'sent':False,'reason':'Job changed; stale message cancelled'}
        if m['category']=='Collection' and m['event_key'].startswith('balance:'):
            _,invoice_id,revision,balance=m['event_key'].split(':')
            invoice,calc=invoice_current(db,invoice_id)
            if invoice['status']!='Issued' or invoice['revision']!=int(revision) or calc['balance_cents']!=int(balance) or pending_received(db,invoice_id) or one(db,"SELECT id FROM cases WHERE lead_id=? AND status!='Resolved'",(m['lead_id'],)):
                db.execute("UPDATE outbound_messages SET status='Cancelled',error='Invoice/payment/dispute changed' WHERE id=?",(mid,));return {'sent':False,'reason':'Collection state changed; stale reminder cancelled'}
        if m['category']=='Collection':
            if not collection_check:raise HTTPException(409,'Check the current invoice balance before sending.')
            invoice,calc=invoice_current(db,collection_check['invoice_id'])
            if (invoice['version']!=collection_check['version'] or invoice['revision']!=collection_check['revision'] or
                    calc['balance_cents']!=collection_check['local_balance_cents'] or calc['balance_cents']<=0 or
                    pending_received(db,invoice['id']) or one(db,"SELECT id FROM cases WHERE lead_id=? AND status!='Resolved'",(l['id'],))):
                db.execute("UPDATE outbound_messages SET status='Cancelled',error='Invoice or receipt changed after the payment check' WHERE id=?",(mid,))
                return {'sent':False,'reason':'Collection state changed; prepare a current reminder'}
        if os.getenv('CROWNOPS_ENV','test')!='production':
            allow={x.strip().lower() for x in os.getenv('CROWNOPS_TEST_EMAIL_ALLOWLIST','').split(',') if x.strip()}
            if m['recipient'].lower() not in allow:raise HTTPException(409,'Add this controlled test recipient to CROWNOPS_TEST_EMAIL_ALLOWLIST before sending.')
        db.execute("UPDATE outbound_messages SET status='Sending' WHERE id=?",(mid,))
        audit(db,m['approved_by'],'Email delivery attempt started','outbound_messages',mid,{'recipient':m['recipient']})
    try:
        pid=send_gmail(path,m)
        with transaction(path) as db:
            db.execute("UPDATE outbound_messages SET status='Sent',provider_id=?,sent_at=? WHERE id=?",(pid,now(),mid));audit(db,m['approved_by'],'Client email sent','outbound_messages',mid,{'provider_id':pid})
            if (m.get('event_key') or '').startswith('quote-delivery:'):
                db.execute("UPDATE leads SET stage='Quote Sent',updated_at=? WHERE id=? AND stage NOT IN ('Accepted','Booked','Closed','Lost')",(now(),m['lead_id']))
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
        env=quickbooks_environment()
        mappings={k:qbo_mapping(path,k) for k in ('service_item_id','tax_code_id','discount_account_id','deposit_account_id')}
        with closing(connect(path)) as db:
            reviewed=settings(db,'quickbooks_mappings_reviewed_'+env,{}) or {}
            health={key:settings(db,'connection_health_'+key,{}) or {} for key in PROVIDERS}
        reviewed_matches=bool(reviewed and reviewed.get('policy')=='quebec-normal-move-v1' and all(str(reviewed.get(k,'')).strip()==str(mappings.get(k,'')).strip() for k in ('service_item_id','tax_code_id','deposit_account_id')))
        mappings_complete=bool(reviewed_matches and mappings['service_item_id'] and mappings['tax_code_id'] and mappings['deposit_account_id'])
        from .quickbooks_status import account_status
        return {'external_actions_enabled':live_enabled(),'gmail':{'authorized_at':connected.get('gmail'),'configured':bool(os.getenv('GOOGLE_CLIENT_ID'))},
          'quickbooks':{'authorized_at':connected.get('quickbooks'),**account_status(path,mappings_complete)},
          'square':square_connection_status(),
          'ai':{'configured':bool(os.getenv('OPENAI_API_KEY') and os.getenv('OPENAI_MODEL'))},
          'intake':{'configured':len(os.getenv('CROWNOPS_WEBHOOK_SECRET',''))>=32},'scanner':{'configured':bool(os.getenv('CROWNOPS_SCAN_COMMAND'))},
          'connection_health':health,'operations':operations,'note':'Configuration/authorization does not certify a successful end-to-end live test.'}

    @app.get('/api/quickbooks/setup')
    def quickbooks_setup(u=Depends(auth)):
        require_role(u,MANAGERS)
        _,realm,_=_qbo_connection(path)
        errors=[]
        try:items=qbo_query(path,"select * from Item where Active = true maxresults 100").get('Item',[])
        except Exception:items=[];errors.append('items')
        try:taxes=qbo_query(path,"select * from TaxCode maxresults 100").get('TaxCode',[])
        except Exception:taxes=[];errors.append('tax_codes')
        try:accounts=qbo_query(path,"select * from Account where Active = true maxresults 100").get('Account',[])
        except Exception:accounts=[];errors.append('accounts')
        try:company=qbo_read(path,'companyinfo/'+realm).get('CompanyInfo',{})
        except Exception:company={};errors.append('company')
        with closing(connect(path)) as db:saved=settings(db,'quickbooks_mappings_'+quickbooks_environment(),{}) or {}
        current={
          'service_item_id':str(saved.get('service_item_id') or os.getenv('QBO_SERVICE_ITEM_ID','')),
          'tax_code_id':str(saved.get('tax_code_id') or os.getenv('QBO_TAX_CODE_ID','')),
          'discount_account_id':str(saved.get('discount_account_id') or os.getenv('QBO_DISCOUNT_ACCOUNT_ID','')),
          'deposit_account_id':str(saved.get('deposit_account_id') or os.getenv('QBO_DEPOSIT_ACCOUNT_ID',''))
        }
        item_rows=[{'id':str(x.get('Id','')),'name':x.get('FullyQualifiedName') or x.get('Name',''),'type':x.get('Type','')} for x in items if x.get('Id')]
        tax_rows=[{'id':str(x.get('Id','')),'name':x.get('Name',''),'description':x.get('Description','')} for x in taxes if x.get('Id')]
        account_rows=[{'id':str(x.get('Id','')),'name':x.get('FullyQualifiedName') or x.get('Name',''),'type':x.get('AccountType',''),'subtype':x.get('AccountSubType','')} for x in accounts if x.get('Id')]
        service_rows=[x for x in item_rows if str(x.get('type','')).lower()=='service']
        def pick(rows,*tests):
            for test in tests:
                for row in rows:
                    if test(row):return row.get('id','')
            return rows[0].get('id','') if rows else ''
        service_id=pick(service_rows,
          lambda x:'moving' in x['name'].lower(),
          lambda x:'hour' in x['name'].lower(),
          lambda x:'service' in x['name'].lower())
        bad_tax=('adjust','exempt','zero','out of scope','non-tax')
        def is_quebec_combo(x):
            label=(x.get('name','')+' '+x.get('description','')).lower()
            return (('gst' in label and 'qst' in label) or ('tps' in label and 'tvq' in label)) and not any(w in label for w in bad_tax)
        quebec_taxes=[x for x in tax_rows if is_quebec_combo(x)]
        tax_id=pick(quebec_taxes,
          lambda x:'gst/qst qc' in (x['name']+' '+x.get('description','')).lower(),
          lambda x:'tps/tvq' in (x['name']+' '+x.get('description','')).lower())
        deposit_id=pick(account_rows,
          lambda x:str(x.get('subtype','')).lower()=='undepositedfunds',
          lambda x:'undeposited' in x['name'].lower())
        discount_id=pick([x for x in account_rows if 'discount' in x['name'].lower()],
          lambda x:'discounts given' in x['name'].lower(),
          lambda x:True)
        return {'environment':quickbooks_environment(),'realm_id':realm,'company_name':company.get('CompanyName') or company.get('LegalName') or '',
          'writes_enabled':quickbooks_write_enabled(),'mappings':current,'errors':errors,
          'recommendations':{'service_item_id':service_id,'tax_code_id':tax_id,'discount_account_id':discount_id,'deposit_account_id':deposit_id},'quebec_tax_ready':bool(quebec_taxes),'quebec_tax_codes':quebec_taxes,
          'items':item_rows,'tax_codes':tax_rows,'accounts':account_rows}

    @app.patch('/api/quickbooks/setup')
    def quickbooks_save_setup(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        env=quickbooks_environment()
        if env=='production' and quickbooks_write_enabled():
            raise HTTPException(409,'Disable production QuickBooks writes before changing accounting mappings.')
        current=quickbooks_setup(u)
        valid_items={x['id'] for x in current['items']};valid_taxes={x['id'] for x in current['tax_codes']};valid_accounts={x['id'] for x in current['accounts']}
        valid_quebec_taxes={x['id'] for x in current.get('quebec_tax_codes',[])}
        mapped={'service_item_id':str(data.get('service_item_id','')).strip(),'tax_code_id':str(data.get('tax_code_id','')).strip(),
          'discount_account_id':str(data.get('discount_account_id','')).strip(),'deposit_account_id':str(data.get('deposit_account_id','')).strip()}
        label='QuickBooks '+env
        if mapped['service_item_id'] not in valid_items:raise HTTPException(422,'Choose a service item from the connected '+label+' company.')
        if mapped['tax_code_id'] not in valid_taxes:raise HTTPException(422,'Choose a tax code from the connected '+label+' company.')
        if mapped['tax_code_id'] not in valid_quebec_taxes:raise HTTPException(422,'For a normal Quebec move, choose the combined GST/QST Quebec tax code. Set up Quebec sales tax in QuickBooks first if it is missing.')
        if mapped['deposit_account_id'] not in valid_accounts:raise HTTPException(422,'Choose a deposit account from the connected '+label+' company.')
        if mapped['discount_account_id'] and mapped['discount_account_id'] not in valid_accounts:raise HTTPException(422,'Choose a discount account from the connected '+label+' company.')
        setting_key='quickbooks_mappings_'+env
        reviewed_key='quickbooks_mappings_reviewed_'+env
        reviewed={'policy':'quebec-normal-move-v1','service_item_id':mapped['service_item_id'],'tax_code_id':mapped['tax_code_id'],'deposit_account_id':mapped['deposit_account_id'],'reviewed_at':now(),'reviewed_by':u['id']}
        with transaction(path) as db:
            put_setting(db,setting_key,mapped);put_setting(db,reviewed_key,reviewed);audit(db,u['id'],'QuickBooks '+env+' mappings saved','settings',setting_key,mapped)
        return {'ok':True,'environment':env,'mappings':mapped,'writes_enabled':quickbooks_write_enabled(),'reviewed':True}

    @app.get('/api/quickbooks/test-connection')
    def quickbooks_test_connection(u=Depends(auth)):
        require_role(u,OFFICE);env=quickbooks_environment();realm=''
        try:
            _,realm,_=_qbo_connection(path)
            info=qbo_read(path,'companyinfo/'+realm).get('CompanyInfo',{})
            if not info.get('Id') or not (info.get('CompanyName') or info.get('LegalName')):
                raise ValueError('Company identity was not confirmed.')
            result={'ok':True,'environment':env,'realm_id':realm,'company_name':info.get('CompanyName') or info.get('LegalName'),
                    'writes_enabled':quickbooks_write_enabled(),'checked_at':now(),'message':'Company account read successfully. No email, invoice or payment was created.'}
        except Exception:
            result={'ok':False,'environment':env,'realm_id':realm,'company_name':'','checked_at':now(),
                    'message':'QuickBooks could not confirm the company account. Check the connection; saved authorization has been retained.'}
        with transaction(path) as db:put_setting(db,'quickbooks_account_check_'+env,result)
        return result

    @app.get('/api/gmail/review')
    def gmail_review(u=Depends(auth)):
        require_role(u,OFFICE)
        try:
            return gmail_sync.review_items(path)
        except Exception as e:
            raise HTTPException(500, 'Gmail review queue error: ' + type(e).__name__ + ': ' + str(e)[:180])

    @app.get('/api/gmail/test-readonly')
    def gmail_test_readonly(u=Depends(auth)):
        require_role(u,MANAGERS)
        t=provider_token(path,'gmail',require_external=False)
        try:
            profile=gmail_sync._api(t['access_token'],'profile')
            label_rows=gmail_sync._api(t['access_token'],'labels').get('labels') or []
            label_ids={x.get('name'):x.get('id') for x in label_rows if x.get('name') and x.get('id')}
            wanted={'quote':'New Quote Request','missed_call':'Missed Calls','voicemail':'Voice Mail'}
            feeds={};missing=[];content_verified=False
            for key,name in wanted.items():
                lid=label_ids.get(name)
                if not lid:
                    feeds[key]={'label':name,'found':False,'recent_14d':0};missing.append(name);continue
                listing=gmail_sync._api(t['access_token'],'messages',{'labelIds':lid,'q':'newer_than:14d','maxResults':1})
                sample=next((x.get('id') for x in listing.get('messages') or [] if x.get('id')),None)
                if sample:
                    gmail_sync._api(t['access_token'],'messages/'+quote(sample,safe=''),{'format':'full'})
                    content_verified=True
                feeds[key]={'label':name,'found':True,'recent_14d':int(listing.get('resultSizeEstimate') or 0),'message_content_verified':bool(sample)}
            return {'ok':not missing and content_verified,'message_content_verified':content_verified,'email':profile.get('emailAddress') or '',
              'messages_total':profile.get('messagesTotal'),'threads_total':profile.get('threadsTotal'),
              'feeds':feeds,'missing_labels':missing}
        except requests.RequestException as e:
            raise HTTPException(502,gmail_sync.gmail_read_failure(e)['error'])

    @app.post('/api/gmail/sync-readonly')
    def gmail_sync_readonly(data:dict=None,u=Depends(auth)):
        require_role(u,MANAGERS)
        if new_lead is None: raise HTTPException(503,'Lead intake is unavailable in this build.')
        t=provider_token(path,'gmail',require_external=False)
        data=data or {}
        try:
            return gmail_sync.sync_gmail_readonly(path,t['access_token'],u['id'],new_lead,data.get('days',14),data.get('max_per_feed',30),fluid=True)
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
            if ev['event_type'] in ('gmail_quote','gmail_missed_call','gmail_voicemail','gmail_email') and ev['status']=='Needs review':raise HTTPException(409,'Choose the correct client service, create a new inquiry, or dismiss this intake with a reason using its review action.')
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
            staff=one(db,"SELECT s.user_id FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1 AND u.role IN ('owner','manager')",(stamp_hash(raw),now()))
            if not staff or staff['user_id']!=s['user_id']:raise HTTPException(403,'Sign in as the manager who started the connection.')
            db.execute('UPDATE oauth_states SET used_at=? WHERE state_hash=?',(now(),stamp_hash(state)))
        cid=os.getenv(cfg['prefix']+'_CLIENT_ID','');cs=os.getenv(cfg['prefix']+'_CLIENT_SECRET','');body={'grant_type':'authorization_code','code':code,'redirect_uri':public_url+'/api/oauth/'+provider+'/callback'}
        kwargs={'auth':(cid,cs)} if provider=='quickbooks' else {}
        if provider=='gmail':body.update(client_id=cid,client_secret=cs)
        try:
            r=requests.post(cfg['token'],data=body,timeout=25,**kwargs);r.raise_for_status();tokens=r.json()
            if not tokens.get('access_token') or not tokens.get('refresh_token'):raise ValueError('Missing credentials')
            if provider=='quickbooks':tokens['realm_id']=request.query_params.get('realmId','')
            with transaction(path) as db:
                if not one(db,'SELECT state_hash FROM oauth_states WHERE state_hash=?',(stamp_hash(state),)):
                    raise ValueError('Connection request was cancelled')
                token_save(db,provider,tokens)
                put_setting(db,'connection_health_'+provider,{'state':'connected','checked_at':now(),'message':'Connection renews automatically.'})
                audit(db,s['user_id'],'Provider authorization completed','connections',provider)
        except Exception:raise HTTPException(502,'Connection exchange failed. Check provider configuration and reconnect. Tokens were not exposed.')
        return RedirectResponse('/#connections',status_code=303)

    @app.post('/api/disconnect/{provider}')
    def disconnect(provider:str,u=Depends(auth)):
        require_role(u,{'owner'})
        if provider not in PROVIDERS:raise HTTPException(422,'Unknown provider.')
        with transaction(path) as db:
            db.execute('DELETE FROM oauth_credentials WHERE provider=?',(provider,));audit(db,u['id'],'Local provider credentials removed','connections',provider)
            db.execute('DELETE FROM oauth_states WHERE provider=?',(provider,))
            put_setting(db,'connection_health_'+provider,{})
        return {'ok':True,'note':'Also revoke access in the provider account if required.'}

    @app.post('/api/outbound/{mid}/send')
    def send(mid:str,u=Depends(auth)):
        require_role(u,SALES)
        return process_mail(path,mid)

    @app.post('/api/invoices/{iid}/quickbooks')
    def sync_invoice(iid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','manager','accounting'});quickbooks_gate()
        if data.get('confirmed',data.get('confirm')) is not True:raise HTTPException(422,'Confirm the accounting write after reviewing mappings and totals.')
        # Capture company scope from the saved grant without an extra provider
        # request or a network call under the invoice writer lock.
        with closing(connect(path)) as db:
            grant=one(db,"SELECT ciphertext FROM oauth_credentials WHERE provider='quickbooks'")
        export_realm=str(decrypt(grant['ciphertext']).get('realm_id') or '') if grant else ''
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
            from .company_profile import company_profile
            with closing(connect(path)) as cdb:company=company_profile(cdb)
            cust=qbo_customer(path,l);payload=qbo_invoice_payload(doc,cust,qbo_mapping(path,'service_item_id'),qbo_mapping(path,'tax_code_id'),qbo_mapping(path,'discount_account_id'),company)
            if not old and j.get('quote_id'):
                with closing(connect(path)) as db:
                    estimate=one(db,'SELECT * FROM qbo_estimate_deliveries WHERE quote_id=?',(j['quote_id'],))
                if estimate:
                    if estimate['environment']!=quickbooks_environment() or estimate['realm_id']!=export_realm:
                        raise ValueError('The accepted estimate belongs to a different QuickBooks company. Reconcile before invoicing.')
                    remote_estimate=qbo_read(path,'estimate/'+estimate['external_id'])['Estimate']
                    from .estimate_delivery import validate_remote
                    validate_remote(estimate,remote_estimate)
                    if remote_estimate.get('TxnStatus')!='Accepted' or str(remote_estimate.get('CustomerRef',{}).get('value'))!=str(cust):
                        raise ValueError('The linked QuickBooks estimate must be accepted for the same customer before invoicing.')
                    payload['LinkedTxn']=[{'TxnId':estimate['external_id'],'TxnType':'Estimate'}]
            if old:
                existing=qbo_call(path,'GET','invoice/'+quote(old['external_id']))['Invoice']
                if existing['SyncToken']!=old['sync_token']:raise ValueError('QuickBooks was edited externally. Reconcile before overwriting.')
                payload.update(Id=old['external_id'],SyncToken=old['sync_token'],sparse=False)
                if existing.get('LinkedTxn'):payload['LinkedTxn']=existing['LinkedTxn']
            r=qbo_call(path,'POST','invoice',payload,key)['Invoice'];remote=cents(r['TotalAmt'])
            with transaction(path) as db:
                insert(db,'external_records',dict(provider='quickbooks',kind='Invoice',object_id=iid,revision=inv['revision'],external_id=r['Id'],sync_token=r['SyncToken'],amount_cents=remote,updated_at=now()))
                db.execute('UPDATE provider_operations SET status=?,result_json=?,updated_at=? WHERE id=?',('Completed' if remote==calc['total_cents'] else 'Mismatch',dumps({'external_id':r['Id'],'remote_total_cents':remote,'expected_total_cents':calc['total_cents'],'environment':quickbooks_environment(),'realm_id':export_realm}),now(),key))
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
            inv,calc=invoice_current(db,iid);invoice_job(db,inv['job_id'],u)
            from .company_profile import company_profile
            checkout_company=company_profile(db)['name']
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
            r=requests.post(host+'/v2/online-checkout/payment-links',headers={'Authorization':'Bearer '+token,'Square-Version':os.getenv('SQUARE_API_VERSION','2026-09-16')},json={'idempotency_key':key,'quick_pay':{'name':checkout_company+' invoice '+inv['number'],'price_money':{'amount':calc['balance_cents'],'currency':'CAD'},'location_id':loc},'checkout_options':{'allow_tipping':False}},timeout=30)
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
