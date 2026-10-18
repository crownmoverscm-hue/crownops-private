"""Read-only Gmail intake for the private CrownOps review build.

This module can list/read Gmail messages after OAuth authorization while outbound
provider actions remain disabled. It never sends, deletes, archives, labels, or
marks Gmail messages read. Non-lead items enter an internal review queue.
"""
import html
import base64
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote

import requests
from fastapi import HTTPException

from .store import connect, transaction, one, rows, insert, audit, uid, now, dumps


MONTREAL = ZoneInfo("America/Toronto")

FEEDS = {
    'quote': 'New Quote Request',
    'bank': 'Scotia Bank',
    'wix_payment': 'We Got Paid $$$',
    'missed_call': 'Missed Calls',
    'voicemail': 'Voice Mail',
}
def _gmail_received_iso(message,headers):
    raw_internal=str((message or {}).get('internalDate') or '').strip()
    if raw_internal:
        try:
            return datetime.fromtimestamp(int(raw_internal)/1000,tz=timezone.utc).isoformat(timespec='seconds')
        except (TypeError,ValueError,OverflowError):
            pass
    raw_date=str((headers or {}).get('date') or '').strip()
    if raw_date:
        try:
            dt=parsedate_to_datetime(raw_date)
            if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat(timespec='seconds')
        except Exception:
            pass
    return ''

def gmail_read_failure(exc):
    """Controlled diagnostics: never expose provider bodies, tokens or message data."""
    response = getattr(exc, 'response', None)
    status = getattr(response, 'status_code', None)
    reasons = set()
    if response is not None:
        try:
            error = response.json().get('error', {})
            if isinstance(error, dict):
                for item in (error.get('errors') or []) + (error.get('details') or []):
                    if isinstance(item, dict):
                        reasons.add(str(item.get('reason') or '').lower())
        except (ValueError, TypeError, AttributeError):
            pass
    code = 'unavailable'
    message = 'Gmail is temporarily unavailable. The next check will retry; messages were not changed.'
    retryable = status == 429 or (status is not None and status >= 500) or isinstance(exc, (requests.Timeout, requests.ConnectionError))
    if (status == 403 and reasons & {'ratelimitexceeded', 'userratelimitexceeded'}) or status == 429:
        code, message, retryable = 'rate_limit', 'Google has temporarily limited Gmail requests. Wait for the next automatic check; reconnecting is not needed.', True
    elif status == 403 and 'dailylimitexceeded' in reasons:
        code, message = 'daily_quota', 'Google reports the Gmail API daily quota is exhausted. The Google Cloud project owner must review its API quota.'
    elif status == 403 and 'domainpolicy' in reasons:
        code, message = 'domain_policy', 'Google Workspace policy blocks Gmail access. Ask your Workspace administrator to approve this app.'
    elif status == 403 and reasons & {'insufficientpermissions', 'access_token_scope_insufficient'}:
        code, message = 'read_permission', 'Google reports missing Gmail read permission. Reconnect Gmail and approve reading email content.'
    elif status == 401:
        code, message = 'authorization', 'Google rejected the Gmail authorization. Reconnect Gmail from Connections.'
    elif status == 403:
        code, message = 'access_blocked', 'Google blocked Gmail access (HTTP 403). Review Google app permissions and API quota; the exact cause was not supplied.'
    elif status == 404:
        code, message = 'message_missing', 'A Gmail message is no longer available. Other messages can still import.'
    return {'code': code, 'error': message, 'retryable': retryable, 'stop_sync': status != 404}


def _api(token, path, params=None):
    last = None
    for attempt in range(3):
        try:
            r = requests.get(
                'https://gmail.googleapis.com/gmail/v1/users/me/' + path,
                params=params or {},
                headers={'Authorization': 'Bearer ' + token},
                timeout=25,
            )
            if gmail_read_failure(requests.HTTPError(response=r))['retryable']:
                last = requests.HTTPError('Gmail temporary error HTTP ' + str(r.status_code), response=r)
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
            r.raise_for_status()
            return r.json()
        except (requests.Timeout, requests.ConnectionError) as exc:
            last = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise
    if last:
        raise last
    raise requests.RequestException('Gmail request failed.')


def _decode(data):
    if not data:
        return ''
    try:
        return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)).decode('utf-8', 'replace')
    except Exception:
        return ''


def _body(payload):
    if not isinstance(payload, dict):
        return ''
    mime = payload.get('mimeType', '')
    data = (payload.get('body') or {}).get('data')
    if data and mime in ('text/plain', 'text/html'):
        return _decode(data)
    plain = []
    html = []
    for part in payload.get('parts') or []:
        text = _body(part)
        if not text:
            continue
        if part.get('mimeType') == 'text/plain':
            plain.append(text)
        else:
            html.append(text)
    return '\n'.join(plain or html)


def _headers(message):
    return {h.get('name', '').lower(): h.get('value', '') for h in (message.get('payload') or {}).get('headers') or []}


def _attachments(payload):
    out = []
    if not isinstance(payload, dict):
        return out
    name = payload.get('filename') or ''
    body = payload.get('body') or {}
    if name:
        out.append({'filename': name, 'attachment_id': body.get('attachmentId'), 'size': body.get('size', 0), 'mime_type': payload.get('mimeType', '')})
    for part in payload.get('parts') or []:
        out.extend(_attachments(part))
    return out


def _clean_html(text):
    if '<' not in text:
        return html.unescape(text)
    text = re.sub(r'(?is)<(script|style).*?>.*?</\1>', ' ', text)
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</(p|div|tr|li|h[1-6])>', '\n', text)
    return html.unescape(re.sub(r'(?s)<[^>]+>', ' ', text))


def _value_after(text, label):
    lines=text.splitlines()
    for i,line in enumerate(lines):
        match=re.fullmatch(r'[ \t]*'+re.escape(label)+r'[ \t]*:?[ \t]*(.*)',line,re.I)
        if not match:continue
        value=match.group(1).strip()
        if value:return value
        for next_line in lines[i+1:i+5]:
            value=next_line.strip()
            if not value:continue
            if re.match(r'^[A-Za-z][A-Za-z /_-]{1,70}:',value) or value.startswith('END_CROWN_'):return ''
            return value
    return ''


def _quote_fields(body, subject):
    text = _clean_html(body).replace('\r', '')
    block = text
    m = re.search(r'CROWN_QUOTE_BUILDER_INPUT(.*?)END_CROWN_QUOTE_BUILDER_INPUT', text, re.S)
    if m:
        block = m.group(1)
    def anyv(*labels):
        for label in labels:
            v = _value_after(block, label)
            if v:
                return v
        for label in labels:
            v = _value_after(text, label)
            if v:
                return v
        return ''
    name = anyv('Client Name', 'Name')
    raw_email = anyv('Email')
    em = re.search(r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}', raw_email, re.I)
    email = em.group(0).lower() if em else ''
    raw_phone = anyv('Phone')
    phone = _digits(raw_phone)
    move_date = anyv('Moving Date')
    pickup = anyv('Pickup Address', 'Pickup Address or Postal Code', 'Pickup')
    dropoff = anyv('Destination Address', 'Destination Address or Postal Code', 'Destination')
    home = anyv('Home Size')
    boxes = anyv('Approximate Boxes', 'Approximate Number of Boxes', 'Boxes')
    pickup_access = anyv('Pickup Access')
    dropoff_access = anyv('Drop-off Access')
    pickup_walk = anyv('Pickup Walking Distance', 'Walk at Pickup')
    dropoff_walk = anyv('Drop-off Walking Distance', 'Walk at Drop-off')
    heavy = anyv('Heavy Items', 'Heavy / Specialty Items')
    details = anyv('Additional Details', 'Additional Details or Other Services', 'Details / Notes')
    photo = anyv('Photo / Video Link', 'Photo or Video Link', 'Client photo or video link')
    labels=['Client Name','Email','Phone','Moving Date','Pickup Address','Destination Address','Greater Montreal Move','Home Size','Pickup Access','Drop-off Access','Pickup Walking Distance','Drop-off Walking Distance','Approximate Boxes','Heavy Items','Additional Details','Photo / Video Link','Submission ID']
    boundary='|'.join(re.escape(x) for x in labels)
    multiline=re.search(r'(?ims)^\s*Additional Details\s*:\s*(.*?)(?=^\s*(?:'+boundary+r')\s*:|END_CROWN_QUOTE_BUILDER_INPUT|\Z)',block)
    if multiline:details=multiline.group(1).strip()
    notes = details
    local_area=anyv('Greater Montreal Move','Local Montreal')
    local_area=local_area if local_area in ('Yes','No','Not sure') else ''
    if photo.startswith('END_CROWN_'):photo=''
    if not move_date:
        date_match=re.search(r'(\d{4}-\d{2}-\d{2})\s*$',subject)
        if date_match:move_date=date_match.group(1)
    if not name:
        name_match=re.match(r'New Crown Movers Quote Request - (.*?) - \d{4}-\d{2}-\d{2}\s*$',subject)
        if name_match:name=name_match.group(1).strip()
    if subject.startswith('New Instant Moving Estimate Request'):
        estimate = re.search(r'(?s)Calculator Estimate\s*\n+\s*(\$[^\n]+)', text)
        if estimate:
            notes = (notes + '\nCalculator estimate: ' + estimate.group(1).strip()).strip()
    return {
        'client_name': name or 'Website inquiry',
        'email': email,
        'phone': phone,
        'move_date': move_date,
        'pickup': pickup,
        'dropoff': dropoff,
        'home_size': home,
        'boxes': boxes,
        'pickup_access': pickup_access,
        'dropoff_access': dropoff_access,
        'pickup_walk': pickup_walk,
        'dropoff_walk': dropoff_walk,
        'heavy_items': heavy,
        'photo_link': photo,
        'notes': notes,
        'inventory': details,
        'intake': {'local_area':local_area,'service_details':details},
        'source': 'Gmail',
        'lead_type': 'Instant estimate' if subject.startswith('New Instant') else 'Form',
        'stage': 'New Lead',
        'priority': 'Normal',
        'language': 'English',
    }


def _payment_payload(kind, subject, body):
    if kind == 'wix_payment':
        m = re.search(r'(.+?) paid ([\d,.]+) CAD for Invoice (\d+)', body, re.I)
        return {'kind': 'Wix payment', 'payer': m.group(1).strip() if m else '', 'amount': m.group(2) if m else '', 'invoice': m.group(3) if m else '', 'subject': subject}
    received = re.search(r"You've received \$([\d,.]+) from (.+?) and it has been automatically deposited", subject, re.I)
    if not received:
        return None
    ref = _value_after(body, 'Reference Number')
    msg = _value_after(body, 'Message')
    return {'kind': 'Interac deposit', 'payer': received.group(2).strip(), 'amount': received.group(1), 'reference': ref, 'message': msg, 'subject': subject}


def _call_payload(kind, subject, body, attachments):
    if kind == 'missed_call':
        who = subject.split(':', 1)[1].strip() if ':' in subject else subject
        event_type = 'Missed call'
    else:
        who = re.sub(r'^You Have a New Voicemail from\s+', '', subject, flags=re.I).strip()
        event_type = 'Voicemail'
    phone_match = re.search(r'(?<!\d)(?:1)?([2-9]\d{9})(?!\d)', who + ' ' + body)
    ts = re.search(r'(?<!\d)(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})(?!\d)', body)
    return {'kind': event_type, 'caller': who, 'phone': phone_match.group(1) if phone_match else '', 'call_time': ts.group(1) if ts else '', 'attachments': attachments}




def _call_occurred_iso(parsed):
    raw=str((parsed or {}).get('call_time') or (parsed or {}).get('occurred_at') or '').strip()
    if not raw:
        return ''
    try:
        local=datetime.strptime(raw,'%m/%d/%Y %H:%M:%S').replace(tzinfo=MONTREAL)
        return local.astimezone(timezone.utc).isoformat(timespec='seconds')
    except ValueError:
        pass
    try:
        dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=MONTREAL)
        return dt.astimezone(timezone.utc).isoformat(timespec='seconds')
    except ValueError:
        return ''

def _duplicate_call(db, parsed, event_type, seconds=90):
    phone = parsed.get('phone') or ''
    caller = (parsed.get('caller') or '').lower()
    stamp = parsed.get('call_time') or ''
    if not stamp:
        return False
    try:
        current = datetime.strptime(stamp, '%m/%d/%Y %H:%M:%S')
    except ValueError:
        return False
    recent = rows(db, "SELECT payload_json FROM integration_events WHERE event_type=? ORDER BY created_at DESC LIMIT 80",(event_type,))
    for item in recent:
        try:
            old = json.loads(item['payload_json']).get('parsed', {})
            old_stamp = old.get('call_time') or ''
            old_dt = datetime.strptime(old_stamp, '%m/%d/%Y %H:%M:%S')
        except Exception:
            continue
        same_identity = (phone and phone == old.get('phone')) or (caller and caller == (old.get('caller') or '').lower())
        if same_identity and abs((current - old_dt).total_seconds()) <= seconds:
            return True
    return False



def _digits(value):
    raw=re.sub(r'\s*(?:ext\.?|extension|x|#)\s*\d+\s*$','',str(value or ''),flags=re.I)
    digits=re.sub(r'\D','',raw)
    if len(digits)==11 and digits.startswith('1'):digits=digits[1:]
    return digits if re.fullmatch(r'[2-9]\d{2}[2-9]\d{6}',digits) else ''


def _caller_name(parsed):
    raw = (parsed.get('caller') or '').strip()
    phone = parsed.get('phone') or ''
    if phone:
        raw = re.sub(r'\b(?:1)?' + re.escape(phone) + r'\b', '', re.sub(r'\D', '', raw)) if raw.isdigit() else raw
    raw = re.sub(r'\b\d{10,11}\b', '', raw).strip(' -:,.')
    if not raw or raw.lower() in ('unknown caller', 'anonymous'):
        return 'Unknown Caller'
    return raw[:180]


def match_inquiry_project(db,parsed,kind='call'):
    """Match exact contact facts and an explicit service date, never recency/name.

    A client's several projects remain separate. Shared contact information and
    conflicting identifiers require staff to choose the correct service.
    """
    email=str(parsed.get('email') or '').strip().lower();phone=_digits(parsed.get('phone'))
    supplied_date=str(parsed.get('move_date') or '').strip()
    try:day=datetime.strptime(supplied_date,'%Y-%m-%d').date().isoformat()
    except ValueError:day=''
    leads=rows(db,"SELECT id,client_name,email,phone,move_date,pickup,stage,assigned_to,version FROM leads WHERE email<>'' OR phone<>'' ORDER BY id")
    emails=[r for r in leads if email and r['email'].strip().lower()==email]
    phones=[r for r in leads if phone and _digits(r['phone'])==phone]
    candidates={r['id']:r for r in emails+phones};items=list(candidates.values());items.sort(key=lambda r:r['id'])
    email_ids={r['id'] for r in emails};phone_ids={r['id'] for r in phones}
    reason='';selected=None;requires_review=False
    if not email and not phone:
        reason='No usable client email or phone was provided. Check the original inquiry before creating a service.';requires_review=True
    elif email_ids and phone_ids and email_ids!=phone_ids:
        reason='Email and phone identify different client services. Compare the original inquiry and choose the correct service.';requires_review=True
    elif email and not emails and phones and any(r['email'].strip() and r['email'].strip().lower()!=email for r in phones):
        reason='This phone belongs to a service with a different email. Confirm the client identity before attaching the inquiry.';requires_review=True
    elif len(items)>1:
        dated=[r for r in items if day and r['move_date']==day]
        if len(dated)==1:selected=dated[0]
        else:
            reason='Several services use this contact information. Choose the service or create a separate new inquiry.';requires_review=True
    elif items:
        candidate=items[0]
        if kind in ('form','quote') and day and candidate['move_date'] and candidate['move_date']!=day:
            reason='The requested date is different from the existing service. A new service inquiry will be created.'
        elif candidate['stage'] in ('Completed','Closed','Lost'):
            reason='This contact matches a closed service. Confirm whether this is a new request or concerns the previous service.';requires_review=True
        else:selected=candidate
    if selected and selected['stage'] in ('Completed','Closed','Lost'):
        selected=None;requires_review=True;reason='The requested date matches a closed service. Confirm whether this is a new inquiry or a follow-up on that service.'
    fingerprint=hashlib.sha256(dumps({'email':email,'phone':phone,'date':day,'kind':kind,'candidates':items}).encode()).hexdigest()
    return {'lead':selected,'candidates':items,'requires_review':requires_review,'reason':reason,'fingerprint':fingerprint}


def _match_lead_by_phone(db,phone):
    result=match_inquiry_project(db,{'phone':phone},'call')
    return result['lead'] if not result['requires_review'] else None


def _matching_payload(result):
    return {k:v for k,v in result.items() if k!='lead'}


def _review_task(db,event_key,title):
    from .platform_common import business_day
    if not one(db,'SELECT id FROM tasks WHERE legacy_id=?',('gmail-match:'+event_key,)):
        insert(db,'tasks',dict(id=uid('TASK'),legacy_id='gmail-match:'+event_key,title=title,due_date=business_day(),owner='',created_at=now()))


def _record_incoming_message(db, lead_id, actor, subject, body, created_at=None):
    # Stable Gmail event IDs provide deduplication in the enclosing transaction.
    # Different replies/forms can legitimately have the same subject.
    mid = uid('MSG')
    insert(db, 'messages', dict(
        id=mid, lead_id=lead_id, kind='Incoming email', subject=(subject or '')[:300],
        body=(body or '')[:12000], language='English', author=actor, created_at=created_at or now()
    ))
    return mid


def _record_call_and_followup(db, lead_id, parsed, subject, actor, gmail_id, event_type):
    cid = uid('CALL')
    title = 'Return missed call' if event_type == 'gmail_missed_call' else 'Review voicemail'
    occurred_at=_call_occurred_iso(parsed) or now()
    details = {
        'phone': parsed.get('phone') or '',
        'caller': _caller_name(parsed),
        'call_time': parsed.get('call_time') or '',
        'occurred_at': occurred_at,
        'subject': subject or '',
        'source': 'Gmail / UniFi',
        'gmail_id': gmail_id,
        'voicemail': event_type == 'gmail_voicemail',
        'attachments': parsed.get('attachments') or [],
    }
    insert(db, 'calls', dict(id=cid, legacy_id=None, lead_id=lead_id, payload_json=dumps(details), created_at=occurred_at))
    open_task = one(db, "SELECT id,title FROM tasks WHERE lead_id=? AND title IN ('Return missed call','Review voicemail') AND status='Open' AND historical=0 ORDER BY CASE WHEN title='Review voicemail' THEN 0 ELSE 1 END, created_at DESC LIMIT 1", (lead_id,))
    if not open_task:
        lead = one(db, 'SELECT assigned_to FROM leads WHERE id=?', (lead_id,)) or {}
        due_date = datetime.fromisoformat(occurred_at).astimezone(ZoneInfo('America/Toronto')).date().isoformat()
        insert(db, 'tasks', dict(id=uid('TASK'), legacy_id=None, lead_id=lead_id, title=title, due_date=due_date, owner=lead.get('assigned_to') or '', status='Open', historical=0, raw_json=dumps({'call_id': cid, 'gmail_id': gmail_id}), created_at=occurred_at))
    elif title=='Review voicemail' and open_task.get('title')!='Review voicemail':
        db.execute("UPDATE tasks SET title=?,raw_json=? WHERE id=?",('Review voicemail',dumps({'call_id':cid,'gmail_id':gmail_id}),open_task['id']))
    return cid


def _quote_inquiry(db,data,payload,actor,event_key,new_lead,matched=None):
    from .inquiry_intake import photo_link
    from .platform_common import add_task
    raw_photo=str(data.get('photo_link') or '').strip()
    creation_data=dict(data)
    if raw_photo.casefold() in ('','-','n/a','na','none','no'):
        creation_data['photo_link']=''
    else:
        try:
            creation_data['photo_link']=photo_link(raw_photo)
        except HTTPException as ex:
            if ex.status_code!=422:raise
            # The original parsed value and body remain in the source event.
            # An optional photo reference must not reject the client's inquiry.
            creation_data['photo_link']=''
            payload['photo_link_review']={'reason':str(ex.detail),'original_preserved':True}
    existing=one(db,'SELECT * FROM leads WHERE external_id=?',(event_key,))
    selected=existing or matched;created=False
    if selected:lead_id=selected['id']
    else:
        selected=new_lead(db,creation_data,actor,external_id=event_key,suppressed=1);lead_id=selected['id'];created=True
        db.execute('UPDATE leads SET created_at=? WHERE id=?',(payload.get('received_at') or now(),lead_id))
    message=_record_incoming_message(db,lead_id,actor,payload.get('subject') or '',payload.get('body') or payload.get('snippet') or '',created_at=payload.get('received_at'))
    payload.update(lead_id=lead_id,message_id=message,origin_label='Quote Form')
    if payload.get('photo_link_review'):
        add_task(db,lead_id,'Review client photo / folder reference in the original quote form',selected.get('assigned_to') or '',key='gmail-photo:'+event_key)
    if not created:
        add_task(db,lead_id,'Review new quote form details for this service',selected.get('assigned_to') or '',key='quote-form:'+event_key)
    return lead_id,created


def _new_call_lead(db,parsed,payload,actor,event_key,new_lead,event_type):
    call_data={'client_name':_caller_name(parsed),'phone':parsed.get('phone') or '','email':'','source':'UniFi',
        'lead_type':'Voicemail' if event_type=='gmail_voicemail' else 'Missed Call','stage':'New Lead','priority':'High',
        'notes':str(payload.get('subject') or payload.get('snippet') or '').strip(),'language':'English'}
    lead=new_lead(db,call_data,actor,external_id=event_key,suppressed=1)
    db.execute("DELETE FROM tasks WHERE lead_id=? AND title='Review new inquiry' AND status='Open'",(lead['id'],))
    return lead


def promote_pending_call_events(db, actor, new_lead, limit=120):
    """Promote already-detected Gmail missed calls/voicemails into local CRM leads/calls without mutating Gmail."""
    promoted=0
    pending=rows(db,"""SELECT * FROM integration_events
      WHERE (event_type IN ('gmail_missed_call','gmail_voicemail') AND status='Needs review')
         OR (event_type='gmail_duplicate_call' AND status='Ignored')
      ORDER BY created_at ASC LIMIT ?""",(max(1,min(int(limit),300)),))
    for ev in pending:
        try:payload=json.loads(ev.get('payload_json') or '{}')
        except Exception:payload={}
        parsed=payload.get('parsed') if isinstance(payload,dict) else {}
        if not isinstance(parsed,dict):parsed={}
        if payload.get('matching',{}).get('requires_review'):continue
        effective_type=ev['event_type']
        if effective_type=='gmail_duplicate_call':
            if 'voice' not in str(parsed.get('kind') or '').lower():
                continue
            effective_type='gmail_voicemail'
        if payload.get('lead_id') and payload.get('call_id'):
            db.execute("UPDATE integration_events SET status='Imported' WHERE id=?",(ev['id'],));continue
        match=match_inquiry_project(db,parsed,'call');matched=match['lead']
        if match['requires_review']:
            payload['matching']=_matching_payload(match)
            payload['origin_label']='Voicemail' if effective_type=='gmail_voicemail' else 'Missed Call'
            db.execute("UPDATE integration_events SET event_type=?,status='Needs review',payload_json=? WHERE id=?",(effective_type,dumps(payload),ev['id']))
            _review_task(db,ev['event_key'],'Match incoming voicemail / missed call to the correct client service')
            continue
        if matched:
            lead_id=matched['id']
        else:
            lead=_new_call_lead(db,parsed,payload,actor,ev['event_key'],new_lead,effective_type)
            lead_id=lead['id']
        call_id=_record_call_and_followup(db,lead_id,parsed,str(payload.get('subject') or ''),actor,str(payload.get('gmail_id') or ev['object_id']),effective_type)
        payload['call_id']=call_id;payload['lead_id']=lead_id
        payload['origin_label']='Voicemail' if effective_type=='gmail_voicemail' else 'Missed Call'
        db.execute("UPDATE integration_events SET event_type=?,object_id=?,payload_json=?,status='Imported' WHERE id=?",(effective_type,lead_id,dumps(payload),ev['id']))
        promoted+=1
    return promoted


def sync_gmail_readonly(path, access_token, actor, new_lead, days=14, max_per_feed=30, fluid=False, feed_names=None):
    days = max(1, min(int(days), 60))
    max_per_feed = max(1, min(int(max_per_feed), 50))
    summary = {'scanned': 0, 'new_events': 0, 'new_leads': 0, 'duplicates': 0, 'ignored': 0, 'needs_review':0, 'feeds': {}, 'errors': [], 'fluid': bool(fluid), 'promoted_existing_calls': 0}
    if fluid:
        with transaction(path) as db:
            summary['promoted_existing_calls']=promote_pending_call_events(db,actor,new_lead)
    try:
        label_rows = _api(access_token, 'labels').get('labels') or []
    except requests.RequestException as exc:
        failure = gmail_read_failure(exc)
        summary['errors'].append({'feed': 'connection', 'error': failure['error'], 'code': failure['code']})
        return summary
    label_ids = {x.get('name'): x.get('id') for x in label_rows if x.get('name') and x.get('id')}
    for feed, label_name in FEEDS.items():
        if feed_names is not None and feed not in feed_names:continue
        label_id = label_ids.get(label_name)
        if not label_id:
            summary['feeds'][feed] = 0
            summary['errors'].append({'feed': feed, 'error': 'Gmail label not found: ' + label_name})
            continue
        try:
            with transaction(path) as db:
                cursor=one(db,"SELECT value_json FROM settings WHERE key=?",('gmail_cursor_'+feed,))
                cursor=json.loads(cursor['value_json']) if cursor else {}
            params={'labelIds':label_id,'q':cursor.get('query') or f'newer_than:{days}d','maxResults':max_per_feed}
            if cursor.get('token'):params['pageToken']=cursor['token']
            listing=_api(access_token,'messages',params)
            messages=list(listing.get('messages') or [])
            pages=1
            while listing.get('nextPageToken') and pages<10:
                params['pageToken']=listing['nextPageToken']
                listing=_api(access_token,'messages',params);messages.extend(listing.get('messages') or []);pages+=1
            from .platform_common import put_setting
            next_cursor={'query':params['q'],'token':listing.get('nextPageToken')} if listing.get('nextPageToken') else {}
            listing['messages']=messages
            if listing.get('nextPageToken'):summary['errors'].append({'feed':feed,'error':'Inquiry backlog is still importing; the next sync will continue from this page.'})
        except requests.RequestException as exc:
            summary['feeds'][feed] = 0
            failure = gmail_read_failure(exc)
            summary['errors'].append({'feed': feed, 'error': failure['error'], 'code': failure['code']})
            return summary
        ids = [x['id'] for x in listing.get('messages') or []]
        summary['feeds'][feed] = len(ids)
        errors_before=len(summary['errors'])
        for mid in ids:
            summary['scanned'] += 1
            event_key = 'gmail:' + mid
            from contextlib import closing
            with closing(connect(path)) as db:
                if one(db, 'SELECT id FROM integration_events WHERE event_key=?', (event_key,)):
                    summary['duplicates'] += 1
                    continue
            try:
                message = _api(access_token, 'messages/' + quote(mid, safe=''), {'format': 'full'})
            except requests.RequestException as exc:
                failure = gmail_read_failure(exc)
                summary['errors'].append({'feed': feed, 'message_id': mid, 'error': failure['error'], 'code': failure['code']})
                if failure['stop_sync']:return summary
                continue
            headers = _headers(message)
            received_at = _gmail_received_iso(message,headers) or now()
            subject = headers.get('subject', '')
            body = _clean_html(_body(message.get('payload') or {}))
            attachments = _attachments(message.get('payload') or {})
            payload = {'gmail_id': mid, 'thread_id': message.get('threadId'), 'subject': subject, 'from': headers.get('from', ''), 'date': headers.get('date', ''), 'received_at': received_at, 'snippet': message.get('snippet', '')}
            event_type = ''
            object_id = mid
            status = 'Needs review'
            with transaction(path) as db:
                # A second sync can pass the earlier read while this one is fetching Gmail.
                if one(db, 'SELECT id FROM integration_events WHERE event_key=?', (event_key,)):
                    summary['duplicates'] += 1
                    continue
                counters={key:summary[key] for key in ('new_leads','ignored','needs_review')}
                db.execute('SAVEPOINT incoming_inquiry')
                try:
                    if feed == 'quote':
                        data = _quote_fields(body, subject)
                        payload['parsed']=data;payload['body']=body[:20000];payload['origin_label']='Quote Form'
                        event_type='gmail_quote'
                        existing = one(db, 'SELECT id FROM leads WHERE external_id=?', (event_key,))
                        match=match_inquiry_project(db,data,'quote')
                        if match['requires_review'] and not existing:
                            payload['matching']=_matching_payload(match);summary['needs_review']+=1
                            _review_task(db,event_key,'Match incoming quote form to the correct client service')
                        else:
                            object_id,created=_quote_inquiry(db,data,payload,actor,event_key,new_lead,match['lead'])
                            if created:summary['new_leads']+=1
                            status='Imported'
                    elif feed in ('bank', 'wix_payment'):
                        parsed = _payment_payload(feed, subject, body)
                        if not parsed:
                            summary['ignored'] += 1
                            event_type = 'gmail_ignored'
                            status = 'Ignored'
                        else:
                            payload['parsed'] = parsed
                            event_type = 'gmail_bank_payment' if feed == 'bank' else 'gmail_wix_payment'
                    elif feed in ('missed_call', 'voicemail'):
                        parsed = _call_payload(feed, subject, body, attachments)
                        payload['parsed'] = parsed
                        payload['origin_label']='Missed Call' if feed=='missed_call' else 'Voicemail'
                        event_type = 'gmail_missed_call' if feed == 'missed_call' else 'gmail_voicemail'
                        if _duplicate_call(db, parsed, event_type):
                            event_type = 'gmail_duplicate_call'
                            status = 'Ignored'
                            summary['ignored'] += 1
                        elif fluid:
                            match=match_inquiry_project(db,parsed,'call');matched=match['lead']
                            if match['requires_review']:
                                payload['matching']=_matching_payload(match);summary['needs_review']+=1
                                _review_task(db,event_key,'Match incoming voicemail / missed call to the correct client service')
                            else:
                                if matched:object_id=matched['id']
                                else:
                                    lead=_new_call_lead(db,parsed,payload,actor,event_key,new_lead,event_type);object_id=lead['id'];summary['new_leads']+=1
                                call_id = _record_call_and_followup(db, object_id, parsed, subject, actor, mid, event_type)
                                payload['call_id'] = call_id;payload['lead_id'] = object_id;status='Imported'
                    else:
                        summary['ignored'] += 1
                        event_type = 'gmail_ignored'
                        status = 'Ignored'
                except HTTPException as ex:
                    if ex.status_code!=422 or feed not in ('quote','missed_call','voicemail'):raise
                    db.execute('ROLLBACK TO incoming_inquiry')
                    summary.update(counters)
                    summary['needs_review']+=1
                    event_type={'quote':'gmail_quote','missed_call':'gmail_missed_call','voicemail':'gmail_voicemail'}[feed]
                    object_id=mid;status='Needs review'
                    payload['body']=body[:20000]
                    payload['validation_review']={'message':str(ex.detail)[:1000],'original_preserved':True}
                    _review_task(db,event_key,'Match incoming quote form after reviewing its details' if feed=='quote' else 'Match incoming voicemail / missed call after reviewing its details')
                finally:
                    db.execute('RELEASE incoming_inquiry')
                insert(db, 'integration_events', dict(id=uid('GMAIL'), event_key=event_key, event_type=event_type, object_id=object_id, payload_json=dumps(payload), status=status, created_at=now()))
                audit(db, actor, 'Read-only Gmail item imported', 'integration_events', event_key, {'type': event_type, 'status': status})
                summary['new_events'] += 1
        if len(summary['errors'])==errors_before:
            with transaction(path) as db:put_setting(db,'gmail_cursor_'+feed,next_cursor)
    return summary


def review_items(path, limit=100):
    from contextlib import closing
    with closing(connect(path)) as db:
        items = rows(db, "SELECT * FROM integration_events WHERE event_type IN ('gmail_bank_payment','gmail_wix_payment','gmail_quote','gmail_missed_call','gmail_voicemail','gmail_email') AND status='Needs review' ORDER BY created_at DESC LIMIT ?", (min(max(int(limit), 1), 200),))
    out = []
    for item in items:
        raw = item.pop('payload_json', '{}')
        try:
            item['payload'] = json.loads(raw or '{}')
        except Exception:
            item['payload'] = {'subject': 'Unreadable imported Gmail item', 'parsed': {}}
            item['payload_error'] = True
        out.append(item)
    return out


def _review_details(db,event):
    try:payload=json.loads(event['payload_json'])
    except (ValueError,TypeError):raise ValueError('The original inquiry needs a technical review before it can be matched.')
    if not isinstance(payload,dict) or not isinstance(payload.get('parsed'),dict):raise ValueError('The original inquiry does not contain usable client details.')
    kind='quote' if event['event_type']=='gmail_quote' else 'email' if event['event_type']=='gmail_email' else 'call'
    match=match_inquiry_project(db,payload['parsed'],kind)
    return payload,match


def resolve_review_match(path,event_id,data,user,new_lead):
    """One audited staff decision resolves one source event; no Gmail mutations."""
    from fastapi import HTTPException
    from .platform_common import SALES,require_role,text
    require_role(user,SALES)
    if set(data)-{'reviewed','decision','lead_id','note','fingerprint'}:raise HTTPException(422,'Unknown inquiry review field.')
    if data.get('reviewed') is not True:raise HTTPException(422,'Review the original inquiry and selected service first.')
    decision=data.get('decision');lead_id=text(data.get('lead_id',''),'Service',150);note=text(data.get('note',''),'Review note',2000)
    if decision not in ('match','new','dismiss'):raise HTTPException(422,'Choose a service, a new inquiry, or dismiss this item.')
    if not note:raise HTTPException(422,'Add a short note explaining your decision.')
    with transaction(path) as db:
        event=one(db,'SELECT * FROM integration_events WHERE id=?',(event_id,))
        if not event or event['event_type'] not in ('gmail_quote','gmail_missed_call','gmail_voicemail','gmail_email'):raise HTTPException(404,'Client inquiry review item not found.')
        try:payload,match=_review_details(db,event)
        except ValueError as ex:raise HTTPException(409,str(ex))
        prior=payload.get('resolution') or {}
        if event['status']!='Needs review':
            if prior.get('decision')==decision and (decision!='match' or prior.get('lead_id')==lead_id):return {'ok':True,'duplicate':True,'lead_id':prior.get('lead_id'),'status':event['status']}
            raise HTTPException(409,'This inquiry was already resolved. Refresh its history before making another decision.')
        if data.get('fingerprint')!=match['fingerprint']:raise HTTPException(409,'The matching services changed. Refresh and compare them before choosing.')
        parsed=payload['parsed'];created=False
        if decision=='dismiss':status='Ignored';lead_id=None
        else:
            selected=next((r for r in match['candidates'] if r['id']==lead_id),None) if decision=='match' else None
            if decision=='match' and not selected:raise HTTPException(409,'Choose one of the current matching services. No inquiry was attached.')
            if event['event_type']=='gmail_quote':
                lead_id,created=_quote_inquiry(db,parsed,payload,user['id'],event['event_key'],new_lead,selected)
            elif event['event_type']=='gmail_email':
                if not selected:
                    fields={k:v for k,v in parsed.items() if k in ('client_name','email','phone','move_date')};fields.update(source='Gmail',lead_type='Email',client_name=fields.get('client_name') or 'Email inquiry')
                    selected=new_lead(db,fields,user['id'],external_id=event['event_key'],suppressed=1);created=True
                lead_id=selected['id'];payload['message_id']=_record_incoming_message(db,lead_id,user['id'],payload.get('subject',''),payload.get('body') or payload.get('snippet') or '',payload.get('received_at'))
                from .platform_common import add_task
                add_task(db,lead_id,'Read incoming client email',selected.get('assigned_to') or '',key='gmail-email:'+event['event_key'])
            else:
                if not selected:selected=_new_call_lead(db,parsed,payload,user['id'],event['event_key'],new_lead,event['event_type']);created=True
                lead_id=selected['id'];payload['call_id']=_record_call_and_followup(db,lead_id,parsed,payload.get('subject',''),user['id'],payload.get('gmail_id') or event['object_id'],event['event_type'])
            payload['lead_id']=lead_id;status='Imported'
        payload['matching']={**_matching_payload(match),'requires_review':False}
        payload['origin_label']='Quote Form' if event['event_type']=='gmail_quote' else 'Email' if event['event_type']=='gmail_email' else 'Voicemail' if event['event_type']=='gmail_voicemail' else 'Missed Call'
        payload['resolution']={'decision':decision,'lead_id':lead_id,'note':note,'actor':user['id'],'resolved_at':now(),'created_service':created}
        db.execute('UPDATE integration_events SET status=?,object_id=?,payload_json=? WHERE id=?',(status,lead_id or event['object_id'],dumps(payload),event_id))
        db.execute("UPDATE tasks SET status='Done' WHERE legacy_id=?",('gmail-match:'+event['event_key'],))
        audit(db,user['id'],'Incoming Gmail inquiry resolved','integration_events',event_id,payload['resolution'])
    return {'ok':True,'duplicate':False,'lead_id':lead_id,'status':status,'created_service':created}


def register_gmail_resolution(app,path,auth,new_lead):
    from contextlib import closing
    from fastapi import Depends,HTTPException
    from .platform_common import SALES,require_role

    @app.get('/api/gmail/review/{event_id}/match-options')
    def options(event_id:str,u=Depends(auth)):
        require_role(u,SALES)
        with closing(connect(path)) as db:
            event=one(db,'SELECT * FROM integration_events WHERE id=?',(event_id,))
            if not event or event['event_type'] not in ('gmail_quote','gmail_missed_call','gmail_voicemail','gmail_email'):raise HTTPException(404,'Client inquiry review item not found.')
            try:payload,match=_review_details(db,event)
            except ValueError as ex:raise HTTPException(409,str(ex))
        return {'id':event['id'],'event_type':event['event_type'],'status':event['status'],'payload':payload,'matching':_matching_payload(match)}

    @app.post('/api/gmail/review/{event_id}/resolve')
    def resolve(event_id:str,data:dict,u=Depends(auth)):
        return resolve_review_match(path,event_id,data,u,new_lead)
