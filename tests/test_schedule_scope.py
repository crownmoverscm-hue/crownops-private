"""Personal schedules follow saved assignments and verified identity, for every staff role."""
from contextlib import closing
import unittest
from app.store import connect,transaction,insert,one,now
import test_job_planning as fixture

class ScheduleScopeTests(unittest.TestCase):
    setUp=fixture.JobPlanningTests.setUp
    staff=fixture.JobPlanningTests.staff
    plan=fixture.JobPlanningTests.plan

    def prepare(self):
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(auto_confirm=True));self.assertEqual(r.status_code,200,r.text)
        with transaction(self.path) as db:
            insert(db,'jobs',dict(id='J2',lead_id='L1',title='Another team move',phase='Moving',start_at='2026-11-15T20:00:00+00:00',end_at='2026-11-15T23:00:00+00:00',created_by='owner',created_at=now()))
            insert(db,'assignments',dict(job_id='J2',resource_type='employee',resource_id='E3'))

    def test_crew_my_jobs_and_all_jobs_have_correct_names_trucks_and_assignment_flags(self):
        self.prepare();crew=self.staff('crew')
        mine=crew.get('/api/ux/final/schedule?scope=mine').json();self.assertEqual([j['id'] for j in mine],['J1'])
        self.assertEqual([e['name'] for e in mine[0]['crew']],['Driver','Mover'])
        self.assertEqual([t['name'] for t in mine[0]['trucks']],['Available truck']);self.assertTrue(mine[0]['assigned_to_me'])
        all_jobs=crew.get('/api/ux/final/schedule?scope=all').json();self.assertEqual([j['id'] for j in all_jobs],['J1','J2'])
        self.assertFalse(all_jobs[1]['assigned_to_me'])
        self.assertEqual(crew.patch('/api/jobs/J2',json={'version':1,'status':'Cancelled'}).status_code,403)
        self.assertEqual(crew.put('/api/jobs/J1/plan',json=self.plan(version=2)).status_code,403)

    def test_manager_my_jobs_resolves_unique_exact_email_without_changing_account_or_pay_privileges(self):
        self.prepare()
        with transaction(self.path) as db:db.execute("INSERT INTO employee_directory_profiles(employee_id,email) VALUES('E1','  DISPATCH@example.invalid  ')")
        manager=self.staff('dispatch');identity=manager.get('/api/schedule-identity').json()
        self.assertEqual((identity['id'],identity['source']),('E1','email'))
        self.assertEqual([j['id'] for j in manager.get('/api/ux/final/schedule?scope=mine').json()],['J1'])
        with closing(connect(self.path)) as db:self.assertIsNone(one(db,"SELECT employee_id FROM users WHERE id='dispatch'")['employee_id'])
        self.assertNotIn('hourly_cents',manager.get('/api/employees').json()[0])

    def test_every_office_role_can_use_personal_scope_and_the_same_all_jobs_calendar(self):
        self.prepare()
        for role in ('owner','sales','dispatch','accounting'):
            with self.subTest(role=role):
                with transaction(self.path) as db:db.execute("UPDATE users SET employee_id='E2' WHERE id=?",(role,))
                client=self.staff(role);self.assertEqual(client.get('/api/schedule-identity').json()['id'],'E2')
                self.assertEqual([j['id'] for j in client.get('/api/ux/final/schedule?scope=mine').json()],['J1'])
                self.assertEqual(len(client.get('/api/ux/final/schedule?scope=all').json()),2)

    def test_missing_or_ambiguous_identity_never_falls_back_to_everyones_jobs_or_a_name_match(self):
        self.prepare()
        with transaction(self.path) as db:db.execute("UPDATE users SET name='Driver' WHERE id='dispatch'")
        client=self.staff('dispatch');self.assertFalse(client.get('/api/schedule-identity').json()['matched'])
        self.assertEqual(client.get('/api/ux/final/schedule?scope=mine').json(),[])
        with transaction(self.path) as db:
            insert(db,'employee_directory_profiles',dict(employee_id='E1',email='dispatch@example.invalid'))
            db.execute("UPDATE employee_directory_profiles SET email='dispatch@example.invalid' WHERE employee_id='E2'")
        identity=client.get('/api/schedule-identity').json();self.assertEqual(identity['source'],'ambiguous')
        self.assertIsNone(identity['id']);self.assertEqual(client.get('/api/ux/final/schedule?scope=mine').json(),[])

    def test_explicit_employee_link_wins_over_email_and_request_parameters_cannot_impersonate_another_worker(self):
        self.prepare()
        with transaction(self.path) as db:
            db.execute("UPDATE users SET employee_id='E3' WHERE id='dispatch'")
            insert(db,'employee_directory_profiles',dict(employee_id='E1',email='dispatch@example.invalid'))
        client=self.staff('dispatch');self.assertEqual(client.get('/api/schedule-identity').json()['source'],'linked')
        self.assertEqual([j['id'] for j in client.get('/api/ux/final/schedule?scope=mine&employee_id=E1&user_id=crew').json()],['J2'])

    def test_reassigning_updates_schedule_and_move_names_from_the_same_saved_records(self):
        self.prepare()
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(version=2,auto_confirm=True,crew_ids=['E2']))
        self.assertEqual(r.status_code,200,r.text)
        schedule=self.owner.get('/api/ux/final/schedule?scope=all').json()[0]
        move=self.owner.get('/api/ux/final/job/J1').json()
        self.assertEqual([e['name'] for e in schedule['crew']],['Mover']);self.assertEqual([e['name'] for e in move['crew']],['Mover'])
        self.assertEqual([t['name'] for t in schedule['trucks']],[t['name'] for t in move['trucks']])
        self.assertEqual(self.staff('crew').get('/api/ux/final/schedule?scope=mine').json(),[])

    def test_local_week_boundaries_and_invalid_scope_are_enforced(self):
        self.prepare()
        with transaction(self.path) as db:db.execute("UPDATE jobs SET start_at='2026-11-16T02:00:00+00:00',end_at='2026-11-16T03:00:00+00:00' WHERE id='J1'")
        r=self.staff('crew').get('/api/ux/final/schedule?scope=mine&start=2026-11-15&end=2026-11-15')
        self.assertEqual([j['id'] for j in r.json()],['J1'])
        self.assertEqual(self.owner.get('/api/ux/final/schedule?scope=someone_else').status_code,422)
