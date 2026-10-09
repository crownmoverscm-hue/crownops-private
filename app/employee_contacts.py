"""Reviewed employee contact imports. No login, payroll or driver authorization writes."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from io import BytesIO
import hashlib
import json
import re
import unicodedata
import zipfile

from openpyxl import load_workbook
from .store import transaction, rows, one, insert, audit, uid, now, dumps

HEADERS = ('Name', 'Full Name', 'Phone', 'Email', 'Position', 'Availability', 'Reported Driver',
           'Emergency Contact', 'Emergency Phone', 'Notes')
PROFILE_FIELDS = {'Phone': 'phone', 'Email': 'email',
                  'Emergency Contact': 'emergency_name', 'Emergency Phone': 'emergency_phone'}
PAY_PENDING = 'contact_import_pay_pending'
SCHEMA = """CREATE TABLE IF NOT EXISTS employee_contact_imports (
 id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, filename TEXT NOT NULL,
 source_sha256 TEXT NOT NULL, status TEXT NOT NULL, preview_json TEXT NOT NULL,
 report_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);"""


class ContactImportError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def clean(value):
    value = unicodedata.normalize('NFC', str(value or ''))
    return ''.join(c for c in value if unicodedata.category(c) != 'Cf').strip()


def phone_key(value):
    digits = re.sub(r'[^0-9]', '', clean(value))
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits if 7 <= len(digits) <= 15 else ''


def name_key(value):
    return ' '.join(clean(value).casefold().split())


def parse_contacts(content, filename):
    if not str(filename).lower().endswith('.xlsx'):
        raise ContactImportError('Upload an .xlsx contact workbook with a Contacts sheet.')
    if len(content) > 2_000_000:
        raise ContactImportError('Contact workbook must be 2 MB or smaller.', 413)
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            if len(archive.infolist()) > 300 or sum(x.file_size for x in archive.infolist()) > 8_000_000:
                raise ContactImportError('Contact workbook expands beyond the import limit.')
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=False)
    except ContactImportError:
        raise
    except Exception:
        raise ContactImportError('Please upload a valid .xlsx contact workbook.') from None
    try:
        if 'Contacts' not in workbook.sheetnames:
            raise ContactImportError('The workbook needs a Contacts sheet.')
        sheet = workbook['Contacts']
        if sheet.max_row is None or sheet.max_column is None:
            sheet.calculate_dimension(force=True)
        if sheet.max_row > 501 or sheet.max_column > len(HEADERS):
            raise ContactImportError('Use the ten contact columns and at most 500 employees.')
        actual = tuple(clean(c.value) for c in next(sheet.iter_rows(min_row=1, max_row=1)))
        if actual != HEADERS:
            raise ContactImportError('Contact headers must be: ' + ', '.join(HEADERS) + '.')
        contacts = []
        seen = {'name': set(), 'phone': set(), 'email': set()}
        limits = dict(zip(HEADERS, (180, 180, 80, 240, 120, 500, 10, 180, 80, 600)))
        for number, cells in enumerate(sheet.iter_rows(min_row=2), 2):
            if not any(c.value not in (None, '') for c in cells):
                continue
            if any(c.data_type in ('f', 'e') for c in cells):
                raise ContactImportError(f'Row {number}: use plain contact values, without formulas or errors.')
            contact = {key: clean(cell.value) for key, cell in zip(HEADERS, cells)}
            if not contact['Name']:
                raise ContactImportError(f'Row {number}: enter the employee name.')
            for key, value in contact.items():
                if len(value) > limits[key]:
                    raise ContactImportError(f'Row {number}: shorten {key}.')
            if contact['Email'] and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', contact['Email']):
                raise ContactImportError(f'Row {number}: review the employee email.')
            if contact['Reported Driver'] not in ('', 'Yes', 'No'):
                raise ContactImportError(f'Row {number}: Reported Driver must be Yes, No or blank.')
            keys = {'name': name_key(contact['Name']), 'phone': phone_key(contact['Phone']),
                    'email': contact['Email'].casefold()}
            for kind, key in keys.items():
                if key and key in seen[kind]:
                    raise ContactImportError(f'Row {number}: duplicate employee {kind}. Review the contact list.')
                if key:
                    seen[kind].add(key)
            full_name = name_key(contact['Full Name'])
            if full_name and full_name != keys['name']:
                if full_name in seen['name']:
                    raise ContactImportError(f'Row {number}: duplicate employee full name. Review the contact list.')
                seen['name'].add(full_name)
            contact['source_row'] = number
            contacts.append(contact)
        if not contacts:
            raise ContactImportError('The Contacts sheet has no employee records.')
        return contacts
    finally:
        workbook.close()


def contact_notes(existing, contact):
    lines = (existing or '').splitlines()
    for key, prefix in (('Availability', 'Roster availability: '),
                        ('Reported Driver', 'Roster driver status: '),
                        ('Full Name', 'Full name (roster): '),
                        ('Position', 'Roster position: ')):
        if contact[key]:
            lines = [line for line in lines if not line.startswith(prefix)]
            lines.append(prefix + contact[key])
    for line in contact['Notes'].splitlines():
        if line and line not in lines:
            lines.append(line)
    result = '\n'.join(lines)
    if len(result) > 3000:
        raise ContactImportError(f"{contact['Name']}: combined manager notes exceed 3,000 characters.")
    return result


def build_plan(db, contacts):
    employees = rows(db, """SELECT e.*, p.phone, p.email, p.emergency_name, p.emergency_phone, p.notes
        FROM employees e LEFT JOIN employee_directory_profiles p ON p.employee_id=e.id ORDER BY e.id""")
    by_id = {e['id']: e for e in employees}
    indexes = {kind: defaultdict(set) for kind in ('name', 'phone', 'email')}
    for employee in employees:
        for kind, key in (('name', name_key(employee['name'])), ('phone', phone_key(employee.get('phone'))),
                          ('email', clean(employee.get('email')).casefold())):
            if key:
                indexes[kind][key].add(employee['id'])
    plan = []
    for contact in contacts:
        matches = set()
        for kind, key in (('name', name_key(contact['Name'])), ('name', name_key(contact['Full Name'])), ('phone', phone_key(contact['Phone'])),
                          ('email', contact['Email'].casefold())):
            if key:
                matches.update(indexes[kind].get(key, set()))
        item = {'source': contact, 'name': contact['Name'], 'employee_id': None,
                'matched_name': '', 'action': '', 'changes': {}}
        if len(matches) > 1:
            item.update(action='conflict', reason='Name, phone or email matches more than one employee. Review the directory first.')
        else:
            employee = by_id[next(iter(matches))] if matches else {}
            item.update(employee_id=employee.get('id'), matched_name=employee.get('name', ''))
            desired = {field: contact[header] for header, field in PROFILE_FIELDS.items() if contact[header]}
            desired['notes'] = contact_notes(employee.get('notes'), contact)
            item['changes'] = {field: {'from': employee.get(field) or '', 'to': value}
                               for field, value in desired.items() if value != (employee.get(field) or '')}
            item['action'] = 'create' if not employee else ('update' if item['changes'] else 'unchanged')
        plan.append(item)
    matched = defaultdict(list)
    for item in plan:
        if item['employee_id']:
            matched[item['employee_id']].append(item)
    for items in matched.values():
        if len(items) > 1:
            for item in items:
                item.update(action='conflict', reason='Two source rows match the same employee. Review the contact list first.')
    summary = {kind: sum(x['action'] == kind for x in plan) for kind in ('create', 'update', 'unchanged', 'conflict')}
    return {'rows': plan, 'summary': summary, 'total': len(plan)}


def initialize_contact_imports(path):
    with transaction(path) as db:
        db.execute(SCHEMA)


def preview_contacts(path, content, filename, actor):
    contacts = parse_contacts(content, filename)
    with transaction(path) as db:
        plan = build_plan(db, contacts)
        batch = uid('CONTACTS')
        expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec='seconds')
        insert(db, 'employee_contact_imports', dict(id=batch, actor_id=actor, filename=clean(filename)[:240],
            source_sha256=hashlib.sha256(content).hexdigest(), status='Preview', preview_json=dumps(plan),
            created_at=now(), expires_at=expires))
    return {**plan, 'batch_id': batch, 'expires_at': expires}


def apply_contacts(path, batch_id, actor):
    with transaction(path) as db:
        batch = one(db, 'SELECT * FROM employee_contact_imports WHERE id=? AND actor_id=?', (batch_id, actor))
        if not batch:
            raise ContactImportError('Contact preview not found. Upload the file again.', 404)
        if batch['status'] == 'Applied':
            return {**json.loads(batch['report_json']), 'already_applied': True}
        if datetime.fromisoformat(batch['expires_at']) <= datetime.now(timezone.utc):
            raise ContactImportError('Contact preview expired. Upload the file again.', 409)
        preview = json.loads(batch['preview_json'])
        if preview['summary']['conflict']:
            raise ContactImportError('Resolve the employee matches before saving contacts.', 409)
        current = build_plan(db, [item['source'] for item in preview['rows']])
        if current != preview:
            raise ContactImportError('Employee contacts changed since this preview. Upload the file again.', 409)
        for item in preview['rows']:
            if item['action'] == 'unchanged':
                continue
            employee_id = item['employee_id']
            if item['action'] == 'create':
                employee_id = uid('EMP')
                insert(db, 'employees', dict(id=employee_id, name=item['source']['Full Name'] or item['name'],
                    role=item['source']['Position'] or 'Staff', active=0, driver_verified=0, hourly_cents=0,
                    raw_json=dumps({'contact_source': item['source'], PAY_PENDING: True})))
            if not one(db, 'SELECT employee_id FROM employee_directory_profiles WHERE employee_id=?', (employee_id,)):
                insert(db, 'employee_directory_profiles', dict(employee_id=employee_id))
            for field, change in item['changes'].items():
                # Fields are produced internally by build_plan, never from HTTP input.
                db.execute(f'UPDATE employee_directory_profiles SET {field}=? WHERE employee_id=?', (change['to'], employee_id))
            db.execute('UPDATE employee_directory_profiles SET updated_by=?,updated_at=? WHERE employee_id=?', (actor, now(), employee_id))
            audit(db, actor, 'Employee contacts imported', 'employees', employee_id, {'batch_id': batch_id, 'fields': list(item['changes'])})
        report = {**preview['summary'], 'total': preview['total'], 'batch_id': batch_id, 'already_applied': False}
        db.execute("UPDATE employee_contact_imports SET status='Applied',report_json=? WHERE id=?", (dumps(report), batch_id))
        audit(db, actor, 'Employee contact import applied', 'employee_contact_imports', batch_id, report)
    return report


def register_employee_contacts(app, path, auth):
    from fastapi import Depends, File, HTTPException, UploadFile
    initialize_contact_imports(path)

    def allowed(user):
        if user['role'] not in {'owner', 'accounting'}:
            raise HTTPException(403, 'Contact imports are restricted to owner and accounting.')

    @app.post('/api/ux/employee-contact-import/preview')
    async def preview(file: UploadFile = File(...), u=Depends(auth)):
        allowed(u)
        content = await file.read(2_000_001)
        try:
            return preview_contacts(path, content, file.filename or '', u['id'])
        except ContactImportError as error:
            raise HTTPException(error.status, str(error)) from None

    @app.post('/api/ux/employee-contact-import/apply')
    def apply(data: dict, u=Depends(auth)):
        allowed(u)
        try:
            return apply_contacts(path, str(data.get('batch_id') or ''), u['id'])
        except ContactImportError as error:
            raise HTTPException(error.status, str(error)) from None
