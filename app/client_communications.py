"""Friendly client messages and short-lived, project-scoped portal access."""
from datetime import datetime, timezone, timedelta
from html import escape
import os
import re
import secrets
import json
import hashlib
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from .store import insert, now, one, rows, dumps
from .platform_common import stamp_hash
from .company_profile import profile_or_legacy, time_note, team_signoff, help_line
from .business_zone import BUSINESS_ZONE, zone_name

CONTACT_FIELDS=frozenset({'client_name','phone'})


def pending_scope_changes(db,lead_id):
    """A contact correction needs review, but does not change the sold service."""
    for change in rows(db,"SELECT payload_json FROM client_changes WHERE lead_id=? AND status='Pending'",(lead_id,)):
        try:fields=json.loads(change['payload_json'])
        except (TypeError,ValueError):return True
        if not isinstance(fields,dict) or not fields or set(fields)-CONTACT_FIELDS:return True
    return False


def quote_scope_current(db,quote,lead):
    """Keep signed snapshots unchanged; bridge only audited contact-only versions.

    Unknown version gaps remain stale. Lead versions still advance for contact
    corrections, retaining optimistic concurrency for staff and client edits.
    """
    if not quote or not lead or quote['lead_id']!=lead['id']:return False
    version=quote['lead_version'];current=lead['version']
    if version==current:return True
    if not isinstance(version,int) or not isinstance(current,int) or version>current:return False
    transitions={}
    for event in rows(db,"SELECT details_json FROM audit WHERE object_type='leads' AND object_id=? AND action='Contact details reviewed' ORDER BY id",(lead['id'],)):
        try:detail=json.loads(event['details_json'])
        except (TypeError,ValueError):continue
        start=detail.get('from_version');end=detail.get('to_version');fields=detail.get('fields',[])
        if (isinstance(start,int) and isinstance(end,int) and end==start+1 and
                isinstance(fields,list) and fields and not set(fields)-CONTACT_FIELDS):
            change=one(db,"SELECT payload_json FROM client_changes WHERE id=? AND lead_id=? AND status='Approved'",(detail.get('change_id'),lead['id']))
            try:payload=json.loads(change['payload_json']) if change else None
            except (TypeError,ValueError):payload=None
            if isinstance(payload,dict) and not set(payload)-CONTACT_FIELDS:transitions[start]=end
    while version<current:
        if version not in transitions:return False
        version=transitions[version]
    return version==current


def booking_details(job,quote):
    """Use the actual scheduled start, preserving the sold window when it fits."""
    from .sales_crm_v6 import quote_start_window
    start=datetime.fromisoformat(job['start_at'].replace('Z','+00:00')).astimezone(BUSINESS_ZONE)
    inputs=json.loads(quote['inputs_json'])
    expected,window=quote_start_window(inputs)
    if start.strftime('%H:%M')!=expected:
        window=start.strftime('%I:%M %p').lstrip('0')
    return {'date':start.date().isoformat(),'arrival_window':window,'start_at':start.isoformat(timespec='seconds')}


def booking_confirmation_key(job,quote):
    detail=booking_details(job,quote)
    fingerprint=hashlib.sha256(dumps({'quote_id':quote['id'],**detail}).encode()).hexdigest()[:16]
    return 'booking-confirmed:'+job['id']+':'+fingerprint


def booking_confirmation_current(db,message):
    job=one(db,'SELECT * FROM jobs WHERE id=?',(message.get('job_id'),))
    if not job or job['status'] not in ('Confirmed','In progress'):return False
    quote=one(db,'SELECT * FROM quotes WHERE id=?',(job['quote_id'],))
    lead=one(db,'SELECT * FROM leads WHERE id=?',(job['lead_id'],))
    return bool(quote and quote['status']=='Accepted' and quote_scope_current(db,quote,lead) and
                not pending_scope_changes(db,lead['id']) and message['event_key']==booking_confirmation_key(job,quote))


def preparation_body(lead,job,link,arrival_window=None,company=None):
    """A useful reminder includes the confirmed service and a direct next step."""
    c=profile_or_legacy(company)
    fr=str(lead.get('language','')).lower().startswith('fr')
    start=datetime.fromisoformat(job['start_at'].replace('Z','+00:00')).astimezone(ZoneInfo(c['timezone']))
    window=arrival_window or start.strftime('%I:%M %p').lstrip('0')
    if fr:
        return f"Bonjour {lead['client_name']},\n\nVotre service {c['name']} approche! Date confirmée : {start.date().isoformat()}. Arrivée prévue : {window}{time_note(c,True)}.\n\nPour aider votre équipe à bien se préparer, vérifiez ces renseignements :\n• Inventaire final et articles lourds ou fragiles.\n• Stationnement, accès au chargement et réservation d’ascenseur.\n• Photos ou lien vers votre dossier de photos et vidéos.\n\nVérifier mon dossier privé :\n{link}\n\nLes renseignements déjà fournis sont remplis. Ajoutez seulement ce qui manque ou a changé, puis transmettez vos renseignements pour révision. Une demande de modification de date ou de service reste à confirmer par {c['short_name']}."+help_line(c,True)+team_signoff(c,True)
    return f"Hi {lead['client_name']},\n\nYour {c['name']} service is coming up! Confirmed date: {start.date().isoformat()}. Expected arrival: {window}{time_note(c,False)}.\n\nTo help your crew prepare, please check:\n• Final inventory and any heavy or fragile items.\n• Parking, loading access and any elevator reservation.\n• Photos or a link to your photo and video folder.\n\nCheck my private moving file:\n{link}\n\nThe details you already shared are filled in. Add only what is missing or changed, then submit your details for review. Requested date or service changes still need confirmation from {c['short_name']}."+help_line(c,False)+team_signoff(c,False)


def public_base():
    return (os.getenv('CROWNOPS_PUBLIC_URL') or ('https://'+os.environ['RAILWAY_PUBLIC_DOMAIN'] if os.getenv('RAILWAY_PUBLIC_DOMAIN') else 'http://127.0.0.1:8080')).rstrip('/')


def deposit_request_context(db, qid):
    """Only request a verified outstanding deposit for a current tentative job."""
    from .deposits import deposit_state
    from .availability import assert_dates_open
    from fastapi import HTTPException
    q=one(db,"SELECT * FROM quotes WHERE id=? AND status='Accepted'",(qid,))
    if not q:return None
    lead=one(db,'SELECT * FROM leads WHERE id=?',(q['lead_id'],))
    job=one(db,"SELECT * FROM jobs WHERE quote_id=? AND status='Tentative' ORDER BY created_at DESC LIMIT 1",(qid,))
    if not lead or not lead.get('email') or not job:return None
    if not quote_scope_current(db,q,lead) or pending_scope_changes(db,lead['id']):return None
    if one(db,"SELECT id FROM quotes WHERE lead_id=? AND revision>? AND status IN ('Approved','Accepted')",(lead['id'],q['revision'])):return None
    if one(db,"SELECT id FROM cases WHERE lead_id=? AND status!='Resolved'",(lead['id'],)):return None
    state=deposit_state(db,lead['id'],q)
    if state['due_cents']<=0 or state['pending_cents']>0:return None
    try:assert_dates_open(db,job['start_at'],job['end_at'])
    except HTTPException:return None
    return q,lead,job,state


def deposit_request_fingerprint(db, context):
    q,lead,job,state=context
    policy=settings_for_deposit(db)
    return hashlib.sha256(dumps({'quote':q['id'],'revision':q['revision'],'lead_version':lead['version'],
        'email':lead['email'],'job_version':job['version'],'start':job['start_at'],'end':job['end_at'],
        'due':state['due_cents'],'policy':policy}).encode()).hexdigest()[:24]


def settings_for_deposit(db):
    from .platform_common import settings
    policy=settings(db,'deposit_policy',{}) or {}
    return {k:policy.get(k) for k in ('approved','enabled','recipient_email','recipient_name','auto_deposit','reference_note')}


def prepare_deposit_request(db, qid, actor):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("prepare deposit request")
    """Prepare one reviewable EN/FR request. Never create a checkout or send mail."""
    from .platform_common import queue_message
    context=deposit_request_context(db,qid)
    if not context:return None
    q,lead,job,state=context
    key='accepted-deposit:'+qid+':'+deposit_request_fingerprint(db,context)
    old=one(db,'SELECT * FROM outbound_messages WHERE event_key=?',(key,))
    if old:return old
    policy=settings_for_deposit(db)
    from .company_profile import company_profile
    c=company_profile(db)
    fr=str(json.loads(q['inputs_json']).get('language') or lead.get('language','')).lower().startswith('fr')
    details=booking_details(job,q);amount=state['due_cents']/100
    link=portal_link(db,lead,actor,tab='details')
    if fr:
        subject='Votre soumission est acceptée — prochaine étape | '+c['name']
        body=f"Bonjour {lead['client_name']},\n\nMerci d’avoir accepté notre soumission! Date demandée : {details['date']}. Arrivée prévue : {details['arrival_window']}{time_note(c,True)}. Votre service reste provisoire jusqu’à la vérification du dépôt et des disponibilités par {c['short_name']}.\n\nDépôt restant à vérifier : {amount:.2f} $ CA. Si vous avez déjà payé, ne payez pas une deuxième fois; contactez-nous pour faire vérifier le paiement."
    else:
        subject='Your estimate is accepted — next step | '+c['name']
        body=f"Hi {lead['client_name']},\n\nThank you for accepting our estimate! Requested date: {details['date']}. Expected arrival: {details['arrival_window']}{time_note(c,False)}. Your service remains tentative until {c['short_name']} verifies the deposit and availability.\n\nOutstanding deposit to verify: CAD {amount:.2f}. If you have already paid, please do not pay again; contact us so we can verify it."
    if policy.get('approved') and policy.get('enabled') and policy.get('recipient_email'):
        body+=('\n\nVirement Interac : ' if fr else '\n\nInterac e-Transfer: ')+str(policy['recipient_email'])+' ('+str(policy.get('recipient_name') or c['name'])+').'
        if policy.get('auto_deposit',True):body+=(' Aucun mot de passe requis : dépôt automatique.' if fr else ' Auto-deposit is enabled; no password is required.')
        if policy.get('reference_note'):body+=' '+str(policy['reference_note'])
    else:
        body+=('\n\nContactez '+c['short_name']+' pour obtenir les instructions de paiement approuvées.' if fr else '\n\nPlease contact '+c['short_name']+' for the approved payment instructions.')
    body+=('\n\nVérifier mon dossier et ajouter mes photos :\n' if fr else '\n\nReview my moving details and add photos:\n')+link
    body+=help_line(c,fr)+team_signoff(c,fr)
    return queue_message(db,lead['id'],subject,body,'Follow-up',actor,key,job['id'],job['version'])


def deposit_request_current(db,message):
    marker=message.get('event_key','').split(':')
    if len(marker)!=3 or marker[0]!='accepted-deposit':return False
    context=deposit_request_context(db,marker[1])
    return bool(context and context[1]['id']==message['lead_id'] and context[2]['id']==message['job_id'] and
        marker[2]==deposit_request_fingerprint(db,context))


def portal_link(db, lead, actor, base=None, tab='details'):
    raw=secrets.token_urlsafe(40)
    insert(db,'portal_invites',dict(token_hash=stamp_hash(raw),lead_id=lead['id'],email=lead['email'],
        expires_at=(datetime.now(timezone.utc)+timedelta(days=14)).isoformat(timespec='seconds'),
        created_by=actor,created_at=now()))
    return (base or public_base()).rstrip('/')+'/portal?tab='+tab+'#access='+raw


def invoice_body(lead, inv, calc, link, payment=None, company=None):
    c=profile_or_legacy(company)
    fr=str(lead.get('language','')).lower().startswith('fr')
    if fr:
        body=f"Bonjour {lead['client_name']},\n\nMerci de nous avoir confié votre service. Votre facture {inv['number']} est prête.\n\nTotal : {calc['total_cents']/100:.2f} $ CA\nPaiements vérifiés : {calc['deposit_cents']/100:.2f} $ CA\nSolde confirmé : {calc['balance_cents']/100:.2f} $ CA\n\nConsulter ou télécharger votre facture :\n{link}\n\nLes paiements récemment recueillis peuvent être en cours de vérification. Si vous avez déjà payé, ne payez pas une deuxième fois."
    else:
        body=f"Hi {lead['client_name']},\n\nThank you for trusting us with your service. Your invoice {inv['number']} is ready.\n\nInvoice total: CAD {calc['total_cents']/100:.2f}\nVerified payments: CAD {calc['deposit_cents']/100:.2f}\nConfirmed balance: CAD {calc['balance_cents']/100:.2f}\n\nView or download your invoice:\n{link}\n\nRecently collected payments may still be awaiting verification. If you have already paid, please do not pay again."
    if payment:
        body+=('\n\nPaiement sécurisé QuickBooks :\n' if fr else '\n\nSecure QuickBooks payment:\n')+payment['url']
        if payment['environment']=='sandbox':body+='\nTEST ONLY / TEST SEULEMENT — QuickBooks sandbox.'
    question=(f"\n\nUne question au sujet de cette facture? Écrivez-nous à {c['email']} ou appelez le {c['phone']}." if fr else f"\n\nQuestions about this invoice? Email {c['email']} or call {c['phone']}.")
    return body+question+team_signoff(c,fr)


def email_html(subject, body, company=None):
    """Render a multipart alternative without trusting any client-supplied HTML."""
    c=profile_or_legacy(company);name=escape(c['name'])
    blocks=[]
    fr=body.lstrip().startswith('Bonjour')
    for paragraph in body.split('\n\n'):
        parts=[]
        for line in paragraph.splitlines():
            candidate=line.strip()
            try:
                parsed=urlsplit(candidate)
                is_portal=parsed.scheme=='https' and parsed.path in ('/portal','/invoice-access') and not parsed.username and not parsed.password
            except ValueError:is_portal=False
            if is_portal:
                label=('Consulter la facture' if fr else 'Open invoice') if parsed.path=='/invoice-access' else ('Ouvrir mon dossier' if fr else 'Open my moving file')
                parts.append('<a href="'+escape(candidate,quote=True)+'" style="display:inline-block;background:#c4510a;color:#ffffff;text-decoration:none;border-radius:8px;padding:14px 22px;font-weight:bold">'+label+'</a>')
            elif re.fullmatch(r'https://[^\s<>]+',candidate):
                parts.append('<a href="'+escape(candidate,quote=True)+'" style="color:#a84005;overflow-wrap:anywhere">'+escape(candidate)+'</a>')
            else:parts.append(escape(line))
        blocks.append('<p style="margin:0 0 20px;line-height:1.65">'+'<br>'.join(parts)+'</p>')
    invoice_access='/invoice-access' in body
    footer=('Gardez ce courriel privé. Si le lien expire, communiquez avec '+name+' pour recevoir un nouvel accès à la facture.' if fr else 'Keep this email private. If your link expires, contact '+name+' for a fresh invoice link.') if invoice_access else ('Gardez ce courriel privé. Pour un nouveau lien, ouvrez votre dossier et demandez un lien par courriel.' if fr else 'Keep this email private. If your link expires, open your moving file and request a fresh email link.')
    return '<!doctype html><html><body style="margin:0;background:#f6f5f2;color:#202020;font:18px Arial,sans-serif"><div style="max-width:600px;margin:auto;padding:24px 16px"><div style="padding:20px 24px;background:#161616;color:white;border-radius:12px 12px 0 0;font-size:22px;font-weight:bold">'+name+'</div><main style="padding:28px 24px;background:white;border:1px solid #deddd9;border-radius:0 0 12px 12px"><h1 style="font-size:23px;margin:0 0 24px">'+escape(subject)+'</h1>'+''.join(blocks)+'</main><p style="font-size:13px;line-height:1.6;color:#626262;padding:0 12px">'+footer+'<br><a href="tel:'+escape(c['phone_tel'],quote=True)+'">'+escape(c['phone'])+'</a> · '+name+'</p></div></body></html>'
