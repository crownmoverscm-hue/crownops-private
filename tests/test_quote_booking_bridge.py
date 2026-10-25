"""Real application routes with simulated providers; NOT live provider proof."""
from contextlib import closing
from copy import deepcopy
import json
import unittest
from unittest.mock import patch,Mock
from datetime import datetime,timedelta,timezone
from fastapi.testclient import TestClient
from app.store import connect,transaction,one,rows,insert,now,dumps
from app.platform_common import put_setting
from app.sender_proof import identity_key
import test_estimate_delivery as fixture


class QuoteBookingBridgeTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.EstimateDeliveryTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path,self.client,self.qid=self.f.path,self.f.client,self.f.qid
        self.base='/api/quotes/'+self.qid+'/booking-workflow'
        env=patch.dict('os.environ',{'CROWNOPS_ENV':'staging','CROWNOPS_GMAIL_QBO_SANDBOX_WORKFLOW':'1','QBO_ENV':'sandbox','CROWNOPS_ALLOW_EXTERNAL':'1'})
        env.start();self.addCleanup(env.stop)
        self.accept_writes=0
        def provider(path,method,resource,body,key):
            if body.get('sparse'):
                self.accept_writes+=1;self.f.remote.update(body);self.f.remote['SyncToken']='1'
                return {'Estimate':deepcopy(self.f.remote)}
            return self.f.create(path,method,resource,body,key)
        for mock in (patch('app.estimate_delivery.qbo_call',side_effect=provider),patch('app.providers.qbo_call',side_effect=provider),patch('app.providers.qbo_read',side_effect=self.f.read)):
            mock.start();self.addCleanup(mock.stop)
        with transaction(self.path) as db:
            cfg={'connected_mailbox':'mailbox@example.invalid','send_as':'info@example.invalid','reply_to':'info@example.invalid','approved':True}
            put_setting(db,'company_communications',cfg)
            put_setting(db,'company_sender_proof',{'state':'Sending enabled','identity_hash':identity_key(cfg),'actual_from':cfg['send_as'],'actual_reply_to':cfg['reply_to']})

    def prepare_review(self):
        r=self.client.post(self.base+'/prepare',json={'confirm':True,'basis':'high'})
        self.assertEqual(r.status_code,200,r.text)
        r=self.client.get(self.base+'/preview');self.assertEqual(r.status_code,200,r.text)
        preview=r.json()
        r=self.client.post(self.base+'/review',json={'confirm':True,'review_hash':preview['review_hash']})
        self.assertEqual(r.status_code,200,r.text)
        return r.json(),preview

    def test_complete_local_journey_one_estimate_one_mail_one_tentative_job(self):
        reviewed,preview=self.prepare_review()
        self.assertEqual(preview['external_id'],'88');self.assertEqual(self.f.create_count,1);self.assertEqual(self.f.send_count,0)
        with closing(connect(self.path)) as db:
            self.assertEqual(rows(db,'SELECT status FROM outbound_messages')[0]['status'],'Draft')
        with patch('app.providers.send_gmail',return_value='TEST-MAIL-ID') as send:
            result=self.client.post(self.base+'/send-email',json={'confirm':True,'review_hash':preview['review_hash']})
            self.assertEqual(result.status_code,200,result.text);self.assertTrue(result.json()['sent'])
            self.client.post(self.base+'/send-email',json={'confirm':True,'review_hash':preview['review_hash']})
            self.assertEqual(send.call_count,1)
        # Authenticated fictional portal session, scoped to this service only.
        from app.platform_common import stamp_hash
        with transaction(self.path) as db:
            insert(db,'portal_sessions',dict(token_hash=stamp_hash('test-client'),lead_id=self.f.q['lead_id'],email='template@example.invalid',csrf='test-client-csrf',created_at=now(),expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
        client=TestClient(self.f.f.app);self.addCleanup(client.close)
        client.cookies.set('crownops_client','test-client');client.headers['x-csrf-token']='test-client-csrf'
        accept={'signed_name':'TEST Client','terms_version':'TEST-1','agree':True}
        r=client.post('/api/portal/quotes/'+self.qid+'/accept',json=accept)
        self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['booking_confirmed'])
        again=client.post('/api/portal/quotes/'+self.qid+'/accept',json=accept)
        self.assertEqual(again.status_code,200,again.text)
        r=self.client.post(self.base+'/sync-acceptance',json={'confirm':True});self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['acceptance']['quickbooks_sync'],'Synced')
        self.client.post(self.base+'/sync-acceptance',json={'confirm':True})
        self.assertEqual(self.accept_writes,1);self.assertEqual(self.f.send_count,0)
        with closing(connect(self.path)) as db:
            jobs=rows(db,'SELECT * FROM jobs');self.assertEqual(len(jobs),1);self.assertEqual(jobs[0]['status'],'Tentative')
            self.assertEqual(rows(db,'SELECT * FROM assignments'),[]);self.assertEqual(rows(db,'SELECT * FROM payments'),[])

    def test_qbo_email_path_is_blocked_for_combined_quotes(self):
        _,preview=self.prepare_review()
        r=self.client.post(self.f.base+'/send',json={'confirm':True,'recipient':'template@example.invalid','review_hash':preview['review_hash']})
        self.assertEqual(r.status_code,409);self.assertEqual(self.f.send_count,0)

    def test_unverified_sender_blocks_email_and_keeps_draft(self):
        _,preview=self.prepare_review()
        with transaction(self.path) as db:put_setting(db,'company_sender_proof',{'state':'Not verified'})
        with patch('app.providers.send_gmail') as send:
            r=self.client.post(self.base+'/send-email',json={'confirm':True,'review_hash':preview['review_hash']})
            self.assertEqual(r.status_code,409,r.text);send.assert_not_called()

    def test_compounded_tax_is_rejected_before_review(self):
        r=self.client.post(self.base+'/prepare',json={'confirm':True,'basis':'high'});self.assertEqual(r.status_code,200,r.text)
        self.f.remote['TxnTaxDetail']['TaxLine'][1]['Amount']+=1
        self.f.remote['TxnTaxDetail']['TotalTax']+=1;self.f.remote['TotalAmt']+=1
        r=self.client.get(self.base+'/preview');self.assertEqual(r.status_code,409,r.text)
        self.assertIn('TPS 5%',r.json()['detail'])

    def test_rollout_flag_cannot_enable_combined_path_in_production(self):
        with patch.dict('os.environ',{'CROWNOPS_ENV':'production'}):
            r=self.client.post(self.base+'/prepare',json={'confirm':True,'basis':'high'})
        self.assertEqual(r.status_code,503);self.assertEqual(self.f.create_count,0)

    def test_changed_booking_terms_block_email_draft_preparation(self):
        r=self.client.post(self.base+'/prepare',json={'confirm':True,'basis':'high'});self.assertEqual(r.status_code,200,r.text)
        preview=self.client.get(self.base+'/preview').json()
        with transaction(self.path) as db:put_setting(db,'client_terms',{'approved':True,'version':'TEST-2','text':'Changed fictional terms'})
        r=self.client.post(self.base+'/review',json={'confirm':True,'review_hash':preview['review_hash']})
        self.assertEqual(r.status_code,409,r.text)
        with closing(connect(self.path)) as db:self.assertEqual(rows(db,'SELECT * FROM outbound_messages'),[])


class SenderProofTests(unittest.TestCase):
    def setUp(self):
        import test_launch_workflow
        self.f=test_launch_workflow.LaunchWorkflowTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path,self.client=self.f.path,self.f.client
        self.cfg={'connected_mailbox':'mailbox@example.invalid','send_as':'info@example.invalid','reply_to':'info@example.invalid','approved':False}
        with transaction(self.path) as db:put_setting(db,'company_communications',self.cfg)

    def test_approval_requires_matching_actual_headers_and_identity(self):
        self.assertEqual(self.client.post('/api/company-sender/proof/approve',json={'confirm':True}).status_code,409)
        with transaction(self.path) as db:
            put_setting(db,'company_sender_proof',{'state':'Pending','identity_hash':identity_key(self.cfg),'message_id':'TEST-ID'})
        response=Mock();response.json.return_value={'id':'TEST-ID','payload':{'headers':[{'name':'From','value':'Crown Movers <info@example.invalid>'},{'name':'Reply-To','value':'info@example.invalid'}]}}
        with patch('app.providers.provider_token',return_value={'access_token':'mock'}),patch('app.sender_proof.requests.get',return_value=response):
            r=self.client.post('/api/company-sender/proof/check');self.assertEqual(r.json()['state'],'Verified')
        self.assertEqual(self.client.post('/api/company-sender/proof/approve',json={'confirm':True}).status_code,200)
        self.assertTrue(self.client.get('/api/company-sender/proof').json()['sending_enabled'])
        with transaction(self.path) as db:put_setting(db,'company_communications',{**self.cfg,'reply_to':'changed@example.invalid'})
        self.assertEqual(self.client.get('/api/company-sender/proof').json()['state'],'Not verified')

    def test_test_send_requires_explicit_approval_and_allowlist(self):
        with patch('app.providers.send_gmail') as send:
            r=self.client.post('/api/company-sender/proof/send-test',json={'request_id':'test-request-001','recipient':'unknown@example.invalid','confirm':True})
            self.assertEqual(r.status_code,403);send.assert_not_called()
