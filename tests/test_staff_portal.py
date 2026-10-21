"""Employee identity, explicit coverage, approval races and document privacy."""
from datetime import datetime, timedelta, timezone
from contextlib import closing
import unittest
from app.store import transaction, connect, insert, one, rows, dumps, now
from app.main import sha
from app.platform_common import put_setting, business_day
from app.staff_portal import explicitly_available
import test_job_planning as fixture


class StaffPortalTests(unittest.TestCase):
    staff=fixture.JobPlanningTests.staff

    def setUp(self):
        fixture.JobPlanningTests.setUp(self)
        self.start='2099-01-02T14:00:00+00:00'  # Friday 09:00 Montreal
        self.end='2099-01-02T19:00:00+00:00'
        with transaction(self.path) as db:
            db.execute("UPDATE jobs SET start_at=?,end_at=?,status='Confirmed' WHERE id='J1'",(self.start,self.end))
            insert(db,'assignments',dict(job_id='J1',resource_type='employee',resource_id='E1'))
            for eid,name in [('E3','Friday replacement'),('E4','Sunday replacement'),('E5','No availability')]:
                if eid=='E3':db.execute("UPDATE employees SET active=1,driver_verified=1 WHERE id='E3'")
                else:insert(db,'employees',dict(id=eid,name=name,role='Mover',active=1,driver_verified=1,hourly_cents=2500))
                user='worker'+eid
                insert(db,'users',dict(id=user,email=user+'@example.invalid',name=name,role='crew',employee_id=eid,password_hash='unused',active=1,created_at=now()))
                insert(db,'sessions',dict(token_hash=sha(user+'-token'),user_id=user,csrf='test-csrf',expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()))
            insert(db,'users',dict(id='manager',email='manager@example.invalid',name='Manager',role='manager',password_hash='unused',active=1,created_at=now()))
            insert(db,'sessions',dict(token_hash=sha('manager-token'),user_id='manager',csrf='test-csrf',expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()))
        self.crew=self.staff('crew');self.manager=self.staff('manager');self.target=self.staff('workerE3')
        self.save(self.target,[{'day':i,'start':'08:00','end':'18:00'} for i in range(6)])
        self.save(self.staff('workerE4'),[{'day':6,'start':'00:00','end':'24:00'}])

    def save(self,client,weekly,exceptions=[]):
        version=client.get('/api/my-portal').json()['availability']['version']
        r=client.put('/api/my-portal/availability',json={'weekly':weekly,'exceptions':exceptions,'version':version})
        self.assertEqual(r.status_code,200,r.text)

    def candidates(self):
        r=self.crew.get('/api/my-portal/jobs/J1/replacements');self.assertEqual(r.status_code,200,r.text)
        return [e['id'] for e in r.json()['employees']]

    def request(self):
        r=self.crew.post('/api/my-portal/jobs/J1/give-away',json={'version':1,'employee_ids':['E3'],'message':'Please cover my shift.'})
        self.assertEqual(r.status_code,200,r.text);return r.json()['id']

    def assigned(self):
        with closing(connect(self.path)) as db:return [r['resource_id'] for r in rows(db,"SELECT resource_id FROM assignments WHERE job_id='J1' AND resource_type='employee'")]

    def test_friday_vs_sunday_and_candidates_not_in_home_payload(self):
        self.assertEqual(self.candidates(),['E3'])
        self.assertNotIn('Friday replacement',self.crew.get('/api/my-portal').text)
        with transaction(self.path) as db:db.execute("UPDATE jobs SET start_at='2099-01-04T14:00:00+00:00',end_at='2099-01-04T19:00:00+00:00' WHERE id='J1'")
        self.assertEqual(self.candidates(),['E4'])

    def test_full_duration_and_exception_override(self):
        self.save(self.target,[{'day':4,'start':'09:00','end':'13:59'}])
        self.assertEqual(self.candidates(),[])
        self.save(self.target,[{'day':4,'start':'08:00','end':'18:00'}],[{'date':'2099-01-02','windows':[]}])
        self.assertEqual(self.candidates(),[])
        self.save(self.target,[],[{'date':'2099-01-02','windows':[{'start':'09:00','end':'14:00'}]}])
        self.assertEqual(self.candidates(),['E3'])

    def test_overnight_requires_both_days_and_gap_is_not_covered(self):
        self.save(self.target,[{'day':4,'start':'20:00','end':'24:00'},{'day':5,'start':'00:00','end':'02:00'}])
        with closing(connect(self.path)) as db:
            self.assertTrue(explicitly_available(db,'E3','2099-01-03T02:00:00+00:00','2099-01-03T06:00:00+00:00'))
            self.assertFalse(explicitly_available(db,'E3','2099-01-03T02:00:00+00:00','2099-01-03T08:00:00+00:00'))

    def test_conflicts_leave_and_qualifications_are_excluded(self):
        with transaction(self.path) as db:db.execute("UPDATE employees SET driver_verified=0 WHERE id='E3'")
        self.assertEqual(self.candidates(),[])
        with transaction(self.path) as db:
            db.execute("UPDATE employees SET driver_verified=1 WHERE id='E3'")
            insert(db,'time_off_requests',dict(id='OFF',employee_id='E3',start_date='2099-01-02',end_date='2099-01-02',status='Approved',requested_by='workerE3',requested_at=now()))
        self.assertEqual(self.candidates(),[])
        with transaction(self.path) as db:
            db.execute("UPDATE time_off_requests SET status='Cancelled'")
            insert(db,'jobs',dict(id='J2',lead_id='L1',title='Clash',phase='Moving',start_at='2099-01-02T10:00:00-05:00',end_at='2099-01-02T12:00:00-05:00',created_by='owner',created_at=now()))
            insert(db,'assignments',dict(job_id='J2',resource_type='employee',resource_id='E3'))
        self.assertEqual(self.candidates(),[])

    def test_acceptance_does_not_reassign_then_approval_is_atomic_and_single_use(self):
        rid=self.request();self.assertEqual(self.assigned(),['E1'])
        self.assertEqual(self.target.post('/api/my-portal/requests/'+rid+'/accept').status_code,200)
        self.assertEqual(self.assigned(),['E1'])
        self.assertEqual(self.target.post('/api/my-portal/requests/'+rid+'/accept').status_code,409)
        self.assertEqual(self.crew.post('/api/my-portal/requests/'+rid+'/review',json={'decision':'Approved'}).status_code,403)
        r=self.manager.post('/api/my-portal/requests/'+rid+'/review',json={'decision':'Approved'})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.assigned(),['E3'])
        self.assertEqual(self.manager.post('/api/my-portal/requests/'+rid+'/review',json={'decision':'Approved'}).status_code,409)
        self.assertEqual(self.crew.get('/api/my-portal').json()['jobs'],[])
        self.assertEqual(self.target.get('/api/my-portal').json()['jobs'][0]['id'],'J1')

    def test_approval_rechecks_availability_and_job_version(self):
        rid=self.request();self.target.post('/api/my-portal/requests/'+rid+'/accept')
        self.save(self.target,[])
        self.assertEqual(self.manager.post('/api/my-portal/requests/'+rid+'/review',json={'decision':'Approved'}).status_code,409)
        self.assertEqual(self.assigned(),['E1'])
        self.save(self.target,[{'day':4,'start':'08:00','end':'18:00'}])
        with transaction(self.path) as db:db.execute("UPDATE jobs SET version=version+1 WHERE id='J1'")
        self.assertEqual(self.manager.post('/api/my-portal/requests/'+rid+'/review',json={'decision':'Approved'}).status_code,409)
        self.assertEqual(self.assigned(),['E1'])

    def test_no_self_approval_duplicate_requests_or_uninvited_acceptance(self):
        rid=self.request()
        self.assertTrue(self.manager.get('/api/my-portal').json()['requests'][0]['can_review'])
        self.assertEqual(self.crew.post('/api/my-portal/jobs/J1/give-away',json={'version':1,'employee_ids':['E3']}).status_code,409)
        self.assertEqual(self.staff('workerE4').post('/api/my-portal/requests/'+rid+'/accept').status_code,403)
        self.target.post('/api/my-portal/requests/'+rid+'/accept')
        with transaction(self.path) as db:db.execute("UPDATE users SET employee_id='E1' WHERE id='manager'")
        self.assertEqual(self.manager.post('/api/my-portal/requests/'+rid+'/review',json={'decision':'Approved'}).status_code,403)
        self.assertEqual(self.crew.post('/api/my-portal/requests/'+rid+'/cancel').status_code,200)
        self.assertEqual(self.manager.post('/api/my-portal/requests/'+rid+'/review',json={'decision':'Approved'}).status_code,409)

    def test_lead_qualification_and_new_conflict_are_rechecked_at_approval(self):
        with transaction(self.path) as db:
            insert(db,'employee_directory_profiles',dict(employee_id='E1',team_lead=1))
        self.assertEqual(self.candidates(),[])
        with transaction(self.path) as db:
            insert(db,'employee_directory_profiles',dict(employee_id='E3',team_lead=1))
        rid=self.request();self.target.post('/api/my-portal/requests/'+rid+'/accept')
        with transaction(self.path) as db:
            insert(db,'jobs',dict(id='NEW',lead_id='L1',title='New conflict',phase='Moving',start_at=self.start,end_at=self.end,created_by='owner',created_at=now()))
            insert(db,'assignments',dict(job_id='NEW',resource_type='employee',resource_id='E3'))
        self.assertEqual(self.manager.post('/api/my-portal/requests/'+rid+'/review',json={'decision':'Approved'}).status_code,409)
        self.assertEqual(self.assigned(),['E1'])

    def test_time_off_cannot_overlap_an_overnight_assignment(self):
        with transaction(self.path) as db:
            db.execute("UPDATE jobs SET start_at='2099-01-03T04:00:00+00:00',end_at='2099-01-03T07:00:00+00:00' WHERE id='J1'")
        r=self.crew.post('/api/my-portal/time-off',json={'start_date':'2099-01-03','end_date':'2099-01-03'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.manager.post('/api/time-off/'+r.json()['id']+'/review',json={'decision':'Approved'}).status_code,409)
        self.assertEqual(self.crew.get('/api/my-portal').json()['time_off'][0]['status'],'Pending')

    def test_private_hours_current_cycle_no_foreign_records_no_write_endpoint(self):
        today=business_day()
        with transaction(self.path) as db:
            put_setting(db,'payroll_policy',{'period_days':14,'period_anchor':today,'approved':True})
            db.execute("UPDATE jobs SET start_at=?,end_at=?,status='Completed' WHERE id='J1'",(today+'T14:00:00+00:00',today+'T19:00:00+00:00'))
            insert(db,'times',dict(id='T1',job_id='J1',employee_id='E1',service_minutes=180,transport_minutes=30,status='Approved',submitted_by='crew',created_at=now()))
            insert(db,'times',dict(id='T2',job_id='J1',employee_id='E3',service_minutes=600,transport_minutes=0,status='Submitted',submitted_by='workerE3',created_at=now()))
        r=self.crew.get('/api/my-portal?employee_id=E3').json()
        self.assertEqual(r['total_minutes'],210);self.assertEqual(r['approved_minutes'],210)
        self.assertNotIn('hourly_cents',str(r));self.assertEqual(len(r['hours']),1)
        self.assertEqual(self.crew.patch('/api/my-portal/hours',json={'total_minutes':999}).status_code,404)
        self.assertEqual(self.crew.post('/api/ux/jobs/J1/crew-hours',json={'employee_ids':['E1'],'team_hours':5}).status_code,409)

    def test_contact_edit_cannot_change_role_pay_or_signin_email(self):
        self.assertEqual(self.crew.patch('/api/my-portal/profile',json={'phone':'555','email':'new@example.invalid','role':'owner'}).status_code,422)
        self.assertEqual(self.crew.patch('/api/my-portal/profile',json={'phone':'555','email':'new@example.invalid'}).status_code,200)
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db,"SELECT email FROM users WHERE id='crew'")['email'],'crew@example.invalid')
            self.assertEqual(one(db,"SELECT phone FROM employee_directory_profiles WHERE employee_id='E1'")['phone'],'555')

    def test_owner_only_publication_recipient_private_download_and_acknowledgement(self):
        payload={'title':'Loading protocol','body':'Read this procedure.','recipients':['crew'],'requires_ack':True}
        self.assertEqual(self.manager.post('/api/my-portal/documents',json=payload).status_code,403)
        r=self.owner.post('/api/my-portal/documents',json=payload);self.assertEqual(r.status_code,200,r.text);did=r.json()['id']
        self.assertGreater(self.crew.get('/api/my-portal/notifications').json()['unread'],0)
        for suffix in ('','/download'):
            self.assertEqual(self.target.get('/api/my-portal/documents/'+did+suffix).status_code,404)
        self.assertEqual(self.crew.post('/api/my-portal/documents/'+did+'/acknowledge').status_code,409)
        self.assertEqual(self.crew.get('/api/my-portal/documents/'+did).json()['body'],payload['body'])
        download=self.crew.get('/api/my-portal/documents/'+did+'/download');self.assertIn('attachment',download.headers['content-disposition']);self.assertIn(payload['body'],download.text)
        self.assertEqual(self.crew.post('/api/my-portal/documents/'+did+'/acknowledge').status_code,200)
        self.assertTrue(self.owner.get('/api/my-portal/documents/'+did).json()['recipients'][0]['ack_at'])
        self.assertEqual(self.target.get('/api/my-portal').json()['documents'],[])

    def test_upload_all_staff_and_unsafe_file_rejection(self):
        values={'title':'Staff information','recipients':'"all"','requires_ack':'true'}
        r=self.owner.post('/api/my-portal/documents/upload',data=values,files={'file':('guide.txt',b'Hello team','text/plain')})
        self.assertEqual(r.status_code,200,r.text)
        did=r.json()['id'];self.assertEqual(self.target.get('/api/my-portal/documents/'+did+'/download').content,b'Hello team')
        for name,body in [('evil.html',b'<script>x</script>'),('fake.pdf',b'not a pdf')]:
            self.assertEqual(self.owner.post('/api/my-portal/documents/upload',data=values,files={'file':(name,body)}).status_code,422)

    def test_manager_message_private_and_response_notifies_employee(self):
        r=self.crew.post('/api/my-portal/contact-manager',json={'subject':'My hours','body':'Please check Friday.'});self.assertEqual(r.status_code,200)
        rid=r.json()['id'];self.assertEqual(self.target.get('/api/my-portal').json()['help_requests'],[])
        self.assertEqual(self.manager.post('/api/my-portal/messages/'+rid+'/reply',json={'reply':'I will review.'}).status_code,200)
        self.assertEqual(self.crew.get('/api/my-portal').json()['help_requests'][0]['reply'],'I will review.')

    def test_personal_leave_uses_identity_and_other_manager_review(self):
        r=self.crew.post('/api/my-portal/time-off',json={'employee_id':'E3','start_date':'2099-02-01','end_date':'2099-02-01'})
        self.assertEqual(r.status_code,200,r.text);rid=r.json()['id']
        with closing(connect(self.path)) as db:self.assertEqual(one(db,'SELECT employee_id FROM time_off_requests WHERE id=?',(rid,))['employee_id'],'E1')
        self.assertEqual(self.manager.post('/api/time-off/'+rid+'/review',json={'decision':'Approved'}).status_code,200)
        self.assertEqual(self.crew.get('/api/my-portal').json()['time_off'][0]['status'],'Approved')
