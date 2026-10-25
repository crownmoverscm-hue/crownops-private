"""Sandbox-only bridge: existing itemized QBO estimates + CrownOps portal/mail.

No new customer, estimate, payment or booking system. All writes are explicit;
opening status never contacts a provider. The rollout flag defaults off.
"""
from contextlib import closing
import json
import os
from fastapi import Depends, HTTPException
from .store import connect, transaction, one, insert, now, dumps, audit
from .platform_common import require_role, SALES, quickbooks_environment, quickbooks_gate


def workflow(db,qid):
    return one(db,'SELECT * FROM quote_booking_workflows WHERE quote_id=?',(qid,))


def gate():
    if (os.getenv('CROWNOPS_GMAIL_QBO_SANDBOX_WORKFLOW')!='1' or os.getenv('CROWNOPS_ENV')!='staging'
            or quickbooks_environment()!='sandbox'):
        raise HTTPException(503,'The combined Gmail/QuickBooks walkthrough is staging-only and has not been enabled.')


def check_delivery(path,qid,data):
    gate()
    from .estimate_delivery import preview, source
    from .sender_proof import verified_company_sender
    with closing(connect(path)) as db:
        row=workflow(db,qid)
        if not row:raise HTTPException(409,'Prepare the combined sandbox workflow first.')
        source(db,qid)
        if data.get('sync_quickbooks') is True:raise HTTPException(409,'This quote already uses the itemized QuickBooks estimate. Do not create a legacy export.')
        if data.get('send_now') is True and not verified_company_sender(db):
            raise HTTPException(409,'Verify and approve the actual company From/Reply-To before sending.')
    remote=preview(path,qid)
    if (not row.get('review_hash') or remote.get('review_hash')!=row['review_hash']
            or data.get('review_hash')!=row['review_hash']):
        raise HTTPException(409,'Review the current linked QuickBooks estimate and exact totals first.')
    if remote.get('provider_email_status') not in ('NotSet',None):
        raise HTTPException(409,'QuickBooks email has already been requested. Reconcile it before using CrownOps delivery.')


def delivery_guard(db,qid):
    """Final send guard, including alternate outbox send controls."""
    gate()
    from .sender_proof import verified_company_sender
    from .estimate_delivery import source
    row=workflow(db,qid);linked=one(db,'SELECT * FROM qbo_estimate_deliveries WHERE quote_id=?',(qid,))
    if not row or not linked or not row['review_hash'] or not verified_company_sender(db):
        raise HTTPException(409,'The combined quote requires reviewed QuickBooks totals and a proven company sender.')
    if source(db,qid,allow_accepted=True)[3]!=linked['source_hash']:
        raise HTTPException(409,'The linked estimate scope changed. Review a current revision before sending.')


def status(db,qid):
    q=one(db,'SELECT * FROM quotes WHERE id=?',(qid,))
    if not q:raise HTTPException(404,'Quote not found.')
    row=workflow(db,qid)
    linked=one(db,'SELECT * FROM qbo_estimate_deliveries WHERE quote_id=?',(qid,))
    mail=one(db,'SELECT id,status,sent_at,error FROM outbound_messages WHERE event_key=?',('quote-delivery:'+qid+':'+str(q['revision']),))
    job=one(db,'SELECT id,status FROM jobs WHERE quote_id=?',(qid,))
    available=(os.getenv('CROWNOPS_ENV')=='staging' and os.getenv('CROWNOPS_GMAIL_QBO_SANDBOX_WORKFLOW')=='1' and quickbooks_environment()=='sandbox')
    return {'quote_id':qid,'workflow_enabled':bool(row),'available':available,'environment':quickbooks_environment(),
            'email':mail or {'status':'Not prepared'},
            'quickbooks_estimate':{'status':linked['state'] if linked else 'Not prepared','id':linked['external_id'] if linked else None,
                'reviewed_totals':json.loads(row['totals_json']) if row else None,'email_requested':False if row else None},
            'acceptance':{'client_status':q['status'],'quickbooks_sync':row['acceptance_state'] if row else 'Not prepared',
                          'error':row['error'] if row else ''},'job':job,'booking_confirmed':bool(job and job['status'] in ('Confirmed','In progress','Completed'))}


def sync_acceptance(path,qid,actor):
    gate();quickbooks_gate()
    from .estimate_delivery import same_company,validate_remote,source
    from .providers import qbo_read,qbo_call
    with closing(connect(path)) as db:
        row=workflow(db,qid);linked=one(db,'SELECT * FROM qbo_estimate_deliveries WHERE quote_id=?',(qid,))
        if not row or not linked:raise HTTPException(409,'No combined sandbox estimate is linked.')
        q,lead,_,fingerprint=source(db,qid,allow_accepted=True)
        if q['status']!='Accepted':raise HTTPException(409,'The customer must accept the CrownOps quote first.')
        if fingerprint!=linked['source_hash']:raise HTTPException(409,'Quote scope or terms changed. Reconcile before updating QuickBooks.')
        ref=json.loads(q['acceptance_ref'] or '{}')
        if not ref.get('portal_email') or not ref.get('name'):raise HTTPException(409,'Verified portal acceptance evidence is required.')
    same_company(path,linked)
    remote=qbo_read(path,'estimate/'+linked['external_id'])['Estimate'];validate_remote(linked,remote,require_quebec=True)
    if remote.get('TxnStatus') not in ('Pending','Accepted'):raise HTTPException(409,'QuickBooks estimate is no longer pending or accepted. Review it before continuing.')
    try:
        if remote.get('TxnStatus')!='Accepted':
            payload={'Id':linked['external_id'],'SyncToken':remote['SyncToken'],'sparse':True,
                     'CustomerRef':remote['CustomerRef'],'TxnStatus':'Accepted','AcceptedBy':ref['name'],
                     'AcceptedDate':str(ref['accepted_at'])[:10]}
            # Intuit requestid is stable; a retry reads remote status first.
            remote=qbo_call(path,'POST','estimate',payload,'portal-acceptance:'+qid)['Estimate']
        validate_remote(linked,remote,require_quebec=True)
        if remote.get('TxnStatus')!='Accepted':raise ValueError('Provider did not confirm acceptance')
    except Exception:
        with transaction(path) as db:
            db.execute("UPDATE quote_booking_workflows SET acceptance_state='Unresolved',error='QuickBooks acceptance was not confirmed; check status before retrying.' WHERE quote_id=?",(qid,))
        raise HTTPException(502,'The client acceptance is saved. QuickBooks synchronization is unresolved; no additional booking was created.')
    with transaction(path) as db:
        db.execute("UPDATE quote_booking_workflows SET acceptance_state='Synced',error='',updated_at=? WHERE quote_id=?",(now(),qid))
        db.execute("UPDATE qbo_estimate_deliveries SET state='Accepted',remote_json=?,checked_at=? WHERE quote_id=?",(dumps(remote),now(),qid))
        db.execute("UPDATE external_records SET sync_token=?,updated_at=? WHERE provider='quickbooks' AND kind='Estimate' AND object_id=?",(remote.get('SyncToken','0'),now(),qid))
        audit(db,actor,'Portal acceptance synchronized to QuickBooks sandbox','quotes',qid,{'external_id':linked['external_id'],'email_requested':False})
        return status(db,qid)


def register_quote_booking_workflow(app,path,auth,finalize):
    with transaction(path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS quote_booking_workflows(
            quote_id TEXT PRIMARY KEY REFERENCES quotes(id), review_hash TEXT NOT NULL DEFAULT '',
            totals_json TEXT NOT NULL DEFAULT '{}', acceptance_state TEXT NOT NULL DEFAULT 'Not accepted',
            error TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")

    @app.get('/api/quotes/{qid}/booking-workflow')
    def get_status(qid:str,u=Depends(auth)):
        require_role(u,SALES)
        with closing(connect(path)) as db:return status(db,qid)

    @app.post('/api/quotes/{qid}/booking-workflow/prepare')
    def prepare(qid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES);gate()
        if data.get('confirm') is not True:raise HTTPException(422,'Confirm preparation of an unsent sandbox estimate.')
        from .estimate_delivery import prepare as prepare_estimate,source
        with transaction(path) as db:
            source(db,qid)
            if not workflow(db,qid):
                if one(db,'SELECT quote_id FROM qbo_estimate_deliveries WHERE quote_id=?',(qid,)):
                    raise HTTPException(409,'This quote already uses another QuickBooks delivery path; do not duplicate it.')
                insert(db,'quote_booking_workflows',{'quote_id':qid,'created_by':u['id'],'created_at':now(),'updated_at':now()})
        result=prepare_estimate(path,qid,data.get('basis'),u['id'])
        return {**result,'email_requested':False,'messages_sent':0}

    @app.get('/api/quotes/{qid}/booking-workflow/preview')
    def preview(qid:str,u=Depends(auth)):
        require_role(u,SALES);gate()
        from .estimate_delivery import preview as estimate_preview
        with closing(connect(path)) as db:
            if not workflow(db,qid):raise HTTPException(409,'Prepare the combined workflow first.')
        return estimate_preview(path,qid)

    @app.post('/api/quotes/{qid}/booking-workflow/review')
    def review(qid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES);gate()
        if data.get('confirm') is not True:raise HTTPException(422,'Confirm the exact line items, totals and taxes.')
        current=preview(qid,u)
        if not current.get('review_hash') or data.get('review_hash')!=current['review_hash']:raise HTTPException(409,'The estimate changed. Preview again.')
        with transaction(path) as db:
            db.execute('UPDATE quote_booking_workflows SET review_hash=?,totals_json=?,updated_at=? WHERE quote_id=?',
                       (current['review_hash'],dumps({k:current[k] for k in ('subtotal_cents','tax_cents','total_cents','taxes','external_id','basis')}),now(),qid))
        # Existing finalize prepares the private portal link and a Draft outbox
        # message. No sending or second accounting export is requested here.
        result=finalize(qid,{'send_now':False,'sync_quickbooks':False,'review_hash':current['review_hash']},u)
        return {**result,'email_requested':False,'review_hash':current['review_hash']}

    @app.post('/api/quotes/{qid}/booking-workflow/send-email')
    def send_email(qid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES);gate()
        if data.get('confirm') is not True:raise HTTPException(422,'Explicitly approve one test quote email.')
        with closing(connect(path)) as db:
            lead=one(db,'SELECT l.email FROM leads l JOIN quotes q ON q.lead_id=l.id WHERE q.id=?',(qid,))
        allowlist={x.strip().lower() for x in os.getenv('CROWNOPS_TEST_EMAIL_ALLOWLIST','').split(',') if x.strip()}
        if not lead or lead['email'].lower() not in allowlist:raise HTTPException(403,'Quote recipient is not on the approved test-email allowlist.')
        return finalize(qid,{'send_now':True,'sync_quickbooks':False,'review_hash':data.get('review_hash')},u)

    @app.post('/api/quotes/{qid}/booking-workflow/sync-acceptance')
    def sync(qid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES)
        if data.get('confirm') is not True:raise HTTPException(422,'Confirm sandbox acceptance synchronization.')
        return sync_acceptance(path,qid,u['id'])
