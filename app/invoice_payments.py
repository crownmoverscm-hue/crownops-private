"""Read QuickBooks receipts and balances; never create a charge or accounting write.

Only linked Payment objects can become local receipts. A lower invoice Balance
alone is never treated as proof of a payment (credits and refunds need review).
"""
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re
from urllib.parse import urlsplit
from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from .store import connect, transaction, one, rows, insert, uid, now, dumps, audit
from .platform_common import invoice_current, invoice_job, pending_received, quickbooks_environment,require_role,MANAGERS,text,settings,put_setting


def _amount(value):
    if isinstance(value, bool):raise ValueError('Invalid accounting amount')
    try:
        number=Decimal(str(value))*100
        if not number.is_finite() or number<0 or number>1000000000000 or number!=number.to_integral_value():raise ValueError('Invalid accounting amount')
        return int(number)
    except (InvalidOperation,TypeError):raise ValueError('Invalid accounting amount')


def _remote_id(value):
    value=str(value or '')
    if not re.fullmatch(r'[0-9]{1,50}',value):raise ValueError('Invalid accounting reference')
    return value


def _payment_snapshot(db,iid,user=None):
    inv,calc=invoice_current(db,iid)
    job=invoice_job(db,inv['job_id'],user) if user else one(db,'SELECT * FROM jobs WHERE id=?',(inv['job_id'],))
    links=rows(db,"SELECT * FROM external_records WHERE provider='quickbooks' AND kind='Invoice' AND object_id=? ORDER BY revision DESC",(iid,))
    ext=next((r for r in links if r['revision']==inv['revision']),None)
    op=one(db,'SELECT * FROM provider_operations WHERE id=?',('qbo:invoice:'+iid+':'+str(inv['revision']),))
    customer=one(db,"SELECT external_id FROM external_records WHERE provider='quickbooks' AND kind='Customer' AND object_id=? AND revision=0",(job['lead_id'],))
    return {'invoice':inv,'calculation':calc,'job':job,'mapped':bool(links or op),'external':ext,'operation':op,
            'customer':customer['external_id'] if customer else None,'pending':pending_received(db,iid),
            'checkout':bool(one(db,"SELECT id FROM payment_links WHERE invoice_id=? AND status IN ('Creating','Active','Unknown','Cancelling')",(iid,)))}


def _binding(snapshot):
    inv=snapshot['invoice'];ext=snapshot['external'];op=snapshot['operation']
    return (inv['version'],inv['revision'],inv['status'],snapshot['job']['lead_id'],snapshot['customer'],
            ext and (ext['external_id'],ext['revision'],ext['amount_cents']),op and op['status'],snapshot['pending'],snapshot['checkout'],snapshot['calculation'])


def _cad_currency(path,entity,context=None):
    """Omitted CurrencyRef is valid only for a verified single-currency CAD company."""
    if 'CurrencyRef' in entity:return isinstance(entity['CurrencyRef'],dict) and entity['CurrencyRef'].get('value')=='CAD'
    from .providers import qbo_read
    context=context if context is not None else {}
    if 'currency_prefs' not in context:context['currency_prefs']=qbo_read(path,'preferences').get('Preferences',{}).get('CurrencyPrefs',{})
    prefs=context['currency_prefs']
    return prefs.get('MultiCurrencyEnabled') is False and prefs.get('HomeCurrency',{}).get('value')=='CAD'


def _receipt_evidence(path,remote,customer,currency_context=None):
    """Validate both sides of each invoice/payment link, including split amounts."""
    from .providers import qbo_read
    currency_context=currency_context if currency_context is not None else {}
    iid=_remote_id(remote['Id']);linked=remote.get('LinkedTxn') or []
    if not isinstance(linked,list) or len(linked)>50:raise ValueError('Too many linked accounting transactions; finance review required.')
    payment_ids=[]
    for link in linked:
        if not isinstance(link,dict) or link.get('TxnType')!='Payment':raise ValueError('Credit, refund or other accounting adjustment requires finance review.')
        pid=_remote_id(link.get('TxnId'))
        if pid not in payment_ids:payment_ids.append(pid)
    evidence=[]
    for pid in payment_ids:
        p=qbo_read(path,'payment/'+pid).get('Payment')
        if not isinstance(p,dict) or str(p.get('Id'))!=pid or str(p.get('CustomerRef',{}).get('value'))!=str(customer) or not _cad_currency(path,p,currency_context):
            raise ValueError('QuickBooks payment identity, customer or currency differs.')
        charge=p.get('CreditCardPayment') or {}
        response=charge.get('CreditChargeResponse') or {} if isinstance(charge,dict) else {}
        status=p.get('PaymentStatus') or p.get('TxnStatus') or response.get('Status')
        if status and str(status).lower() not in ('completed','paid','success','succeeded','processed'):
            raise ValueError('The QuickBooks payment is pending, declined or refunded. Finance must review it before crediting the invoice.')
        total=_amount(p['TotalAmt']);unapplied=_amount(p.get('UnappliedAmt',0));applied=0;all_applied=0
        if not total or unapplied>total:raise ValueError('Voided, refunded or invalid payment requires finance review.')
        day=date.fromisoformat(str(p['TxnDate'])).isoformat()
        lines=p.get('Line') or []
        if not isinstance(lines,list) or not lines:raise ValueError('The payment does not identify its invoice allocation.')
        for line in lines:
            amount=_amount(line['Amount']);targets=line.get('LinkedTxn') or []
            # A line containing several targets has no unambiguous per-invoice amount.
            if len(targets)!=1 or targets[0].get('TxnType')!='Invoice':raise ValueError('Split credit or payment adjustment requires finance review.')
            target=_remote_id(targets[0].get('TxnId'));all_applied+=amount
            if target==iid:applied+=amount
            elif amount:raise ValueError('A payment shared across service invoices requires finance review.')
        if not applied or all_applied+unapplied!=total:raise ValueError('QuickBooks payment allocations do not match its total.')
        evidence.append({'id':pid,'total_cents':total,'applied_cents':applied,'date':day,'sync_token':str(p.get('SyncToken',''))})
    if sum(p['applied_cents'] for p in evidence)!=_amount(remote['TotalAmt'])-_amount(remote['Balance']):
        raise ValueError('Invoice balance includes a credit, missing receipt or adjustment. Finance must reconcile it.')
    return evidence


def _import_receipts(db,snapshot,evidence,actor,apply=True):
    """Apply an all-or-nothing proven allocation plan, reusing exported receipts."""
    iid=snapshot['invoice']['id'];lid=snapshot['job']['lead_id'];plans=[];added=0
    for p in evidence:
        matches=rows(db,"SELECT DISTINCT object_id FROM external_records WHERE provider='quickbooks' AND kind='Payment' AND external_id=?",(p['id'],))
        if len(matches)>1:raise ValueError('Several local receipts match the same QuickBooks payment.')
        local=one(db,'SELECT * FROM payments WHERE id=?',(matches[0]['object_id'],)) if matches else None
        if matches and (not local or local['lead_id']!=lid or not local['verified'] or local['reverses_id'] or local['amount_cents']!=p['total_cents'] or one(db,'SELECT id FROM payments WHERE reverses_id=?',(local['id'],))):
            raise ValueError('The linked local receipt was changed or reversed; finance review required.')
        if not local:
            # An unmatched local receipt may be this very payment. Never guess by amount/name.
            unmatched=one(db,"""SELECT p.id FROM payments p WHERE p.lead_id=? AND p.amount_cents>0 AND p.reverses_id IS NULL
                AND NOT EXISTS(SELECT 1 FROM payments rev WHERE rev.reverses_id=p.id)
                AND NOT EXISTS(SELECT 1 FROM external_records e WHERE e.provider='quickbooks' AND e.kind='Payment' AND e.object_id=p.id)
                LIMIT 1""",(lid,))
            if unmatched:raise ValueError('Match the locally recorded receipt to QuickBooks before importing another receipt.')
        current=db.execute('SELECT coalesce(sum(amount_cents),0) FROM invoice_payment_allocations WHERE invoice_id=? AND payment_id=?',(iid,local['id'] if local else '')).fetchone()[0]
        if current>p['applied_cents']:raise ValueError('A payment was reduced or removed in QuickBooks; review the refund or reallocation.')
        used=db.execute('SELECT coalesce(sum(amount_cents),0) FROM invoice_payment_allocations WHERE payment_id=?',(local['id'] if local else '',)).fetchone()[0]
        delta=p['applied_cents']-current
        if used+delta>p['total_cents']:raise ValueError('The receipt has already been allocated to another invoice.')
        plans.append((p,local,delta));added+=delta
    expected=snapshot['calculation']['total_cents']-sum(p['applied_cents'] for p in evidence)
    if snapshot['calculation']['balance_cents']-added!=expected:
        raise ValueError('Local payments and QuickBooks payments differ. Review unmatched deposits, credits or refunds.')
    if not apply:return sum(1 for p,local,delta in plans if not local)
    changed=0
    for p,local,delta in plans:
        if not local:
            pid=uid('PAY')
            insert(db,'payments',dict(id=pid,lead_id=lid,amount_cents=p['total_cents'],method='QuickBooks',purpose='Final payment',reference='QuickBooks Payment '+p['id'],verified=1,recorded_by=actor,created_at=p['date']+'T12:00:00+00:00'))
            insert(db,'external_records',dict(provider='quickbooks',kind='Payment',object_id=pid,revision=1,external_id=p['id'],sync_token=p['sync_token'],amount_cents=p['total_cents'],updated_at=now()))
            # Export-payment uses this reservation key; importing must also prevent a second QBO posting.
            insert(db,'provider_operations',dict(id='qbo:Payment:'+pid+':1',provider='quickbooks',object_id=pid,kind='Payment',status='Completed',result_json=dumps({'external_id':p['id'],'imported':True,'money_moved':False}),created_at=now(),updated_at=now()))
            local={'id':pid};changed+=1
        if delta:
            insert(db,'invoice_payment_allocations',dict(id=uid('ALLOC'),invoice_id=iid,payment_id=local['id'],amount_cents=delta,allocated_by=actor,created_at=now()))
            audit(db,actor,'QuickBooks receipt reconciled without moving money','job_invoices',iid,{'payment_id':local['id'],'external_id':p['id'],'amount_cents':delta})
    if added:
        db.execute('UPDATE job_invoices SET version=version+1,updated_at=? WHERE id=?',(now(),iid))
        db.execute("UPDATE outbound_messages SET status='Cancelled',error='QuickBooks receipts reconciled; review the current balance' WHERE lead_id=? AND category='Collection' AND status IN ('Draft','Approved')",(lid,))
    return changed


def quickbooks_payment_state(path,iid,*,actor='quickbooks-reconciliation',reconcile=False,user=None,expected_version=None,expected_revision=None):
    """Fresh authoritative state; optional local receipt import makes no QBO writes.

    `verified` proves the remote invoice identity, not bank settlement. `can_collect`
    additionally requires matching local allocations and no unresolved receipts.
    No cached state is accepted for collection or checkout decisions.
    """
    from .providers import qbo_read
    with closing(connect(path)) as db:snapshot=_payment_snapshot(db,iid,user)
    inv=snapshot['invoice'];calc=snapshot['calculation']
    if (expected_version is not None and expected_version!=inv['version']) or (expected_revision is not None and expected_revision!=inv['revision']):
        raise HTTPException(409,'The invoice changed. Refresh before reconciling receipts.')
    state={'mapped':snapshot['mapped'],'status':'Not linked','verified':False,'can_collect':False,'invoice_id':iid,
           'revision':inv['revision'],'version':inv['version'],'total_cents':calc['total_cents'],
           'local_balance_cents':calc['balance_cents'],'remote_balance_cents':None,'checked_at':now(),'reason':'','reconciled_receipts':0}
    if not snapshot['mapped']:
        state['can_collect']=inv['status']=='Issued' and calc['balance_cents']>0 and not snapshot['pending'] and not snapshot['checkout']
        return state
    try:
        ext=snapshot['external'];op=snapshot['operation']
        if inv['status']!='Issued' or not ext or not op or op['status']!='Completed' or not snapshot['customer'] or ext['amount_cents']!=calc['total_cents']:
            raise ValueError('Synchronize and reconcile the current invoice revision in QuickBooks first.')
        remote_id=_remote_id(ext['external_id'])
        found=qbo_read(path,'query',{'query':"SELECT * FROM Invoice WHERE Id='"+remote_id+"'",'include':'invoiceLink'}).get('QueryResponse',{}).get('Invoice',[])
        if len(found)!=1:raise ValueError('The saved invoice was removed or is unavailable in QuickBooks.')
        remote=found[0];balance=_amount(remote['Balance']);currency_context={}
        if str(remote.get('Id'))!=remote_id or not _cad_currency(path,remote,currency_context) or str(remote.get('CustomerRef',{}).get('value'))!=str(snapshot['customer']) or _amount(remote['TotalAmt'])!=calc['total_cents'] or balance>calc['total_cents']:
            raise ValueError('QuickBooks invoice identity, customer, currency or total differs. Management must reconcile it.')
        state.update(verified=True,remote_balance_cents=balance,provider_email_status=remote.get('EmailStatus'),status='Paid' if balance==0 else 'Partial' if balance<calc['total_cents'] else 'Unpaid')
        if snapshot['pending'] or snapshot['checkout']:raise ValueError('Reconcile the recorded receipt or existing checkout before requesting another payment.')
        if balance>calc['balance_cents']:raise ValueError('QuickBooks shows a larger balance. Review an unposted receipt, refund or payment reversal.')
        evidence=_receipt_evidence(path,remote,snapshot['customer'],currency_context)
        if reconcile:
            with transaction(path) as db:
                current=_payment_snapshot(db,iid,user)
                if _binding(current)!=_binding(snapshot):raise ValueError('The invoice or its receipts changed during the check. Refresh before reconciling.')
                state['reconciled_receipts']=_import_receipts(db,current,evidence,actor)
                current,updated=invoice_current(db,iid)
                state.update(version=current['version'],local_balance_cents=updated['balance_cents'])
        elif balance!=calc['balance_cents']:
            raise ValueError('A QuickBooks payment needs reconciliation into this invoice. Refresh payment status before collecting.')
        else:
            with closing(connect(path)) as db:
                current=_payment_snapshot(db,iid,user)
                if _binding(current)!=_binding(snapshot):raise ValueError('The invoice changed during the check. Refresh before collecting.')
                _import_receipts(db,current,evidence,actor,apply=False)
        state['can_collect']=balance>0 and balance==state['local_balance_cents']
        return state
    except (ValueError,KeyError,TypeError,ArithmeticError) as ex:
        state.update(status='Review required',can_collect=False,reason=str(ex) if isinstance(ex,ValueError) else 'QuickBooks returned incomplete payment evidence. Management must reconcile it.')
    except Exception:
        state.update(status='Unavailable',can_collect=False,reason='QuickBooks payment status could not be checked. No payment reminder or new payment request is permitted until it can be verified.')
    return state


def collection_payment_check(path,iid,expected_balance_cents=None):
    state=quickbooks_payment_state(path,iid,reconcile=True,actor='quickbooks-collection-check')
    if expected_balance_cents is not None and state['local_balance_cents']!=expected_balance_cents:
        state.update(can_collect=False,reason='The balance changed. Prepare and review a new reminder.')
    return state


def match_quickbooks_receipt(path,iid,local_payment_id,remote_payment_id,*,user,note,version,revision):
    """Management binds one already-verified receipt to an actual QBO Payment.

    The accounting total, invoice allocation, project, customer, currency and
    current invoice version must all agree. Failure rolls back mapping and imports.
    """
    from .providers import qbo_read
    require_role(user,MANAGERS)
    try:remote_payment_id=_remote_id(remote_payment_id)
    except ValueError:raise HTTPException(422,'Enter a valid QuickBooks Payment ID.')
    local_payment_id=text(local_payment_id,'Local receipt',150);note=text(note,'Matching evidence',2000)
    if not note:raise HTTPException(422,'Explain how you verified this is the same receipt in both systems.')
    with closing(connect(path)) as db:snapshot=_payment_snapshot(db,iid,user)
    inv=snapshot['invoice'];ext=snapshot['external'];op=snapshot['operation']
    if type(version)is not int or type(revision)is not int or version!=inv['version'] or revision!=inv['revision']:
        raise HTTPException(409,'The invoice changed. Refresh before matching a receipt.')
    if not ext or not op or op['status']!='Completed' or inv['status']!='Issued' or not snapshot['customer'] or ext['amount_cents']!=snapshot['calculation']['total_cents']:
        raise HTTPException(409,'Synchronize and reconcile this issued invoice in QuickBooks first.')
    if snapshot['pending'] or snapshot['checkout']:raise HTTPException(409,'Verify pending receipts and close unresolved checkouts before matching.')
    try:
        remote_id=_remote_id(ext['external_id'])
        found=qbo_read(path,'query',{'query':"SELECT * FROM Invoice WHERE Id='"+remote_id+"'",'include':'invoiceLink'}).get('QueryResponse',{}).get('Invoice',[])
        if len(found)!=1:raise ValueError('The saved QuickBooks invoice is unavailable.')
        remote=found[0];currency_context={}
        if str(remote.get('Id'))!=remote_id or not _cad_currency(path,remote,currency_context) or str(remote.get('CustomerRef',{}).get('value'))!=str(snapshot['customer']) or _amount(remote['TotalAmt'])!=snapshot['calculation']['total_cents'] or _amount(remote['Balance'])>_amount(remote['TotalAmt']):
            raise ValueError('QuickBooks invoice identity, customer, currency or total differs.')
        evidence=_receipt_evidence(path,remote,snapshot['customer'],currency_context)
        payment=next((p for p in evidence if p['id']==remote_payment_id),None)
        if not payment:raise ValueError('That QuickBooks Payment is not linked to this exact invoice.')
        with transaction(path) as db:
            current=_payment_snapshot(db,iid,user)
            if _binding(current)!=_binding(snapshot):raise ValueError('The invoice changed during the check. Refresh before matching.')
            local=one(db,'SELECT * FROM payments WHERE id=?',(local_payment_id,))
            if not local or local['lead_id']!=current['job']['lead_id'] or not local['verified'] or local['reverses_id'] or local['amount_cents']!=payment['total_cents'] or one(db,'SELECT id FROM payments WHERE reverses_id=?',(local_payment_id,)):
                raise ValueError('Choose the unreversed, verified receipt from this project with the exact QuickBooks total.')
            mappings=rows(db,"SELECT object_id,external_id FROM external_records WHERE provider='quickbooks' AND kind='Payment' AND (object_id=? OR external_id=?)",(local_payment_id,remote_payment_id))
            if any(m['object_id']!=local_payment_id or m['external_id']!=remote_payment_id for m in mappings):raise ValueError('One of these receipts is already linked to another payment.')
            if not mappings:
                insert(db,'external_records',dict(provider='quickbooks',kind='Payment',object_id=local_payment_id,revision=1,external_id=remote_payment_id,sync_token=payment['sync_token'],amount_cents=payment['total_cents'],updated_at=now()))
            imported=_import_receipts(db,current,evidence,user['id'])
            key='qbo:Payment:'+local_payment_id+':1';result=dumps({'external_id':remote_payment_id,'matched_existing':True,'money_moved':False})
            db.execute("INSERT INTO provider_operations(id,provider,object_id,kind,status,result_json,created_at,updated_at) VALUES(?,?,?,?,'Completed',?,?,?) ON CONFLICT(id) DO UPDATE SET status='Completed',result_json=excluded.result_json,error=NULL,updated_at=excluded.updated_at",(key,'quickbooks',local_payment_id,'Payment',result,now(),now()))
            audit(db,user['id'],'Existing receipt matched to QuickBooks without moving money','payments',local_payment_id,{'external_id':remote_payment_id,'invoice_id':iid,'evidence':note})
            updated,calc=invoice_current(db,iid)
        return {'mapped':True,'verified':True,'can_collect':calc['balance_cents']>0,'status':'Paid' if calc['balance_cents']==0 else 'Partial',
                'invoice_id':iid,'revision':updated['revision'],'version':updated['version'],'total_cents':calc['total_cents'],
                'local_balance_cents':calc['balance_cents'],'remote_balance_cents':_amount(remote['Balance']),'checked_at':now(),
                'reason':'','reconciled_receipts':imported,'matched_payment_id':local_payment_id,'money_moved':False}
    except HTTPException:raise
    except (ValueError,KeyError,TypeError,ArithmeticError) as ex:
        raise HTTPException(409,str(ex) if isinstance(ex,ValueError) else 'QuickBooks returned incomplete payment evidence. No receipt was matched.')
    except Exception:raise HTTPException(502,'QuickBooks could not confirm this match. No receipt was matched; check the connection and try again.')


def poll_quickbooks_receipts(path):
    """Import proven invoice receipts only; never charge, refund or write to QBO."""
    import os
    if os.getenv('CROWNOPS_ALLOW_QBO_RECEIPT_SYNC')!='1':return {'enabled':False}
    from .providers import _qbo_connection
    _,realm,_=_qbo_connection(path);env=quickbooks_environment()
    with closing(connect(path)) as db:
        cursor=settings(db,'quickbooks_receipt_cursor','') or ''
        candidates=rows(db,"SELECT i.id,o.result_json FROM job_invoices i JOIN provider_operations o ON o.id=('qbo:invoice:'||i.id||':'||i.revision) WHERE i.status='Issued' AND o.status='Completed' ORDER BY i.id")
    scoped=[]
    for row in candidates:
        scope=json.loads(row['result_json'])
        if scope.get('realm_id')==realm and scope.get('environment')==env:scoped.append(row)
    ordered=[r for r in scoped if r['id']>cursor]+[r for r in scoped if r['id']<=cursor]
    selected=ordered[:25];errors=0;imported=0
    for row in selected:
        try:
            result=quickbooks_payment_state(path,row['id'],reconcile=True,actor='quickbooks-receipt-sync')
            imported+=result['reconciled_receipts']
            errors+=int(result['status'] in ('Unavailable','Review required'))
        except Exception:errors+=1
    state={'enabled':True,'environment':env,'realm_id':realm,'checked_at':now(),'checked_invoices':len(selected),'needs_review':errors,'imported_receipts':imported}
    with transaction(path) as db:
        if selected:put_setting(db,'quickbooks_receipt_cursor',selected[-1]['id'])
        put_setting(db,'quickbooks_receipt_sync_status',state)
    return state


def register_invoice_reconciliation(app,path,auth):
    @app.get('/api/invoices/{iid}/payment-state')
    def payment_state(iid:str,u=Depends(auth)):
        return JSONResponse(quickbooks_payment_state(path,iid,user=u),headers={'Cache-Control':'no-store'})

    @app.post('/api/invoices/{iid}/reconcile-payments')
    def reconcile_payments(iid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        if set(data)-{'confirmed','version','revision'}:raise HTTPException(422,'Unknown reconciliation field.')
        if data.get('confirmed') is not True:raise HTTPException(422,'Confirm refreshing verified QuickBooks receipts. This does not move money.')
        with closing(connect(path)) as db:snapshot=_payment_snapshot(db,iid,u)
        if type(data.get('version'))is not int or type(data.get('revision'))is not int or data['version']!=snapshot['invoice']['version'] or data['revision']!=snapshot['invoice']['revision']:
            raise HTTPException(409,'The invoice changed. Refresh before reconciling receipts.')
        return JSONResponse(quickbooks_payment_state(path,iid,actor=u['id'],reconcile=True,user=u,expected_version=data['version'],expected_revision=data['revision']),headers={'Cache-Control':'no-store'})

    @app.post('/api/invoices/{iid}/match-quickbooks-receipt')
    def match_receipt(iid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        if set(data)-{'confirmed','version','revision','payment_id','quickbooks_payment_id','note'}:raise HTTPException(422,'Unknown receipt matching field.')
        if data.get('confirmed') is not True:raise HTTPException(422,'Confirm matching these existing receipts without moving money.')
        return JSONResponse(match_quickbooks_receipt(path,iid,data.get('payment_id',''),data.get('quickbooks_payment_id',''),user=u,note=data.get('note',''),version=data.get('version'),revision=data.get('revision')),headers={'Cache-Control':'no-store'})


def checked_quickbooks_link(path, iid, user, *, invoice_scope=None):
    from .providers import qbo_read
    # A public caller must first authenticate its signed invoice-only session.
    # The explicit exact scope avoids manufacturing a privileged staff identity.
    if user is None and invoice_scope!=iid:raise HTTPException(403,'Invoice access is required.')

    def snapshot(db):
        inv, calc = invoice_current(db, iid)
        job = invoice_job(db, inv['job_id'], user) if user is not None else one(db,'SELECT * FROM jobs WHERE id=?',(inv['job_id'],))
        if inv['status'] != 'Issued' or calc['balance_cents'] <= 0:
            raise HTTPException(409, 'A finalized invoice with an unpaid balance is required.')
        if pending_received(db, iid):
            raise HTTPException(409, 'Reconcile payments already received before sharing another payment link.')
        if one(db, "SELECT id FROM payment_links WHERE invoice_id=? AND status IN ('Creating','Active','Unknown','Cancelling')", (iid,)):
            raise HTTPException(409, 'Reconcile or close the existing checkout before sharing a QuickBooks payment link.')
        ext = one(db, "SELECT * FROM external_records WHERE provider='quickbooks' AND kind='Invoice' AND object_id=? AND revision=?", (iid, inv['revision']))
        op = one(db, 'SELECT status FROM provider_operations WHERE id=?', ('qbo:invoice:' + iid + ':' + str(inv['revision']),))
        customer = one(db, "SELECT external_id FROM external_records WHERE provider='quickbooks' AND kind='Customer' AND object_id=?", (job['lead_id'],))
        if not ext or not op or op['status'] != 'Completed' or not customer or ext['amount_cents'] != calc['total_cents']:
            raise HTTPException(409, 'An Owner or Manager must synchronize and reconcile this invoice in QuickBooks first.')
        if quickbooks_environment() == 'production':
            issued = one(db, 'SELECT document_json FROM invoice_revisions WHERE invoice_id=? AND revision=?', (iid, inv['revision']))
            if not issued or json.loads(issued['document_json']).get('test_mode', True):
                raise HTTPException(409, 'A test invoice cannot request a production payment.')
        return inv, calc, ext, customer['external_id']

    with closing(connect(path)) as db:
        inv, calc, ext, customer = snapshot(db)
    remote_id = str(ext['external_id'])
    if not re.fullmatch(r'[0-9]{1,50}', remote_id):
        raise HTTPException(409, 'The saved QuickBooks invoice needs reconciliation.')
    try:
        found = qbo_read(path, 'query', {'query': "SELECT * FROM Invoice WHERE Id='" + remote_id + "'", 'include': 'invoiceLink'}).get('QueryResponse', {}).get('Invoice', [])
        if len(found) != 1:
            raise ValueError()
        remote = found[0];currency_context={}
        from decimal import Decimal
        if (str(remote.get('Id')) != remote_id or not _cad_currency(path,remote,currency_context) or
                str(remote.get('CustomerRef', {}).get('value')) != str(customer) or
                Decimal(str(remote['TotalAmt'])) * 100 != calc['total_cents'] or
                Decimal(str(remote['Balance'])) * 100 != calc['balance_cents']):
            raise ValueError()
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, ArithmeticError):
        raise HTTPException(409, 'QuickBooks invoice or balance differs. An Owner or Manager must reconcile it before requesting payment.')
    except Exception:
        raise HTTPException(502, 'The QuickBooks payment link could not be checked. Review the connection and try again.')
    try:
        evidence=_receipt_evidence(path,remote,customer,currency_context)
        with closing(connect(path)) as db:_import_receipts(db,_payment_snapshot(db,iid,user),evidence,'quickbooks-link-check',apply=False)
    except (ValueError,KeyError,TypeError,ArithmeticError) as ex:
        raise HTTPException(409,str(ex) if isinstance(ex,ValueError) else 'The linked payment evidence needs management review.')
    except Exception:
        raise HTTPException(502,'Linked QuickBooks receipts could not be checked. No payment link was shared.')
    url = remote.get('InvoiceLink', '')
    if not isinstance(url, str):url = ''
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
        valid = (parsed.scheme == 'https' and host.endswith('.intuit.com') and
                 not parsed.username and not parsed.password and parsed.port in (None, 443))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(409, 'No QuickBooks payment link is available. Enable online payments for this invoice in QuickBooks, then check again.')
    with closing(connect(path)) as db:
        current, current_calc, current_ext, current_customer = snapshot(db)
        if (current['version'] != inv['version'] or current['revision'] != inv['revision'] or
                current_calc != calc or current_ext['external_id'] != remote_id or current_customer != customer):
            raise HTTPException(409, 'The invoice changed while checking its payment link. Reload it before sharing.')
    return {'url': url, 'invoice_id': iid, 'revision': inv['revision'], 'version': inv['version'],
            'amount_cents': calc['balance_cents'], 'environment': quickbooks_environment(), 'paid': False}
