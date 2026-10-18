"""Fictional assigned-team briefs, reviewed guides and durable crew handoffs."""
from contextlib import closing
import json
import unittest

from app.job_handoff import CHECK_KEYS
from app.store import connect, dumps, insert, now, one, rows, transaction
import test_launch_access as fixture


class JobHandoffTests(unittest.TestCase):
    staff = fixture.LaunchAccessTests.staff
    plan = fixture.LaunchAccessTests.plan

    def setUp(self):
        fixture.LaunchAccessTests.setUp(self)
        with transaction(self.path) as db:
            db.execute("UPDATE leads SET email='handoff-client@example.invalid' WHERE id='L1'")
        response = self.owner.put('/api/jobs/J1/plan', json=self.plan(auto_confirm=True))
        self.assertEqual(response.status_code, 200, response.text)
        with transaction(self.path) as db:
            quote = json.loads(one(db, "SELECT result_json FROM quotes WHERE id='Q1'")['result_json'])
            quote['line_items'] = [{'kind': 'material', 'code': 'BAG', 'name': 'Mattress bag', 'qty': 2, 'unit_cents': 1499, 'note': 'King beds'}]
            db.execute("UPDATE quotes SET result_json=? WHERE id='Q1'", (dumps(quote),))
            insert(db, 'jobs', dict(id='J2', lead_id='L1', title='Other team service', phase='Moving',
                start_at='2026-11-16T14:00:00+00:00', end_at='2026-11-16T20:00:00+00:00', created_by='owner', created_at=now()))
            insert(db, 'assignments', dict(job_id='J2', resource_type='employee', resource_id='E3'))
            insert(db, 'saved_media_links', dict(id='SHARED', lead_id='L1', label='Shared inventory', url='https://drive.google.com/drive/folders/fictional', created_by='owner', created_at=now()))
            insert(db, 'saved_media_links', dict(id='OTHER', lead_id='L1', job_id='J2', label='Other service', url='https://example.invalid/other', created_by='owner', created_at=now()))
        self.crew = self.staff('crew')

    def payload(self, **extra):
        current = self.crew.get('/api/jobs/J1/handoff').json()['checklist']
        return {**{key: current[key] for key in ('version', 'job_version', 'protocol_version')},
                'checks': {key: True for key in CHECK_KEYS}, 'notes': 'Fictional team reviewed the service.', **extra}

    def guide(self, **extra):
        return {'version': 0, 'title': 'Reviewed company guide', 'preparation': 'Confirm the plan with the manager.',
            'service': 'Follow the approved company training. Contact the manager about unclear conditions.',
            'completion': 'Record actual time and verify the client handoff.', 'reviewed': True, **extra}

    def test_assigned_brief_has_actual_people_leads_truck_route_and_materials_without_pay(self):
        response = self.crew.get('/api/jobs/J1/handoff'); self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual([person['name'] for person in data['crew']], ['Driver', 'Mover'])
        self.assertEqual(data['leads'], ['Mover'])
        self.assertEqual([truck['name'] for truck in data['trucks']], ['Available truck'])
        self.assertEqual([stop['address'] for stop in data['route']], ['Test A', 'Test B'])
        self.assertEqual(data['materials'], [{'name': 'Mattress bag', 'quantity': 2, 'note': 'King beds'}])
        self.assertEqual([link['id'] for link in data['media_links']], ['SHARED'])
        self.assertFalse(data['protocols']['approved'])
        for private in ('hourly_cents', 'home_address', 'emergency_phone', 'unit_cents', 'PRIVATE ADDRESS'):
            self.assertNotIn(private, json.dumps(data))

    def test_assignment_required_even_for_designated_crew_lead_and_office_roles_do_not_mutate(self):
        payload = self.payload()
        for role in ('crew', 'lead'):
            user = self.staff(role)
            self.assertEqual(user.get('/api/jobs/J2/handoff').status_code, 403)
            self.assertEqual(user.put('/api/jobs/J2/handoff', json=payload).status_code, 403)
        for role in ('sales', 'dispatch', 'accounting'):
            user = self.staff(role)
            self.assertEqual(user.get('/api/jobs/J1/handoff').status_code, 403)
            self.assertEqual(user.put('/api/jobs/J1/handoff', json=payload).status_code, 403)
        self.assertEqual(self.manager.get('/api/jobs/J2/handoff').status_code, 200)
        self.assertEqual(self.owner.get('/api/jobs/J2/handoff').status_code, 200)

    def test_checklist_persists_actor_history_without_changing_job_invoice_or_pay(self):
        before = self.owner.get('/api/jobs').json()
        response = self.crew.put('/api/jobs/J1/handoff', json=self.payload())
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json(); self.assertEqual(result['version'], 1)
        self.assertEqual(result['updated_by_name'], 'Crew'); self.assertTrue(all(result['checks'].values()))
        self.assertEqual(self.owner.get('/api/jobs').json(), before)
        self.assertEqual(self.manager.get('/api/jobs/J1/handoff').json()['checklist']['notes'], 'Fictional team reviewed the service.')
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db, "SELECT actor FROM audit WHERE object_type='job_handoffs'")['actor'], 'crew')
            self.assertEqual(rows(db, 'SELECT id FROM job_invoices'), [])
            self.assertEqual(rows(db, 'SELECT id FROM times'), [])

    def test_stale_save_does_not_overwrite_a_teammate_or_new_job_plan(self):
        payload = self.payload()
        self.assertEqual(self.crew.put('/api/jobs/J1/handoff', json=payload).status_code, 200)
        self.assertEqual(self.manager.put('/api/jobs/J1/handoff', json=payload).status_code, 409)
        payload = self.payload()
        with transaction(self.path) as db:
            db.execute("UPDATE jobs SET version=version+1,notes='Changed instructions' WHERE id='J1'")
        self.assertEqual(self.crew.put('/api/jobs/J1/handoff', json=payload).status_code, 409)
        self.assertTrue(self.crew.get('/api/jobs/J1/handoff').json()['checklist']['review_required'])
        self.assertEqual(self.crew.put('/api/jobs/J1/handoff', json=self.payload()).status_code, 200)
        self.assertFalse(self.crew.get('/api/jobs/J1/handoff').json()['checklist']['review_required'])

    def test_revoked_assignment_immediately_blocks_brief_and_checklist(self):
        payload = self.payload()
        with transaction(self.path) as db:
            db.execute("DELETE FROM assignments WHERE job_id='J1' AND resource_id='E1'")
        self.assertEqual(self.crew.get('/api/jobs/J1/handoff').status_code, 403)
        self.assertEqual(self.crew.put('/api/jobs/J1/handoff', json=payload).status_code, 403)

    def test_protocols_require_manager_review_and_preserve_version_actor(self):
        for role in ('crew', 'lead', 'sales', 'dispatch', 'accounting'):
            self.assertEqual(self.staff(role).put('/api/crew-protocols', json=self.guide()).status_code, 403)
        self.assertEqual(self.manager.put('/api/crew-protocols', json=self.guide(reviewed=False)).status_code, 422)
        response = self.manager.put('/api/crew-protocols', json=self.guide()); self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['updated_by_name'], 'Test Manager')
        self.assertTrue(self.crew.get('/api/crew-protocols').json()['approved'])
        self.assertEqual(self.owner.put('/api/crew-protocols', json=self.guide()).status_code, 409)
        self.assertFalse(self.crew.get('/api/crew-protocols').json()['can_edit'])

    def test_published_protocol_change_requires_checklist_review(self):
        self.crew.put('/api/jobs/J1/handoff', json=self.payload())
        old = self.payload()
        self.manager.put('/api/crew-protocols', json=self.guide())
        self.assertTrue(self.crew.get('/api/jobs/J1/handoff').json()['checklist']['review_required'])
        self.assertEqual(self.crew.put('/api/jobs/J1/handoff', json=old).status_code, 409)
        self.assertEqual(self.crew.put('/api/jobs/J1/handoff', json=self.payload()).status_code, 200)

    def test_invalid_or_cancelled_checklists_leave_history_intact(self):
        valid = self.payload()
        for change in ({'checks': {}}, {'checks': {**valid['checks'], 'plan_reviewed': 'yes'}}, {'version': True}, {'notes': 'x' * 4001}, {'hidden': 1}):
            response = self.crew.put('/api/jobs/J1/handoff', json={**valid, **change})
            self.assertIn(response.status_code, (409, 422), (change, response.text))
        with transaction(self.path) as db:
            db.execute("UPDATE jobs SET status='Cancelled' WHERE id='J1'")
        self.assertEqual(self.crew.put('/api/jobs/J1/handoff', json=self.payload()).status_code, 409)
        data = self.crew.get('/api/jobs/J1/handoff').json()
        self.assertFalse(data['can_update']); self.assertFalse(data['can_check_truck'])
        with closing(connect(self.path)) as db:
            self.assertEqual(rows(db, 'SELECT * FROM job_handoffs'), [])

    def test_assigned_truck_check_api_accepts_actual_crew_and_returns_in_brief(self):
        with transaction(self.path) as db:
            insert(db, 'fleet_profiles', dict(vehicle_id='V1', odometer_km=100, check_required=1, updated_at=now()))
        check = {'job_id': 'J1', 'odometer_km': 101, 'severity': 'None', 'notes': '',
            'checklist': {key: True for key in ('tires', 'lights', 'brakes', 'fluids', 'body', 'dollies_3', 'straps_2', 'blankets', 'paperwork', 'locked')}}
        response = self.crew.post('/api/fleet/V1/inspection', json=check)
        self.assertEqual(response.status_code, 200, response.text)
        truck = self.crew.get('/api/jobs/J1/handoff').json()['trucks'][0]
        self.assertEqual(truck['odometer_km'], 101); self.assertEqual(truck['latest_check']['severity'], 'None')
        self.assertEqual(self.crew.post('/api/fleet/V2/inspection', json=check).status_code, 403)


if __name__ == '__main__':
    unittest.main()
