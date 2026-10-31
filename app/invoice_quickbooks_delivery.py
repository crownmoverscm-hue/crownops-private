"""Assigned crew can send an issued, reconciled invoice through QuickBooks.

Accounting creation remains an office action. No email is sent by a worker.
"""
from contextlib import closing
import json
import os
import re
from fastapi import Depends, HTTPException
from .store import connect, transaction, one, rows, insert, audit, now, dumps
from .platform_common import quickbooks_environment, quickbooks_gate
from .invoice_payments import _payment_snapshot, _binding, quickbooks_payment_state, _amount, _cad_currency
from .estimate_delivery import digest, normalized_lines
from . import providers


def operation_key(iid,revision):return 'qbo:InvoiceEmail:'+iid+':'+str(revision)


def mailbox_record(db,iid,revision):
    return one(db,"SELECT * FROM outbound_messages WHERE event_key IN (?,?) AND status NOT IN ('Draft','Cancelled') ORDER BY created_at DESC LIMIT 1",
               ('invoice-delivery:'+iid+':'+str(revision),'invoice-mail:'+iid+':'+str(revision)))


def preview(path,iid,user):
    with closing(connect(path)) as db:
        snapshot=_payment_snapshot(db,iid,user);inv=snapshot['invoice']
        issued=one(db,'SELECT document_json FROM invoice_revisions WHERE invoice_id=? AND revision=?',(iid,inv['revision']))
        op=one(db,'SELECT * FROM provider_operations WHERE id=?',(operation_key(iid,inv['revision']),))
        lead=one(db,'SELECT * FROM leads WHERE id=?',(snapshot['job']['lead_id'],))
        prior=mailbox_record(db,iid,inv['revision'])
    if inv['status']!='Issued' or not issued:raise HTTPException(409,'Finalize the invoice before preparing email delivery.')
    if not snapshot['external'] or not snapshot['operation'] or snapshot['operation']['status']!='Completed':
        return {'state':'Not synchronized','can_send':False,'message':'An owner or manager must synchronize this issued invoice to QuickBooks first.'}
    doc=json.loads(issued['document_json']);env=quickbooks_environment()
    if env=='production' and doc.get('test_mode',True):raise HTTPException(409,'A test invoice cannot be emailed through production QuickBooks.')
    if lead['notifications_suppressed']:raise HTTPException(409,'Review and activate this client before email delivery.')
    recipient=str(doc['terms']['customer'].get('email') or lead['email']).strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',recipient):raise HTTPException(422,'Correct the approved invoice billing email first.')
    _,realm,_=providers._qbo_connection(path)
    export=json.loads(snapshot['operation'].get('result_json') or '{}')
    if export.get('realm_id') and (export['realm_id']!=realm or export.get('environment')!=env):
        raise HTTPException(409,'The invoice was exported to another QuickBooks company or environment.')
    if op:
        saved=json.loads(op['result_json'])
        if saved.get('environment')!=env or saved.get('realm_id')!=realm:raise HTTPException(409,'The QuickBooks company changed. Review the original company before delivery.')
    payment=quickbooks_payment_state(path,iid,user=user,reconcile=False)
    if not payment['verified'] or payment['status'] in ('Unavailable','Review required') or payment['local_balance_cents']!=payment['remote_balance_cents']:
        raise HTTPException(409,payment['reason'] or 'Reconcile this invoice and its receipts in QuickBooks before emailing.')
    eid=str(snapshot['external']['external_id'])
    if not eid.isdigit():raise HTTPException(409,'Review the linked QuickBooks invoice ID.')
    remote=providers.qbo_read(path,'invoice/'+eid)['Invoice']
    if remote.get('PrivateNote')!='CrownOps '+iid+' revision '+str(inv['revision']):
        raise HTTPException(409,'QuickBooks does not identify this CrownOps invoice revision. Management must reconcile the link.')
    if str(remote.get('Id'))!=eid or str(remote.get('CustomerRef',{}).get('value'))!=str(snapshot['customer']) or not _cad_currency(path,remote):
        raise HTTPException(409,'QuickBooks invoice identity differs. No email was sent.')
    if _amount(remote.get('TotalAmt'))!=snapshot['calculation']['total_cents'] or _amount(remote.get('Balance'))!=payment['local_balance_cents']:
        raise HTTPException(409,'QuickBooks total or balance changed. Refresh and reconcile first.')
    expected=providers.qbo_invoice_payload(doc,snapshot['customer'],providers.qbo_mapping(path,'service_item_id'),providers.qbo_mapping(path,'tax_code_id'),providers.qbo_mapping(path,'discount_account_id'))
    if normalized_lines(remote)!=normalized_lines(expected) or remote.get('DueDate','')!=expected.get('DueDate',''):
        raise HTTPException(409,'QuickBooks line items or due date differ from the issued invoice. Management must review the current invoice revision.')
    remote_hash=digest({k:remote.get(k) for k in ('Id','SyncToken','CustomerRef','CurrencyRef','Line','TotalAmt','Balance','TxnTaxDetail','BillEmail','CustomerMemo','DueDate','EmailStatus')})
    with closing(connect(path)) as db:
        current=_payment_snapshot(db,iid,user)
        if _binding(current)!=_binding(snapshot):raise HTTPException(409,'Invoice changed during the check. Reopen the preview.')
    state='Sent' if remote.get('EmailStatus')=='EmailSent' else 'Unknown' if op else 'Ready'
    if state=='Sent' and op and op['status'] in ('Unknown','Running'):
        with transaction(path) as db:
            db.execute("UPDATE provider_operations SET status='Completed',error=NULL,updated_at=? WHERE id=? AND status IN ('Unknown','Running')",(now(),op['id']))
            audit(db,user['id'],'QuickBooks invoice email status reconciled; no resend','job_invoices',iid)
    if op and op['status']=='Completed' and state!='Sent':state='Review required'
    allowed=os.getenv('CROWNOPS_ALLOW_QBO_INVOICE_DELIVERY')=='1'
    if prior:state='Already using CrownOps email'
    return {'state':state,'can_send':state=='Ready' and allowed,'invoice_id':iid,'revision':inv['revision'],'version':inv['version'],
        'external_id':eid,'recipient':recipient,'environment':env,'realm_id':realm,'binding':digest(_binding(snapshot)),
        'review_hash':digest({'remote':remote_hash,'version':inv['version'],'recipient':recipient,'realm':realm,'environment':env}),
        'total_cents':payment['total_cents'],'balance_cents':payment['local_balance_cents'],'due_date':remote.get('DueDate'),
        'memo':remote.get('CustomerMemo',{}).get('value',''),'sent_at':remote.get('DeliveryInfo',{}).get('DeliveryTime'),
        'lines':[{'description':x.get('Description','') or ('Discount' if x.get('DetailType')=='DiscountLineDetail' else ''),
                  'amount_cents':_amount(x.get('Amount',0))*(-1 if x.get('DetailType')=='DiscountLineDetail' else 1)}
                 for x in remote.get('Line',[]) if x.get('DetailType')!='SubTotalLineDetail'],
        'message':('QuickBooks invoice email is awaiting the sandbox walkthrough.' if not allowed else
                   'Review this invoice and recipient before sending. QuickBooks controls email branding.'),
        'inbox_delivery_verified':False}


def send(path,iid,data,user):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("send")
    if os.getenv('CROWNOPS_ALLOW_QBO_INVOICE_DELIVERY')!='1':raise HTTPException(503,'QuickBooks invoice email must pass the isolated walkthrough before activation.')
    quickbooks_gate()
    if data.get('confirm') is not True:raise HTTPException(422,'Confirm the reviewed invoice and billing recipient.')
    current=preview(path,iid,user)
    if current['state']=='Sent':return {'sent':True,'duplicate':True,'inbox_delivery_verified':False}
    if not current.get('can_send'):raise HTTPException(409,current.get('message') or 'An existing invoice delivery needs review.')
    if data.get('review_hash')!=current['review_hash'] or data.get('recipient')!=current['recipient']:
        raise HTTPException(409,'The invoice or recipient changed. Review the latest preview before sending.')
    if current['environment']=='sandbox' or os.getenv('CROWNOPS_ENV')!='production':
        allowed={v.strip().lower() for v in os.getenv('CROWNOPS_TEST_EMAIL_ALLOWLIST','').split(',') if v.strip()}
        if current['recipient'] not in allowed:raise HTTPException(403,'This test email address is not approved.')
    key=operation_key(iid,current['revision'])
    with transaction(path) as db:
        snapshot=_payment_snapshot(db,iid,user)
        if digest(_binding(snapshot))!=current['binding']:raise HTTPException(409,'The invoice changed. Reopen the preview.')
        if mailbox_record(db,iid,current['revision']):raise HTTPException(409,'A CrownOps invoice email is already approved, sent or unresolved.')
        if one(db,'SELECT id FROM provider_operations WHERE id=?',(key,)):raise HTTPException(409,'A QuickBooks send already exists. Check delivery status; do not resend.')
        insert(db,'provider_operations',dict(id=key,provider='quickbooks',object_id=iid,kind='InvoiceEmail',status='Running',
            result_json=dumps({k:current[k] for k in ('environment','realm_id','external_id','recipient','version','review_hash')}),created_at=now(),updated_at=now()))
        db.execute("UPDATE outbound_messages SET status='Cancelled',error='Invoice delivery selected through QuickBooks' WHERE event_key IN (?,?) AND status='Draft'",
                   ('invoice-delivery:'+iid+':'+str(current['revision']),'invoice-mail:'+iid+':'+str(current['revision'])))
    try:
        remote=providers.qbo_send_invoice(path,current['external_id'],current['recipient'])['Invoice']
        if str(remote.get('Id'))!=current['external_id'] or remote.get('EmailStatus')!='EmailSent':raise ValueError('Sending unconfirmed')
        with transaction(path) as db:
            db.execute("UPDATE provider_operations SET status='Completed',updated_at=? WHERE id=?",(now(),key))
            audit(db,user['id'],'QuickBooks confirmed invoice email send','job_invoices',iid,{'recipient':current['recipient'],'external_id':current['external_id']})
        return {'sent':True,'inbox_delivery_verified':False}
    except Exception:
        with transaction(path) as db:
            db.execute("UPDATE provider_operations SET status='Unknown',error='Email completion unconfirmed. Check QuickBooks before any retry.',updated_at=? WHERE id=?",(now(),key))
        raise HTTPException(502,'QuickBooks email completion is unknown. Check delivery status; do not resend.')


def register_invoice_quickbooks_delivery(app,path,auth):
    @app.get('/api/invoices/{iid}/quickbooks-email')
    def get_preview(iid:str,u=Depends(auth)):
        try:return preview(path,iid,u)
        except (ValueError,KeyError,TypeError,ArithmeticError):raise HTTPException(409,'QuickBooks returned incomplete invoice data. Management must reconcile it before sending.')

    @app.post('/api/invoices/{iid}/quickbooks-email/send')
    def send_route(iid:str,data:dict,u=Depends(auth)):
        try:return send(path,iid,data,u)
        except (ValueError,KeyError,TypeError,ArithmeticError):raise HTTPException(409,'QuickBooks returned incomplete invoice data. No new email was confirmed; review delivery status.')
