"""Company-reference tests use fictional contacts and disposable databases."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import Workbook
from app.company_ops import (HEADERS, ReferenceError, initialize_reference, parse_reference,
    preview_reference, apply_reference)
from app.store import initialize, connect, transaction, insert, one, rows, now, dumps


def fixture_bytes(contact=True, repair=True):
    source = {name: [list(headers)] for name, headers in HEADERS.items()}
    if contact:
        source['Important contacts'].append(['Example Towing', 'Towing', 'Test Contact', '514-555-1000', '', '', '', 'Test area', 'Call dispatch first'])
    source['Trucks'].append(['Test Truck', 2020, 'Example Make', 'Example Model', 'Diesel', 'TEST001', 'TESTVIN001', 'Month only; confirm exact expiry'])
    source['Service history'].append(['Test Truck', '2026-01-02', 1200, 'Oil & Filter', 'Next: 9,200'])
    if repair:
        source['Service history'].append(['Test Truck', '', '', 'Other', 'Front tires - DUE'])
    source['Review notes'].append(['Unlabelled source item', 'Confirm its purpose before sharing'])
    return source


def workbook_bytes(source):
    book = Workbook(); book.remove(book.active)
    for name, matrix in source.items():
        sheet = book.create_sheet(name)
        for record in matrix:
            sheet.append(record)
    output = BytesIO(); book.save(output); book.close()
    return output.getvalue()


class CompanyReferenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'reference.sqlite3'
        initialize(self.path); initialize_reference(self.path)

    def preview(self, source=None, actor='OWNER'):
        return preview_reference(self.path, workbook_bytes(source or fixture_bytes()), 'company.xlsx', actor)

    def test_preview_and_apply_preserve_history_without_fabricating_safety_checks_or_dates(self):
        p = self.preview()
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM vehicles').fetchone()[0], 0)
        apply_reference(self.path, p['batch_id'], 'OWNER')
        with closing(connect(self.path)) as db:
            truck = one(db, 'SELECT * FROM vehicles')
            profile = one(db, 'SELECT * FROM fleet_profiles')
            reference = one(db, 'SELECT * FROM fleet_reference')
            self.assertEqual(truck['active'], 0)
            self.assertEqual(profile['inspection_due'], '')
            self.assertEqual(profile['odometer_km'], 1200)
            self.assertEqual(profile['next_service_km'], 9200)
            self.assertEqual(profile['check_required'], 1)
            self.assertEqual(reference['review_required'], 1)
            self.assertEqual(reference['odometer_recorded_date'], '2026-01-02')
            self.assertEqual(db.execute('SELECT count(*) FROM inspections').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM fleet_service_history').fetchone()[0], 2)
            repair = one(db, 'SELECT * FROM maintenance')
            self.assertEqual((repair['status'], repair['due_date']), ('Open', ''))

    def test_repeat_apply_and_upload_do_not_duplicate_contacts_trucks_or_service(self):
        p = self.preview(); apply_reference(self.path, p['batch_id'], 'OWNER')
        self.assertTrue(apply_reference(self.path, p['batch_id'], 'OWNER')['already_applied'])
        again = self.preview()
        for kind in ('contacts', 'trucks', 'history'):
            self.assertEqual(again['summary'][kind]['create'], 0)
            self.assertEqual(again['summary'][kind]['update'], 0)
        apply_reference(self.path, again['batch_id'], 'OWNER')
        with closing(connect(self.path)) as db:
            for table, count in [('company_contacts', 1), ('vehicles', 1), ('fleet_service_history', 2), ('maintenance', 1)]:
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0], count)

    def test_existing_availability_color_capacity_inspection_date_and_newer_odometer_are_preserved(self):
        with transaction(self.path) as db:
            insert(db, 'vehicles', dict(id='TRUCK', name='Test Truck', active=1, color_hex='#ABCDEF', display_order=8))
            insert(db, 'fleet_profiles', dict(vehicle_id='TRUCK', plate='TEST001', vin='TESTVIN001', model='Example Model',
                odometer_km=4500, gvwr_kg=6500, payload_kg=3000, cargo_length_ft=20, inspection_due='2027-04-30', next_service_km=12500, updated_at=now()))
        p = self.preview(); apply_reference(self.path, p['batch_id'], 'OWNER')
        with closing(connect(self.path)) as db:
            v = one(db, 'SELECT * FROM vehicles'); p = one(db, 'SELECT * FROM fleet_profiles')
            self.assertEqual((v['active'], v['color_hex'], v['display_order']), (1, '#ABCDEF', 8))
            self.assertEqual((p['odometer_km'], p['gvwr_kg'], p['inspection_due'], p['next_service_km']), (4500, 6500, '2027-04-30', 12500))
            self.assertEqual(v['version'], 2)
            self.assertEqual(one(db, 'SELECT * FROM fleet_reference')['odometer_recorded_date'], '')

    def test_conflicting_identity_blocks_the_entire_import(self):
        with transaction(self.path) as db:
            insert(db, 'vehicles', dict(id='TRUCK', name='Test Truck'))
            insert(db, 'fleet_profiles', dict(vehicle_id='TRUCK', vin='OTHER-VIN', updated_at=now()))
        p = self.preview(); self.assertTrue(p['conflicts'])
        with self.assertRaises(ReferenceError):
            apply_reference(self.path, p['batch_id'], 'OWNER')
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM company_contacts').fetchone()[0], 0)

    def test_stale_actor_and_expiry_guards(self):
        p = self.preview()
        with self.assertRaises(ReferenceError):apply_reference(self.path, p['batch_id'], 'OTHER')
        with transaction(self.path) as db:
            insert(db, 'vehicles', dict(id='NEW-TRUCK', name='Concurrent edit'))
        with self.assertRaises(ReferenceError):apply_reference(self.path, p['batch_id'], 'OWNER')
        p = self.preview()
        with transaction(self.path) as db:
            db.execute('UPDATE company_reference_imports SET expires_at=? WHERE id=?', ('2000-01-01T00:00:00+00:00', p['batch_id']))
        with self.assertRaises(ReferenceError):apply_reference(self.path, p['batch_id'], 'OWNER')

    def test_duplicate_formula_bad_date_and_unknown_truck_inputs_are_rejected(self):
        for change in ('duplicate', 'formula', 'date', 'truck', 'duplicate_identity'):
            source = fixture_bytes()
            if change == 'duplicate':source['Service history'].append(source['Service history'][1])
            if change == 'formula':source['Important contacts'][1][3] = '=1+1'
            if change == 'date':source['Service history'][1][1] = '2026-02-30'
            if change == 'truck':source['Service history'][1][0] = 'Missing truck'
            if change == 'duplicate_identity':source['Trucks'].append(['Another Truck', 2020, 'Example Make', 'Example Model', 'Diesel', 'TEST001', 'TESTVIN002', ''])
            with self.subTest(change=change), self.assertRaises(ReferenceError):
                parse_reference(workbook_bytes(source), 'company.xlsx')

    def test_zero_odometer_is_retained_and_ambiguous_numbers_stay_in_private_review_notes(self):
        source = fixture_bytes(); source['Service history'][1][2] = 0
        parsed = parse_reference(workbook_bytes(source), 'company.xlsx')
        self.assertEqual(parsed['Service history'][0]['Odometer km'], '0')
        p = self.preview(source); apply_reference(self.path, p['batch_id'], 'OWNER')
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM company_contacts').fetchone()[0], 1)


@unittest.skipUnless(importlib.util.find_spec('fastapi') and importlib.util.find_spec('httpx'), 'Web dependencies unavailable locally')
class CompanyAccessTests(unittest.TestCase):
    preview = CompanyReferenceTests.preview

    def setUp(self):
        CompanyReferenceTests.setUp(self)
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.company_ops import register_company_ops
        self.user = {'id': 'OWNER', 'role': 'owner'}
        self.app = FastAPI(); register_company_ops(self.app, self.path, lambda: self.user)
        self.client = TestClient(self.app); self.addCleanup(self.client.close)

    def test_contact_reads_and_privileged_routes_are_enforced_on_server(self):
        for role in ('owner', 'dispatch', 'sales', 'accounting', 'crew'):
            self.user['role'] = role
            self.assertEqual(self.client.get('/api/company/contacts').status_code, 200)
            self.assertEqual(self.client.get('/api/company/fleet').status_code, 403 if role == 'crew' else 200)
            self.assertEqual(self.client.get('/api/company/launch-checks').status_code, 200 if role == 'owner' else 403)
            r = self.client.post('/api/company/contacts', json={'service': 'Example '+role, 'category': 'Towing', 'phone': '5145551000'})
            self.assertEqual(r.status_code, 200 if role in ('owner', 'dispatch') else 403)
            r = self.client.post('/api/company/reference-import/preview', files={'file': ('company.xlsx', workbook_bytes(fixture_bytes()))})
            self.assertEqual(r.status_code, 200 if role == 'owner' else 403)
            if role != 'owner':
                self.assertEqual(self.client.post('/api/company/reference-import/apply', json={'batch_id': 'unknown'}).status_code, 403)

    def test_truck_review_requires_exact_inspection_and_resolved_repairs_and_still_needs_preuse_check(self):
        p = self.preview(); apply_reference(self.path, p['batch_id'], 'OWNER')
        with closing(connect(self.path)) as db:vid = one(db, 'SELECT * FROM vehicles')['id']
        url = '/api/company/fleet/'+vid
        evidence = {'reviewed': True, 'note': 'Registration and workshop documents verified by test owner'}
        self.assertEqual(self.client.post(url+'/review', json=evidence).status_code, 409)
        expiry = (date.today()+timedelta(days=365)).isoformat()
        self.assertEqual(self.client.put(url+'/reference', json={'version': 1, 'year': '2020', 'make': 'Example Make', 'fuel': 'Diesel',
            'inspection_due': expiry}).status_code, 200)
        self.assertEqual(self.client.post(url+'/review', json=evidence).status_code, 409)
        with transaction(self.path) as db:db.execute("UPDATE maintenance SET status='Completed',notes='Test repair evidence recorded'")
        self.assertEqual(self.client.post(url+'/review', json=evidence).status_code, 200)
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db, 'SELECT * FROM vehicles')['active'], 1)
            self.assertEqual(one(db, 'SELECT * FROM fleet_profiles')['check_required'], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM inspections').fetchone()[0], 0)
        self.user['role'] = 'sales'
        self.assertEqual(self.client.put(url+'/reference', json={}).status_code, 403)
        self.assertEqual(self.client.post(url+'/review', json=evidence).status_code, 403)

    def test_contact_edit_rejects_stale_version_and_preserves_other_contacts(self):
        r = self.client.post('/api/company/contacts', json={'service': 'Example Towing', 'category': 'Towing', 'phone': '5145551000'}).json()
        self.assertEqual(self.client.post('/api/company/contacts', json={'service': 'example towing', 'category': 'Towing', 'phone': '514-555-1000'}).status_code, 409)
        url = '/api/company/contacts/'+r['id']
        self.assertEqual(self.client.put(url, json={'version': 1, 'phone': '5145552000'}).status_code, 200)
        self.assertEqual(self.client.put(url, json={'version': 1, 'phone': '5145553000'}).status_code, 409)


@unittest.skipUnless(importlib.util.find_spec('fastapi') and importlib.util.find_spec('httpx'), 'Web dependencies unavailable locally')
class DispatchStartTests(unittest.TestCase):
    def setUp(self):
        from app.main import create_app, sha
        from fastapi.testclient import TestClient
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'dispatch.sqlite3'
        env = patch.dict(os.environ, {'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0'})
        env.start(); self.addCleanup(env.stop)
        self.app = create_app(self.path)
        with transaction(self.path) as db:
            insert(db, 'users', dict(id='OWNER', name='Test Owner', email='owner@example.invalid', role='owner', password_hash='unused', created_at=now()))
            insert(db, 'sessions', dict(token_hash=sha('test-session'), user_id='OWNER', csrf='test-csrf', expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
            insert(db, 'leads', dict(id='LEAD', client_name='Test Client', phone='5145559000', pickup='Test pickup', dropoff='Test destination', created_at=now(), updated_at=now()))
            insert(db, 'jobs', dict(id='JOB', lead_id='LEAD', title='Test move', phase='Moving', status='Confirmed', start_at='2027-01-01T14:00:00+00:00',
                                  end_at='2027-01-01T18:00:00+00:00', created_by='OWNER', created_at=now()))
        self.client = TestClient(self.app); self.addCleanup(self.client.close)
        self.client.cookies.set('crownops_session', 'test-session'); self.client.headers['x-csrf-token'] = 'test-csrf'

    def test_confirmed_job_without_resources_cannot_start(self):
        response = self.client.patch('/api/jobs/JOB', json={'version': 1, 'status': 'In progress'})
        self.assertEqual(response.status_code, 409, response.text)
        with closing(connect(self.path)) as db:self.assertEqual(one(db, 'SELECT * FROM jobs')['status'], 'Confirmed')

    def test_legacy_fleet_routes_keep_sales_read_only_and_crew_sees_assigned_trucks(self):
        with transaction(self.path) as db:
            insert(db,'employees',dict(id='E',name='Test Worker',role='Mover'))
            for vid in ('V','OTHER'):
                insert(db,'vehicles',dict(id=vid,name='Test '+vid,active=1))
                insert(db,'fleet_profiles',dict(vehicle_id=vid,updated_at=now()))
            insert(db,'assignments',dict(job_id='JOB',resource_type='employee',resource_id='E'))
            insert(db,'assignments',dict(job_id='JOB',resource_type='vehicle',resource_id='V'))
            db.execute("UPDATE users SET role='sales' WHERE id='OWNER'")
        self.assertEqual(self.client.get('/api/company/fleet').status_code,200)
        for method,url,body in [('post','/api/vehicles',{'name':'Another truck'}),
                               ('put','/api/vehicles/V',{'name':'Renamed'}),
                               ('post','/api/vehicles/V/activate',{'note':'Review note'}),
                               ('put','/api/fleet/V/profile',{}),
                               ('post','/api/fleet/V/maintenance',{}),
                               ('post','/api/fleet/V/inspection',{})]:
            self.assertEqual(getattr(self.client,method)(url,json=body).status_code,403,url)
        with transaction(self.path) as db:db.execute("UPDATE users SET role='crew',employee_id='E' WHERE id='OWNER'")
        self.assertEqual(self.client.get('/api/company/contacts').status_code,200)
        self.assertEqual(self.client.get('/api/company/fleet').status_code,403)
        fleet=self.client.get('/api/fleet').json()
        self.assertEqual([v['id'] for v in fleet],['V'])
        self.assertEqual((fleet[0]['maintenance'],fleet[0]['documents']),([],[]))

    def test_resource_readiness_requires_quoted_crew_and_verified_drivers(self):
        from app.security import job_resource_blockers
        with transaction(self.path) as db:
            insert(db, 'employees', dict(id='E', name='Test Worker', role='Mover', active=1, driver_verified=0))
            insert(db, 'vehicles', dict(id='V', name='Test Truck', active=1))
            insert(db, 'assignments', dict(job_id='JOB', resource_type='employee', resource_id='E'))
            insert(db, 'assignments', dict(job_id='JOB', resource_type='vehicle', resource_id='V'))
            insert(db, 'pricing_versions', dict(id='P', label='Test rates', rates_json='[]', settings_json='{}', created_at=now()))
            insert(db, 'quotes', dict(id='Q', lead_id='LEAD', revision=1, status='Accepted', inputs_json='{}', result_json=dumps({'movers':3,'trucks':2}),
                pricing_id='P',lead_snapshot_json='{}',lead_version=1,created_by='OWNER',created_at=now()))
            db.execute("UPDATE jobs SET quote_id='Q' WHERE id='JOB'")
            problems = job_resource_blockers(db, one(db, 'SELECT * FROM jobs'))
            self.assertIn('Assign 3 crew members',problems)
            self.assertIn('Assign 2 trucks',problems)
            self.assertTrue(any('verified driver' in x for x in problems))
            db.execute("UPDATE employees SET driver_verified=1,raw_json=? WHERE id='E'", (dumps({'contact_import_pay_pending': True}),))
            self.assertTrue(any('pay rate' in x for x in job_resource_blockers(db, one(db, 'SELECT * FROM jobs'))))
            db.execute("UPDATE employees SET raw_json='{}' WHERE id='E'")
            db.execute("UPDATE jobs SET phase='Packing' WHERE id='JOB'")
            db.execute("DELETE FROM assignments WHERE resource_type='vehicle'")
            problems=job_resource_blockers(db,one(db,'SELECT * FROM jobs'))
            self.assertFalse(any('truck' in x or 'driver' in x for x in problems))

    def test_completed_job_does_not_hold_future_resource_capacity(self):
        with transaction(self.path) as db:
            db.execute("UPDATE jobs SET status='Completed'")
            insert(db, 'employees', dict(id='E', name='Test Worker', role='Mover', active=1, driver_verified=1))
            insert(db, 'vehicles', dict(id='V', name='Test Truck', active=1))
            insert(db, 'assignments', dict(job_id='JOB', resource_type='employee', resource_id='E'))
            insert(db, 'assignments', dict(job_id='JOB', resource_type='vehicle', resource_id='V'))
        response = self.client.post('/api/jobs', json={'lead_id': 'LEAD', 'title': 'New plan', 'phase': 'Moving', 'start_at': '2027-01-01T14:00:00+00:00',
            'end_at': '2027-01-01T18:00:00+00:00', 'crew_ids': ['E'], 'truck_ids': ['V']})
        self.assertEqual(response.status_code, 200, response.text)


if __name__ == '__main__':
    unittest.main()
