"""Durable Wix automation intake. Source notices are never bank receipts.

Uses the existing Wix Send HTTP request contract. The shared key is only
compared against an environment hash; it is never persisted in the database.
Calendar entries remain source records until an office user plans the service.
"""
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import hashlib
import hmac
import json
import logging
import os
import re

from fastapi import Depends, HTTPException, Request
from .store import connect, transaction, one, rows, insert, dumps, now, uid, audit
from .platform_common import require_role
from .schedule_importer import setup as setup_schedule
from .schedule_paste import same_client
from .availability import assert_day_open

OFFICE = ('owner', 'manager', 'sales', 'dispatch', 'accounting')
EDITORS = ('owner', 'manager', 'sales', 'dispatch')
ET = ZoneInfo('America/Toronto')
CUTOFF = '2026-10-19'
FIELDS = ('event', 'documentId', 'number', 'title', 'customer', 'email', 'phone',
          'address', 'city', 'contactId', 'total', 'currency')
LOG = logging.getLogger('crownops.wix')


def title_schedule(title):
    """Existing Crown Wix titles use MM/DD/YYYY. Never infer a missing year.

    Multiple dates, alternatives, time ranges and DST anomalies require review.
    A known date with an unknown start can still appear as an untimed source.
    """
    matches = list(re.finditer(r'(?<!\d)(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})(?!\d)', title))
    if len(matches) != 1:
        return '', '', 'Review service date: use one YYYY-MM-DD or MM/DD/YYYY date, including year.'
    try:
        raw = matches[0][0]
        day = date.fromisoformat(raw) if '-' in raw else datetime.strptime(raw, '%m/%d/%Y').date()
    except ValueError:
        return '', '', 'The service date is invalid.'
    if re.search(r'\b(tbd|tentative|or|ou)\b|\?', title, re.I):
        return '', '', 'The title contains an unconfirmed date or alternative; review it first.'
    times = list(re.finditer(r'(?<![\d:])(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?(?!\w)', title, re.I))
    start = ''
    if len(times) == 1:
        t = times[0]; hour = int(t[1]); minute = int(t[2] or 0)
        if not 1 <= hour <= 12 or minute > 59:
            return day.isoformat(), '', 'Review the invalid start time.'
        hour = hour % 12 + (12 if t[3].lower() == 'p' else 0)
        start = f'{hour:02d}:{minute:02d}'
    elif not times:
        # A colon is required for 24-hour time, avoiding staff extension numbers.
        clock = re.findall(r'(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])', title)
        if len(clock) == 1 and int(clock[0][0]) <= 23 and int(clock[0][1]) <= 59:
            start = f'{int(clock[0][0]):02d}:{int(clock[0][1]):02d}'
    if not start:
        return day.isoformat(), '', 'Start time needs review; no time or duration has been assumed.'
    local = datetime.fromisoformat(day.isoformat() + 'T' + start)
    a, b = local.replace(tzinfo=ET, fold=0), local.replace(tzinfo=ET, fold=1)
    if a.utcoffset() != b.utcoffset() or a.astimezone(timezone.utc).astimezone(ET).replace(tzinfo=None) != local:
        return day.isoformat(), '', 'Start time falls in a daylight-saving change; review it.'
    return day.isoformat(), start, ''


def normalize(data):
    if not isinstance(data, dict):
        raise HTTPException(422, 'Expected a JSON object under data.')
    out = {}
    for field in FIELDS:
        value = data.get(field, '')
        if value is None: value = ''
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise HTTPException(422, f'Invalid {field}.')
        value = str(value).strip()
        if len(value) > (2000 if field == 'title' else 500):
            raise HTTPException(422, f'{field} is too long.')
        out[field] = value
    if out['event'] not in ('quote_accepted', 'invoice_paid'):
        raise HTTPException(422, 'Unsupported Wix event.')
    if not out['documentId'] or '{{' in out['documentId']:
        raise HTTPException(422, 'Map the Wix document ID before enabling this automation.')
    out['email'] = out['email'].lower()
    out['currency'] = out['currency'].upper()
    if out['total']:
        try:
            amount = Decimal(out['total'])
            if not amount.is_finite() or amount < 0 or amount > 100000000:
                raise InvalidOperation
            out['total'] = format(amount.quantize(Decimal('.01')), 'f')
        except (InvalidOperation, ValueError):
            raise HTTPException(422, 'Invalid Wix total.')
    return out


def configuration(db):
    row = one(db, "SELECT value_json FROM settings WHERE key='wix_sync'")
    return json.loads(row['value_json']) if row else {'paused': False}


def add_calendar(db, record, payload, day, start, note):
    if day < CUTOFF:
        return 'Outside import range', 'Service is before October 19, 2026.', None
    assert_day_open(db, day)
    # Match conservatively: even a name-only match is held for office review,
    # never silently merged or used to overwrite an existing booking.
    person = {'client_name': payload['customer'], 'email': payload['email'], 'phone': payload['phone']}
    for old in rows(db, 'SELECT * FROM imported_schedule_events WHERE job_date=?', (day,)):
        if same_client(person, old):
            return 'Needs review', 'Possible existing schedule entry for this client and day; no duplicate added.', None
    for old in rows(db, 'SELECT j.start_at,l.client_name,l.email,l.phone FROM jobs j JOIN leads l ON l.id=j.lead_id WHERE j.status!=?', ('Cancelled',)):
        if datetime.fromisoformat(old['start_at']).astimezone(ET).date().isoformat() == day and same_client(person, old):
            return 'Needs review', 'Possible existing job for this client and day; no duplicate added.', None
    source_key = 'WIX:quote:' + hashlib.sha256(payload['documentId'].encode()).hexdigest()
    batch = 'WIX-' + record['id']
    insert(db, 'schedule_import_batches', dict(id=batch, sha256=record['fingerprint'], filename='Wix accepted quote',
        range_start=day, range_end=day, created_at=now(), report_json=dumps({'wix': True, 'events': 1})))
    calendar_id = uid('WIXCAL')
    calendar = dict(id=calendar_id, source_key=source_key, source_type='wix', source_ref='Wix quote '+payload['number'],
        date=day, timezone='America/Toronto', client_name=payload['customer'], email=payload['email'], phone=payload['phone'], lead_id=record.get('lead_id'),
        service='Moving / service', time_label=start or 'Time to confirm', start_time=start or None,
        source_status='Accepted in Wix', pickup_raw='', dropoffs_raw=[], crew_raw='', truck_label='',
        notes_raw=payload['title'], flags=['WIX_SERVICE_PLAN_REQUIRED', 'DURATION_NOT_SUPPLIED', 'CREW_NOT_ASSIGNED', 'TRUCK_NOT_IDENTIFIED'] + (['START_TIME_NOT_EXACT'] if not start else []),
        disposition='Wix accepted - plan service', notifications_suppressed=True, links=[], comments=[], raw_cells=[])
    insert(db, 'imported_schedule_events', dict(id=calendar_id, batch_id=batch, source_key=source_key, job_date=day,
        client_name=payload['customer'], email=payload['email'], phone=payload['phone'], service=calendar['service'],
        time_label=calendar['time_label'], payload_json=dumps(calendar), disposition=calendar['disposition']))
    return 'On schedule', note or 'Review duration, addresses, crew and truck before confirming the service.', calendar_id


def process_quote(db, record, payload, new_lead):
    day, start, note = title_schedule(payload['title'])
    record.update(service_date=day, start_time=start, status='Needs review', note=note)
    if day and day < CUTOFF:
        record.update(status='Outside import range', note='Service is before October 19, 2026.')
        return
    if not payload['customer'] or '{{' in payload['customer']:
        record['note'] = 'Client name is missing; check the Wix field mapping.'
        return
    if payload['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', payload['email']):
        record['note'] = 'Client email needs correction in Wix.'
        return
    candidates = rows(db, 'SELECT * FROM leads WHERE archived_import=0 AND move_date=?', (day,)) if day else []
    candidates = [x for x in candidates if same_client(x, {'client_name': payload['customer'], 'email': payload['email'], 'phone': payload['phone']})]
    if len(candidates) > 1:
        record['note'] = 'Several CRM records may match this client and date. Review before linking.'
        return
    if candidates:
        record['lead_id'] = candidates[0]['id']
    else:
        lead = new_lead(db, dict(client_name=payload['customer'], email=payload['email'], phone=payload['phone'],
            move_date=day, source='Wix', lead_type='Quote Form', notes='Accepted in Wix: '+payload['number']+'\n'+payload['title']+'\nBilling address (not a service address): '+payload['address']+' '+payload['city']),
            'wix', external_id='wix:quote:'+payload['documentId'], suppressed=1, prepare_estimate=False)
        record['lead_id'] = lead['id']
    if day:
        try:
            status, reason, calendar_id = add_calendar(db, record, payload, day, start, note)
            record.update(status=status, note=reason, calendar_id=calendar_id)
        except HTTPException as error:
            if error.status_code != 409: raise
            record['note'] = 'Day is fully booked. Resolve capacity before adding this accepted quote to the schedule.'


def register_wix(app, path, auth, new_lead):
    @app.post('/api/webhooks/wix')
    async def receive(request: Request):
        expected = os.getenv('CROWNOPS_WIX_KEY_SHA256', '')
        if not re.fullmatch(r'[a-f0-9]{64}', expected):
            raise HTTPException(503, 'Wix receiver is not configured.')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 100000: raise HTTPException(413, 'Wix request is too large.')
        try:
            body = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, 'Invalid JSON.')
        data = body.get('data') if isinstance(body, dict) else None
        key = data.get('connectionKey') if isinstance(data, dict) else None
        if not isinstance(key, str) or len(key) < 32 or not hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(), expected):
            raise HTTPException(401, 'Invalid Wix connection key.')
        # A test URL checks real delivery without creating clients or jobs.
        if request.query_params.get('probe') == '1':
            with transaction(path) as db:
                db.execute("INSERT INTO settings(key,value_json) VALUES('wix_last_test',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (dumps({'received_at': now()}),))
            LOG.info('Wix authenticated delivery test succeeded; no business records created')
            return {'ok': True, 'test': True, 'business_records_created': 0}
        payload = normalize(data)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with transaction(path) as db:
            if configuration(db).get('paused'):
                raise HTTPException(503, 'Wix intake is paused by the owner. Retry after it resumes.')
            old = one(db, 'SELECT * FROM wix_events WHERE event=? AND document_id=? AND fingerprint=?', (payload['event'], payload['documentId'], fingerprint))
            if old:
                db.execute('UPDATE wix_events SET deliveries=deliveries+1,last_received_at=? WHERE id=?', (now(), old['id']))
                return {'ok': True, 'duplicate': True, 'status': old['status']}
            prior = one(db, 'SELECT * FROM wix_events WHERE event=? AND document_id=? ORDER BY received_at,id LIMIT 1', (payload['event'], payload['documentId']))
            record = dict(id=uid('WIX'), event=payload['event'], document_id=payload['documentId'], fingerprint=fingerprint,
                payload_json=dumps(payload), received_at=now(), last_received_at=now(), status='Needs review', note='',
                service_date='', start_time='', lead_id=None, calendar_id=None)
            if prior:
                record.update(note='Wix sent changed details for an existing document. Review the original entry; it has not been overwritten.', lead_id=prior['lead_id'], calendar_id=prior['calendar_id'])
            elif payload['event'] == 'invoice_paid':
                record.update(status='Payment review', note='Wix reports this invoice paid. Match it to QuickBooks or bank evidence before recording or allocating a payment.')
            else:
                setup_schedule(db)
                process_quote(db, record, payload, new_lead)
            insert(db, 'wix_events', record)
            audit(db, 'wix', 'Wix notice received', 'wix_events', record['id'], {'event': record['event'], 'status': record['status']})
        LOG.info('Wix notice received: %s; status=%s', payload['event'], record['status'])
        return {'ok': True, 'duplicate': False, 'status': record['status']}

    @app.get('/api/wix/status')
    def status(u=Depends(auth)):
        require_role(u, OFFICE)
        with closing(connect(path)) as db:
            test = one(db, "SELECT value_json FROM settings WHERE key='wix_last_test'")
            return dict(configured=bool(re.fullmatch(r'[a-f0-9]{64}', os.getenv('CROWNOPS_WIX_KEY_SHA256', ''))),
                paused=bool(configuration(db).get('paused')), last_test=json.loads(test['value_json']) if test else None,
                last_received=one(db, 'SELECT max(last_received_at) AS at FROM wix_events')['at'],
                counts=rows(db, 'SELECT status,count(*) AS count FROM wix_events GROUP BY status'), cutoff=CUTOFF, date_format='MM/DD/YYYY or YYYY-MM-DD')

    @app.put('/api/wix/settings')
    def settings(data: dict, u=Depends(auth)):
        require_role(u, ('owner',))
        if type(data.get('paused')) is not bool: raise HTTPException(422, 'Choose pause or resume.')
        with transaction(path) as db:
            db.execute("INSERT INTO settings(key,value_json) VALUES('wix_sync',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (dumps({'paused': data['paused']}),))
            audit(db, u['id'], 'Wix intake paused' if data['paused'] else 'Wix intake resumed', 'settings', 'wix_sync')
        return {'paused': data['paused']}

    @app.get('/api/wix/events')
    def events(offset: int = 0, u=Depends(auth)):
        require_role(u, OFFICE)
        if offset < 0: raise HTTPException(422, 'Invalid page.')
        with closing(connect(path)) as db:
            result = rows(db, 'SELECT * FROM wix_events ORDER BY received_at DESC,id DESC LIMIT 50 OFFSET ?', (offset,))
            total = one(db, 'SELECT count(*) AS n FROM wix_events')['n']
        for rec in result:
            rec['payload'] = json.loads(rec.pop('payload_json')); rec.pop('fingerprint')
        return {'items': result, 'total': total}

    @app.post('/api/wix/events/{eid}/review')
    def review(eid: str, data: dict, u=Depends(auth)):
        require_role(u, OFFICE)
        note = data.get('note', '')
        if not isinstance(note, str) or not 5 <= len(note.strip()) <= 2000:
            raise HTTPException(422, 'Add a short review note explaining what was resolved.')
        with transaction(path) as db:
            rec = one(db, 'SELECT * FROM wix_events WHERE id=?', (eid,))
            if not rec: raise HTTPException(404, 'Wix notice not found.')
            if rec['event'] == 'invoice_paid': require_role(u, ('owner', 'manager', 'accounting'))
            db.execute("UPDATE wix_events SET status='Reviewed',review_note=?,reviewed_by=?,reviewed_at=? WHERE id=?", (note.strip(), u['id'], now(), eid))
            audit(db, u['id'], 'Wix notice reviewed; no payment created', 'wix_events', eid)
        return {'ok': True, 'payments_created': 0}

    @app.post('/api/wix/events/{eid}/schedule')
    def schedule(eid: str, data: dict, u=Depends(auth)):
        require_role(u, EDITORS)
        try:
            day = date.fromisoformat(data.get('date', '')).isoformat()
        except (TypeError, ValueError): raise HTTPException(422, 'Choose the service date.')
        start = data.get('time', '')
        if not isinstance(start, str) or (start and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', start)):
            raise HTTPException(422, 'Use a valid 24-hour start time.')
        with transaction(path) as db:
            rec = one(db, 'SELECT * FROM wix_events WHERE id=?', (eid,))
            if not rec: raise HTTPException(404, 'Wix notice not found.')
            if rec['event'] != 'quote_accepted' or rec['calendar_id']:
                raise HTTPException(409, 'Use the existing service entry; no extra booking has been created.')
            if one(db, 'SELECT id FROM wix_events WHERE event=? AND document_id=? AND id!=?', (rec['event'], rec['document_id'], eid)):
                raise HTTPException(409, 'This document has changed. Resolve its versions before scheduling.')
            payload = json.loads(rec['payload_json'])
            if not payload['customer']: raise HTTPException(422, 'Correct the missing customer in Wix first.')
            setup_schedule(db)
            state, reason, calendar_id = add_calendar(db, rec, payload, day, start, '')
            if not calendar_id: raise HTTPException(409, reason)
            db.execute('UPDATE wix_events SET status=?,note=?,calendar_id=?,service_date=?,start_time=? WHERE id=?', (state, reason, calendar_id, day, start, eid))
            if rec['lead_id']:
                db.execute("UPDATE leads SET move_date=?,version=version+1,updated_at=? WHERE id=? AND move_date=''", (day, now(), rec['lead_id']))
            audit(db, u['id'], 'Wix service date reviewed and added to calendar', 'wix_events', eid)
        return {'ok': True, 'calendar_id': calendar_id}
