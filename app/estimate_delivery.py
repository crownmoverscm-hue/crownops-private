"""Reviewed QuickBooks estimate delivery and read-only provider status polling.

No worker sends mail. Acceptance creates only a tentative, unassigned service.
Provider writes stay disabled until the isolated sandbox walkthrough is approved.
"""
from contextlib import closing
from decimal import Decimal
import hashlib
import json
import os
import re
from fastapi import Depends, HTTPException
from .store import connect, transaction, one, rows, insert, audit, now, dumps
from .platform_common import SALES, require_role, settings, add_task, quickbooks_environment, quickbooks_gate
from .client_communications import quote_scope_current, pending_scope_changes
from .quote_options import expired_quote
from .providers import qbo_read, qbo_call, qbo_mapping, qbo_customer, _qbo_connection


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def enabled():
    if os.getenv('CROWNOPS_ALLOW_QBO_ESTIMATE_DELIVERY') != '1':
        raise HTTPException(503, 'QuickBooks estimate delivery is awaiting the sandbox email and acceptance test. An owner must enable this connection before use.')


def source(db, qid, allow_accepted=False):
    q = one(db, 'SELECT * FROM quotes WHERE id=?', (qid,))
    if not q: raise HTTPException(404, 'Estimate not found.')
    lead = one(db, 'SELECT * FROM leads WHERE id=?', (q['lead_id'],))
    if q['status'] not in (('Draft', 'Approved', 'Accepted') if allow_accepted else ('Draft', 'Approved')):
        raise HTTPException(409, 'Use a current draft or approved estimate.')
    if not quote_scope_current(db, q, lead) or pending_scope_changes(db, lead['id']):
        raise HTTPException(409, 'Review the changed client information and save a current estimate first.')
    if one(db, "SELECT id FROM quotes WHERE lead_id=? AND revision>? AND status IN ('Approved','Accepted')", (lead['id'], q['revision'])):
        raise HTTPException(409, 'A newer approved estimate exists. Review that revision instead.')
    terms = settings(db, 'client_terms', {}) or {}
    if not terms.get('approved') or not terms.get('version') or not terms.get('text'):
        raise HTTPException(409, 'An owner or manager must approve the client booking terms first.')
    fingerprint = digest({'quote': {k: q[k] for k in ('id','revision','inputs_json','result_json','lead_snapshot_json')},
                          'lead': {k: lead[k] for k in ('email','client_name','phone','pickup','dropoff','move_date')},
                          'terms': {k: terms[k] for k in ('version','text')}})
    return q, lead, terms, fingerprint


def delivery_source(db, qid):
    q, lead, terms, fingerprint = source(db, qid)
    if expired_quote(q): raise HTTPException(409, 'This estimate has expired. Prepare a new revision.')
    inp = json.loads(q['inputs_json'])
    if not inp.get('expiry_date'): raise HTTPException(422, 'Add an expiration date to this estimate before sending through QuickBooks.')
    if not all(str(lead.get(k) or '').strip() for k in ('client_name','phone','move_date')):
        raise HTTPException(422,'Complete the client name, phone and requested service date first.')
    if inp.get('service_type','Moving') in ('Moving','Long Distance') and (not lead['pickup'] or not lead['dropoff']):
        raise HTTPException(422,'Complete pickup and destination addresses first.')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', lead['email'] or ''):
        raise HTTPException(422, 'Add a valid client email first.')
    if lead['notifications_suppressed']: raise HTTPException(409, 'Review and activate this client project before delivery.')
    from .availability import assert_day_open
    assert_day_open(db, inp.get('move_date') or lead.get('move_date'))
    message = one(db, 'SELECT status FROM outbound_messages WHERE event_key=?', ('quote-delivery:'+qid+':'+str(q['revision']),))
    if message and message['status'] not in ('Draft', 'Cancelled'):
        raise HTTPException(409, 'A CrownOps email is already approved, sent or unresolved. Reconcile that delivery before choosing another sender.')
    return q, lead, terms, fingerprint


def estimate_payload(q, lead, terms, basis, item, tax, discount_account):
    if basis not in ('low', 'high'): raise HTTPException(422, 'Choose which end of the estimate range QuickBooks will display.')
    if not item or not tax: raise HTTPException(503, 'Configure the reviewed QuickBooks service item and tax code.')
    inp = json.loads(q['inputs_json']); result = json.loads(q['result_json'])
    if not result.get('line_items'):
        raise HTTPException(409, 'Create a new quote revision in the current line-item editor before using QuickBooks delivery.')
    lines = []
    for index, original in enumerate(result['line_items']):
        line = dict(original)
        if index == 0 and result.get('hourly_rate_cents'):
            line.update(qty=result['service_'+basis], total_cents=result['service_'+basis+'_cents'])
        amount = line['total_cents']
        if amount < 0:
            if not discount_account: raise HTTPException(503, 'Configure the reviewed QuickBooks discount account.')
            lines.append({'DetailType':'DiscountLineDetail','Amount':-amount/100,
                          'DiscountLineDetail':{'PercentBased':False,'DiscountAccountRef':{'value':discount_account}}})
        elif amount:
            lines.append({'DetailType':'SalesItemLineDetail','Amount':amount/100,
                          'Description':line['name'] + ('\n'+line['note'] if line.get('note') else ''),
                          'SalesItemLineDetail':{'ItemRef':{'value':item},'TaxCodeRef':{'value':tax},
                                                 'Qty':line['qty'],'UnitPrice':line['unit_cents']/100}})
    net = sum(int(round(x['Amount']*100)) * (-1 if x['DetailType']=='DiscountLineDetail' else 1) for x in lines)
    if net != result[basis+'_cents']: raise HTTPException(409, 'The line items do not match this estimate total. Review the quote.')
    from .sales_crm_v6 import quote_start_window
    _, window = quote_start_window(inp)
    fr = str(inp.get('language',lead.get('language',''))).lower().startswith('fr')
    billing = inp.get('billing') or {}
    template = inp.get('template_snapshot') or {}
    details = '\n'.join(filter(None, [inp.get('quote_title') or inp.get('service_type'),
        ('Date souhaitée / arrivée : ' if fr else 'Requested service / arrival: ') + str(inp.get('move_date') or lead.get('move_date'))+' / '+window+' (Montréal)',
        ('Départ : ' if fr else 'Pickup: ')+lead.get('pickup',''),
        ('Destination : ' if fr else 'Destination: ')+lead.get('dropoff',''),
        ('Facturation : ' if fr else 'Billing: ')+' · '.join(str(billing.get(k) or '') for k in ('name','company','address','email','phone')),
        inp.get('client_note'), inp.get('payment_terms'), template.get('liability'), template.get('fragile'),
        ('Conditions de réservation — ' if fr else 'Booking terms — ')+str(terms['version']), terms['text']]))
    # Description-only lines preserve full terms; never truncate a signed scope.
    for start in range(0,len(details),3500):
        lines.append({'DetailType':'DescriptionOnly','Description':details[start:start+3500]})
    memo = (f"Bonjour {lead['client_name']}, merci de considérer Crown Movers! Estimation avant taxes : {result['low_cents']/100:.2f}–{result['high_cents']/100:.2f} $ CA. Le total affiché utilise la borne {'haute' if basis=='high' else 'basse'}. Le temps réel sera facturé selon les conditions ci-dessous. Acceptez ce devis pour demander la réservation. La date et l’équipe restent à confirmer par Crown. Besoin d’aide? 514-606-4030." if fr else
            f"Hi {lead['client_name']}, thank you for considering Crown Movers! Estimated range before tax: CAD {result['low_cents']/100:.2f}–{result['high_cents']/100:.2f}. The displayed total uses the {basis} end. Actual service time is billed under the terms below. Accept this estimate to request booking. Crown will confirm your date and crew separately. Questions? 514-606-4030.")
    return {'CurrencyRef':{'value':'CAD'},'GlobalTaxCalculation':'TaxExcluded','ApplyTaxAfterDiscount':True,
            'TxnDate':now()[:10],'ExpirationDate':inp['expiry_date'],'BillEmail':{'Address':lead['email']},
            'Line':lines,'CustomerMemo':{'value':memo},'PrivateNote':'CrownOps '+q['id']+' revision '+str(q['revision']),
            'TxnStatus':'Pending','EmailStatus':'NotSet'}


def normalized_lines(entity):
    result=[]
    for line in entity.get('Line',[]):
        kind=line.get('DetailType')
        if kind == 'SubTotalLineDetail': continue
        detail=line.get(kind,{})
        result.append({'type':kind,'description':line.get('Description',''),
            'amount':str(Decimal(str(line.get('Amount',0))).normalize()),
            'qty':str(Decimal(str(detail.get('Qty',0))).normalize()),
            'unit':str(Decimal(str(detail.get('UnitPrice',0))).normalize()),
            'item':str((detail.get('ItemRef') or {}).get('value','')),
            'tax':str((detail.get('TaxCodeRef') or {}).get('value','')),
            'discount':str((detail.get('DiscountAccountRef') or {}).get('value','')),
            'percent':detail.get('PercentBased',False)})
    return result


def validate_remote(row, remote):
    expected=json.loads(row['payload_json'])
    if str(remote.get('Id')) != str(row['external_id']): raise HTTPException(409, 'QuickBooks returned a different estimate.')
    for key in ('CustomerRef','CurrencyRef'):
        if str(remote.get(key,{}).get('value')) != str(expected.get(key,{}).get('value')):
            raise HTTPException(409, 'QuickBooks customer or currency changed. Review before continuing.')
    if (normalized_lines(remote)!=normalized_lines(expected) or remote.get('ExpirationDate')!=expected['ExpirationDate'] or
        remote.get('CustomerMemo',{}).get('value')!=expected['CustomerMemo']['value'] or
        remote.get('BillEmail',{}).get('Address','').lower()!=row['recipient'].lower() or
        remote.get('GlobalTaxCalculation') != 'TaxExcluded'):
        raise HTTPException(409, 'QuickBooks contents differ from the reviewed CrownOps estimate. Review the changes before delivery or booking.')
    from .invoice_payments import _amount
    try:
        total=_amount(remote.get('TotalAmt'));tax=_amount(remote.get('TxnTaxDetail',{}).get('TotalTax',0))
        net=sum(_amount(x['Amount'])*(-1 if x['DetailType']=='DiscountLineDetail' else 1) for x in expected['Line'] if 'Amount' in x)
        if sum(_amount(line.get('Amount')) for line in remote.get('TxnTaxDetail',{}).get('TaxLine',[]))!=tax:
            raise ValueError('Incomplete tax breakdown')
    except ValueError:raise HTTPException(409,'QuickBooks amounts or tax breakdown are incomplete. Review before sending or booking.')
    if total-tax != net: raise HTTPException(409, 'QuickBooks total does not match the estimate plus tax.')
    return total,tax


def review_hash(remote):
    return digest({k:remote.get(k) for k in ('Id','SyncToken','TotalAmt','TxnTaxDetail','ExpirationDate','CustomerRef','CurrencyRef','Line','CustomerMemo','BillEmail','TxnStatus','EmailStatus')})


def same_company(path, row):
    _,realm,_ = _qbo_connection(path)
    if row['environment']!=quickbooks_environment() or row['realm_id']!=realm:
        raise HTTPException(409,'The connected QuickBooks company or environment changed. Reconnect the original company before continuing.')


def prepare(path, qid, basis, actor):
    enabled();quickbooks_gate()
    with transaction(path) as db:
        old=one(db,'SELECT * FROM qbo_estimate_deliveries WHERE quote_id=?',(qid,))
        if old: return {'prepared':old['state']=='Ready','state':old['state'],'external_id':old['external_id']}
        q,lead,terms,fingerprint=delivery_source(db,qid)
    body=estimate_payload(q,lead,terms,basis,qbo_mapping(path,'service_item_id'),qbo_mapping(path,'tax_code_id'),qbo_mapping(path,'discount_account_id'))
    _,realm,_=_qbo_connection(path)
    with transaction(path) as db:
        if delivery_source(db,qid)[3]!=fingerprint: raise HTTPException(409,'Estimate changed. Reopen it.')
        if one(db,'SELECT quote_id FROM qbo_estimate_deliveries WHERE quote_id=?',(qid,)): raise HTTPException(409,'Preparation is already in progress. Refresh its status.')
        # Older draft exports have no company/scope snapshot. Do not silently
        # replace or send them, or manufacture another provider record.
        if one(db,"SELECT object_id FROM external_records WHERE provider='quickbooks' AND kind='Estimate' AND object_id=?",(qid,)) or one(db,"SELECT id FROM provider_operations WHERE object_id=? AND kind='Estimate'",(qid,)):
            raise HTTPException(409,'This revision already has a legacy QuickBooks draft. Review/close that draft in QuickBooks, then create a revised quote for the new delivery flow. Nothing was sent or duplicated.')
        insert(db,'qbo_estimate_deliveries',dict(quote_id=qid,environment=quickbooks_environment(),realm_id=realm,basis=basis,
            state='Preparing',source_hash=fingerprint,payload_json=dumps(body),recipient=lead['email'],created_by=actor,created_at=now()))
    try:
        body['CustomerRef']={'value':qbo_customer(path,lead)}
        customer=qbo_read(path,'customer/'+body['CustomerRef']['value'])['Customer']
        if customer.get('DisplayName') != lead['client_name'][:65]+' | '+lead['id'][-12:] or customer.get('PrimaryEmailAddr',{}).get('Address','').lower()!=lead['email'].lower():
            raise HTTPException(409,'Review the QuickBooks customer mapping before preparing this estimate.')
        remote=qbo_call(path,'POST','estimate',body,'estimate-delivery:'+qid)['Estimate']
        with transaction(path) as db:
            db.execute("UPDATE qbo_estimate_deliveries SET external_id=?,payload_json=?,remote_json=?,state='Ready' WHERE quote_id=?",(remote['Id'],dumps(body),dumps(remote),qid))
            insert(db,'external_records',dict(provider='quickbooks',kind='Estimate',object_id=qid,revision=q['revision'],external_id=remote['Id'],sync_token=remote.get('SyncToken','0'),amount_cents=json.loads(q['result_json'])[basis+'_cents'],updated_at=now()))
            audit(db,actor,'QuickBooks itemized estimate prepared; not sent','quotes',qid)
        return {'prepared':True,'state':'Ready','external_id':remote['Id']}
    except Exception:
        with transaction(path) as db:db.execute("UPDATE qbo_estimate_deliveries SET state='Unknown',error='Preparation was not confirmed. Check QuickBooks before retrying.' WHERE quote_id=?",(qid,))
        raise HTTPException(502,'QuickBooks preparation is unresolved. Check provider records before retrying; no email was requested.')


def preview(path, qid):
    with closing(connect(path)) as db:
        row=one(db,'SELECT * FROM qbo_estimate_deliveries WHERE quote_id=?',(qid,))
    if not row:return {'state':'Not prepared','enabled':os.getenv('CROWNOPS_ALLOW_QBO_ESTIMATE_DELIVERY')=='1'}
    if not row['external_id']: return {'state':row['state'],'error':row['error']}
    same_company(path,row)
    remote=qbo_read(path,'estimate/'+row['external_id'])['Estimate']
    total,tax=validate_remote(row,remote)
    # This preview exposes exactly what the provider stored, including tax lines.
    tax_lines=[]
    for line in remote.get('TxnTaxDetail',{}).get('TaxLine',[]):
        rate_id=str(line.get('TaxLineDetail',{}).get('TaxRateRef',{}).get('value',''))
        if not rate_id.isdigit():raise HTTPException(409,'Review the QuickBooks tax breakdown.')
        rate=qbo_read(path,'taxrate/'+rate_id)['TaxRate']
        tax_lines.append({'name':rate.get('Name','Tax'),'amount_cents':int(round(line['Amount']*100))})
    return {'state':row['state'],'provider_status':remote.get('TxnStatus'),'provider_email_status':remote.get('EmailStatus'),
        'external_id':row['external_id'],'recipient':row['recipient'],'basis':row['basis'],
        'subtotal_cents':total-tax,'tax_cents':tax,'taxes':tax_lines,'total_cents':total,
        'expiry':remote.get('ExpirationDate'),'memo':remote.get('CustomerMemo',{}).get('value'),
        'lines':[{'description':x.get('Description','') or ('Discount' if x.get('DetailType')=='DiscountLineDetail' else ''),'amount_cents':int(round(x.get('Amount',0)*100))*(-1 if x.get('DetailType')=='DiscountLineDetail' else 1),
                  'quantity':x.get('SalesItemLineDetail',{}).get('Qty'),'unit_price':x.get('SalesItemLineDetail',{}).get('UnitPrice')}
                 for x in remote.get('Line',[]) if x.get('DetailType')!='SubTotalLineDetail'],
        'review_hash':review_hash(remote),'error':row['error'],'sent_at':row['sent_at'],'checked_at':row['checked_at']}


def send(path,qid,data,actor):
    enabled();quickbooks_gate()
    if data.get('confirm') is not True: raise HTTPException(422,'Review the recipient, tax and full estimate, then confirm sending.')
    with closing(connect(path)) as db:
        row=one(db,'SELECT * FROM qbo_estimate_deliveries WHERE quote_id=?',(qid,))
    if not row:raise HTTPException(409,'Prepare and review the QuickBooks estimate first.')
    if row['state'] in ('Sent','Accepted','Rejected'):return {'sent':bool(row['sent_at']),'duplicate':True,'state':row['state']}
    if row['state']!='Ready':raise HTTPException(409,'An existing provider operation needs review. Do not send again.')
    if data.get('recipient')!=row['recipient']:raise HTTPException(409,'The recipient changed. Reopen the preview.')
    allowlist={x.strip().lower() for x in os.getenv('CROWNOPS_TEST_EMAIL_ALLOWLIST','').split(',') if x.strip()}
    if (quickbooks_environment()=='sandbox' or os.getenv('CROWNOPS_ENV')!='production') and row['recipient'].lower() not in allowlist:
        raise HTTPException(403,'This test recipient has not been approved. No email was sent.')
    same_company(path,row)
    remote=qbo_read(path,'estimate/'+row['external_id'])['Estimate'];validate_remote(row,remote)
    if remote.get('TxnStatus')!='Pending' or remote.get('EmailStatus') not in ('NotSet',None):
        raise HTTPException(409,'QuickBooks already sent or changed this estimate. Check its status instead of sending again.')
    if data.get('review_hash')!=review_hash(remote):raise HTTPException(409,'QuickBooks changed since preview. Review the latest amounts before sending.')
    with transaction(path) as db:
        q,lead,_,fingerprint=delivery_source(db,qid)
        if fingerprint!=row['source_hash']:raise HTTPException(409,'The quote or booking terms changed. Prepare a new revision.')
        changed=db.execute("UPDATE qbo_estimate_deliveries SET state='Sending',remote_json=? WHERE quote_id=? AND state='Ready'",(dumps(remote),qid)).rowcount
        if changed!=1:raise HTTPException(409,'Sending is already in progress. Refresh status.')
        db.execute("UPDATE outbound_messages SET status='Cancelled',error='Delivery selected through QuickBooks' WHERE event_key=? AND status='Draft'",('quote-delivery:'+qid+':'+str(q['revision']),))
        db.execute("UPDATE quotes SET status='Approved',approved_by=?,approved_at=? WHERE id=? AND status='Draft'",(actor,now(),qid))
    try:
        from .providers import qbo_send_estimate
        result=qbo_send_estimate(path,row['external_id'],row['recipient'])['Estimate']
        if str(result.get('Id'))!=row['external_id'] or result.get('EmailStatus')!='EmailSent':raise ValueError('Unconfirmed send')
        with transaction(path) as db:
            db.execute("UPDATE qbo_estimate_deliveries SET state=CASE WHEN state IN ('Accepted','Rejected') THEN state ELSE 'Sent' END,sent_at=?,checked_at=?,error='' WHERE quote_id=?",(now(),now(),qid))
            db.execute("UPDATE leads SET stage='Quote Sent',updated_at=? WHERE id=? AND stage NOT IN ('Accepted','Booked','Closed','Lost')",(now(),lead['id']))
            audit(db,actor,'QuickBooks confirmed estimate email send','quotes',qid,{'recipient':row['recipient'],'external_id':row['external_id']})
        return {'sent':True,'state':'Sent','inbox_delivery_verified':False}
    except Exception:
        with transaction(path) as db:db.execute("UPDATE qbo_estimate_deliveries SET state='Unknown',error='Email completion is unknown. Check QuickBooks status before any retry.' WHERE quote_id=?",(qid,))
        raise HTTPException(502,'QuickBooks did not confirm sending. Check status; do not send a duplicate email.')


def synchronize(path,qid,actor='quickbooks-sync'):
    with closing(connect(path)) as db:row=one(db,'SELECT * FROM qbo_estimate_deliveries WHERE quote_id=?',(qid,))
    if not row or not row['external_id']:raise HTTPException(409,'No linked QuickBooks estimate is ready to check.')
    try:
        same_company(path,row)
        remote=qbo_read(path,'estimate/'+row['external_id'])['Estimate'];validate_remote(row,remote)
        before=json.loads(row['remote_json'])
        if row['state'] not in ('Ready','Preparing') and (remote.get('TotalAmt')!=before.get('TotalAmt') or remote.get('TxnTaxDetail')!=before.get('TxnTaxDetail')):
            raise HTTPException(409,'QuickBooks tax or total changed after delivery. Review before booking.')
        with transaction(path) as db:
            q,lead,_,fingerprint=source(db,qid,allow_accepted=True)
            if fingerprint!=row['source_hash']:raise HTTPException(409,'Client details or booking terms changed. Review before booking.')
            status=remote.get('TxnStatus','Pending');job=None;problem=''
            state=row['state']
            if status=='Accepted':
                if q['status']!='Accepted' and expired_quote(q):raise HTTPException(409,'QuickBooks reports acceptance of an expired estimate. Review the acceptance date and renew the quote before booking.')
                if q['status']!='Accepted':
                    ref=dumps({'provider':'quickbooks','external_id':row['external_id'],'accepted_by':remote.get('AcceptedBy'),
                               'provider_accepted_date':remote.get('AcceptedDate'),'observed_at':now(),'source':'Provider status; may include staff-recorded acceptance'})
                    db.execute("UPDATE quotes SET status='Accepted',acceptance_ref=?,accepted_at=? WHERE id=?",(ref,now(),qid))
                    db.execute("UPDATE leads SET stage='Accepted',updated_at=? WHERE id=? AND stage NOT IN ('Booked','Completed','Closed')",(now(),lead['id']))
                from .sales_crm_v6 import ensure_tentative_job
                try:
                    other=one(db,"SELECT id FROM jobs WHERE lead_id=? AND (quote_id IS NULL OR quote_id!=?) AND status!='Cancelled'",(lead['id'],qid))
                    if other:raise HTTPException(409,'This service already has a job linked to another estimate. Review that job before replacing its terms.')
                    job=ensure_tentative_job(db,q,lead,actor)
                except HTTPException as error:
                    if error.status_code!=409:raise
                    problem='Accepted — scheduling review required: '+str(error.detail)
                if not job and not problem:problem='Accepted — add the requested service date before scheduling.'
                add_task(db,lead['id'],'Accepted QuickBooks estimate — review date, deposit and assign crew/truck',lead['assigned_to'],'accepted:'+qid)
                state='Accepted'
                if job and not problem:
                    from .client_communications import prepare_deposit_request
                    prepare_deposit_request(db,qid,actor)
                if row['state']!='Accepted':audit(db,actor,'QuickBooks acceptance synchronized; booking not confirmed','quotes',qid,{'job_id':job['id'] if job else None})
            elif status=='Rejected':
                state='Rejected';problem='Client declined in QuickBooks. Review the client follow-up; existing jobs were not cancelled.'
                add_task(db,lead['id'],'QuickBooks estimate declined — review client follow-up',lead['assigned_to'],'qbo-declined:'+qid)
            elif status=='Closed':
                raise HTTPException(409,'QuickBooks estimate is closed or converted. Review the invoice and service; no automatic booking was made.')
            elif remote.get('EmailStatus')=='EmailSent':
                state='Sent'
                db.execute("UPDATE quotes SET status='Approved',approved_by=?,approved_at=? WHERE id=? AND status='Draft'",(actor,now(),qid))
            elif row['state'] in ('Sending','Unknown'):
                problem='Sending is unresolved. Provider status does not yet confirm email; do not resend.'
            db.execute('UPDATE qbo_estimate_deliveries SET state=?,error=?,checked_at=?,sent_at=CASE WHEN ? THEN COALESCE(sent_at,?) ELSE sent_at END WHERE quote_id=?',
                       (state,problem,now(),remote.get('EmailStatus')=='EmailSent',now(),qid))
            return {'state':state,'provider_status':status,'job_id':job['id'] if job else None,'booking_confirmed':False,'message':problem or ('Ready for scheduling; final confirmation is still required.' if state=='Accepted' else 'QuickBooks status checked.')}
    except Exception as error:
        problem=str(error.detail) if isinstance(error,HTTPException) else 'QuickBooks status could not be checked. Saved records remain unchanged.'
        with transaction(path) as db:
            db.execute('UPDATE qbo_estimate_deliveries SET error=?,checked_at=? WHERE quote_id=?',(problem[:600],now(),qid))
            lead=one(db,'SELECT lead_id FROM quotes WHERE id=?',(qid,))
            if lead and isinstance(error,HTTPException) and error.status_code==409:
                add_task(db,lead['lead_id'],'QuickBooks estimate needs reconciliation before booking','','qbo-review:'+qid)
        if isinstance(error,HTTPException):raise
        raise HTTPException(502,problem)


def poll_estimates(path):
    if os.getenv('CROWNOPS_ALLOW_QBO_ESTIMATE_DELIVERY')!='1':return
    with closing(connect(path)) as db:
        candidates=rows(db,"SELECT quote_id FROM qbo_estimate_deliveries WHERE external_id IS NOT NULL AND state IN ('Sent','Sending','Unknown') ORDER BY COALESCE(checked_at,created_at) LIMIT 25")
    for row in candidates:
        try:synchronize(path,row['quote_id'])
        except Exception:pass  # Per-record error is retained above; never retry a send.


def register_estimate_delivery(app,path,auth):
    @app.get('/api/quotes/{qid}/quickbooks-delivery')
    def get_preview(qid:str,u=Depends(auth)):
        require_role(u,SALES);return preview(path,qid)

    @app.post('/api/quotes/{qid}/quickbooks-delivery/prepare')
    def prepare_route(qid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES)
        if data.get('confirm') is not True:raise HTTPException(422,'Confirm preparation of an unsent QuickBooks estimate.')
        return prepare(path,qid,data.get('basis'),u['id'])

    @app.post('/api/quotes/{qid}/quickbooks-delivery/send')
    def send_route(qid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES);return send(path,qid,data,u['id'])

    @app.post('/api/quotes/{qid}/quickbooks-delivery/sync')
    def sync_route(qid:str,u=Depends(auth)):
        require_role(u,SALES);return synchronize(path,qid,u['id'])
