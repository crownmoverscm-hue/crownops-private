"""Routine planning is direct, audited and distinct from ordinary Crew access."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import json
import os
import tempfile
import unittest
from fastapi.testclient import TestClient
from app.main import create_app, sha
from app.store import connect, transaction, insert, one, rows, now, dumps


class JobPlanningTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.path=Path(folder.name)/'crownops.sqlite3'
        env=patch.dict(os.environ,{'CROWNOPS_ENV':'test','CROWNOPS_ALLOW_EXTERNAL':'0','CROWNOPS_RUN_WORKER':'0',
            'CROWNOPS_UPLOADS_ENABLED':'0','RAILWAY_PROJECT_ID':'','RAILWAY_PUBLIC_DOMAIN':'','RAILWAY_VOLUME_MOUNT_PATH':''})
        env.start();self.addCleanup(env.stop);self.app=create_app(self.path)
        expires=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
        with transaction(self.path) as db:
            for eid,name,active,driver in [('E1','Driver',1,1),('E2','Mover',1,0),('E3','Imported worker',0,0)]:
                insert(db,'employees',dict(id=eid,name=name,role='Mover',active=active,driver_verified=driver,hourly_cents=2500))
            for user,role,eid in [('owner','owner',None),('sales','sales',None),('dispatch','dispatch',None),('accounting','accounting',None),('crew','crew','E1'),('lead','crew','E2')]:
                insert(db,'users',dict(id=user,email=user+'@example.invalid',name=user.title(),role=role,employee_id=eid,password_hash='unused',active=1,created_at=now()))
                insert(db,'sessions',dict(token_hash=sha(user+'-token'),user_id=user,csrf='test-csrf',expires_at=expires))
            insert(db,'employee_directory_profiles',dict(employee_id='E2',team_lead=1,home_address='PRIVATE ADDRESS',emergency_phone='PRIVATE PHONE'))
            insert(db,'vehicles',dict(id='V1',name='Available truck',active=1))
            insert(db,'vehicles',dict(id='V2',name='Inactive truck',active=0))
            insert(db,'leads',dict(id='L1',client_name='Test move',phone='5145550100',pickup='Test A',dropoff='Test B',created_at=now(),updated_at=now()))
            insert(db,'pricing_versions',dict(id='PRICE',label='Test',rates_json='{}',settings_json='{}',created_at=now()))
            insert(db,'quotes',dict(id='Q1',lead_id='L1',revision=1,status='Accepted',inputs_json=dumps({'deposit_required':300,'service_type':'Local'}),
                result_json=dumps({'movers':2,'trucks':1,'service_high':3,'transport_hours':1,'service_type':'Local'}),pricing_id='PRICE',lead_snapshot_json='{}',lead_version=1,created_by='owner',created_at=now()))
            insert(db,'jobs',dict(id='J1',lead_id='L1',quote_id='Q1',title='Test move',phase='Moving',start_at='2026-11-15T14:00:00+00:00',end_at='2026-11-15T19:00:00+00:00',created_by='owner',created_at=now()))
            insert(db,'payments',dict(id='P1',lead_id='L1',amount_cents=30000,method='Interac e-Transfer',purpose='Deposit',reference='TEST',verified=1,recorded_by='owner',created_at=now()))
        self.owner=self.staff('owner')

    def staff(self,user):
        c=TestClient(self.app);self.addCleanup(c.close)
        c.cookies.set('crownops_session',user+'-token');c.headers['x-csrf-token']='test-csrf';return c

    def plan(self,**extra):
        data={'version':1,'title':'Changed move','start_at':'2026-11-15T14:00:00+00:00','end_at':'2026-11-15T19:00:00+00:00',
            'quote_id':'Q1','crew_ids':['E1','E2'],'truck_ids':['V1'],'stops':['Test A','Test B'],'notes':'Instructions'}
        data.update(extra);return data

    def snapshot(self):
        with closing(connect(self.path)) as db:
            return one(db,"SELECT * FROM jobs WHERE id='J1'"),rows(db,"SELECT * FROM assignments WHERE job_id='J1'"),rows(db,"SELECT * FROM audit WHERE object_type='jobs' AND object_id='J1'")

    def test_office_and_designated_team_lead_save_without_reason_or_acknowledgement(self):
        for version,user in enumerate(('owner','sales','dispatch','accounting','lead'),1):
            with self.subTest(user=user):
                c=self.staff(user);r=c.put('/api/jobs/J1/plan',json=self.plan(version=version,title='Changed by '+user))
                self.assertEqual(r.status_code,200,r.text)
                self.assertEqual(r.json()['status'],'Tentative')
                job,assigned,history=self.snapshot();self.assertEqual(job['version'],version+1);self.assertEqual(len(assigned),3)
                self.assertEqual(history[-1]['actor'],user)
                details=json.loads(history[-1]['details_json']);self.assertEqual(details['before']['title'],'Test move' if version==1 else 'Changed by '+('owner','sales','dispatch','accounting')[version-2])
                self.assertEqual(details['after']['title'],'Changed by '+user);self.assertEqual(details['note'],'');self.assertIn('title',details['changed_fields'])
        history=self.owner.get('/api/jobs/J1/planning').json()['history'];self.assertTrue(all(x['actor_name'] for x in history))

    def test_ordinary_crew_cannot_plan_even_if_assigned_or_capability_is_forged(self):
        with transaction(self.path) as db:insert(db,'assignments',dict(job_id='J1',resource_type='employee',resource_id='E1'))
        c=self.staff('crew');self.assertFalse(c.get('/api/me').json()['user']['can_plan_jobs'])
        self.assertEqual(c.get('/api/jobs/J1/planning').status_code,403)
        self.assertEqual(c.put('/api/jobs/J1/plan',json=self.plan(can_plan_jobs=True)).status_code,403)
        self.assertEqual(c.post('/api/jobs/J1/confirm',json={'version':1}).status_code,403)
        self.assertEqual(c.patch('/api/ux/employee-directory/E1',json={'team_lead':True}).status_code,403)
        self.assertEqual(self.snapshot()[0]['version'],1)

    def test_team_lead_authority_is_revoked_on_next_request(self):
        c=self.staff('lead');self.assertTrue(c.get('/api/me').json()['user']['can_plan_jobs'])
        self.assertEqual(c.get('/api/jobs/J1/planning').status_code,200)
        with transaction(self.path) as db:db.execute("UPDATE employee_directory_profiles SET team_lead=0 WHERE employee_id='E2'")
        self.assertFalse(c.get('/api/me').json()['user']['can_plan_jobs'])
        self.assertEqual(c.put('/api/jobs/J1/plan',json=self.plan()).status_code,403)
        with transaction(self.path) as db:
            db.execute("UPDATE employee_directory_profiles SET team_lead=1 WHERE employee_id='E2'");db.execute("UPDATE employees SET active=0 WHERE id='E2'")
        self.assertEqual(c.get('/api/jobs/J1/planning').status_code,403)

    def test_save_partial_plan_retains_warnings_and_confirm_is_atomic(self):
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(crew_ids=[],truck_ids=[]));self.assertEqual(r.status_code,200,r.text)
        planning=self.owner.get('/api/jobs/J1/planning').json();self.assertFalse(planning['readiness']['ready'])
        self.assertIn('Assign 2 crew members',planning['readiness']['missing'])
        before=self.snapshot();r=self.owner.put('/api/jobs/J1/plan',json=self.plan(version=2,confirm=True,crew_ids=['E1']))
        self.assertEqual(r.status_code,422,r.text);self.assertEqual(before,self.snapshot())
        r=self.staff('sales').put('/api/jobs/J1/plan',json=self.plan(version=2,confirm=True));self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['status'],'Confirmed');self.assertEqual(self.snapshot()[0]['capacity_reviewed'],1)
        self.assertTrue(self.owner.get('/api/jobs/J1/planning').json()['readiness']['ready'])

    def test_confirmation_retains_deposit_quote_driver_and_duration_checks(self):
        cases=[('deposit',"UPDATE payments SET verified=0",self.plan(confirm=True)),
            ('quote',"UPDATE quotes SET status='Draft'",self.plan(confirm=True)),
            ('driver',"UPDATE employees SET driver_verified=0",self.plan(confirm=True)),
            ('duration',None,self.plan(confirm=True,end_at='2026-11-15T15:00:00+00:00'))]
        for label,sql,payload in cases:
            with self.subTest(label=label):
                if sql:
                    with transaction(self.path) as db:db.execute(sql)
                before=self.snapshot();r=self.owner.put('/api/jobs/J1/plan',json=payload)
                self.assertEqual(r.status_code,422,r.text);self.assertEqual(before,self.snapshot())
                with transaction(self.path) as db:
                    db.execute("UPDATE payments SET verified=1");db.execute("UPDATE quotes SET status='Accepted'");db.execute("UPDATE employees SET driver_verified=1 WHERE id='E1'")

    def test_conflict_time_off_and_inactive_resources_still_block_saves(self):
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(crew_ids=['E3']));self.assertEqual(r.status_code,422,r.text)
        with transaction(self.path) as db:
            insert(db,'time_off_requests',dict(id='OFF',employee_id='E1',start_date='2026-11-15',end_date='2026-11-15',status='Approved',requested_by='crew',requested_at=now()))
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan());self.assertEqual(r.status_code,409,r.text)
        with transaction(self.path) as db:
            db.execute("DELETE FROM time_off_requests")
            insert(db,'jobs',dict(id='J2',lead_id='L1',title='Conflicting job',phase='Packing',start_at='2026-11-15T14:00:00+00:00',end_at='2026-11-15T18:00:00+00:00',created_by='owner',created_at=now()))
            insert(db,'assignments',dict(job_id='J2',resource_type='employee',resource_id='E1'))
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan());self.assertEqual(r.status_code,409,r.text)
        resource=next(x for x in self.owner.get('/api/jobs/J1/planning').json()['resources']['crew'] if x['id']=='E1')
        self.assertFalse(resource['available']);self.assertIn('Conflicting job',' '.join(resource['reasons']))
        self.assertEqual(self.snapshot()[0]['version'],1)

    def test_resource_choices_include_inactive_records_without_hr_or_pay_data(self):
        data=self.staff('sales').get('/api/jobs/J1/planning').json()
        self.assertEqual(len(data['resources']['crew']),3);self.assertEqual(len(data['resources']['trucks']),2)
        for kind in ('crew','trucks'):
            self.assertTrue(any(not r['available'] and 'Inactive' in r['reasons'][0] for r in data['resources'][kind]))
            for r in data['resources'][kind]:self.assertEqual(set(r),{'id','name','active','available','reasons','driver_verified'})
        self.assertNotIn('PRIVATE',json.dumps(data));self.assertNotIn('hourly',json.dumps(data));self.assertNotIn('raw_json',json.dumps(data))

    def test_stale_and_closed_plans_remain_protected(self):
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(version=0));self.assertEqual(r.status_code,409,r.text)
        with transaction(self.path) as db:db.execute("UPDATE jobs SET status='Completed' WHERE id='J1'")
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan());self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.snapshot()[0]['version'],1)

    def test_confirmed_job_can_be_reassigned_without_reason_and_reconfirmed(self):
        with transaction(self.path) as db:db.execute("UPDATE jobs SET status='Confirmed' WHERE id='J1'")
        r=self.staff('dispatch').put('/api/jobs/J1/plan',json=self.plan(confirm=True));self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['status'],'Confirmed');self.assertEqual(self.snapshot()[0]['capacity_reviewed'],1)
        r=self.staff('sales').put('/api/jobs/J1/plan',json=self.plan(version=2,crew_ids=['E1']));self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.snapshot()[0]['capacity_reviewed'],0)
        self.assertEqual(self.snapshot()[0]['plan_review_required'],1)

    def test_confirmed_job_timing_cannot_shorten_accepted_service_during_partial_save(self):
        with transaction(self.path) as db:db.execute("UPDATE jobs SET status='Confirmed' WHERE id='J1'")
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(end_at='2026-11-15T15:00:00+00:00'))
        self.assertEqual(r.status_code,422,r.text);self.assertEqual(self.snapshot()[0]['version'],1)

    def test_one_save_confirms_complete_plans_for_every_authorized_planner(self):
        for version,user in enumerate(('owner','sales','dispatch','accounting','lead'),1):
            with self.subTest(user=user):
                r=self.staff(user).put('/api/jobs/J1/plan',json=self.plan(version=version,auto_confirm=True))
                self.assertEqual(r.status_code,200,r.text)
                self.assertEqual(r.json()['status'],'Confirmed');self.assertTrue(r.json()['confirmed'])
                job,assigned,history=self.snapshot()
                self.assertEqual((job['capacity_reviewed'],job['plan_review_required']),(1,0))
                self.assertEqual(history[-1]['actor'],user)
                self.assertEqual(json.loads(history[-1]['details_json'])['confirmation_mode'],'save-auto')
                self.assertEqual(len(assigned),3)
        self.assertTrue(self.owner.get('/api/jobs/J1/planning').json()['readiness']['ready'])
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM outbound_messages').fetchone()[0],0)

    def test_one_save_keeps_partial_assignments_then_clears_review_when_complete(self):
        with transaction(self.path) as db:db.execute("UPDATE jobs SET status='Confirmed' WHERE id='J1'")
        r=self.staff('dispatch').put('/api/jobs/J1/plan',json=self.plan(auto_confirm=True,crew_ids=['E1'],truck_ids=[]))
        self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['confirmed'])
        self.assertTrue(r.json()['confirmation_pending'])
        job,assigned,_=self.snapshot();self.assertEqual(job['plan_review_required'],1)
        self.assertEqual([a['resource_id'] for a in assigned],['E1'])
        self.assertEqual(self.owner.patch('/api/jobs/J1',json={'version':2,'status':'In progress'}).status_code,409)
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(version=2,auto_confirm=True))
        self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['confirmed'])
        self.assertEqual(self.snapshot()[0]['plan_review_required'],0)
        self.assertEqual(self.owner.patch('/api/jobs/J1',json={'version':3,'status':'In progress'}).status_code,200)

    def test_one_save_keeps_deposit_pending_until_it_is_verified(self):
        with transaction(self.path) as db:db.execute('UPDATE payments SET verified=0')
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(auto_confirm=True))
        self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['confirmed'])
        self.assertEqual(r.json()['status'],'Tentative');self.assertIn('deposit',r.json()['confirmation_pending'][0])
        self.assertEqual(len(self.snapshot()[1]),3)
        with transaction(self.path) as db:db.execute('UPDATE payments SET verified=1')
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(version=2,auto_confirm=True))
        self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['confirmed'])

    def test_one_save_does_not_confirm_missing_quote_or_stale_client_details(self):
        r=self.staff('sales').put('/api/jobs/J1/plan',json=self.plan(auto_confirm=True,quote_id=None))
        self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['confirmed'])
        with transaction(self.path) as db:db.execute("UPDATE leads SET version=version+1 WHERE id='L1'")
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(version=2,auto_confirm=True))
        self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['confirmed'])
        self.assertIn('Client details changed',r.json()['confirmation_pending'][0])

    def test_one_save_retains_driver_and_duration_requirements(self):
        with transaction(self.path) as db:db.execute('UPDATE employees SET driver_verified=0')
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(auto_confirm=True))
        self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['confirmed'])
        self.assertIn('verified driver',r.json()['confirmation_pending'][0])
        before=self.snapshot()
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(version=2,auto_confirm=True,end_at='2026-11-15T15:00:00+00:00'))
        self.assertEqual(r.status_code,422,r.text);self.assertEqual(before,self.snapshot())

    def test_one_save_does_not_bypass_access_conflicts_or_stale_versions(self):
        self.assertEqual(self.staff('crew').put('/api/jobs/J1/plan',json=self.plan(auto_confirm=True)).status_code,403)
        self.assertEqual(self.owner.put('/api/jobs/J1/plan',json=self.plan(auto_confirm=True,version=0)).status_code,409)
        with transaction(self.path) as db:
            insert(db,'time_off_requests',dict(id='OFF',employee_id='E1',start_date='2026-11-15',end_date='2026-11-15',status='Approved',requested_by='crew',requested_at=now()))
        before=self.snapshot()
        self.assertEqual(self.owner.put('/api/jobs/J1/plan',json=self.plan(auto_confirm=True)).status_code,409)
        self.assertEqual(before,self.snapshot())

    def test_one_save_keeps_in_progress_plan_validation_strict(self):
        with transaction(self.path) as db:db.execute("UPDATE jobs SET status='In progress' WHERE id='J1'")
        before=self.snapshot()
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(auto_confirm=True,crew_ids=['E1']))
        self.assertEqual(r.status_code,422,r.text);self.assertEqual(before,self.snapshot())
        r=self.owner.put('/api/jobs/J1/plan',json=self.plan(auto_confirm=True))
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['status'],'In progress')
        self.assertTrue(r.json()['plan_reviewed']);self.assertFalse(r.json()['confirmed'])
