"""Read-only Gmail intake for the private CrownOps review build.

This module can list/read Gmail messages after OAuth authorization while outbound
provider actions remain disabled. It never sends, deletes, archives, labels, or
marks Gmail messages read. Non-lead items enter an internal review queue.
"""
import base64
import json
import re
import time
from datetime import datetime
from urllib.parse import quote

import requests

from .store import connect, transaction, one, rows, insert, audit, uid, now, dumps


FEEDS = {
    'quote': 'New Quote Request',
    'bank': 'Scotia Bank',
    'wix_payment': 'We Got Paid $$$',
    'missed_call': 'Missed Calls',
    'voicemail': 'Voice Mail',
}
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
            if r.status_code == 429 or r.status_code >= 500:
                last = requests.HTTPError('Gmail temporary error HTTP ' + str(r.status_code), response=r)
                if attempt < 2:
                    time.sleep(0.4 * (attempt + 1))
                    continue
            r.raise_for_status()
            return r.json()
        except (requests.Timeout, requests.ConnectionError) as exc:
            last = exc
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
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
        return text
    text = re.sub(r'(?is)<(script|style).*?>.*?</\1>', ' ', text)
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</(p|div|tr|li|h[1-6])>', '\n', text)
    return re.sub(r'(?s)<[^>]+>', ' ', text)


def _value_after(text, label):
    m = re.search(r'(?im)^\s*' + re.escape(label) + r'\s*:?\s*(.*?)\s*$', text)
    if m and m.group(1):
        return m.group(1).strip()
    lines = [x.strip() for x in text.splitlines()]
    for i, line in enumerate(lines):
        if line.rstrip(':').strip().lower() == label.lower():
            for nxt in lines[i + 1:i + 5]:
                if nxt:
                    return nxt
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
    pm = re.search(r'(?<!\d)(?:1[- .()]*)?([2-9]\d{2})[- .()]*([2-9]\d{2})[- .()]*([0-9]{4})(?!\d)', raw_phone)
    phone = ''.join(pm.groups()) if pm else re.sub(r'\D', '', raw_phone)[:10]
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
    notes = details
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



def _duplicate_call(db, parsed, seconds=90):
    phone = parsed.get('phone') or ''
    caller = (parsed.get('caller') or '').lower()
    stamp = parsed.get('call_time') or ''
    if not stamp:
        return False
    try:
        current = datetime.strptime(stamp, '%m/%d/%Y %H:%M:%S')
    except ValueError:
        return False
    recent = rows(db, "SELECT payload_json FROM integration_events WHERE event_type IN ('gmail_missed_call','gmail_voicemail') ORDER BY created_at DESC LIMIT 80")
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


def sync_gmail_readonly(path, access_token, actor, new_lead, days=14, max_per_feed=30):
    days = max(1, min(int(days), 60))
    max_per_feed = max(1, min(int(max_per_feed), 50))
    summary = {'scanned': 0, 'new_events': 0, 'new_leads': 0, 'duplicates': 0, 'ignored': 0, 'feeds': {}, 'errors': []}
    label_rows = _api(access_token, 'labels').get('labels') or []
    label_ids = {x.get('name'): x.get('id') for x in label_rows if x.get('name') and x.get('id')}
    for feed, label_name in FEEDS.items():
        label_id = label_ids.get(label_name)
        if not label_id:
            summary['feeds'][feed] = 0
            summary['errors'].append({'feed': feed, 'error': 'Gmail label not found: ' + label_name})
            continue
        try:
            listing = _api(access_token, 'messages', {'labelIds': label_id, 'q': f'newer_than:{days}d', 'maxResults': max_per_feed})
        except requests.RequestException as exc:
            summary['feeds'][feed] = 0
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            summary['errors'].append({'feed': feed, 'error': 'Gmail list failed' + (f' (HTTP {status})' if status else '')})
            continue
        ids = [x['id'] for x in listing.get('messages') or []]
        summary['feeds'][feed] = len(ids)
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
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                summary['errors'].append({'feed': feed, 'message_id': mid, 'error': 'Gmail message read failed' + (f' (HTTP {status})' if status else '')})
                continue
            headers = _headers(message)
            subject = headers.get('subject', '')
            body = _clean_html(_body(message.get('payload') or {}))
            attachments = _attachments(message.get('payload') or {})
            payload = {'gmail_id': mid, 'thread_id': message.get('threadId'), 'subject': subject, 'from': headers.get('from', ''), 'date': headers.get('date', ''), 'snippet': message.get('snippet', '')}
            event_type = ''
            object_id = mid
            status = 'Needs review'
            with transaction(path) as db:
                if feed == 'quote':
                    data = _quote_fields(body, subject)
                    existing = one(db, 'SELECT id FROM leads WHERE external_id=?', (event_key,))
                    if existing:
                        object_id = existing['id']
                        status = 'Imported'
                    else:
                        lead = new_lead(db, data, actor, external_id=event_key, suppressed=1)
                        object_id = lead['id']
                        summary['new_leads'] += 1
                        status = 'Imported'
                    payload['parsed'] = data
                    event_type = 'gmail_quote'
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
                    payload['parsed'] = _call_payload(feed, subject, body, attachments)
                    event_type = 'gmail_missed_call' if feed == 'missed_call' else 'gmail_voicemail'
                    if _duplicate_call(db, payload['parsed']):
                        event_type = 'gmail_duplicate_call'
                        status = 'Ignored'
                        summary['ignored'] += 1
                else:
                    summary['ignored'] += 1
                    event_type = 'gmail_ignored'
                    status = 'Ignored'
                insert(db, 'integration_events', dict(id=uid('GMAIL'), event_key=event_key, event_type=event_type, object_id=object_id, payload_json=dumps(payload), status=status, created_at=now()))
                audit(db, actor, 'Read-only Gmail item imported', 'integration_events', event_key, {'type': event_type, 'status': status})
                summary['new_events'] += 1
    return summary


def review_items(path, limit=100):
    from contextlib import closing
    with closing(connect(path)) as db:
        items = rows(db, "SELECT * FROM integration_events WHERE event_type LIKE 'gmail_%' AND status='Needs review' ORDER BY created_at DESC LIMIT ?", (min(max(int(limit), 1), 200),))
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
