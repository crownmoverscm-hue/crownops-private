"""Inbound identity/service handoffs use disposable records and mocked Gmail reads."""
from contextlib import closing
from unittest.mock import patch
import base64,json,unittest
from fastapi.testclient import TestClient
from app.gmail_sync import FEEDS,sync_gmail_readonly,match_inquiry_project,promote_pending_call_events,review_items,register_gmail_resolution
from app.store import connect,transaction,one,rows,insert,uid,now,dumps
import test_template_quotes as fixture


class GmailAmbiguityTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TemplateQuoteTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path=self.f.path;self.sales=self.f.client;self.app=self.f.app
        if not any(r.path=='/api/gmail/review/{event_id}/resolve' for r in self.app.routes):
            auth=next(r for r in self.app.routes if r.path=='/api/expenses').dependant.dependencies[0].call
            register_gmail_resolution(self.app,self.path,auth,self.new_lead)
        self.messages={};self.feed='missed_call';self.reads=[]

    def lead(self,lid='L1',**overrides):
        values=dict(id=lid,client_name='Test client',email='client@example.invalid',phone='5145550100',move_date='2026-11-15',pickup='Original address',inventory='Original inventory',stage='Contacted',assigned_to='sales',source='Manual',lead_type='Moving',notifications_suppressed=0,created_at='2026-10-01T12:00:00+00:00',updated_at='2026-10-01T12:00:00+00:00',version=1)
        values.update(overrides)
        with transaction(self.path) as db:insert(db,'leads',values)
        return values

    def new_lead(self,db,data,actor,external_id=None,suppressed=1):
        allowed=('client_name','email','phone','move_date','pickup','dropoff','inventory','source','lead_type','stage','priority','notes','language')
        values={k:v for k,v in data.items() if k in allowed};values.update(id=uid('LEAD'),external_id=external_id,notifications_suppressed=suppressed,created_at=now(),updated_at=now())
        insert(db,'leads',values)
        return values

    def message(self,mid='source',feed='missed_call',body=None,subject=None):
        self.feed=feed
        if body is None:body='Missed call 10/16/2026 22:15:00'
        if subject is None:subject='You Have a Missed Call from Crown Movers: TEST CLIENT 5145550100'
        self.messages[mid]={'id':mid,'internalDate':'1792203300000','payload':{'headers':[{'name':'Subject','value':subject},{'name':'From','value':'notices@example.invalid'}],'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(body.encode()).decode().rstrip('=')}}}

    def quote(self,mid='source',day='2026-11-15',email='client@example.invalid',phone='5145550100',inventory='New details'):
        body='CROWN_QUOTE_BUILDER_INPUT\nClient Name: Test client\nEmail: '+email+'\nPhone: '+phone+'\nMoving Date: '+day+'\nPickup Address: Changed address\nDestination Address: Destination\nAdditional Details: '+inventory+'\nEND_CROWN_QUOTE_BUILDER_INPUT'
        self.message(mid,'quote',body,'New Crown Movers Quote Request - Test client - '+day)

    def api(self,token,path,params=None):
        self.reads.append(path)
        if path=='labels':return {'labels':[{'id':self.feed,'name':FEEDS[self.feed]}]}
        if path=='messages':return {'messages':[{'id':mid} for mid in self.messages]}
        if path.startswith('messages/'):return self.messages[path.split('/')[1]]
        raise AssertionError('Unexpected provider operation: '+path)

    def sync(self):
        with patch('app.gmail_sync._api',side_effect=self.api):return sync_gmail_readonly(self.path,'mock-token','sales',self.new_lead,fluid=True,feed_names=(self.feed,))

    def event(self):
        with closing(connect(self.path)) as db:return one(db,'SELECT * FROM integration_events ORDER BY created_at,id LIMIT 1')

    def count(self,table):
        with closing(connect(self.path)) as db:return db.execute('SELECT count(*) FROM '+table).fetchone()[0]

    def options(self,event=None):
        response=self.sales.get('/api/gmail/review/'+(event or self.event())['id']+'/match-options');self.assertEqual(response.status_code,200,response.text);return response.json()

    def resolve(self,decision='match',lead_id='L1',**overrides):
        event=self.event();data={'reviewed':True,'decision':decision,'lead_id':lead_id,'note':'Compared service dates with the original inquiry.','fingerprint':self.options(event)['matching']['fingerprint'],**overrides}
        return self.sales.post('/api/gmail/review/'+event['id']+'/resolve',json=data)

    def test_ambiguous_missed_call_is_queued_with_candidates_and_creates_no_project_or_call(self):
        self.lead();self.lead('L2',move_date='2026-12-01',updated_at='2026-10-16T23:00:00+00:00');self.message()
        result=self.sync();self.assertEqual(result['needs_review'],1);self.assertEqual(result['new_leads'],0)
        event=self.event();self.assertEqual(event['status'],'Needs review');payload=json.loads(event['payload_json'])
        self.assertEqual(payload['origin_label'],'Missed Call');self.assertEqual({r['id'] for r in payload['matching']['candidates']},{'L1','L2'})
        self.assertEqual(self.count('leads'),2);self.assertEqual(self.count('calls'),0);self.assertEqual(self.count('tasks'),1)
        queue=review_items(self.path);self.assertEqual(queue[0]['id'],event['id']);self.assertIn('Several services',queue[0]['payload']['matching']['reason'])

    def test_repeated_sync_does_not_promote_previously_flagged_ambiguity(self):
        self.lead();self.lead('L2',move_date='2026-12-01');self.message();self.sync()
        with transaction(self.path) as db:db.execute("UPDATE leads SET phone='5145559999',email='other@example.invalid' WHERE id='L2'")
        result=self.sync();self.assertEqual(result['duplicates'],1);self.assertEqual(self.event()['status'],'Needs review');self.assertEqual(self.count('calls'),0);self.assertEqual(self.count('tasks'),1)

    def test_existing_ambiguous_voicemail_promotion_stages_review_instead_of_guessing(self):
        self.lead();self.lead('L2',move_date='2026-12-01')
        with transaction(self.path) as db:
            insert(db,'integration_events',dict(id='OLD',event_key='gmail:old',event_type='gmail_voicemail',object_id='old',payload_json=dumps({'parsed':{'phone':'5145550100','caller':'Test','call_time':'10/16/2026 22:15:00'},'subject':'Voicemail'}),status='Needs review',created_at=now()))
            self.assertEqual(promote_pending_call_events(db,'sales',self.new_lead),0)
        self.assertTrue(json.loads(self.event()['payload_json'])['matching']['requires_review']);self.assertEqual(self.count('calls'),0)

    def test_legacy_duplicate_voicemail_ambiguity_stays_visible_in_review(self):
        self.lead();self.lead('L2',move_date='2026-12-01')
        with transaction(self.path) as db:
            insert(db,'integration_events',dict(id='OLD',event_key='gmail:old',event_type='gmail_duplicate_call',object_id='old',payload_json=dumps({'parsed':{'kind':'Voicemail','phone':'5145550100','caller':'Test','call_time':'10/16/2026 22:15:00'},'subject':'Voicemail'}),status='Ignored',created_at=now()))
            self.assertEqual(promote_pending_call_events(db,'sales',self.new_lead),0)
        event=self.event();self.assertEqual(event['event_type'],'gmail_voicemail');self.assertEqual(event['status'],'Needs review');self.assertEqual(review_items(self.path)[0]['id'],'OLD')

    def test_single_known_caller_keeps_service_and_sales_owner(self):
        self.lead();self.message();result=self.sync();self.assertEqual(result['new_leads'],0);self.assertEqual(self.count('calls'),1)
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db,'SELECT lead_id FROM calls')['lead_id'],'L1');self.assertEqual(one(db,'SELECT owner FROM tasks')['owner'],'sales')

    def test_explicit_quote_date_selects_one_service_without_overwriting_sold_details(self):
        self.lead();self.lead('L2',move_date='2026-12-01');self.quote();result=self.sync()
        self.assertEqual(result['new_leads'],0);self.assertEqual(self.event()['object_id'],'L1');self.assertEqual(self.count('messages'),1)
        with closing(connect(self.path)) as db:
            lead=one(db,"SELECT * FROM leads WHERE id='L1'");self.assertEqual(lead['pickup'],'Original address');self.assertEqual(lead['inventory'],'Original inventory');self.assertEqual(lead['version'],1);self.assertEqual(lead['created_at'],'2026-10-01T12:00:00+00:00')
            self.assertIn('quote form',one(db,'SELECT title FROM tasks')['title'])

    def test_quote_date_without_unique_project_stays_in_review(self):
        self.lead();self.lead('L2',move_date='2026-12-01');self.quote(day='2026-12-15');self.sync()
        self.assertEqual(self.event()['status'],'Needs review');self.assertEqual(self.count('leads'),2);self.assertEqual(self.count('messages'),0)

    def test_one_known_client_with_different_form_date_gets_a_new_service(self):
        self.lead();self.quote(day='2026-12-15');result=self.sync();self.assertEqual(result['new_leads'],1);self.assertEqual(self.count('leads'),2);self.assertNotEqual(self.event()['object_id'],'L1')

    def test_conflicting_email_and_phone_are_not_resolved_by_recency_or_date(self):
        self.lead(phone='5145553333');self.lead('L2',email='other@example.invalid',move_date='2026-12-01');self.quote();self.sync()
        self.assertEqual(self.event()['status'],'Needs review');self.assertIn('different client services',json.loads(self.event()['payload_json'])['matching']['reason'])

    def test_phone_with_another_existing_email_requires_identity_review(self):
        self.lead();self.quote(email='different@example.invalid');self.sync();self.assertEqual(self.event()['status'],'Needs review');self.assertEqual(self.count('leads'),1)

    def test_same_subject_new_gmail_id_keeps_each_original_form_but_retry_is_idempotent(self):
        self.lead();self.quote('first',inventory='First details');self.quote('second',inventory='Second details');self.sync();self.sync()
        self.assertEqual(self.count('leads'),1);self.assertEqual(self.count('messages'),2);self.assertEqual(self.count('integration_events'),2)
        with closing(connect(self.path)) as db:self.assertEqual(len({r['body'] for r in rows(db,'SELECT body FROM messages')}),2)

    def test_explicit_match_attaches_once_and_closes_review_task(self):
        self.lead();self.lead('L2',move_date='2026-12-01');self.message();self.sync()
        response=self.resolve();self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['lead_id'],'L1')
        again=self.resolve();self.assertEqual(again.status_code,200,again.text);self.assertTrue(again.json()['duplicate']);self.assertEqual(self.count('calls'),1)
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db,'SELECT lead_id FROM calls')['lead_id'],'L1');self.assertEqual(one(db,"SELECT status FROM tasks WHERE legacy_id LIKE 'gmail-match:%'")['status'],'Done')

    def test_create_new_is_explicit_and_retry_safe(self):
        self.lead();self.lead('L2',move_date='2026-12-01');self.message();self.sync()
        response=self.resolve(decision='new');self.assertEqual(response.status_code,200,response.text);self.assertTrue(response.json()['created_service']);self.assertEqual(self.count('leads'),3)
        again=self.resolve(decision='new');self.assertEqual(again.status_code,200,again.text);self.assertTrue(again.json()['duplicate']);self.assertEqual(self.count('leads'),3);self.assertEqual(self.count('calls'),1)

    def test_reviewed_quote_match_preserves_scope_and_one_original_message(self):
        self.lead();self.lead('L2',move_date='2026-12-01');self.quote(day='2026-12-15');self.sync()
        response=self.resolve();self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['lead_id'],'L1')
        self.assertEqual(self.resolve().status_code,200);self.assertEqual(self.count('messages'),1)
        with closing(connect(self.path)) as db:
            lead=one(db,"SELECT * FROM leads WHERE id='L1'");self.assertEqual(lead['move_date'],'2026-11-15');self.assertEqual(lead['inventory'],'Original inventory');self.assertEqual(lead['version'],1)
            self.assertEqual(json.loads(self.event()['payload_json'])['origin_label'],'Quote Form')

    def test_dismiss_requires_reason_and_keeps_original_evidence(self):
        self.lead();self.lead('L2',move_date='2026-12-01');self.message();self.sync()
        self.assertEqual(self.resolve(decision='dismiss',note='').status_code,422)
        response=self.resolve(decision='dismiss',note='Confirmed this is a spam callback.');self.assertEqual(response.status_code,200,response.text)
        event=self.event();self.assertEqual(event['status'],'Ignored');payload=json.loads(event['payload_json']);self.assertEqual(payload['resolution']['note'],'Confirmed this is a spam callback.');self.assertIn('parsed',payload);self.assertEqual(self.count('calls'),0)

    def test_changed_candidates_require_fresh_review_and_cannot_attach_another_project(self):
        self.lead();self.lead('L2',move_date='2026-12-01');self.message();self.sync();old=self.options()['matching']['fingerprint']
        with transaction(self.path) as db:db.execute("UPDATE leads SET version=version+1 WHERE id='L1'")
        self.assertEqual(self.resolve(fingerprint=old).status_code,409);self.assertEqual(self.resolve(lead_id='OTHER').status_code,409);self.assertEqual(self.count('calls'),0)

    def test_crew_and_signed_out_cannot_resolve_customer_identity(self):
        self.lead();self.lead('L2',move_date='2026-12-01');self.message();self.sync();event=self.event()
        crew=TestClient(self.app);self.addCleanup(crew.close);crew.cookies.set('crownops_session','crew-token');crew.headers['x-csrf-token']='crew-csrf'
        for suffix in ('match-options','resolve'):
            response=crew.get('/api/gmail/review/'+event['id']+'/'+suffix) if suffix=='match-options' else crew.post('/api/gmail/review/'+event['id']+'/'+suffix,json={})
            self.assertEqual(response.status_code,403)
        public=TestClient(self.app);self.addCleanup(public.close);self.assertEqual(public.get('/api/gmail/review/'+event['id']+'/match-options').status_code,401)

    def test_closed_services_and_foreign_phone_suffix_do_not_get_silent_matches(self):
        self.lead(stage='Completed')
        with closing(connect(self.path)) as db:
            closed=match_inquiry_project(db,{'phone':'5145550100'},'call');self.assertTrue(closed['requires_review']);self.assertIsNone(closed['lead'])
            foreign=match_inquiry_project(db,{'phone':'+44 5145550100'},'call');self.assertTrue(foreign['requires_review']);self.assertEqual(foreign['candidates'],[])
            extension=match_inquiry_project(db,{'phone':'+1 (514) 555-0100 ext 23'},'call');self.assertEqual(len(extension['candidates']),1)

    def test_email_identity_helper_keeps_multiple_services_in_review_without_date(self):
        self.lead();self.lead('L2',move_date='2026-12-01')
        with closing(connect(self.path)) as db:
            match=match_inquiry_project(db,{'email':'CLIENT@example.invalid'},'email');self.assertTrue(match['requires_review']);self.assertIsNone(match['lead']);self.assertEqual(len(match['candidates']),2)


if __name__=='__main__':unittest.main()
