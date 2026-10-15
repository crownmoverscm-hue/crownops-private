"""Template drafts use disposable clients and never call email or payment providers."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import copy
import tempfile
import unittest

from fastapi.testclient import TestClient
from app.main import create_app, sha
from app.store import connect, transaction, insert, now, one


class TemplateQuoteTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict('os.environ',{'CROWNOPS_ENV':'test','CROWNOPS_ALLOW_EXTERNAL':'0'})
        env.start();self.addCleanup(env.stop)
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'drafts.sqlite3';self.app=create_app(self.path)
        expires=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        with transaction(self.path) as db:
            insert(db,'pricing_versions',dict(id='TEST-PRICE',label='Template test',rates_json='{}',settings_json='{}',approved=1,created_at=now()))
            for role in ('sales','crew'):
                insert(db,'users',dict(id=role,email=role+'@example.invalid',name=role,role=role,password_hash='unused',active=1,created_at=now()))
                insert(db,'sessions',dict(token_hash=sha(role+'-token'),user_id=role,csrf=role+'-csrf',expires_at=expires))
            insert(db,'annual_rate_periods',dict(id='TEST-RATE',label='Test dates',start_date='2026-11-01',end_date='2026-11-30',
                rate_2_cents=13500,rate_3_cents=19500,rate_4_cents=26000,rate_5_cents=30000,rate_6_cents=35000,active=1,version=1,note='test',approved_by='sales',created_at=now()))
        self.client=TestClient(self.app);self.client.cookies.set('crownops_session','sales-token');self.client.headers['x-csrf-token']='sales-csrf';self.addCleanup(self.client.close)
        self.data=dict(service_type='Moving',language='English',template_key='moving-en',move_date='2026-11-15',movers=2,trucks=1,
            hours_low=3,hours_high=4,hourly_rate=135,minimum_hours=3,transport_hours=1,start_window='morning',deposit_required=300,
            quote_title='Local move — editable title',client_note='Include the desk assembly.',draft_key='test-draft',
            new_client=dict(client_name='TEST Template Client',email='template@example.invalid',phone='5145550123'),
            service_details=dict(pickup='100 Test Street',dropoff='200 Test Street',inventory='Desk and boxes'))

    def count(self,table):
        with closing(connect(self.path)) as db:return db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

    def save(self,data=None):return self.client.post('/api/crm/quotes',json=data or self.data)

    def test_opening_template_setup_has_no_client_or_service_side_effect(self):
        before=(self.count('leads'),self.count('client_accounts'),self.count('quotes'))
        result=self.client.get('/api/crm/quote-setup');self.assertEqual(result.status_code,200,result.text)
        self.assertIsNone(result.json()['lead']);self.assertIsNone(result.json()['client'])
        self.assertEqual(before,(self.count('leads'),self.count('client_accounts'),self.count('quotes')))
        crew=TestClient(self.app);self.addCleanup(crew.close);crew.cookies.set('crownops_session','crew-token')
        self.assertEqual(crew.get('/api/crm/quote-setup').status_code,403)

    def test_saves_contact_service_and_quote_together_without_sending_or_booking(self):
        result=self.save();self.assertEqual(result.status_code,200,result.text);quote=result.json()
        self.assertEqual(quote['status'],'Draft');self.assertEqual(quote['inputs']['client_note'],self.data['client_note'])
        self.assertEqual(quote['lead_snapshot']['pickup'],'100 Test Street');self.assertEqual(quote['result']['low_cents'],57500)
        self.assertEqual(self.count('leads'),1);self.assertEqual(self.count('client_accounts'),1)
        self.assertEqual(self.count('outbound_messages'),0);self.assertEqual(self.count('jobs'),0)
        self.assertNotIn('new_client',quote['inputs']);self.assertNotIn('service_details',quote['inputs'])

    def test_invalid_template_rolls_back_contact_and_service(self):
        data=copy.deepcopy(self.data);data['template_key']='packing-en'
        result=self.save(data);self.assertEqual(result.status_code,422,result.text)
        self.assertEqual(self.count('client_accounts'),0);self.assertEqual(self.count('leads'),0);self.assertEqual(self.count('quotes'),0)

    def test_repeated_save_is_idempotent_and_changed_retry_is_rejected(self):
        first=self.save();second=self.save();self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(first.json()['id'],second.json()['id']);self.assertEqual(self.count('quotes'),1)
        changed=copy.deepcopy(self.data);changed['client_note']='Changed after saved response'
        self.assertEqual(self.save(changed).status_code,409);self.assertEqual(self.count('leads'),1)

    def test_duplicate_contact_must_be_selected_without_creating_another_service(self):
        self.assertEqual(self.save().status_code,200)
        data=copy.deepcopy(self.data);data['draft_key']='second-draft'
        self.assertEqual(self.save(data).status_code,409)
        self.assertEqual(self.count('client_accounts'),1);self.assertEqual(self.count('leads'),1)
        with closing(connect(self.path)) as db:cid=one(db,'SELECT id FROM client_accounts')['id']
        data.pop('new_client');data['client_id']=cid
        result=self.save(data);self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.count('client_accounts'),1);self.assertEqual(self.count('leads'),2)

    def test_existing_inquiry_keeps_history_and_rejects_stale_or_wrong_client(self):
        saved=self.save().json();data=copy.deepcopy(self.data)
        data.pop('new_client');data.pop('service_details');data['lead_id']=saved['lead_id'];data['lead_version']=saved['lead_version'];data['draft_key']='revision'
        data['hours_high']=5;result=self.save(data);self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['revision'],2);self.assertEqual(self.count('leads'),1)
        old=self.client.get('/api/crm/quotes/'+saved['id']).json()['quote']
        self.assertEqual(old['inputs']['hours_high'],4);self.assertEqual(old['status'],'Draft')
        data['draft_key']='stale';data['lead_version']=999
        self.assertEqual(self.save(data).status_code,409)
        data['draft_key']='wrong-client';data['lead_version']=saved['lead_version'];data['client_id']='wrong'
        self.assertEqual(self.save(data).status_code,409)

    def test_client_portal_receives_edited_quote_copy(self):
        saved=self.save();self.assertEqual(saved.status_code,200,saved.text);quote=saved.json()
        finalized=self.client.post('/api/crm/quotes/'+quote['id']+'/finalize',json={'send_now':False,'sync_quickbooks':False})
        self.assertEqual(finalized.status_code,200,finalized.text);token=finalized.json()['portal_url'].split('#access=')[1]
        portal=TestClient(self.app);self.addCleanup(portal.close)
        self.assertEqual(portal.post('/api/portal/exchange',json={'token':token}).status_code,200)
        project=portal.get('/api/portal/project');self.assertEqual(project.status_code,200,project.text)
        inputs=project.json()['quotes'][0]['inputs']
        self.assertEqual(inputs['quote_title'],self.data['quote_title']);self.assertEqual(inputs['client_note'],self.data['client_note'])
        self.assertNotIn('draft_request_hash',inputs);self.assertNotIn('draft_key',inputs)
