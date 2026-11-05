"""Run only on the company's authorized installation. Generates reviewable drafts.
No financial action or client email is sent automatically by this worker.
"""
from datetime import datetime,timezone,timedelta
from contextlib import closing
import json, time
from .store import connect,transaction,rows,one,now,dumps,uid,insert
from .platform_common import settings,queue_message,add_task,invoice_current,pending_received,business_day


def tick(path):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("tick")
    made=0;t=datetime.now(timezone.utc)
    payment_checks={}
    # Never hold the SQLite writer lock during provider requests. Every result
    # is checked against the current revision/version in the draft transaction.
    with closing(connect(path)) as db:
        from .company_profile import company_profile
        if not company_profile(db)['ready']:return {'drafts_created':0,'skipped':'company profile not configured'}
        policy=settings(db,'automation_policy',{})
        candidates=rows(db,"SELECT i.id,r.issued_at FROM job_invoices i JOIN invoice_revisions r ON r.invoice_id=i.id AND r.revision=i.revision JOIN jobs j ON j.id=i.job_id JOIN leads l ON l.id=j.lead_id WHERE i.status='Issued' AND l.notifications_suppressed=0 AND l.email!=''") if policy.get('enabled') and policy.get('invoice_followup') else []
    from .invoice_payments import collection_payment_check
    for inv in candidates:
        if t-datetime.fromisoformat(inv['issued_at'])>=timedelta(days=7):payment_checks[inv['id']]=collection_payment_check(path,inv['id'])
    with transaction(path) as db:
        cfg=settings(db,'automation_policy',{})
        # Retire legacy automatic review drafts even if automation is disabled.
        db.execute("UPDATE outbound_messages SET status='Cancelled',error='Review outreach is now manual only' WHERE category='Review request' AND created_by='automation' AND status IN ('Draft','Approved')")
        if not cfg.get('enabled'):return {'drafts_created':0,'enabled':False}
        if cfg.get('preparation'):
            jobs=rows(db,"SELECT j.*,l.client_name,l.email,l.language,l.notifications_suppressed FROM jobs j JOIN leads l ON l.id=j.lead_id WHERE j.status='Confirmed'")
            for j in jobs:
                if j['notifications_suppressed'] or not j['email']:continue
                delta=datetime.fromisoformat(j['start_at'])-t
                if not timedelta(0)<=delta<=timedelta(days=3):continue
                key='prep:'+j['id']+':'+str(j['version'])
                if one(db,'SELECT id FROM outbound_messages WHERE event_key=?',(key,)):continue
                from .client_communications import portal_link,preparation_body,booking_details
                lead=one(db,'SELECT * FROM leads WHERE id=?',(j['lead_id'],))
                quote=one(db,'SELECT * FROM quotes WHERE id=?',(j['quote_id'],)) if j.get('quote_id') else None
                window=booking_details(j,quote)['arrival_window'] if quote else None
                from .company_profile import company_profile
                body=preparation_body(lead,j,portal_link(db,lead,'automation',tab='details'),window,company_profile(db))
                queue_message(db,j['lead_id'],'Move preparation | Preparation du demenagement',body,'Preparation','automation',key,j['id'],j['version']);made+=1
        if cfg.get('invoice_followup'):
            invoices=rows(db,"SELECT i.*,j.lead_id,l.client_name,l.email,l.language,l.notifications_suppressed,r.issued_at FROM job_invoices i JOIN jobs j ON j.id=i.job_id JOIN leads l ON l.id=j.lead_id JOIN invoice_revisions r ON r.invoice_id=i.id AND r.revision=i.revision WHERE i.status='Issued'")
            for inv in invoices:
                if inv['notifications_suppressed'] or not inv['email'] or t-datetime.fromisoformat(inv['issued_at'])<timedelta(days=7):continue
                if pending_received(db,inv['id']) or one(db,"SELECT id FROM cases WHERE lead_id=? AND status!='Resolved'",(inv['lead_id'],)):continue
                _,calc=invoice_current(db,inv['id']);balance=calc['balance_cents']
                if balance<=0:continue
                check=payment_checks.get(inv['id'])
                if not check or not check['can_collect'] or check['version']!=inv['version'] or check['revision']!=inv['revision'] or check['local_balance_cents']!=balance:continue
                key='balance:'+inv['id']+':'+str(inv['revision'])+':'+str(balance)
                if one(db,'SELECT id FROM outbound_messages WHERE event_key=?',(key,)):continue
                from .company_profile import company_profile
                company=company_profile(db)
                body=f"Hello {inv['client_name']},\n\nOur records show an outstanding balance of CA${balance/100:.2f} for invoice {inv['number']}. Please let us know about any payment already sent or any questions so we can reconcile the file.\n\nThank you,\n{company['name']}"
                if inv['language'].lower().startswith('fr'):body=f"Bonjour {inv['client_name']},\n\nSelon notre dossier, le solde de la facture {inv['number']} est de {balance/100:.2f} $ CA. Merci de nous informer de tout paiement deja transmis ou de toute question afin de verifier le dossier.\n\nMerci,\n{company['name']}"
                queue_message(db,inv['lead_id'],'Invoice follow-up | Suivi de facture',body,'Collection','automation',key,inv['job_id']);made+=1
        if cfg.get('fleet_tasks'):
            for v in rows(db,'SELECT p.*,v.name FROM fleet_profiles p JOIN vehicles v ON v.id=p.vehicle_id'):
                if (v['next_service_date'] and v['next_service_date']<=business_day()) or (v['next_service_km'] is not None and v['odometer_km']>=v['next_service_km']):
                    key='fleet:'+v['vehicle_id']+':'+str(v['next_service_date'])+':'+str(v['next_service_km'])
                    add_task(db,None,'Fleet maintenance due: '+v['name'],key=key)
    return {'drafts_created':made,'enabled':True}
