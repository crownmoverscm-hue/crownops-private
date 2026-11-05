"""Reviewed TSV schedule snapshots. Never infer confirmations or financial receipts."""
import csv
import hashlib
import io
import json
import re
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException
from .store import connect, transaction, rows, insert, dumps, now, audit
from .business_zone import BUSINESS_ZONE, zone_name


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def norm(value):
    return ' '.join(str(value or '').casefold().split())


def phone(value):
    value = re.sub(r'\D', '', str(value or ''))
    return value[-10:] if len(value) in (10, 11) else value


def same_client(a, b):
    return bool((a.get('email') and norm(a['email']) == norm(b.get('email'))) or
                (phone(a.get('phone')) and phone(a.get('phone')) == phone(b.get('phone'))) or
                norm(a.get('client_name')) == norm(b.get('client_name')))


def parse_paste(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > 1_000_000:
        raise ValueError('Paste a schedule of up to 1 MB, including its column headings.')
    data = list(csv.reader(io.StringIO(value), delimiter='\t'))
    if len(data) > 5000:
        raise ValueError('Paste no more than 5,000 spreadsheet rows at a time.')
    header = next((i for i, r in enumerate(data) if len(r) > 18 and
                   norm(r[8]) == 'status' and norm(r[9]) == 'start date' and
                   norm(r[2]) == 'services' and norm(r[3]) == 'client name | email'), None)
    if header is None:
        raise ValueError('Include the original schedule headings: Services, Client Name | Email, Status and Start date.')
    events = []
    for line, cells in enumerate(data[header + 1:], header + 2):
        r = [c.strip() for c in cells]
        r += [''] * max(0, 46 - len(r))
        if norm(r[8]) != 'in progress':
            continue
        if not r[3]:
            raise ValueError(f'Row {line}: the In progress service needs a client name.')
        day = ''
        if r[9]:
            for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
                try:
                    day = datetime.strptime(r[9], fmt).date().isoformat()
                    break
                except ValueError:
                    pass
            if not day:
                raise ValueError(f'Row {line}: use MM/DD/YYYY or YYYY-MM-DD for the service date.')
        email = re.search(r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}', r[3], re.I)
        email = email[0].lower() if email else ''
        name = r[3].split('|')[0].strip()
        if not name or '@' in name:
            raise ValueError(f'Row {line}: enter the client name before the email address.')
        flags = ['Booking state needs review', 'Truck needs review']
        for missing, label in [(not day, 'Date missing'), (not r[2], 'Service type missing'),
                               (not r[18], 'Pickup missing'), (not any(r[19:25]), 'Destination missing'),
                               (not r[17], 'Crew names missing'), (not email, 'Email missing')]:
            if missing:
                flags.append(label)
        if not re.fullmatch(r'\d{1,2}:\d{2}(?:\s*[AP]M)?', r[10], re.I):
            flags.append('Exact start time needs review')
        if r[13] or r[14]:
            flags.append('Deposit/payment notes are unverified')
        identity = [norm(name), day, norm(r[2])]
        e = dict(id='CSP-' + digest(identity)[:20], source_key='PASTE:' + digest(identity),
                 source_type='pasted_schedule', source_row=line, source_ref=f'Pasted schedule / row {line}',
                 source_fingerprint=digest(r), date=day, timezone=zone_name(), client_name=name,
                 client_label_raw=r[3], email=email, phone=r[4], service=r[2], source_status=r[8],
                 sales_owner=r[0], crew_size=r[1], crew_raw=r[17], truck_label='', time_label=r[10] or 'Time needed',
                 start_time=None, depart_base_raw=r[11], quoted_hours_raw=r[12], actual_hours_raw=r[16],
                 deposit_raw=r[13], payment_raw=r[14], pickup_raw=r[18],
                 dropoffs_raw=[{'address':v, 'source_cell':f'column {i+1}'} for i, v in enumerate(r) if 19 <= i < 25 and v],
                 notes_raw=r[6], referral_raw=r[5], raw_values=r,
                 raw_cells=[{'cell':f'column {i+1}', 'value':v} for i,v in enumerate(r) if v],
                 comments=[], links=[], flags=flags,
                 disposition='Imported - review required', notifications_suppressed=True)
        events.append(e)
    if not events:
        raise ValueError('No services marked In progress were found.')
    if len(events) > 1000:
        raise ValueError('Import at most 1,000 services at a time.')
    return events


def preview(db, value):
    events = parse_paste(value)
    existing = [json.loads(r['payload_json']) for r in rows(db, 'SELECT payload_json FROM imported_schedule_events')]
    jobs = rows(db, "SELECT j.id,j.phase service,j.start_at,l.client_name,l.email,l.phone FROM jobs j JOIN leads l ON l.id=j.lead_id WHERE j.status!='Cancelled'")
    for j in jobs:
        j['date'] = datetime.fromisoformat(j['start_at']).astimezone(BUSINESS_ZONE).date().isoformat()
    seen = set()
    for e in events:
        e['action'] = 'Import'
        if e['source_key'] in seen:
            e['action'] = 'Skip duplicate row'
        else:
            old = next((x for x in existing if x.get('source_key') == e['source_key'] or
                        (x.get('date') == e['date'] and norm(x.get('service')) == norm(e['service']) and same_client(e, x))), None)
            job = next((x for x in jobs if x['date'] == e['date'] and
                        norm(x['service']) == norm(e['service']) and same_client(e, x)), None)
            if old:
                e['action'] = 'Skip existing import'
                if old.get('source_fingerprint') != e['source_fingerprint']:
                    e['flags'].append('Existing source differs; retained without overwrite')
            elif job:
                e['action'] = 'Skip existing job'
                e['flags'].append('Existing job retained without overwrite')
                e['existing_job_id'] = job['id']
        seen.add(e['source_key'])
    summary = dict(selected=len(events), new=sum(e['action'] == 'Import' for e in events),
                   skipped=sum(e['action'] != 'Import' for e in events), undated=sum(not e['date'] for e in events),
                   missing_service=sum(not e['service'] for e in events), emails_sent=0, payments_created=0)
    return dict(events=events, summary=summary, preview_token=digest(events))


def register_pasted_schedule(app, path, auth):
    def require_import(u):
        if u['role'] not in ('owner', 'manager'):
            raise HTTPException(403, 'Only owners and managers can import a schedule.')

    @app.post('/api/schedule-imports/paste-preview')
    def preview_route(data: dict, u=Depends(auth)):
        require_import(u)
        try:
            with closing(connect(path)) as db:
                return preview(db, data.get('text'))
        except (ValueError, csv.Error) as ex:
            raise HTTPException(422, str(ex))

    @app.post('/api/schedule-imports/paste')
    def import_route(data: dict, u=Depends(auth)):
        require_import(u)
        try:
            with transaction(path) as db:
                p = preview(db, data.get('text'))
                if data.get('preview_token') != p['preview_token']:
                    raise HTTPException(409, 'The source or existing records changed. Preview again before importing.')
                selected = [e for e in p['events'] if e['action'] == 'Import']
                if not selected:
                    return dict(imported=0, skipped=p['summary']['skipped'], undated=0, emails_sent=0, payments_created=0)
                bid = 'CSPB-' + digest([data['text'], p['preview_token']])[:24]
                dates = [e['date'] for e in selected if e['date']]
                report = dict(imported=len(selected), skipped=p['summary']['skipped'], undated=sum(not e['date'] for e in selected), emails_sent=0, payments_created=0)
                insert(db, 'schedule_import_batches', dict(id=bid, sha256=digest(data['text']), filename='Pasted current services',
                       range_start=min(dates, default=''), range_end=max(dates, default=''), created_at=now(), report_json=dumps(report)))
                for e in selected:
                    insert(db, 'imported_schedule_events', dict(id=e['id'], batch_id=bid, source_key=e['source_key'], job_date=e['date'],
                           client_name=e['client_name'], email=e['email'], phone=e['phone'], service=e['service'], time_label=e['time_label'], payload_json=dumps(e)))
                audit(db, u['id'], 'Import current schedule for review', 'schedule', bid, report)
                return report
        except (ValueError, csv.Error) as ex:
            raise HTTPException(422, str(ex))
