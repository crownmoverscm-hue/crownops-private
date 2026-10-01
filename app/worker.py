"""Run only on the company's authorized installation. Generates reviewable drafts.
No financial action or client email is sent automatically by this worker.
"""
from datetime import datetime,timezone,timedelta
import json, time
from .store import transaction,rows,one,now,dumps,uid,insert
from .platform_common import settings,queue_message,add_task,invoice_current,pending_received


def tick(path):
    made=0;t=datetime.now(timezone.utc)
    with transaction(path) as db:
        cfg=settings(db,'automation_policy',{})
        if not cfg.get('enabled'):return {'drafts_created':0,'enabled':False}
        if cfg.get('preparation'):
            jobs=rows(db,"SELECT j.*,l.client_name,l.email,l.language,l.notifications_suppressed FROM jobs j JOIN leads l ON l.id=j.lead_id WHERE j.status='Confirmed'")
            for j in jobs:
                if j['notifications_suppressed'] or not j['email']:continue
                delta=datetime.fromisoformat(j['start_at'])-t
                if not timedelta(0)<=delta<=timedelta(days=3):continue
                key='prep:'+j['id']+':'+str(j['version'])
                if one(db,'SELECT id FROM outbound_messages WHERE event_key=?',(key,)):continue
                body=f"Hi {j['client_name']},\n\nYour Crown Movers service is approaching. Please confirm your final inventory, parking/loading access and any elevator reservation with our team. You can upload details in your private client portal.\n\nThank you,\nCrown Movers"
                if j['language'].lower().startswith('fr'):body=f"Bonjour {j['client_name']},\n\nVotre service Crown Movers approche. Merci de confirmer votre inventaire final, le stationnement et toute reservation d'ascenseur. Vous pouvez transmettre les details dans votre portail client.\n\nMerci,\nCrown Movers"
                queue_message(db,j['lead_id'],'Move preparation | Preparation du demenagement',body,'Preparation','automation',key,j['id'],j['version']);made+=1
        if cfg.get('review_request') and cfg.get('review_url'):
            for j in rows(db,"SELECT j.*,l.email,l.client_name,l.language,l.notifications_suppressed FROM jobs j JOIN leads l ON l.id=j.lead_id WHERE j.status='Completed'"):
                if j['notifications_suppressed'] or not j['email']:continue
                key='neutral-review:'+j['id']
                if one(db,'SELECT id FROM outbound_messages WHERE event_key=?',(key,)):continue
                # No sentiment filter and no testimonial/credit eligibility filter.
                body=f"Hi {j['client_name']},\n\nThank you for choosing Crown Movers. You're welcome to share an honest review of your experience here: {cfg['review_url']}\n\nLeaving a review is optional. No discount or benefit depends on a public review.\n\nThank you,\nCrown Movers"
                if j['language'].lower().startswith('fr'):body=f"Bonjour {j['client_name']},\n\nMerci d'avoir choisi Crown Movers. Vous pouvez partager votre experience honnete ici : {cfg['review_url']}\n\nL'avis est facultatif. Aucun rabais ni avantage ne depend d'un avis public.\n\nMerci,\nCrown Movers"
                queue_message(db,j['lead_id'],'Thank you | Merci',body,'Review request','automation',key,j['id']);made+=1
        if cfg.get('invoice_followup'):
            invoices=rows(db,"SELECT i.*,j.lead_id,l.client_name,l.email,l.language,l.notifications_suppressed,r.issued_at FROM job_invoices i JOIN jobs j ON j.id=i.job_id JOIN leads l ON l.id=j.lead_id JOIN invoice_revisions r ON r.invoice_id=i.id AND r.revision=i.revision WHERE i.status='Issued'")
            for inv in invoices:
                if inv['notifications_suppressed'] or not inv['email'] or t-datetime.fromisoformat(inv['issued_at'])<timedelta(days=7):continue
                if pending_received(db,inv['id']) or one(db,"SELECT id FROM cases WHERE lead_id=? AND status!='Resolved'",(inv['lead_id'],)):continue
                _,calc=invoice_current(db,inv['id']);balance=calc['balance_cents']
                if balance<=0:continue
                key='balance:'+inv['id']+':'+str(inv['revision'])+':'+str(balance)
                if one(db,'SELECT id FROM outbound_messages WHERE event_key=?',(key,)):continue
                body=f"Hello {inv['client_name']},\n\nOur records show an outstanding balance of CA${balance/100:.2f} for invoice {inv['number']}. Please let us know about any payment already sent or any questions so we can reconcile the file.\n\nThank you,\nCrown Movers"
                if inv['language'].lower().startswith('fr'):body=f"Bonjour {inv['client_name']},\n\nSelon notre dossier, le solde de la facture {inv['number']} est de {balance/100:.2f} $ CA. Merci de nous informer de tout paiement deja transmis ou de toute question afin de verifier le dossier.\n\nMerci,\nCrown Movers"
                queue_message(db,inv['lead_id'],'Invoice follow-up | Suivi de facture',body,'Collection','automation',key,inv['job_id']);made+=1
        if cfg.get('fleet_tasks'):
            for v in rows(db,'SELECT p.*,v.name FROM fleet_profiles p JOIN vehicles v ON v.id=p.vehicle_id'):
                if (v['next_service_date'] and v['next_service_date']<=now()[:10]) or (v['next_service_km'] is not None and v['odometer_km']>=v['next_service_km']):
                    key='fleet:'+v['vehicle_id']+':'+str(v['next_service_date'])+':'+str(v['next_service_km'])
                    add_task(db,None,'Fleet maintenance due: '+v['name'],key=key)
    return {'drafts_created':made,'enabled':True}
