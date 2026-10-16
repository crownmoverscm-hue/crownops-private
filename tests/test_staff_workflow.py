"""Employee onboarding, partial assignment, private wages and ordered locations."""
from contextlib import closing
import json
from pathlib import Path
import unittest
from fastapi.testclient import TestClient
from app.main import create_app,sha
from app.store import connect,transaction,insert,one,rows,now,dumps,initialize
import test_job_planning as fixture

class StaffWorkflowTests(unittest.TestCase):
    setUp=fixture.JobPlanningTests.setUp
    staff=fixture.JobPlanningTests.staff
    plan=fixture.JobPlanningTests.plan
    snapshot=fixture.JobPlanningTests.snapshot

    def pay_records(self):
        with transaction(self.path) as db:
            db.execute("UPDATE users SET name='Jonathan Probonas' WHERE id='owner'")
            db.execute("UPDATE users SET employee_id='E2' WHERE id='sales'")
            insert(db,'users',dict(id='carl',name='Carl',email='carl@example.invalid',role='owner',password_hash='unused',active=1,created_at=now()))
            insert(db,'sessions',dict(token_hash=sha('carl-token'),user_id='carl',csrf='test-csrf',expires_at=one(db,"SELECT expires_at FROM sessions WHERE user_id='owner'")['expires_at']))
            db.execute("UPDATE employees SET hourly_cents=2345 WHERE id='E1'")
            db.execute("UPDATE employees SET hourly_cents=5823 WHERE id='E2'")
            insert(db,'job_invoices',dict(id='INV',job_id='J1',number='TEST-INV',terms_json='{}',terms_approved_by='owner',terms_approved_at=now(),created_by='owner',created_at=now(),updated_at=now()))
            for i,eid,rate in [(1,'E1',2345),(2,'E2',5823)]:
                insert(db,'times',dict(id='T'+str(i),job_id='J1',employee_id=eid,service_minutes=120,transport_minutes=60,submitted_by='owner',created_at=now()))
                insert(db,'payroll_drafts',dict(id='PD'+str(i),job_id='J1',employee_id=eid,invoice_id='INV',invoice_revision=1,service_minutes=120,transport_minutes=60,hourly_cents=rate,created_at=now()))
                insert(db,'payroll_adjustments',dict(id='PA'+str(i),draft_id='PD'+str(i),base_draft_id='PD'+str(i),job_id='J1',employee_id=eid,effective_date='2026-11-15',service_delta=60,transport_delta=0,break_delta=0,extra_delta=0,pay_delta_cents=rate,note='Test adjustment',approved_by='owner',approved_at=now()))

    def test_every_pay_read_route_enforces_owner_or_exact_employee(self):
        self.pay_records()
        for user,eid,owner in [('owner',None,True),('carl',None,True),('dispatch',None,False),('accounting',None,False),('sales','E2',False),('crew','E1',False),('lead','E2',False)]:
            client=self.staff(user)
            for endpoint,key,id_key,pay_keys in [('/api/employees',None,'id',['hourly_cents']),('/api/times',None,'employee_id',['hourly_cents']),('/api/ux/employee-directory','employees','id',['hourly_cents']),('/api/ux/payroll-summary?start=2026-11-01&end=2026-11-30','employees','employee_id',['hourly_cents','gross_cents']),('/api/payroll-drafts',None,'employee_id',['hourly_cents','estimated_pay_cents']),('/api/payroll-adjustments',None,'employee_id',['pay_delta_cents'])]:
                with self.subTest(user=user,endpoint=endpoint):
                    r=client.get(endpoint);self.assertEqual(r.status_code,200,r.text);data=r.json();data=data[key] if key else data
                    for record in data:
                        expected=owner or record[id_key]==eid
                        for field in pay_keys:self.assertEqual(field in record,expected,(user,endpoint,field,record))
            if not owner:
                self.assertEqual(client.get('/api/payroll-export?period_start=2026-11-01').status_code,403)
                self.assertEqual(client.get('/api/payroll-adjustments/export?period_start=2026-11-01').status_code,403)
                self.assertEqual(client.post('/api/payroll-drafts/PD1/reconcile',json={}).status_code,403)
                self.assertEqual(client.patch('/api/ux/employee-directory/E1',json={'hourly_rate':1}).status_code,403)

    def test_new_crew_invitation_creates_and_links_once_without_manual_steps(self):
        payload={'name':'New Worker','email':'new.worker@example.invalid','role':'crew'}
        first=self.owner.post('/api/invitations',json=payload);self.assertEqual(first.status_code,200,first.text)
        token=first.json()['url'].split('#invite=')[1]
        with closing(connect(self.path)) as db:
            invitation=one(db,'SELECT * FROM invitations WHERE email=?',(payload['email'],));eid=invitation['employee_id']
            employee=one(db,'SELECT * FROM employees WHERE id=?',(eid,));self.assertEqual(employee['active'],1);self.assertEqual(employee['driver_verified'],0)
            self.assertTrue(json.loads(employee['raw_json'])['contact_import_pay_pending'])
        r=self.owner.post('/api/invitations',json=payload);self.assertEqual(r.status_code,200,r.text)
        with closing(connect(self.path)) as db:self.assertEqual(db.execute("SELECT count(*) FROM employees WHERE name='New Worker'").fetchone()[0],1)
        # The newest private link replaces the earlier one, and acceptance cannot choose another employee.
        token=r.json()['url'].split('#invite=')[1]
        r=TestClient(self.app).post('/api/accept-invitation',json={'token':token,'password':'Test-password-long-2026','employee_id':'E1','role':'owner'})
        self.assertEqual(r.status_code,200,r.text)
        with closing(connect(self.path)) as db:
            user=one(db,'SELECT * FROM users WHERE email=?',(payload['email'],));self.assertEqual(user['employee_id'],eid);self.assertEqual(user['role'],'crew')

    def test_automatic_employee_match_uses_exact_email_and_never_name_only(self):
        with transaction(self.path) as db:db.execute("UPDATE employee_directory_profiles SET email='EXISTING@example.invalid' WHERE employee_id='E2'")
        r=self.owner.post('/api/invitations',json={'name':'Existing worker','email':'existing@example.invalid','role':'crew'})
        self.assertEqual(r.status_code,200,r.text)
        with closing(connect(self.path)) as db:self.assertEqual(one(db,"SELECT * FROM invitations WHERE email='existing@example.invalid'")['employee_id'],'E2')
        r=self.owner.post('/api/invitations',json={'name':'Mover','email':'different@example.invalid','role':'crew'});self.assertEqual(r.status_code,200,r.text)
        with closing(connect(self.path)) as db:self.assertNotEqual(one(db,"SELECT * FROM invitations WHERE email='different@example.invalid'")['employee_id'],'E2')

    def test_single_employee_save_updates_directory_and_does_not_confirm_incomplete_plan(self):
        with transaction(self.path) as db:db.execute("UPDATE jobs SET status='Confirmed' WHERE id='J1'")
        r=self.staff('dispatch').put('/api/jobs/J1/plan',json=self.plan(crew_ids=['E1'],truck_ids=[]));self.assertEqual(r.status_code,200,r.text)
        directory=self.staff('dispatch').get('/api/ux/employee-directory').json()['employees']
        first=next(e for e in directory if e['id']=='E1');self.assertEqual([j['id'] for j in first['assigned_services']],['J1'])
        self.assertEqual(self.snapshot()[0]['plan_review_required'],1)
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(version=2));self.assertEqual(r.status_code,200,r.text)
        r=self.owner.patch('/api/jobs/J1',json={'version':3,'status':'In progress'});self.assertEqual(r.status_code,409,r.text)
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(version=3,confirm=True));self.assertEqual(r.status_code,200,r.text)
        r=self.owner.patch('/api/jobs/J1',json={'version':4,'status':'In progress'});self.assertEqual(r.status_code,200,r.text)

    def test_pending_pay_does_not_block_assigning_or_confirming_an_active_employee(self):
        with transaction(self.path) as db:db.execute("UPDATE employees SET hourly_cents=0,raw_json=? WHERE id='E2'",(dumps({'contact_import_pay_pending':True}),))
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(confirm=True));self.assertEqual(r.status_code,200,r.text)
        r=self.owner.patch('/api/jobs/J1',json={'version':2,'status':'In progress'});self.assertEqual(r.status_code,200,r.text)
        directory=self.owner.get('/api/ux/employee-directory').json();self.assertTrue(next(e for e in directory['employees'] if e['id']=='E2')['pay_rate_pending'])

    def test_ordered_route_and_repeat_locations_survive_save_and_crew_read(self):
        route=[{'kind':'Pickup','address':'Test A','access':'Elevator','notes':'Call first'},{'kind':'Pickup','address':'Second pickup','access':'Stairs','notes':''},{'kind':'Storage','address':'Storage address','access':'Dock','notes':'Locker 12'},{'kind':'Return','address':'Test A','access':'','notes':'Return keys'}]
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(route_stops=route,confirm=True));self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.owner.get('/api/jobs/J1/planning').json()['job']['route_stops'],route)
        self.assertEqual(self.staff('crew').get('/api/ux/final/job/J1').json()['route_stops'],route)
        job=next(j for j in self.owner.get('/api/jobs').json() if j['id']=='J1');self.assertEqual(job['stops'],[s['address'] for s in route])
        history=json.loads(self.snapshot()[2][-1]['details_json']);self.assertEqual(history['after']['route_stops'],route)

    def test_legacy_addresses_are_preserved_and_invalid_routes_are_atomic(self):
        with transaction(self.path) as db:db.execute("UPDATE jobs SET stops_json=? WHERE id='J1'",(dumps(['Test A','Intermediate address','Test B']),))
        route=self.owner.get('/api/jobs/J1/planning').json()['job']['route_stops'];self.assertEqual([r['address'] for r in route],['Test A','Intermediate address','Test B'])
        before=self.snapshot()
        for bad in [[{'address':'','kind':'Stop'}],[{'address':'Address','kind':'Unknown'}],{'address':'Object'}]:
            r=self.owner.put('/api/jobs/J1/plan',json=self.plan(route_stops=bad));self.assertEqual(r.status_code,422,r.text);self.assertEqual(self.snapshot(),before)
        initialize(self.path);initialize(self.path)
        self.assertEqual(self.owner.get('/api/jobs/J1/planning').json()['job']['route_stops'],route)

    def test_client_page_with_no_jobs_still_opens(self):
        with transaction(self.path) as db:insert(db,'leads',dict(id='EMPTY',client_name='No services yet',created_at=now(),updated_at=now()))
        r=self.owner.get('/api/ux/final/client/EMPTY');self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['services'],[])

    def test_older_assignment_client_keeps_stop_access_and_instructions(self):
        route=[{'kind':'Pickup','address':'Test A','access':'Elevator booking','notes':'Call the client first'},
               {'kind':'Delivery','address':'Test B','access':'Dock','notes':'Use the side door'}]
        first=self.owner.put('/api/jobs/J1/plan',json=self.plan(route_stops=route));self.assertEqual(first.status_code,200,first.text)
        # An already-open older screen knows only the unchanged address list.
        second=self.staff('dispatch').put('/api/jobs/J1/plan',json=self.plan(version=2,crew_ids=['E1'],truck_ids=[],stops=['Test A','Test B']))
        self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(self.owner.get('/api/jobs/J1/planning').json()['job']['route_stops'],route)
