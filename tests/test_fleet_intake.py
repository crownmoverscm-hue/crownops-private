"""Availability, durable maintenance history and inquiry-to-draft workflows."""
from contextlib import closing
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch
import copy,json,unittest
from app.store import connect,transaction,insert,one,now,dumps
import test_job_planning as planning
import test_template_quotes as quoting
import test_move_photos as photos

class FleetAvailabilityTests(unittest.TestCase):
    setUp=planning.JobPlanningTests.setUp
    staff=planning.JobPlanningTests.staff
    plan=planning.JobPlanningTests.plan
    snapshot=planning.JobPlanningTests.snapshot

    def test_manager_can_activate_incomplete_profile_and_assign_it_immediately(self):
        with transaction(self.path) as db:
            db.execute("UPDATE vehicles SET active=0 WHERE id='V1'")
            insert(db,'fleet_reference',dict(vehicle_id='V1',review_required=1,updated_at=now()))
        r=self.staff('dispatch').patch('/api/company/fleet/V1/availability',json={'version':1,'active':True})
        self.assertEqual(r.status_code,200,r.text)
        r=self.staff('dispatch').put('/api/jobs/J1/plan',json=self.plan(auto_confirm=True))
        self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['confirmed'])
        fleet=self.owner.get('/api/company/fleet').json()['trucks'];v=next(v for v in fleet if v['id']=='V1')
        self.assertEqual(v['reference']['review_required'],1)
        self.assertEqual([j['id'] for j in v['assigned_services']],['J1'])
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM inspections').fetchone()[0],0)

    def test_inactive_keeps_assignments_and_marks_the_move_for_attention(self):
        self.assertEqual(self.owner.put('/api/jobs/J1/plan',json=self.plan(auto_confirm=True)).status_code,200)
        r=self.staff('dispatch').patch('/api/company/fleet/V1/availability',json={'version':1,'active':False})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['affected_jobs'],1)
        job,assigned,history=self.snapshot();self.assertEqual(job['plan_review_required'],1)
        self.assertIn('V1',[a['resource_id'] for a in assigned])
        readiness=self.owner.get('/api/jobs/J1/planning').json()['readiness']
        self.assertTrue(any('inactive' in x for x in readiness['missing']))
        self.assertNotEqual(self.owner.patch('/api/jobs/J1',json={'version':job['version'],'status':'In progress'}).status_code,200)
        self.assertEqual(self.owner.put('/api/jobs/J1/plan',json=self.plan(version=job['version'],auto_confirm=True)).status_code,422)
        with closing(connect(self.path)) as db:
            a=one(db,"SELECT * FROM audit WHERE action='Truck availability changed'")
            self.assertEqual(a['actor'],'dispatch')

    def test_only_owner_and_manager_can_toggle_and_stale_or_invalid_requests_do_not_write(self):
        for user in ('sales','accounting','crew','lead'):
            self.assertEqual(self.staff(user).patch('/api/company/fleet/V1/availability',json={'version':1,'active':False}).status_code,403)
        self.assertEqual(self.owner.patch('/api/company/fleet/V1/availability',json={'version':0,'active':False}).status_code,409)
        self.assertEqual(self.owner.patch('/api/company/fleet/V1/availability',json={'version':1,'active':'false'}).status_code,422)
        with closing(connect(self.path)) as db:self.assertEqual(one(db,"SELECT active FROM vehicles WHERE id='V1'")['active'],1)

    def test_missing_metadata_is_optional_but_recorded_defects_and_expired_inspection_still_block(self):
        with transaction(self.path) as db:
            insert(db,'fleet_profiles',dict(vehicle_id='V1',inspection_due='2000-01-01',updated_at=now()))
        self.assertEqual(self.owner.patch('/api/company/fleet/V1/availability',json={'version':1,'active':True}).status_code,409)
        with transaction(self.path) as db:
            db.execute("UPDATE fleet_profiles SET inspection_due='' WHERE vehicle_id='V1'")
            insert(db,'inspections',dict(id='DEFECT',vehicle_id='V1',checked_by='owner',checked_at=now(),odometer_km=0,checklist_json='{}',severity='Major',notes='Recorded defect'))
        self.assertEqual(self.owner.patch('/api/company/fleet/V1/availability',json={'version':1,'active':True}).status_code,409)
        self.assertEqual(self.owner.patch('/api/company/fleet/V1/availability',json={'version':1,'active':False}).status_code,200)

    def test_manual_service_log_records_manager_and_is_idempotent_without_changing_odometer_or_activation(self):
        day=datetime.now(ZoneInfo('America/Toronto')).date().isoformat()
        d={'version':1,'request_id':'service-test-123','service_date':day,'odometer_km':'1234','work':'Oil and filter — Test Garage','notes':'Invoice TEST-001'}
        client=self.staff('dispatch');r=client.post('/api/company/fleet/V1/service-history',json=d)
        self.assertEqual(r.status_code,200,r.text);saved=r.json()['id']
        self.assertEqual(client.post('/api/company/fleet/V1/service-history',json=d).json()['id'],saved)
        changed={**d,'work':'Different work'}
        self.assertEqual(client.post('/api/company/fleet/V1/service-history',json=changed).status_code,409)
        fleet=client.get('/api/company/fleet').json()['trucks'];v=next(v for v in fleet if v['id']=='V1')
        self.assertEqual(len(v['history']),1);self.assertEqual(v['history'][0]['recorded_by_name'],'Dispatch')
        self.assertEqual(v['active'],1);self.assertEqual(v['version'],2)
        with closing(connect(self.path)) as db:self.assertEqual(db.execute("SELECT count(*) FROM inspections").fetchone()[0],0)

    def test_invalid_service_entries_do_not_create_history(self):
        day=datetime.now(ZoneInfo('America/Toronto')).date()
        good={'version':1,'request_id':'service-test-123','service_date':day.isoformat(),'work':'Oil change'}
        for change in ({'work':''},{'odometer_km':-1},{'service_date':(day+timedelta(days=1)).isoformat()},{'request_id':''}):
            self.assertEqual(self.owner.post('/api/company/fleet/V1/service-history',json={**good,**change}).status_code,422)
        for user in ('sales','accounting','crew'):
            self.assertEqual(self.staff(user).post('/api/company/fleet/V1/service-history',json=good).status_code,403)
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM fleet_service_history').fetchone()[0],0)

class InquiryDraftTests(unittest.TestCase):
    setUp=quoting.TemplateQuoteTests.setUp
    count=quoting.TemplateQuoteTests.count

    def inquiry(self):
        return {'client_name':'TEST Call Intake','email':'call@example.invalid','phone':'5145550100','lead_type':'Moving','move_date':'2026-11-15',
            'pickup':'100 Test Street','dropoff':'200 Test Street','home_size':'3 bedroom home','boxes':'50-60',
            'pickup_access':'1 flight of stairs','dropoff_access':'Elevator','pickup_walk':'30-50 ft','dropoff_walk':'0-10 ft',
            'inventory':'Beds, sofa and boxes','photo_link':'https://drive.google.com/drive/folders/TEST',
            'intake':{'local_area':'Yes','service_details':'Locker included','heavy_items':['Large treadmill'],'additional_services':['Packing'],
                'start_window':'morning','between_stops_minutes':'20','pickup_base_minutes':'15','return_base_minutes':'25'}}

    def test_call_details_and_photo_link_flow_into_same_inquiry_and_draft_snapshot(self):
        d=self.inquiry();r=self.client.post('/api/leads',json=d);self.assertEqual(r.status_code,200,r.text);lead=r.json()
        setup=self.client.get('/api/crm/quote-setup?lead_id='+lead['id']);self.assertEqual(setup.status_code,200,setup.text)
        saved=setup.json()['lead'];self.assertEqual(saved['photo_link'],d['photo_link'])
        self.assertEqual(json.loads(saved['intake_json'])['heavy_items'],['Large treadmill'])
        q=copy.deepcopy(self.data);q.pop('new_client');q.pop('service_details');q.update(lead_id=lead['id'],lead_version=lead.get('version',1))
        r=self.client.post('/api/crm/quotes',json=q);self.assertEqual(r.status_code,200,r.text)
        snap=r.json()['lead_snapshot'];self.assertEqual(snap['photo_link'],d['photo_link']);self.assertEqual(snap['boxes'],'50-60')
        self.assertEqual(json.loads(snap['intake_json'])['between_stops_minutes'],'20')
        self.assertEqual(self.count('leads'),1);self.assertEqual(self.count('jobs'),0);self.assertEqual(self.count('outbound_messages'),0)

    def test_partial_inquiry_can_save_without_photos_travel_or_all_estimate_fields(self):
        r=self.client.post('/api/leads',json={'client_name':'TEST Partial call','intake':{}})
        self.assertEqual(r.status_code,200,r.text)
        lid=r.json()['id'];detail=self.client.get('/api/leads/'+lid).json()
        self.assertEqual(detail['photo_link'],'');self.assertEqual(json.loads(detail['intake_json'])['between_stops_minutes'],'')

    def test_invalid_photo_links_and_structured_answers_roll_back_the_inquiry(self):
        for change in ({'photo_link':'javascript:alert(1)'},{'photo_link':'http://example.invalid/photos'},
                       {'intake':{'local_area':True}},{'intake':{'between_stops_minutes':'NaN'}},{'intake':{'heavy_items':'Piano'}},{'intake':{'unknown':'value'}}):
            r=self.client.post('/api/leads',json={**self.inquiry(),**change});self.assertEqual(r.status_code,422,r.text)
        self.assertEqual(self.count('leads'),0)

    def test_edit_preserves_photos_when_omitted_and_rejects_stale_intake(self):
        lead=self.client.post('/api/leads',json=self.inquiry()).json();lid=lead['id']
        r=self.client.patch('/api/leads/'+lid,json={'version':1,'intake':{'local_area':'No'}});self.assertEqual(r.status_code,200,r.text)
        r=self.client.patch('/api/leads/'+lid,json={'version':1,'intake':{'local_area':'Yes'}});self.assertEqual(r.status_code,409,r.text)
        saved=self.client.get('/api/leads/'+lid).json();self.assertEqual(saved['photo_link'],self.inquiry()['photo_link'])
        self.assertEqual(json.loads(saved['intake_json'])['local_area'],'No')

class InquiryPhotoTests(unittest.TestCase):
    setUp=photos.MovePhotoTests.setUp
    staff=photos.MovePhotoTests.staff
    image=photos.MovePhotoTests.image

    def test_office_can_upload_to_new_inquiry_before_any_job_is_scheduled(self):
        lead=self.owner.post('/api/leads',json={'client_name':'TEST Photo inquiry','lead_type':'Moving'}).json()
        r=self.owner.post('/api/leads/'+lead['id']+'/uploads',data={'category':'Move photos'},files={'file':('room.png',self.image(),'image/png')})
        self.assertEqual(r.status_code,200,r.text);aid=r.json()['id']
        self.assertEqual(self.owner.get('/api/uploads/'+aid).status_code,200)
        self.assertEqual(self.crew.get('/api/uploads/'+aid).status_code,403)

    def test_folder_link_follows_assigned_crew_access(self):
        link='https://drive.google.com/drive/folders/TEST'
        with transaction(self.path) as db:db.execute("UPDATE leads SET photo_link=? WHERE id='L1'",(link,))
        for client,allowed in ((self.crew,True),(self.other,False)):
            data=client.get('/api/jobs').json();j=next(j for j in data if j['id']=='J1')
            self.assertEqual(j['client'].get('photo_link'),link if allowed else None)
            data=client.get('/api/ux/final/job/J1').json()
            self.assertEqual(data['client'].get('photo_link'),link if allowed else None)
