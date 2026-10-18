"""Wix retries, scheduling boundaries, accounting isolation and permissions."""
from contextlib import closing
import hashlib
import json
import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import test_job_planning as fixture
from app.store import connect, transaction, rows, one
from app.wix_sync import title_schedule


class WixSyncTests(unittest.TestCase):
    staff = fixture.JobPlanningTests.staff

    def setUp(self):
        fixture.JobPlanningTests.setUp(self)
        self.key = 'fictional-wix-key-for-isolated-tests-only'
        env = patch.dict(os.environ, {'CROWNOPS_WIX_KEY_SHA256': hashlib.sha256(self.key.encode()).hexdigest()})
        env.start(); self.addCleanup(env.stop)
        self.web = TestClient(self.app); self.addCleanup(self.web.close)

    def payload(self, **extra):
        data = dict(connectionKey=self.key, event='quote_accepted', documentId='fictional-quote-1', number='TEST-1',
            title='Moving Service | 10/30/2026 - 9:00 a.m. | Staff extension 103', customer='Fictional Wix Client',
            email='wix-client@example.invalid', phone='5145550123', address='10 Billing Only', city='Montreal',
            total='2000.50', currency='CAD', contactId='fictional-contact', occurredAt='2026-10-18T12:00:00Z')
        data.update(extra); return {'data': data}

    def send(self, **extra):
        response = self.web.post('/api/webhooks/wix', json=self.payload(**extra))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def records(self):
        return self.owner.get('/api/wix/events').json()['items']

    def test_receiver_authentication_and_input_limits(self):
        self.assertEqual(self.web.post('/api/webhooks/wix', json=self.payload(connectionKey='x'*40)).status_code, 401)
        self.assertEqual(self.web.post('/api/webhooks/wix', json=[]).status_code, 401)
        self.assertEqual(self.web.post('/api/webhooks/wix', content='x'*100001).status_code, 413)
        self.assertEqual(self.web.post('/api/webhooks/wix', content='{').status_code, 400)
        for change in ({'documentId':''}, {'documentId':'{{var("id")}}'}, {'event':'other'}, {'total':'NaN'}, {'total':True}, {'customer':[]}, {'total':'Infinity'}):
            self.assertEqual(self.web.post('/api/webhooks/wix', json=self.payload(**change)).status_code, 422)
        with patch.dict(os.environ, {'CROWNOPS_WIX_KEY_SHA256':''}):
            self.assertEqual(self.web.post('/api/webhooks/wix', json=self.payload()).status_code, 503)
        self.assertEqual(self.records(), [])

    def test_valid_quote_creates_crm_and_calendar_without_estimate_payment_or_notifications(self):
        result = self.send(); self.assertEqual(result['status'], 'On schedule')
        rec = self.records()[0]
        with closing(connect(self.path)) as db:
            lead = one(db, 'SELECT * FROM leads WHERE id=?', (rec['lead_id'],))
            self.assertEqual(lead['move_date'], '2026-10-30'); self.assertEqual(lead['pickup'], '')
            self.assertEqual(lead['notifications_suppressed'], 1)
            self.assertIn('Billing address', lead['notes'])
            self.assertEqual(rows(db, 'SELECT * FROM quotes WHERE lead_id=?', (lead['id'],)), [])
            self.assertEqual(rows(db, 'SELECT * FROM payments WHERE lead_id=?', (lead['id'],)), [])
            self.assertEqual(rows(db, 'SELECT * FROM jobs WHERE lead_id=?', (lead['id'],)), [])
        event = self.owner.get('/api/schedule-imports/events').json()[0]
        self.assertEqual(event['start_time'], '09:00'); self.assertEqual(event['lead_id'], lead['id'])
        self.assertNotIn(self.key, json.dumps(self.records())+json.dumps(event))

    def test_retries_ignore_timestamp_and_key_order_and_numeric_format(self):
        self.send()
        self.assertTrue(self.send(occurredAt='2026-10-19T12:00:00Z', total=2000.5)['duplicate'])
        data=self.payload()['data']; data=dict(reversed(list(data.items())))
        self.assertTrue(self.web.post('/api/webhooks/wix',json={'data':data}).json()['duplicate'])
        self.assertEqual(len(self.records()),1); self.assertEqual(self.records()[0]['deliveries'],3)
        self.assertEqual(len(self.owner.get('/api/schedule-imports/events').json()),1)

    def test_source_planning_reuses_crm_and_saves_reviewed_service_addresses(self):
        self.send(); rec=self.records()[0]
        source=self.owner.get('/api/schedule-imports/events').json()[0]
        result=self.owner.post('/api/schedule-imports/promote',json={'source_key':source['source_key'],'reviewed':True,
            'note':'Reviewed addresses and duration with client','client':{'pickup':'100 Test Pickup','dropoff':'200 Test Destination'},
            'start_at':'2026-10-30T13:00:00+00:00','end_at':'2026-10-30T17:00:00+00:00','crew_ids':['E1','E2'],'truck_ids':['V1']})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['lead_id'],rec['lead_id'])
        with closing(connect(self.path)) as db:
            lead=one(db,'SELECT * FROM leads WHERE id=?',(rec['lead_id'],))
            self.assertEqual(lead['pickup'],'100 Test Pickup');self.assertEqual(lead['dropoff'],'200 Test Destination')
            self.assertEqual(len(rows(db,'SELECT * FROM leads')),2)

    def test_changed_source_is_retained_without_overwriting_service(self):
        self.send(); self.assertEqual(self.send(title='Moving | 11/03/2026 10am')['status'], 'Needs review')
        self.assertEqual(len(self.records()),2)
        self.assertEqual(len(self.owner.get('/api/schedule-imports/events').json()),1)
        self.assertEqual(self.owner.get('/api/schedule-imports/events').json()[0]['date'], '2026-10-30')

    def test_fully_booked_day_blocks_until_office_reopens_and_resolves(self):
        r=self.owner.put('/api/schedule/availability/2026-10-30',json={'status':'Fully booked','reason':'No capacity','version':0})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.send()['status'],'Needs review'); rec=self.records()[0]
        self.assertEqual(self.owner.get('/api/schedule-imports/events').json(),[])
        self.assertEqual(self.owner.post('/api/wix/events/'+rec['id']+'/schedule',json={'date':'2026-10-30','time':'09:00'}).status_code,409)
        self.owner.put('/api/schedule/availability/2026-10-30',json={'status':'Open','reason':'Available','version':1})
        r=self.staff('sales').post('/api/wix/events/'+rec['id']+'/schedule',json={'date':'2026-10-30','time':'09:00'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.owner.post('/api/wix/events/'+rec['id']+'/schedule',json={'date':'2026-10-30'}).status_code,409)

    def test_unknown_date_can_be_reviewed_and_out_of_scope_is_ignored(self):
        self.assertEqual(self.send(title='Moving | Date TBD')['status'],'Needs review')
        rec=self.records()[0]
        r=self.owner.post('/api/wix/events/'+rec['id']+'/schedule',json={'date':'2027-02-01','time':''})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.send(documentId='old-quote',title='Moving | 10/18/2026 9am')['status'],'Outside import range')
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db,'SELECT move_date FROM leads WHERE id=?',(rec['lead_id'],))['move_date'],'2027-02-01')
            self.assertIsNone(one(db,"SELECT id FROM leads WHERE external_id='wix:quote:old-quote'"))

    def test_possible_duplicate_job_and_calendar_are_not_added(self):
        self.send()
        self.assertEqual(self.send(documentId='second-quote')['status'],'Needs review')
        self.assertEqual(len(self.owner.get('/api/schedule-imports/events').json()),1)
        result=self.send(documentId='fixture-job',customer='Test move',phone='5145550100',title='Moving | 11/15/2026 9am')
        self.assertEqual(result['status'],'Needs review')
        self.assertEqual(len(self.owner.get('/api/schedule-imports/events').json()),1)

    def test_invoice_notice_does_not_create_money_or_crm_and_requires_accounting_review(self):
        self.assertEqual(self.send(event='invoice_paid')['status'],'Payment review')
        rec=self.records()[0]; self.assertIsNone(rec['lead_id'])
        self.assertEqual(self.staff('sales').post('/api/wix/events/'+rec['id']+'/review',json={'note':'Checked source'}).status_code,403)
        self.assertEqual(self.owner.post('/api/wix/events/'+rec['id']+'/review',json={'note':'Matched independently in QuickBooks'}).status_code,200)
        with closing(connect(self.path)) as db:
            self.assertEqual(len(rows(db,'SELECT * FROM payments')),1)
            self.assertEqual(len(rows(db,'SELECT * FROM leads')),1)

    def test_roles_pause_and_delivery_probe(self):
        self.assertEqual(self.staff('crew').get('/api/wix/status').status_code,403)
        self.assertEqual(self.staff('crew').get('/api/wix/events').status_code,403)
        self.assertEqual(self.staff('sales').put('/api/wix/settings',json={'paused':True}).status_code,403)
        self.assertEqual(self.owner.put('/api/wix/settings',json={'paused':True}).status_code,200)
        self.assertEqual(self.web.post('/api/webhooks/wix',json=self.payload()).status_code,503)
        probe=self.web.post('/api/webhooks/wix?probe=1',json=self.payload())
        self.assertEqual(probe.status_code,200); self.assertEqual(probe.json()['business_records_created'],0)
        self.assertEqual(self.web.post('/api/webhooks/wix?probe=1',json=self.payload(documentId='')).status_code,422)
        self.assertTrue(self.owner.get('/api/wix/status').json()['last_test']);self.assertEqual(self.records(),[])
        self.owner.put('/api/wix/settings',json={'paused':False});self.send()

    def test_title_dates_times_and_dst(self):
        for title,expected in [
            ('Moving 2026-10-30 14:30',('2026-10-30','14:30')),
            ('Moving 02/01/2027 9:00 a.m.',('2027-02-01','09:00')),
            ('Moving 10/30/2026 12pm',('2026-10-30','12:00')),
            ('Moving 10/30/2026 Afternoon',('2026-10-30','')),
            ('Moving 10/30/2026 9am - 12pm',('2026-10-30','')),
            ('Moving 10/30/2026 9-12pm',('2026-10-30','')),
            ('Moving 10/30/2026 + 10/31/2026',('','')),
            ('Moving 02/30/2026',('','')),
            ('Moving 10/30',('','')),
            ('Moving 10/30/2026 OR TBD',('','')),
            ('Moving 11/01/2026 1:30am',('2026-11-01','')),
            ('Moving 03/14/2027 2:30am',('2027-03-14','')),
        ]:
            with self.subTest(title=title):self.assertEqual(title_schedule(title)[:2],expected)
