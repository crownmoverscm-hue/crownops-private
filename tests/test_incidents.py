"""Incident submissions, scoped access, immutable history and manager sign-off."""
from contextlib import closing
import unittest
from app.main import create_app
from app.store import connect,transaction,insert,one,rows,now
import test_job_planning as fixture

class IncidentTests(unittest.TestCase):
    setUp=fixture.JobPlanningTests.setUp
    staff=fixture.JobPlanningTests.staff

    def report(self,client=None,**changes):
        data={'request_key':'incident-test-key-0001','category':'Property damage','title':'Test incident','detail':'Description of what happened','location':'Test location'}
        data.update(changes);return (client or self.owner).post('/api/incidents',json=data)

    def test_every_employee_can_submit_all_categories_without_client_or_truck(self):
        crew=self.staff('crew')
        for index,kind in enumerate(('Client move','Property damage','Company truck','Other vehicle','Other incident')):
            r=self.report(crew,category=kind,request_key='incident-category-'+str(index),status='Completed',reported_by='owner',submitted_at='2000-01-01')
            self.assertEqual(r.status_code,200,r.text)
            d=crew.get('/api/incidents/'+r.json()['id']).json()['report'];self.assertEqual(d['reported_by'],'crew');self.assertEqual(d['status'],'Submitted');self.assertNotEqual(d['submitted_at'],'2000-01-01')
        self.assertEqual(crew.get('/api/incidents').json()['total'],5)

    def test_duplicate_submission_does_not_create_another_incident(self):
        first=self.report();second=self.report();self.assertEqual(first.status_code,200,first.text);self.assertEqual(second.json()['id'],first.json()['id']);self.assertTrue(second.json()['duplicate'])
        self.assertEqual(self.report(title='Changed payload').status_code,409)
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM incident_events').fetchone()[0],1)

    def test_manager_verification_and_completion_record_authenticated_accounts(self):
        iid=self.report().json()['id']
        for user in ('crew','sales','accounting','lead'):
            r=self.staff(user).post('/api/incidents/'+iid+'/review',json={'version':1,'status':'Completed'});self.assertEqual(r.status_code,403,r.text)
        r=self.owner.post('/api/incidents/'+iid+'/review',json={'version':1,'status':'Verified','reviewed_by':'forged'});self.assertEqual(r.status_code,200,r.text)
        d=self.owner.get('/api/incidents/'+iid).json();self.assertEqual(d['report']['verified_by'],'owner');self.assertEqual(d['report']['verifier_name'],'Owner')
        r=self.staff('dispatch').post('/api/incidents/'+iid+'/review',json={'version':2,'status':'Completed','completed_by':'forged'});self.assertEqual(r.status_code,200,r.text)
        d=self.owner.get('/api/incidents/'+iid).json();self.assertEqual(d['report']['completed_by'],'dispatch');self.assertEqual(d['report']['completer_name'],'Dispatch');self.assertEqual(d['report']['verified_by'],'owner')
        self.assertEqual([e['actor'] for e in d['history']],['owner','owner','dispatch'])
        self.assertEqual(self.owner.post('/api/incidents/'+iid+'/review',json={'version':2,'status':'In review'}).status_code,409)
        r=self.owner.post('/api/incidents/'+iid+'/review',json={'version':3,'status':'In review','note':'New information'});self.assertEqual(r.status_code,200,r.text)
        d=self.owner.get('/api/incidents/'+iid).json();self.assertIsNone(d['report']['completed_by']);self.assertEqual(d['report']['detail'],'Description of what happened');self.assertEqual(len(d['history']),4)

    def test_crew_reads_only_own_reports_and_cannot_link_an_unassigned_move(self):
        crew=self.staff('crew');owner_id=self.report().json()['id']
        self.assertEqual(crew.get('/api/incidents/'+owner_id).status_code,403)
        self.assertEqual(crew.get('/api/incidents').json()['total'],0)
        self.assertEqual(self.report(crew,job_id='J1').status_code,403)
        with transaction(self.path) as db:insert(db,'assignments',dict(job_id='J1',resource_type='employee',resource_id='E1'))
        r=self.report(crew,job_id='J1',vehicle_id='V1');self.assertEqual(r.status_code,200,r.text);iid=r.json()['id']
        self.assertEqual(crew.get('/api/incidents/options').json()['jobs'][0]['id'],'J1')
        with transaction(self.path) as db:db.execute("DELETE FROM assignments WHERE job_id='J1'")
        self.assertEqual(crew.get('/api/incidents/'+iid).status_code,200)
        self.assertEqual(crew.get('/api/incidents/options').json()['jobs'],[])
        self.assertEqual(self.staff('dispatch').get('/api/incidents').json()['total'],2)

    def test_list_is_ordered_by_submission_date_and_can_filter_status(self):
        first=self.report().json()['id'];second=self.report(request_key='incident-test-key-0002',category='Company truck').json()['id']
        with transaction(self.path) as db:
            db.execute('UPDATE incident_reports SET submitted_at=? WHERE id=?',('2026-10-14T10:00:00+00:00',first))
            db.execute('UPDATE incident_reports SET submitted_at=? WHERE id=?',('2026-10-15T10:00:00+00:00',second))
        self.owner.post('/api/incidents/'+first+'/review',json={'version':1,'status':'Completed'})
        data=self.owner.get('/api/incidents').json();self.assertEqual([r['id'] for r in data['items']],[second,first])
        self.assertEqual(self.owner.get('/api/incidents?status=Completed').json()['total'],1)
        self.assertEqual(self.owner.get('/api/incidents?category=Company%20truck').json()['items'][0]['id'],second)
        self.assertEqual(self.owner.get('/api/incidents?limit=1&offset=1').json()['items'][0]['id'],first)

    def test_historical_cases_preserve_original_details_without_fabricated_reviewer(self):
        with transaction(self.path) as db:insert(db,'cases',dict(id='OLD',lead_id='L1',title='Earlier issue',type='Incident',detail='Original details',status='Resolved',created_by='owner',created_at='2026-09-01T10:00:00+00:00',updated_at='2026-09-02T10:00:00+00:00'))
        create_app(self.path);create_app(self.path)
        d=self.owner.get('/api/incidents/INC-LEGACY-OLD').json();self.assertEqual(d['report']['detail'],'Original details');self.assertEqual(d['report']['status'],'Completed');self.assertIsNone(d['report']['completed_by']);self.assertEqual(len(d['history']),1)
        self.assertEqual(d['report']['client_name'],'Test move')

    def test_bad_inputs_and_unauthenticated_requests_do_not_create_records(self):
        for changes in [{'category':[]},{'vehicle_id':{}},{'job_id':'UNKNOWN'},{'detail':''},{'request_key':'bad'}]:
            r=self.report(**changes);self.assertEqual(r.status_code,422,r.text)
        from fastapi.testclient import TestClient
        self.assertEqual(TestClient(self.app).get('/api/incidents').status_code,401)
        self.assertEqual(self.owner.get('/api/incidents').json()['total'],0)
