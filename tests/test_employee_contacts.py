import importlib.util
from contextlib import closing
from io import BytesIO
import json
import os
import re
import zipfile
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook
from app.employee_contacts import (HEADERS, PAY_PENDING, ContactImportError, parse_contacts,
    initialize_contact_imports, preview_contacts, apply_contacts)
from app.store import initialize, transaction, connect, insert, one


def workbook_bytes(*records):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Contacts'
    sheet.append(HEADERS)
    for record in records:
        sheet.append([record.get(header, '') for header in HEADERS])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class EmployeeContactImportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'contacts.sqlite3'
        initialize(self.path)
        initialize_contact_imports(self.path)

    def tearDown(self):
        self.folder.cleanup()

    def existing(self, employee_id='EMP-EXISTING', name='Example Worker', phone='514-555-1000', email='worker@example.invalid'):
        with transaction(self.path) as db:
            insert(db, 'employees', dict(id=employee_id, name=name, role='Senior mover', active=1,
                hourly_cents=2875, driver_verified=1))
            insert(db, 'employee_directory_profiles', dict(employee_id=employee_id, phone=phone, email=email,
                home_address='Private address', emergency_name='Existing contact', emergency_phone='514-555-2000',
                team_lead=1, notes='Keep manager note', license_original_name='license.pdf', license_stored_name='private-license.pdf'))

    def preview(self, *records):
        return preview_contacts(self.path, workbook_bytes(*records), 'contacts.xlsx', 'OWNER')

    def test_preview_does_not_create_employees_and_apply_creates_inactive_contacts_without_driver_approval(self):
        preview = self.preview({'Name': 'New Worker', 'Phone': '514-555-3000', 'Position': 'Lead',
            'Reported Driver': 'Yes', 'Availability': 'Weekends', 'Emergency Phone': '+39 123 456 7890'})
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM employees').fetchone()[0], 0)
        self.assertEqual(preview['summary']['create'], 1)
        report = apply_contacts(self.path, preview['batch_id'], 'OWNER')
        self.assertEqual(report['create'], 1)
        with closing(connect(self.path)) as db:
            employee = one(db, 'SELECT * FROM employees')
            profile = one(db, 'SELECT * FROM employee_directory_profiles')
            self.assertEqual((employee['active'], employee['driver_verified'], employee['hourly_cents']), (0, 0, 0))
            self.assertTrue(json.loads(employee['raw_json'])[PAY_PENDING])
            self.assertEqual(profile['emergency_phone'], '+39 123 456 7890')
            self.assertIn('Roster availability: Weekends', profile['notes'])
            self.assertIn('Roster driver status: Yes', profile['notes'])

    def test_updates_contacts_preserving_wages_approvals_and_missing_source_fields(self):
        self.existing()
        preview = self.preview({'Name': '  example worker ', 'Phone': '514-555-1001', 'Position': 'Mover', 'Reported Driver': 'No'})
        self.assertEqual(preview['summary']['update'], 1)
        self.assertEqual(preview['rows'][0]['changes']['phone']['from'], '514-555-1000')
        apply_contacts(self.path, preview['batch_id'], 'OWNER')
        with closing(connect(self.path)) as db:
            employee = one(db, 'SELECT * FROM employees')
            profile = one(db, 'SELECT * FROM employee_directory_profiles')
            self.assertEqual((employee['name'], employee['role'], employee['active'], employee['driver_verified'], employee['hourly_cents']),
                ('Example Worker', 'Senior mover', 1, 1, 2875))
            self.assertEqual(profile['email'], 'worker@example.invalid')
            self.assertEqual(profile['emergency_name'], 'Existing contact')
            self.assertEqual(profile['home_address'], 'Private address')
            self.assertEqual(profile['license_stored_name'], 'private-license.pdf')
            self.assertEqual(profile['team_lead'], 1)
            self.assertIn('Keep manager note', profile['notes'])

    def test_repeat_apply_and_repeat_upload_are_idempotent(self):
        record = {'Name': 'New Worker', 'Phone': '514-555-3000', 'Availability': 'Weekends'}
        preview = self.preview(record)
        apply_contacts(self.path, preview['batch_id'], 'OWNER')
        self.assertTrue(apply_contacts(self.path, preview['batch_id'], 'OWNER')['already_applied'])
        again = self.preview(record)
        self.assertEqual(again['summary'], {'create': 0, 'update': 0, 'unchanged': 1, 'conflict': 0})
        apply_contacts(self.path, again['batch_id'], 'OWNER')
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM employees').fetchone()[0], 1)

    def test_conflicting_identifiers_block_entire_batch(self):
        self.existing()
        self.existing('EMP-OTHER', 'Other Worker', '514-555-4000', 'other@example.invalid')
        preview = self.preview({'Name': 'Example Worker', 'Phone': '514-555-4000'}, {'Name': 'New Worker'})
        self.assertEqual(preview['summary']['conflict'], 1)
        with self.assertRaises(ContactImportError) as caught:
            apply_contacts(self.path, preview['batch_id'], 'OWNER')
        self.assertEqual(caught.exception.status, 409)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM employees').fetchone()[0], 2)

    def test_stale_actor_bound_and_expired_previews_cannot_apply(self):
        self.existing()
        preview = self.preview({'Name': 'Example Worker', 'Phone': '514-555-1001'})
        with self.assertRaises(ContactImportError) as caught:
            apply_contacts(self.path, preview['batch_id'], 'OTHER-OWNER')
        self.assertEqual(caught.exception.status, 404)
        with transaction(self.path) as db:
            db.execute("UPDATE employee_directory_profiles SET phone='514-555-9999'")
        with self.assertRaises(ContactImportError) as caught:
            apply_contacts(self.path, preview['batch_id'], 'OWNER')
        self.assertEqual(caught.exception.status, 409)
        with transaction(self.path) as db:
            db.execute("UPDATE employee_contact_imports SET expires_at='2000-01-01T00:00:00+00:00'")
        with self.assertRaises(ContactImportError) as caught:
            apply_contacts(self.path, preview['batch_id'], 'OWNER')
        self.assertEqual(caught.exception.status, 409)

    def test_phone_normalization_matches_existing_name_without_renaming(self):
        self.existing(name='Example Worker Full Name')
        preview = self.preview({'Name': 'Example', 'Phone': '+1 514‑555‑1000\u202c', 'Availability': 'Friday - Sunday'})
        self.assertEqual(preview['summary']['create'], 0)
        self.assertEqual(preview['rows'][0]['matched_name'], 'Example Worker Full Name')

    def test_two_source_rows_matching_one_employee_block_import(self):
        self.existing()
        preview = self.preview({'Name': 'Example Worker'}, {'Name': 'Alias', 'Phone': '514-555-1000'})
        self.assertEqual(preview['summary']['conflict'], 2)

    def test_explicit_full_name_matches_existing_employee_and_prevents_nickname_duplicates(self):
        self.existing(name='Example Worker Full Name')
        preview = self.preview({'Name': 'Example', 'Full Name': 'Example Worker Full Name'})
        self.assertEqual(preview['summary']['create'], 0)
        apply_contacts(self.path, preview['batch_id'], 'OWNER')
        again = self.preview({'Name': 'Example', 'Full Name': 'Example Worker Full Name'})
        self.assertEqual(again['summary']['unchanged'], 1)

    def test_invalid_duplicate_and_formula_workbooks_are_rejected(self):
        for content in (b'not a workbook', workbook_bytes({'Name': 'Worker', 'Email': 'Full Name'}),
                        workbook_bytes({'Name': '=1+1'}), workbook_bytes({'Name': 'Worker'}, {'Name': ' worker '}),
                        workbook_bytes({'Name': 'Worker', 'Phone': '514-555-1000'}, {'Name': 'Other', 'Phone': '+1 514-555-1000'})):
            with self.subTest(content_size=len(content)):
                with self.assertRaises(ContactImportError):
                    parse_contacts(content, 'contacts.xlsx')

    def test_workbooks_without_dimension_metadata_are_supported(self):
        original = workbook_bytes({'Name': 'New Worker', 'Phone': '514-555-3000'})
        output = BytesIO()
        with zipfile.ZipFile(BytesIO(original)) as source, zipfile.ZipFile(output, 'w') as target:
            for entry in source.infolist():
                data = source.read(entry.filename)
                if entry.filename == 'xl/worksheets/sheet1.xml':
                    data = re.sub(rb'<dimension ref="[^"]*"\s*/>', b'', data)
                target.writestr(entry, data)
        parsed = parse_contacts(output.getvalue(), 'contacts.xlsx')
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]['Phone'], '514-555-3000')

    @unittest.skipUnless(importlib.util.find_spec('fastapi') and importlib.util.find_spec('httpx'), 'Web dependencies unavailable in local artifact runtime')
    def test_http_routes_restrict_roles_and_require_actor_owned_preview(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.employee_contacts import register_employee_contacts
        user = {'id': 'OWNER', 'role': 'owner'}
        app = FastAPI()
        register_employee_contacts(app, self.path, lambda: user)
        client = TestClient(app)
        content = workbook_bytes({'Name': 'New Worker', 'Phone': '514-555-3000'})
        for role in ('crew', 'sales', 'dispatch', 'accounting'):
            user['role'] = role
            self.assertEqual(client.post('/api/ux/employee-contact-import/preview', files={'file': ('contacts.xlsx', content)}).status_code, 403)
            self.assertEqual(client.post('/api/ux/employee-contact-import/apply', json={'batch_id': 'unreviewed'}).status_code, 403)
        user['role'] = 'owner'
        response = client.post('/api/ux/employee-contact-import/preview', files={'file': ('contacts.xlsx', content)})
        self.assertEqual(response.status_code, 200)
        batch_id = response.json()['batch_id']
        user['id'] = 'OTHER-OWNER'
        self.assertEqual(client.post('/api/ux/employee-contact-import/apply', json={'batch_id': batch_id}).status_code, 404)
        user['id'] = 'OWNER'
        self.assertEqual(client.post('/api/ux/employee-contact-import/apply', json={'batch_id': batch_id}).status_code, 200)

    @unittest.skipUnless(importlib.util.find_spec('fastapi') and importlib.util.find_spec('httpx'), 'Web dependencies unavailable in local artifact runtime')
    def test_crownops_directory_marks_unknown_pay_until_owner_reviews_rate(self):
        from app.main import create_app
        from fastapi import HTTPException
        old = os.environ.get('CROWNOPS_ENV')
        os.environ['CROWNOPS_ENV'] = 'test'
        try:
            app = create_app(self.path)
            preview = self.preview({'Name': 'New Worker'})
            apply_contacts(self.path, preview['batch_id'], 'OWNER')
            endpoints = {r.path: r.endpoint for r in app.routes if hasattr(r, 'endpoint')}
            self.assertIn('/api/ux/employee-contact-import/preview', endpoints)
            owner = {'id': 'OWNER', 'role': 'owner'}
            directory = endpoints['/api/ux/employee-directory'](u=owner)
            employee = next(e for e in directory['employees'] if e['full_name'] == 'New Worker')
            self.assertTrue(employee['pay_rate_pending'])
            self.assertFalse(employee['active'])
            endpoint = endpoints['/api/ux/employee-directory/{eid}']
            endpoint(employee['id'], {'hourly_rate': '', 'active': False, 'phone': '514-555-3000'}, u=owner)
            still_pending = endpoints['/api/ux/employee-directory'](u=owner)
            self.assertTrue(next(e for e in still_pending['employees'] if e['id']==employee['id'])['pay_rate_pending'])
            with self.assertRaises(HTTPException):
                endpoint(employee['id'], {'hourly_rate': '', 'active': True}, u=owner)
            endpoint(employee['id'], {'hourly_rate': '26.50', 'active': True}, u=owner)
            after = endpoints['/api/ux/employee-directory'](u=owner)
            employee = next(e for e in after['employees'] if e['full_name'] == 'New Worker')
            self.assertFalse(employee['pay_rate_pending'])
            self.assertEqual(employee['hourly_cents'], 2650)
            self.assertTrue(employee['active'])
        finally:
            if old is None:
                os.environ.pop('CROWNOPS_ENV', None)
            else:
                os.environ['CROWNOPS_ENV'] = old


if __name__ == '__main__':
    unittest.main()
