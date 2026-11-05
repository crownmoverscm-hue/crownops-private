"""Private company reference data, shared contacts and reviewed fleet imports."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from zoneinfo import ZoneInfo
import hashlib
import json
import re
import zipfile

from openpyxl import load_workbook
from .store import connect, transaction, rows, one, insert, audit, now, uid, dumps
from .employee_contacts import clean, phone_key, name_key
from .business_zone import BUSINESS_ZONE, zone_name

STAFF = {'owner','manager', 'sales', 'dispatch', 'accounting', 'crew'}
EDITORS = {'owner','manager', 'dispatch'}
CATEGORIES = ('Roadside assistance', 'Towing', 'Truck repairs', 'Glass', 'Rentals',
              'Equipment', 'Logistics', 'Locations', 'General')
HEADERS = {
    'Important contacts': ('Service', 'Category', 'Contact', 'Phone', 'Email', 'Website', 'Address', 'Area', 'Notes'),
    'Trucks': ('Name', 'Year', 'Make', 'Model', 'Fuel', 'Plate', 'VIN', 'Inspection note'),
    'Service history': ('Truck', 'Service Date', 'Odometer km', 'Work', 'Notes'),
    'Review notes': ('Item', 'Review needed'),
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS company_contacts (
 id TEXT PRIMARY KEY, service TEXT NOT NULL, category TEXT NOT NULL,
 contact TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL, email TEXT NOT NULL DEFAULT '',
 website TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', area TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1,
 updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fleet_reference (
 vehicle_id TEXT PRIMARY KEY REFERENCES vehicles(id), year TEXT NOT NULL DEFAULT '',
 make TEXT NOT NULL DEFAULT '', fuel TEXT NOT NULL DEFAULT '', inspection_note TEXT NOT NULL DEFAULT '',
 odometer_recorded_date TEXT NOT NULL DEFAULT '', review_required INTEGER NOT NULL DEFAULT 1,
 reviewed_by TEXT NOT NULL DEFAULT '', reviewed_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fleet_service_history (
 id TEXT PRIMARY KEY, vehicle_id TEXT NOT NULL REFERENCES vehicles(id), source_key TEXT UNIQUE NOT NULL,
 service_date TEXT NOT NULL DEFAULT '', odometer_km REAL, work TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
 maintenance_id TEXT REFERENCES maintenance(id), imported_by TEXT NOT NULL, imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS company_reference_imports (
 id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, filename TEXT NOT NULL, status TEXT NOT NULL,
 preview_json TEXT NOT NULL, report_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
"""


class ReferenceError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def initialize_reference(path):
    with closing(connect(path)) as db:
        db.executescript(SCHEMA)


def contact_values(source):
    values = dict(zip(('service', 'category', 'contact', 'phone', 'email', 'website', 'address', 'area', 'notes'),
                      [clean(source.get(h)) for h in HEADERS['Important contacts']]))
    if not values['service'] or not phone_key(values['phone']):
        raise ReferenceError('Each contact needs a service name and a valid phone number.')
    if values['category'] not in CATEGORIES:
        raise ReferenceError('Choose one of the supported contact categories.')
    if values['email'] and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', values['email']):
        raise ReferenceError('Review the contact email.')
    if values['website'] and not re.fullmatch(r'https?://[^\s]+', values['website']):
        raise ReferenceError('Website links must start with https:// or http://.')
    if any(len(v) > (1500 if k == 'notes' else 500) for k, v in values.items()):
        raise ReferenceError('Shorten the contact fields.')
    return values


def duplicate_contact(db, values, exclude=''):
    return any(c['id'] != exclude and name_key(c['service']) == name_key(values['service']) and
               phone_key(c['phone']) == phone_key(values['phone'])
               for c in rows(db, 'SELECT id,service,phone FROM company_contacts WHERE active=1'))


def numeric(value, label):
    if value in ('', None):
        return None
    try:
        number = float(value)
        if not 0 <= number <= 10_000_000:
            raise ValueError()
    except (ValueError, TypeError):
        raise ReferenceError(f'{label} must be a non-negative number.') from None
    return number


def parse_reference(content, filename):
    if not str(filename).lower().endswith('.xlsx') or len(content) > 2_000_000:
        raise ReferenceError('Upload an .xlsx company reference workbook, no larger than 2 MB.')
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            if len(archive.infolist()) > 300 or sum(x.file_size for x in archive.infolist()) > 8_000_000:
                raise ReferenceError('Workbook expands beyond the import limit.')
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=False)
    except ReferenceError:
        raise
    except Exception:
        raise ReferenceError('Upload a valid .xlsx company reference workbook.') from None
    result = {key: [] for key in HEADERS}
    try:
        if not {'Important contacts', 'Trucks', 'Service history'} <= set(workbook.sheetnames):
            raise ReferenceError('The workbook needs Important contacts, Trucks and Service history sheets.')
        if set(workbook.sheetnames) - set(HEADERS):
            raise ReferenceError('Use only the company reference sheets.')
        for sheet in workbook:
            if sheet.max_row is None or sheet.max_column is None:
                sheet.calculate_dimension(force=True)
            expected = HEADERS[sheet.title]
            if sheet.max_row > 501 or sheet.max_column != len(expected):
                raise ReferenceError(f'{sheet.title}: use the required columns and at most 500 records.')
            actual = tuple(clean(c.value) for c in next(sheet.iter_rows(min_row=1, max_row=1)))
            if actual != expected:
                raise ReferenceError(f'{sheet.title} headers must be: ' + ', '.join(expected))
            for row_number, cells in enumerate(sheet.iter_rows(min_row=2), 2):
                if not any(c.value not in ('', None) for c in cells):
                    continue
                if any(c.data_type in ('f', 'e') for c in cells):
                    raise ReferenceError(f'{sheet.title} row {row_number}: use plain values, without formulas.')
                record = {h: (c.value.date().isoformat() if isinstance(c.value, datetime) else clean(str(c.value) if c.value is not None else ''))
                          for h, c in zip(expected, cells)}
                if any(len(v) > 1500 for v in record.values()):
                    raise ReferenceError(f'{sheet.title} row {row_number}: shorten the fields.')
                result[sheet.title].append(record)
    finally:
        workbook.close()
    keys = set()
    for record in result['Important contacts']:
        contact_values(record)
        key = (name_key(record['Service']), phone_key(record['Phone']))
        if key in keys:
            raise ReferenceError('A service and phone number appears twice in the contacts sheet.')
        keys.add(key)
    names, identities = set(), {'Plate': set(), 'VIN': set()}
    for record in result['Trucks']:
        key = name_key(record['Name'])
        if not key or key in names:
            raise ReferenceError('Each truck needs a unique name.')
        names.add(key)
        for field, seen in identities.items():
            value = record[field].casefold()
            if value and value in seen:
                raise ReferenceError('Two trucks share the same ' + field + '. Review their identities.')
            if value:
                seen.add(value)
        if record['Year'] and not re.fullmatch(r'(19|20)\d{2}', record['Year']):
            raise ReferenceError('Truck years must contain four digits.')
        if record['Fuel'] not in ('', 'Diesel', 'Gas', 'Electric', 'Other'):
            raise ReferenceError('Review the truck fuel type.')
    service_keys = set()
    for record in result['Service history']:
        key = history_key(record)
        if key in service_keys:
            raise ReferenceError('The same service record appears twice in the workbook.')
        service_keys.add(key)
        if name_key(record['Truck']) not in names or not record['Work']:
            raise ReferenceError('Every service record must name a truck from the Trucks sheet and describe the work.')
        if record['Service Date']:
            try:
                date.fromisoformat(record['Service Date'])
            except ValueError:
                raise ReferenceError('Service dates must use YYYY-MM-DD.') from None
        numeric(record['Odometer km'], 'Service odometer')
    if not result['Trucks'] and not result['Important contacts']:
        raise ReferenceError('The workbook contains no company reference data.')
    return result


def snapshot(db):
    state = [rows(db, 'SELECT * FROM ' + table + ' ORDER BY ' + key) for table, key in
             (('company_contacts', 'id'), ('vehicles', 'id'), ('fleet_profiles', 'vehicle_id'),
              ('fleet_reference', 'vehicle_id'), ('fleet_service_history', 'id'), ('maintenance', 'id'))]
    return hashlib.sha256(dumps(state).encode()).hexdigest()


def changes(before, after):
    return {k: {'from': before.get(k, ''), 'to': v} for k, v in after.items()
            if v not in ('', None) and before.get(k, '') != v}


def history_key(record):
    return hashlib.sha256(dumps(record).encode()).hexdigest()


def build_plan(db, source):
    plan = {'source': source, 'snapshot': snapshot(db), 'contacts': [], 'trucks': [], 'history': [], 'conflicts': []}
    contacts = rows(db, 'SELECT * FROM company_contacts')
    for record in source['Important contacts']:
        values = contact_values(record)
        matches = [c for c in contacts if name_key(c['service']) == name_key(values['service'])
                   and phone_key(c['phone']) == phone_key(values['phone'])]
        if len(matches) > 1:
            plan['conflicts'].append('More than one contact matches ' + values['service'])
            continue
        existing = matches[0] if matches else {}
        delta = changes(existing, values)
        plan['contacts'].append({'id': existing.get('id'), 'name': values['service'], 'values': values,
                                 'changes': delta, 'action': 'update' if existing and delta else 'unchanged' if existing else 'create'})
    fleet = rows(db, 'SELECT * FROM vehicles')
    for record in source['Trucks']:
        matches = []
        for vehicle in fleet:
            p = one(db, 'SELECT * FROM fleet_profiles WHERE vehicle_id=?', (vehicle['id'],)) or {}
            if (name_key(vehicle['name']) == name_key(record['Name']) or
                    (record['VIN'] and record['VIN'].casefold() == p.get('vin', '').casefold()) or
                    (record['Plate'] and record['Plate'].casefold() == p.get('plate', '').casefold())):
                matches.append(vehicle)
        if len(matches) > 1:
            plan['conflicts'].append('More than one truck matches ' + record['Name'])
            continue
        vehicle = matches[0] if matches else {}
        profile = one(db, 'SELECT * FROM fleet_profiles WHERE vehicle_id=?', (vehicle['id'],)) or {} if vehicle else {}
        reference = one(db, 'SELECT * FROM fleet_reference WHERE vehicle_id=?', (vehicle['id'],)) or {} if vehicle else {}
        identity = {'plate': record['Plate'], 'vin': record['VIN'], 'model': record['Model']}
        for field in ('plate', 'vin'):
            if profile.get(field) and identity[field] and profile[field].casefold() != identity[field].casefold():
                plan['conflicts'].append(record['Name'] + ': ' + field + ' differs from the saved record.')
        relevant = [r for r in source['Service history'] if name_key(r['Truck']) == name_key(record['Name'])]
        readings = sorted([r for r in relevant if r['Service Date'] and r['Odometer km']], key=lambda r: r['Service Date'])
        if readings and float(readings[-1]['Odometer km']) > profile.get('odometer_km', 0):
            identity['odometer_km'] = float(readings[-1]['Odometer km'])
        oil = sorted([r for r in relevant if r['Service Date'] and name_key(r['Work']) == 'oil & filter'], key=lambda r: r['Service Date'])
        if oil and profile.get('next_service_km') is None:
            next_km = re.search(r'Next:\s*([\d,]+)', oil[-1]['Notes'])
            if next_km:
                identity['next_service_km'] = float(next_km[1].replace(',', ''))
        ref_values = dict(zip(('year', 'make', 'fuel', 'inspection_note'), [record[h] for h in ('Year', 'Make', 'Fuel', 'Inspection note')]))
        if readings and (float(readings[-1]['Odometer km']) > profile.get('odometer_km', 0) or
                         not reference.get('odometer_recorded_date') and float(readings[-1]['Odometer km']) == profile.get('odometer_km', 0)):
            ref_values['odometer_recorded_date'] = readings[-1]['Service Date']
        delta = {**changes(profile, identity), **changes(reference, ref_values)}
        plan['trucks'].append({'id': vehicle.get('id'), 'name': record['Name'], 'profile': identity, 'reference': ref_values,
                               'changes': delta, 'action': 'update' if vehicle and delta else 'unchanged' if vehicle else 'create'})
    for record in source['Service history']:
        key = history_key(record)
        exists = one(db, 'SELECT id FROM fleet_service_history WHERE source_key=?', (key,))
        plan['history'].append({'source': record, 'key': key, 'action': 'unchanged' if exists else 'create',
                                'status': 'Recorded service' if record['Service Date'] else 'Needs repair review'})
    plan['summary'] = {kind: {action: sum(x['action'] == action for x in plan[kind]) for action in ('create', 'update', 'unchanged')}
                       for kind in ('contacts', 'trucks', 'history')}
    return plan


def preview_reference(path, content, filename, actor):
    source = parse_reference(content, filename)
    with transaction(path) as db:
        plan = build_plan(db, source)
        batch_id = uid('REFERENCE')
        expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec='seconds')
        insert(db, 'company_reference_imports', dict(id=batch_id, actor_id=actor, filename=clean(filename)[:240],
            status='Preview', preview_json=dumps(plan), created_at=now(), expires_at=expires))
    return {**plan, 'batch_id': batch_id, 'expires_at': expires}


def apply_reference(path, batch_id, actor):
    with transaction(path) as db:
        batch = one(db, 'SELECT * FROM company_reference_imports WHERE id=? AND actor_id=?', (batch_id, actor))
        if not batch:
            raise ReferenceError('Import preview not found. Upload the file again.', 404)
        if batch['status'] == 'Applied':
            return {**json.loads(batch['report_json']), 'already_applied': True}
        if datetime.fromisoformat(batch['expires_at']) <= datetime.now(timezone.utc):
            raise ReferenceError('Preview expired. Upload the file again.', 409)
        plan = json.loads(batch['preview_json'])
        if plan['conflicts']:
            raise ReferenceError('Resolve the contact or truck matches before importing.', 409)
        if snapshot(db) != plan['snapshot']:
            raise ReferenceError('Company records changed since the preview. Upload the file again.', 409)
        for item in plan['contacts']:
            if item['action'] == 'create':
                insert(db, 'company_contacts', dict(id=uid('CONTACT'), **item['values'], updated_by=actor, updated_at=now()))
            elif item['action'] == 'update':
                for field, delta in item['changes'].items():
                    db.execute(f'UPDATE company_contacts SET {field}=? WHERE id=?', (delta['to'], item['id']))
                db.execute('UPDATE company_contacts SET version=version+1,updated_by=?,updated_at=? WHERE id=?', (actor, now(), item['id']))
        truck_ids, versioned_trucks = {}, set()
        palette = ['#DC2626', '#2563EB', '#EAB308', '#7C3AED', '#059669', '#475569']
        for index, item in enumerate(plan['trucks']):
            vid = item['id'] or uid('TRUCK')
            truck_ids[name_key(item['name'])] = vid
            if item['action'] == 'create':
                order = db.execute('SELECT coalesce(max(display_order),0)+1 FROM vehicles').fetchone()[0]
                insert(db, 'vehicles', dict(id=vid, name=item['name'], active=0, color_hex=palette[index % len(palette)], display_order=order))
            if item['action'] != 'unchanged':
                versioned_trucks.add(vid)
                if item['id']:
                    db.execute('UPDATE vehicles SET version=version+1 WHERE id=?', (vid,))
                if not one(db, 'SELECT vehicle_id FROM fleet_profiles WHERE vehicle_id=?', (vid,)):
                    insert(db, 'fleet_profiles', dict(vehicle_id=vid, check_required=1, updated_at=now()))
                for field, value in item['profile'].items():
                    if value not in ('', None):
                        db.execute(f'UPDATE fleet_profiles SET {field}=? WHERE vehicle_id=?', (value, vid))
                if not one(db, 'SELECT vehicle_id FROM fleet_reference WHERE vehicle_id=?', (vid,)):
                    insert(db, 'fleet_reference', dict(vehicle_id=vid, updated_at=now()))
                for field, value in item['reference'].items():
                    if value:
                        db.execute(f'UPDATE fleet_reference SET {field}=? WHERE vehicle_id=?', (value, vid))
                db.execute('UPDATE fleet_reference SET review_required=1,updated_at=? WHERE vehicle_id=?', (now(), vid))
        for item in plan['history']:
            if item['action'] == 'unchanged':
                continue
            r = item['source']; vid = truck_ids[name_key(r['Truck'])]; mid = None
            if not r['Service Date']:
                mid = uid('SERVICE')
                insert(db, 'maintenance', dict(id=mid, vehicle_id=vid, title=r['Notes'] or r['Work'], notes='Imported undated work: ' + r['Work'],
                                               author=actor, created_at=now()))
                db.execute('UPDATE fleet_reference SET review_required=1,updated_at=? WHERE vehicle_id=?', (now(), vid))
                if vid not in versioned_trucks:
                    db.execute('UPDATE vehicles SET version=version+1 WHERE id=?', (vid,))
                    versioned_trucks.add(vid)
            insert(db, 'fleet_service_history', dict(id=uid('HISTORY'), vehicle_id=vid, source_key=item['key'], service_date=r['Service Date'],
                odometer_km=numeric(r['Odometer km'], 'Service odometer'), work=r['Work'], notes=r['Notes'], maintenance_id=mid,
                imported_by=actor, imported_at=now()))
        report = {'summary': plan['summary'], 'review_notes': plan['source']['Review notes'], 'already_applied': False}
        db.execute("UPDATE company_reference_imports SET status='Applied',report_json=? WHERE id=?", (dumps(report), batch_id))
        audit(db, actor, 'Company contacts and fleet reference imported', 'company_reference_imports', batch_id, {'summary': plan['summary']})
    return report


def fleet_reference_blockers(db, vid):
    # Profile completion is advisory. Availability is an explicit office decision;
    # known major defects and expired inspections have their own dispatch checks.
    return []


def register_company_ops(app, path, auth):
    from fastapi import Depends, File, HTTPException, UploadFile
    from .platform_common import require_role, OFFICE, text, settings
    initialize_reference(path)

    @app.get('/api/company/contacts')
    def contacts(u=Depends(auth)):
        require_role(u, STAFF)
        with closing(connect(path)) as db:
            return {'editable': u['role'] in EDITORS, 'categories': CATEGORIES,
                    'contacts': rows(db, 'SELECT * FROM company_contacts WHERE active=1 ORDER BY category,service,contact')}

    @app.post('/api/company/contacts')
    def add_contact(data: dict, u=Depends(auth)):
        require_role(u, EDITORS)
        try:
            values = contact_values({h: data.get(k, '') for h, k in zip(HEADERS['Important contacts'],
                ('service', 'category', 'contact', 'phone', 'email', 'website', 'address', 'area', 'notes'))})
        except ReferenceError as e:
            raise HTTPException(e.status, str(e)) from None
        with transaction(path) as db:
            if duplicate_contact(db, values):
                raise HTTPException(409, 'This service and phone are already saved. Edit the existing contact.')
            record = dict(id=uid('CONTACT'), **values, updated_by=u['id'], updated_at=now())
            insert(db, 'company_contacts', record)
            audit(db, u['id'], 'Company contact added', 'company_contacts', record['id'])
        return record

    @app.put('/api/company/contacts/{cid}')
    def update_contact(cid: str, data: dict, u=Depends(auth)):
        require_role(u, EDITORS)
        with transaction(path) as db:
            old = one(db, 'SELECT * FROM company_contacts WHERE id=?', (cid,))
            if not old:
                raise HTTPException(404, 'Contact not found.')
            if data.get('version') != old['version']:
                raise HTTPException(409, 'Contact changed. Reopen it before saving.')
            try:
                values = contact_values({h: data.get(k, old[k]) for h, k in zip(HEADERS['Important contacts'],
                    ('service', 'category', 'contact', 'phone', 'email', 'website', 'address', 'area', 'notes'))})
            except ReferenceError as e:
                raise HTTPException(e.status, str(e)) from None
            if duplicate_contact(db, values, cid):
                raise HTTPException(409, 'Another contact already has this service and phone.')
            active = int(data.get('active', bool(old['active'])) is True)
            db.execute('UPDATE company_contacts SET ' + ','.join(k + '=?' for k in values) +
                       ',active=?,version=version+1,updated_by=?,updated_at=? WHERE id=?', [*values.values(), active, u['id'], now(), cid])
            audit(db, u['id'], 'Company contact updated', 'company_contacts', cid)
        return {'ok': True}

    @app.get('/api/company/fleet')
    def fleet(u=Depends(auth)):
        require_role(u, OFFICE)
        from .operations import fleet_warnings
        with closing(connect(path)) as db:
            result = []
            for v in rows(db, 'SELECT * FROM vehicles ORDER BY display_order,name'):
                p, warnings, latest = fleet_warnings(db, v)
                ref = one(db, 'SELECT * FROM fleet_reference WHERE vehicle_id=?', (v['id'],)) or {}
                history = rows(db, '''SELECT h.*,m.status repair_status,u.name recorded_by_name FROM fleet_service_history h
                    LEFT JOIN maintenance m ON m.id=h.maintenance_id LEFT JOIN users u ON u.id=h.imported_by
                    WHERE h.vehicle_id=? ORDER BY h.service_date DESC,h.rowid DESC''', (v['id'],))
                result.append({**v, 'profile': p, 'reference': ref, 'warnings': warnings, 'latest_check': latest,
                    'assigned_services': rows(db, '''SELECT j.id,j.title,j.phase,j.start_at,j.end_at,j.status,l.client_name
                        FROM assignments a JOIN jobs j ON j.id=a.job_id JOIN leads l ON l.id=j.lead_id
                        WHERE a.resource_type='vehicle' AND a.resource_id=? ORDER BY j.start_at DESC LIMIT 200''',(v['id'],)),
                    'history': history, 'maintenance': rows(db, 'SELECT * FROM maintenance WHERE vehicle_id=? ORDER BY created_at DESC', (v['id'],)),
                    'documents': rows(db, '''SELECT id,category,original_name,scan_status FROM fleet_documents
                        WHERE vehicle_id=? AND deleted_at IS NULL ORDER BY created_at DESC''', (v['id'],))})
            return {'editable': u['role'] in EDITORS, 'trucks': result}

    @app.patch('/api/company/fleet/{vid}/availability')
    def truck_availability(vid: str, data: dict, u=Depends(auth)):
        require_role(u, EDITORS)
        if type(data.get('active')) is not bool:raise HTTPException(422,'Choose Active or Inactive.')
        with transaction(path) as db:
            v=one(db,'SELECT * FROM vehicles WHERE id=?',(vid,))
            if not v:raise HTTPException(404,'Truck not found.')
            if data.get('version')!=v['version']:raise HTTPException(409,'Truck changed. Refresh before saving availability.')
            if data['active']:
                if one(db,"SELECT id FROM inspections WHERE vehicle_id=? AND severity='Major' AND cleared_at IS NULL",(vid,)):
                    raise HTTPException(409,'Clear the recorded major defect before making this truck active.')
                p=one(db,'SELECT inspection_due FROM fleet_profiles WHERE vehicle_id=?',(vid,)) or {}
                if p.get('inspection_due') and p['inspection_due']<datetime.now(BUSINESS_ZONE).date().isoformat():
                    raise HTTPException(409,'Update the recorded expired inspection before making this truck active.')
            affected=rows(db,"""SELECT j.id FROM assignments a JOIN jobs j ON j.id=a.job_id
                WHERE a.resource_type='vehicle' AND a.resource_id=? AND j.status NOT IN ('Completed','Cancelled')""",(vid,))
            db.execute('UPDATE vehicles SET active=?,version=version+1 WHERE id=?',(int(data['active']),vid))
            if not data['active']:
                for job in affected:
                    db.execute('UPDATE jobs SET plan_review_required=1,capacity_reviewed=0,version=version+1 WHERE id=?',(job['id'],))
            audit(db,u['id'],'Truck availability changed','vehicles',vid,{'before':bool(v['active']),'active':data['active'],'affected_jobs':[j['id'] for j in affected]})
        return {'ok':True,'active':data['active'],'affected_jobs':len(affected)}

    @app.post('/api/company/fleet/{vid}/service-history')
    def add_service_history(vid: str, data: dict, u=Depends(auth)):
        require_role(u, EDITORS)
        work=text(data.get('work',''),'Work performed',2000)
        notes=text(data.get('notes',''),'Service notes',5000)
        day=text(data.get('service_date',''),'Service date',10)
        try:date.fromisoformat(day)
        except ValueError:raise HTTPException(422,'Enter the service date as YYYY-MM-DD.')
        if day>datetime.now(BUSINESS_ZONE).date().isoformat():raise HTTPException(422,'Use a maintenance task for future work. Service history records completed work.')
        if not work:raise HTTPException(422,'Describe the work performed.')
        key=text(data.get('request_id',''),'Request ID',100)
        if not re.fullmatch(r'[a-zA-Z0-9-]{8,100}',key):raise HTTPException(422,'Refresh the service form before saving.')
        try:reading=numeric(data.get('odometer_km'),'Odometer km')
        except ReferenceError as e:raise HTTPException(e.status,str(e)) from None
        source='manual:'+vid+':'+key
        with transaction(path) as db:
            v=one(db,'SELECT * FROM vehicles WHERE id=?',(vid,))
            if not v:raise HTTPException(404,'Truck not found.')
            old=one(db,'SELECT * FROM fleet_service_history WHERE source_key=?',(source,))
            if old:
                if (old['work'],old['notes'],old['service_date'],old['odometer_km'])!=(work,notes,day,reading):raise HTTPException(409,'This saved service entry differs. Open a new service form.')
                return {'ok':True,'id':old['id'],'already_saved':True}
            if data.get('version')!=v['version']:raise HTTPException(409,'Truck changed. Reopen the service form.')
            rec=dict(id=uid('HISTORY'),vehicle_id=vid,source_key=source,service_date=day,odometer_km=reading,work=work,notes=notes,imported_by=u['id'],imported_at=now())
            insert(db,'fleet_service_history',rec)
            db.execute('UPDATE vehicles SET version=version+1 WHERE id=?',(vid,))
            audit(db,u['id'],'Truck service recorded','vehicles',vid,{'service_id':rec['id'],'service_date':day,'work':work})
        return {'ok':True,'id':rec['id']}

    @app.put('/api/company/fleet/{vid}/reference')
    def update_reference(vid: str, data: dict, u=Depends(auth)):
        require_role(u, EDITORS)
        core = {'plate','vin','model','payload_kg','gvwr_kg','cargo_length_ft','odometer_km','next_service_km',
                'next_service_date','inspection_due','inspection_interval_hours','check_required'}
        allowed = {'year', 'make', 'fuel', 'inspection_note'} | core | {'version'}
        if set(data) - allowed:
            raise HTTPException(422, 'Unknown truck reference fields.')
        with transaction(path) as db:
            vehicle = one(db, 'SELECT * FROM vehicles WHERE id=?', (vid,))
            if not vehicle:
                raise HTTPException(404, 'Truck not found.')
            if data.get('version') != vehicle['version']:
                raise HTTPException(409, 'Truck changed. Reopen its details before saving.')
            saved_ref = one(db, 'SELECT * FROM fleet_reference WHERE vehicle_id=?', (vid,)) or {}
            record = {k: text(data.get(k, saved_ref.get(k,'')), k, 500) for k in ('year','make','fuel','inspection_note')}
            if record['year'] and not re.fullmatch(r'(19|20)\d{2}', record['year']):
                raise HTTPException(422, 'Year needs four digits.')
            if record['fuel'] not in ('', 'Diesel', 'Gas', 'Electric', 'Other'):
                raise HTTPException(422, 'Review the fuel type.')
            old = one(db, 'SELECT * FROM fleet_profiles WHERE vehicle_id=?', (vid,)) or {}
            profile = {k: text(data.get(k,old.get(k,'')),k,150) for k in ('plate','vin','model','next_service_date','inspection_due')}
            for k in ('next_service_date','inspection_due'):
                if profile[k]:
                    try:date.fromisoformat(profile[k])
                    except ValueError:raise HTTPException(422,'Use YYYY-MM-DD for inspection and service dates.')
            for k in ('payload_kg','gvwr_kg','cargo_length_ft','odometer_km','next_service_km'):
                try:profile[k] = numeric(data.get(k,old.get(k)),k)
                except ReferenceError as e:raise HTTPException(e.status,str(e)) from None
            profile['odometer_km'] = profile['odometer_km'] if profile['odometer_km'] is not None else 0
            if profile['odometer_km'] < old.get('odometer_km',0):
                raise HTTPException(409,'Odometer cannot decrease. Review the saved reading.')
            interval = data.get('inspection_interval_hours',old.get('inspection_interval_hours',24))
            try:interval = float(interval)
            except (ValueError,TypeError):raise HTTPException(422,'Pre-use check validity must be between 1 and 168 hours.')
            if not 1 <= interval <= 168 or not interval.is_integer():
                raise HTTPException(422,'Pre-use check validity must be between 1 and 168 whole hours.')
            profile.update(inspection_interval_hours=int(interval),check_required=int(data.get('check_required',bool(old.get('check_required',1))) is True))
            if not old:
                insert(db,'fleet_profiles',dict(vehicle_id=vid,updated_at=now()))
            for k,v in profile.items():
                db.execute(f'UPDATE fleet_profiles SET {k}=? WHERE vehicle_id=?',(v,vid))
            db.execute('UPDATE fleet_profiles SET updated_at=? WHERE vehicle_id=?',(now(),vid))
            if not one(db, 'SELECT vehicle_id FROM fleet_reference WHERE vehicle_id=?', (vid,)):
                insert(db, 'fleet_reference', dict(vehicle_id=vid, updated_at=now()))
            for k, v in record.items():
                db.execute(f'UPDATE fleet_reference SET {k}=? WHERE vehicle_id=?', (v, vid))
            if profile['odometer_km'] > old.get('odometer_km',0):
                db.execute('UPDATE fleet_reference SET odometer_recorded_date=? WHERE vehicle_id=?',
                    (datetime.now(BUSINESS_ZONE).date().isoformat(),vid))
            db.execute('UPDATE fleet_reference SET review_required=1,updated_at=? WHERE vehicle_id=?', (now(), vid))
            db.execute('UPDATE vehicles SET version=version+1 WHERE id=?',(vid,))
            audit(db, u['id'], 'Truck reference details updated', 'vehicles', vid)
        return {'ok': True}

    @app.post('/api/company/fleet/{vid}/review')
    def review_truck(vid: str, data: dict, u=Depends(auth)):
        require_role(u, EDITORS)
        note = text(data.get('note', ''), 'Truck review evidence', 3000)
        if data.get('reviewed') is not True or len(note) < 12:
            raise HTTPException(422, 'Confirm the truck details and record the safety and inspection evidence.')
        with transaction(path) as db:
            v = one(db, 'SELECT * FROM vehicles WHERE id=?', (vid,))
            p = one(db, 'SELECT * FROM fleet_profiles WHERE vehicle_id=?', (vid,)) or {}
            r = one(db, 'SELECT * FROM fleet_reference WHERE vehicle_id=?', (vid,)) or {}
            if not v:
                raise HTTPException(404, 'Truck not found.')
            if not all(p.get(k) for k in ('plate', 'vin', 'model')) or not all(r.get(k) for k in ('year', 'make', 'fuel')):
                raise HTTPException(409, 'Complete the plate, VIN, model, year, make and fuel before activating.')
            if not p.get('inspection_due') or p['inspection_due'] < datetime.now(BUSINESS_ZONE).date().isoformat():
                raise HTTPException(409, 'Enter and verify the exact current inspection expiry date.')
            unresolved = one(db, '''SELECT h.id FROM fleet_service_history h JOIN maintenance m ON m.id=h.maintenance_id
                WHERE h.vehicle_id=? AND m.status!='Completed' LIMIT 1''', (vid,))
            if unresolved or one(db, "SELECT id FROM inspections WHERE vehicle_id=? AND severity='Major' AND cleared_at IS NULL", (vid,)):
                raise HTTPException(409, 'Resolve imported repairs and clear any major defect before activating.')
            db.execute('UPDATE fleet_reference SET review_required=0,reviewed_by=?,reviewed_at=?,updated_at=? WHERE vehicle_id=?', (u['id'], now(), now(), vid))
            db.execute('UPDATE fleet_profiles SET check_required=1 WHERE vehicle_id=?', (vid,))
            db.execute('UPDATE vehicles SET active=1,version=version+1 WHERE id=?', (vid,))
            audit(db, u['id'], 'Truck reference reviewed and activated', 'vehicles', vid, {'evidence': note})
        return {'ok': True, 'preuse_check_required': True}

    @app.post('/api/company/reference-import/preview')
    async def preview(file: UploadFile = File(...), u=Depends(auth)):
        require_role(u, {'owner','manager'})
        try:
            return preview_reference(path, await file.read(2_000_001), file.filename or '', u['id'])
        except ReferenceError as e:
            raise HTTPException(e.status, str(e)) from None

    @app.post('/api/company/reference-import/apply')
    def apply(data: dict, u=Depends(auth)):
        require_role(u, {'owner','manager'})
        try:
            return apply_reference(path, str(data.get('batch_id') or ''), u['id'])
        except ReferenceError as e:
            raise HTTPException(e.status, str(e)) from None

    @app.get('/api/company/launch-checks')
    def launch_checks(u=Depends(auth)):
        require_role(u, {'owner','manager'})
        with closing(connect(path)) as db:
            employees = rows(db, 'SELECT * FROM employees')
            pay_pending = sum(bool(e['active'] and json.loads(e['raw_json']).get('contact_import_pay_pending')) for e in employees)
            trucks = rows(db, 'SELECT * FROM vehicles')
            contacts = db.execute('SELECT count(*) FROM company_contacts WHERE active=1').fetchone()[0]
            fleet_review = db.execute('SELECT count(*) FROM fleet_reference WHERE review_required=1').fetchone()[0]
            repairs = db.execute("SELECT count(*) FROM maintenance WHERE status!='Completed'").fetchone()[0]
            policies = {k: bool((settings(db, k, {}) or {}).get('approved')) for k in ('deposit_policy', 'client_terms', 'billing_policy')}
            users = rows(db, 'SELECT role,count(*) count FROM users WHERE active=1 GROUP BY role')
            mfa = bool(one(db, 'SELECT user_id FROM auth_factors WHERE user_id=? AND enabled=1', (u['id'],)))
        return {'contacts': contacts, 'employees': len(employees), 'active_employees': sum(e['active'] for e in employees),
                'pay_rates_pending': pay_pending, 'verified_drivers': sum(bool(e['active'] and e['driver_verified']) for e in employees),
                'trucks': len(trucks), 'active_trucks': sum(v['active'] for v in trucks), 'fleet_reviews_pending': fleet_review,
                'open_repairs': repairs, 'policies': policies, 'accounts': users, 'owner_mfa_enabled': mfa,
                'live_operation_verified': False}
