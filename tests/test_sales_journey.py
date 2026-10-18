"""Sales/client round trips use disposable records and never send email or charge clients."""
from contextlib import closing
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from urllib.parse import urlsplit
import json,unittest
from fastapi.testclient import TestClient
from app.main import sha
from app.store import connect,transaction,insert,one,rows,now,dumps
from app.platform_common import put_setting
import test_template_quotes as fixture


class SalesJourneyTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TemplateQuoteTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.sales=self.f.client;self.path=self.f.path;self.app=self.f.app
        self.q=self.f.save().json();self.lid=self.q['lead_id'];self.qid=self.q['id']
        self.portal=TestClient(self.app);self.addCleanup(self.portal.close)
        self.manager=TestClient(self.app);self.addCleanup(self.manager.close)
        self.manager.cookies.set('crownops_session','manager-token');self.manager.headers['x-csrf-token']='manager-csrf'
        self.crew=TestClient(self.app);self.addCleanup(self.crew.close)
        self.crew.cookies.set('crownops_session','crew-token');self.crew.headers['x-csrf-token']='crew-csrf'
        with transaction(self.path) as db:
            insert(db,'users',dict(id='manager',email='manager@example.invalid',name='Manager Test',role='manager',password_hash='unused',active=1,created_at=now()))
            insert(db,'sessions',dict(token_hash=sha('manager-token'),user_id='manager',csrf='manager-csrf',expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
            put_setting(db,'client_terms',{'approved':True,'version':'test-terms','text':'Fictional terms.'})

    def invite(self):
        url=self.sales.post('/api/leads/'+self.lid+'/portal-invite',json={}).json()['url']
        result=self.portal.post('/api/portal/exchange',json={'token':urlsplit(url).fragment.removeprefix('access=')});self.assertEqual(result.status_code,200,result.text)
        self.portal.headers['x-csrf-token']=result.json()['csrf']

    def finalize(self):
        r=self.sales.post('/api/crm/quotes/'+self.qid+'/finalize',json={'send_now':False});self.assertEqual(r.status_code,200,r.text);return r.json()

    def request(self,key='details-test-key',**overrides):
        return self.sales.post('/api/leads/'+self.lid+'/detail-requests',json={'request_key':key,'fields':['inventory','photos'],'note':'Please include the garage.',**overrides})

    def test_waiting_outcome_does_not_stale_ready_quote(self):
        self.finalize()
        with transaction(self.path) as db:insert(db,'tasks',dict(id='WAITING-TEST',lead_id=self.lid,title='Follow up',owner='sales',due_date='2026-11-01',created_at=now()))
        r=self.sales.post('/api/ux/actions/task/WAITING-TEST/outcome',json={'outcome':'waiting','note':'Client considering quote'})
        self.assertEqual(r.status_code,200,r.text)
        self.invite();self.assertFalse(self.portal.get('/api/portal/project').json()['quotes'][0]['stale'])

    def test_claim_and_internal_notes_preserve_quote_but_scope_edit_invalidates_it(self):
        self.finalize();self.invite()
        self.assertEqual(self.sales.post('/api/leads/'+self.lid+'/claim',json={}).status_code,200)
        self.assertEqual(self.sales.patch('/api/leads/'+self.lid,json={'version':1,'notes':'Internal reminder','stage':'Waiting on client'}).status_code,200)
        self.assertFalse(self.portal.get('/api/portal/project').json()['quotes'][0]['stale'])
        self.assertEqual(self.sales.patch('/api/leads/'+self.lid,json={'version':1,'inventory':'New piano'}).status_code,200)
        self.assertTrue(self.portal.get('/api/portal/project').json()['quotes'][0]['stale'])

    def test_sales_cannot_approve_due_on_service_exception_manager_can(self):
        self.finalize();r=self.sales.post('/api/quotes/'+self.qid+'/accept',json={'reference':'Fictional acceptance'});self.assertEqual(r.status_code,200,r.text)
        data={'method':'Cash','mode':'due_on_service'}
        self.assertEqual(self.sales.post('/api/crm/quotes/'+self.qid+'/manual-deposit',json=data).status_code,403)
        r=self.manager.post('/api/crm/quotes/'+self.qid+'/manual-deposit',json=data);self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['exception'])
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM payments').fetchone()[0],0)

    def test_sales_can_still_record_received_deposit(self):
        self.finalize();self.sales.post('/api/quotes/'+self.qid+'/accept',json={'reference':'Fictional acceptance'})
        r=self.sales.post('/api/crm/quotes/'+self.qid+'/manual-deposit',json={'method':'Cash','mode':'received','amount':300,'reference':'test-received'})
        self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['confirmed'])

    def test_request_is_prefilled_friendly_draft_and_retries_are_safe(self):
        first=self.request();self.assertEqual(first.status_code,200,first.text);self.assertFalse(first.json()['sent'])
        self.assertTrue(self.request().json()['duplicate']);self.assertEqual(self.request(note='Changed').status_code,409)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM outbound_messages').fetchone()[0],1)
            m=one(db,'SELECT * FROM outbound_messages');self.assertEqual(m['status'],'Draft');self.assertIn('already have are filled in',m['body']);self.assertIn('Google Drive',m['body'])
            link=next(line for line in m['body'].splitlines() if '/portal?' in line)
        r=self.portal.post('/api/portal/exchange',json={'token':link.split('#access=')[1]});self.assertEqual(r.status_code,200,r.text);self.portal.headers['x-csrf-token']=r.json()['csrf']
        project=self.portal.get('/api/portal/project').json();self.assertEqual(project['client']['pickup'],'100 Test Street');self.assertEqual(project['details_progress']['request']['fields'],['inventory','photos'])
        self.assertEqual(project['client']['inventory'],'Desk and boxes')

    def test_new_click_reuses_open_request_without_extra_email(self):
        a=self.request().json();b=self.request(key='another-click').json();self.assertEqual(a['mail_id'],b['mail_id'])

    def test_crew_cannot_request_details_or_read_sales_priorities(self):
        self.assertEqual(self.crew.get('/api/leads/'+self.lid+'/detail-requests').status_code,403)
        self.assertEqual(self.crew.post('/api/leads/'+self.lid+'/detail-requests',json={'fields':['inventory'],'request_key':'forbidden'}).status_code,403)
        self.assertEqual(self.crew.get('/api/sales/overview').status_code,403)

    def test_draft_progress_survives_new_session_without_changing_live_scope(self):
        self.invite();data={'version':1,'revision':0,'fields':{'inventory':'New wardrobe'}}
        r=self.portal.put('/api/portal/details-progress',json=data);self.assertEqual(r.status_code,200,r.text)
        project=self.portal.get('/api/portal/project').json();self.assertEqual(project['client']['inventory'],'Desk and boxes');self.assertEqual(project['details_progress']['draft']['fields']['inventory'],'New wardrobe');self.assertEqual(project['changes'],[])
        self.assertEqual(self.portal.put('/api/portal/details-progress',json=data).status_code,409)
        self.invite();self.assertEqual(self.portal.get('/api/portal/details-progress').json()['draft']['fields']['inventory'],'New wardrobe')
        self.assertNotIn('draft',self.sales.get('/api/leads/'+self.lid+'/detail-requests').json())

    def test_progress_rejects_unsupported_fields_and_cross_version_overwrite(self):
        self.invite()
        self.assertEqual(self.portal.put('/api/portal/details-progress',json={'version':1,'revision':0,'fields':{'stage':'Booked'}}).status_code,422)
        self.assertEqual(self.portal.put('/api/portal/details-progress',json={'version':2,'revision':0,'fields':{'inventory':'Wrong version'}}).status_code,409)
        self.assertEqual(self.portal.put('/api/portal/details-progress',json={'version':1,'revision':0,'fields':{'inventory':123}}).status_code,422)

    def test_submitted_details_clear_draft_create_review_and_preserve_accepted_terms(self):
        self.request();self.finalize();self.invite()
        self.portal.put('/api/portal/details-progress',json={'version':1,'revision':0,'fields':{'inventory':'Two desks'}})
        r=self.portal.post('/api/portal/changes',json={'version':1,'fields':{'inventory':'Two desks','pickup':'100 Test Street'}});self.assertEqual(r.status_code,200,r.text)
        project=self.portal.get('/api/portal/project').json();self.assertIsNone(project['details_progress']['draft']);self.assertEqual(project['details_progress']['request']['status'],'Submitted');self.assertTrue(project['quotes'][0]['stale'])
        change=self.sales.get('/api/client-changes').json()[0];self.assertEqual(json.loads(change['payload_json']),{'inventory':'Two desks'})
        self.assertEqual(self.sales.post('/api/client-changes/'+change['id']+'/review',json={'decision':'Approve','lead_version':1}).status_code,200)
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db,'SELECT inventory FROM leads WHERE id=?',(self.lid,))['inventory'],'Two desks')
            self.assertEqual(json.loads(one(db,'SELECT lead_snapshot_json FROM quotes WHERE id=?',(self.qid,))['lead_snapshot_json'])['inventory'],'Desk and boxes')

    def test_confirming_unchanged_details_does_not_invalidate_quote(self):
        request=self.request().json();self.finalize();self.invite()
        r=self.portal.post('/api/portal/changes',json={'version':1,'fields':{'inventory':'Desk and boxes'}});self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(self.portal.get('/api/portal/project').json()['quotes'][0]['stale'])
        self.assertEqual(self.sales.post('/api/leads/'+self.lid+'/detail-requests/'+request['id']+'/complete',json={}).status_code,200)

    def test_cannot_complete_request_before_pending_changes_reviewed(self):
        request=self.request().json();self.invite();self.portal.post('/api/portal/changes',json={'version':1,'fields':{'inventory':'Piano'}})
        self.assertEqual(self.sales.post('/api/leads/'+self.lid+'/detail-requests/'+request['id']+'/complete',json={}).status_code,409)

    def test_review_conflicting_scope_requires_explicit_comparison_and_note(self):
        self.invite();self.portal.post('/api/portal/changes',json={'version':1,'fields':{'inventory':'Piano'}})
        change=self.sales.get('/api/client-changes').json()[0]
        self.sales.patch('/api/leads/'+self.lid,json={'version':1,'inventory':'New team note'})
        url='/api/client-changes/'+change['id']+'/review';data={'decision':'Approve','lead_version':2}
        self.assertEqual(self.sales.post(url,json=data).status_code,409)
        self.assertEqual(self.sales.post(url,json={**data,'reconcile':True,'note':'Compared against current inventory with client.'}).status_code,200)

    def test_view_is_scoped_deduplicated_and_recorded_only_by_explicit_view_event(self):
        self.finalize();self.invite();self.portal.get('/api/portal/project')
        self.assertEqual(self.sales.get('/api/sales/overview').json()['quote_delivery'][self.qid]['opens'],0)
        r=self.portal.post('/api/portal/quotes/'+self.qid+'/view',json={});self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['duplicate'])
        self.assertTrue(self.portal.post('/api/portal/quotes/'+self.qid+'/view',json={}).json()['duplicate'])
        self.assertEqual(self.portal.post('/api/portal/quotes/not-this-client/view',json={}).status_code,404)
        self.assertEqual(self.sales.get('/api/sales/overview').json()['quote_delivery'][self.qid]['opens'],1)
        self.assertEqual(self.sales.get('/api/crm/quotes/'+self.qid).json()['delivery']['opens'],1)

    def test_quote_ready_sent_accepted_states_are_distinct_and_view_notifies_once(self):
        mail=self.finalize();self.invite()
        def summary():return self.sales.get('/api/sales/overview').json()['quote_delivery'][self.qid]
        self.assertEqual(summary()['state'],'Ready')
        with transaction(self.path) as db:db.execute("UPDATE outbound_messages SET status='Sent',sent_at=? WHERE id=?",(now(),mail['mail_id']))
        self.assertEqual(summary()['state'],'Sent');self.portal.post('/api/portal/quotes/'+self.qid+'/view',json={});self.portal.post('/api/portal/quotes/'+self.qid+'/view',json={})
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks WHERE legacy_id=?',('quote-view:'+self.qid,)).fetchone()[0],1)
        self.sales.post('/api/quotes/'+self.qid+'/accept',json={'reference':'Fictional acceptance'});self.assertEqual(summary()['state'],'Accepted')

    def test_daily_suggestions_prioritize_callback_and_show_reason_without_sending(self):
        self.invite();self.portal.post('/api/portal/callbacks',json={'request_key':'daily-callback-test','phone':'5145550123','preferred_time':'Tomorrow','message':'Packing'})
        overview=self.sales.get('/api/sales/overview').json();self.assertEqual(overview['suggestions'][0]['action'],'Return requested call');self.assertIn('client requested',overview['suggestions'][0]['reason'])
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM outbound_messages').fetchone()[0],0)

    def test_connection_failure_is_actionable_and_does_not_expose_provider_error(self):
        from app.automatic_intake import run_intake
        with patch('app.providers.provider_token',side_effect=RuntimeError('secret-provider-token')):
            result=run_intake(self.path,lambda *args:None,force=True)
        self.assertEqual(result['error_code'],'gmail_connection');self.assertIn('Connections',result['error']);self.assertNotIn('secret',dumps(result))
