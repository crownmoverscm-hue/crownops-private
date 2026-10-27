"""Disposable databases; every provider request mocked. Never touches Estimate185."""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import patch
import requests
from app.store import connect, transaction, one, rows
from app.platform_common import settings, put_setting, decrypt
from app import sandbox_estimate_send as delivery
from app import sandbox_estimate_observation as observation
from app import quote_staging as scope
import test_sandbox_revision_reconciliation as fixtures


def response(body, status=200):
    r = requests.Response(); r.status_code = status
    r._content = json.dumps(body).encode()
    r.headers.update({'intuit_tid':'TEST-TRACE-ONLY', 'Date':'TEST-UTC', 'Set-Cookie':'SECRET', 'Authorization':'SECRET'})
    return r


class PostSendTests(unittest.TestCase):
    def setUp(self):
        self.r=fixtures.RevisionReconciliationTests();self.r.setUp();self.addCleanup(self.r.doCleanups)
        self.f=self.r.f;self.path=self.r.path;self.client=self.r.client
        self.r.apply();self.data=self.f.approve_simulated_send()
    def saved(self):
        with closing(connect(self.path)) as db:
            return {t:rows(db,'SELECT * FROM '+t+' ORDER BY rowid') for t in ('quotes','leads','client_accounts','external_records','qbo_estimate_deliveries','sandbox_revision_reconciliations','audit')}
    def sent(self):
        def post(*a,**kw):
            self.assertEqual(kw['params']['sendTo'],delivery.RECIPIENT)
            self.assertEqual(kw['data'],b'')
            self.f.remote.update(EmailStatus='EmailSent',BillEmail={'Address':delivery.RECIPIENT},SyncToken='1',
                                 DeliveryInfo={'DeliveryType':'Email','DeliveryTime':'TEST-UTC'},MetaData={'LastUpdatedTime':'TEST-UTC'})
            return response({'Estimate':deepcopy(self.f.remote)})
        with patch('app.sandbox_estimate_send.requests.post',side_effect=post) as send:
            result=self.client.post(self.f.base+'/sandbox-send',json=self.data)
            self.assertEqual(result.status_code,200,result.text);self.assertEqual(send.call_count,1)
    def test_completed_send_preserves_history_and_observation_does_not_grant_send(self):
        original=self.saved();self.sent();after_send=self.saved()
        for table in original:
            if table!='audit':self.assertEqual(original[table],after_send[table])
        p=self.f.review();self.assertTrue(p['postsend']['historical_authorization']['authorization_consistent'])
        self.assertEqual(p['postsend']['historical_authorization']['evidence'],'Captured at claim')
        self.assertTrue(p['postsend']['current_integrity']['valid'],p['postsend'])
        self.assertIn('Provider reports sent',p['postsend']['delivery_label'])
        self.assertFalse(p['can_send']);self.assertFalse(p['inbox_delivery_verified'])
        self.assertEqual(self.saved(),after_send)
        with patch('app.sandbox_estimate_send.qbo_read') as read,patch('requests.post') as post:
            self.assertEqual(self.client.post(self.f.base+'/sandbox-send',json=self.data).status_code,409)
            read.assert_not_called();post.assert_not_called()
        for t in ('jobs','payments','outbound_messages'):self.assertEqual(self.f.f.count(t),0)
    def test_legacy_completed_attempt_is_reconstructed_without_rewriting_it(self):
        self.sent()
        with transaction(self.path) as db:
            slot=settings(db,delivery.SEND_SLOT);slot.pop('authorization_at_claim');put_setting(db,delivery.SEND_SLOT,slot)
        before=self.saved()
        p=self.f.review()['postsend']
        self.assertTrue(p['historical_authorization']['authorization_consistent'])
        self.assertIn('Reconstructed',p['historical_authorization']['evidence'])
        self.assertTrue(p['current_integrity']['valid'],p)
        self.assertEqual(self.saved(),before)
    def test_later_accepted_status_is_only_observed_and_never_applied(self):
        self.sent();before=self.saved()
        self.f.remote.update(TxnStatus='Accepted',SyncToken='2')
        p=self.f.review();self.assertEqual(p['provider_status'],'Accepted')
        self.assertTrue(p['postsend']['current_integrity']['valid'],p['postsend'])
        self.assertFalse(p['customer_click_verified']);self.assertFalse(p['gates']['acceptance'])
        self.assertEqual(self.saved(),before)
        for route in ('/accept','/quickbooks-delivery/sync','/booking-workflow/sync-acceptance'):
            self.assertEqual(self.client.post(self.f.base+route,json={}).status_code,409)
    def test_material_provider_changes_rejected(self):
        self.sent();original=deepcopy(self.f.remote)
        for key,value in [('TotalAmt',600),('CustomerRef',{'value':'71'}),('BillEmail',{'Address':'other@example.invalid'}),('CustomerMemo',{'value':'Changed terms'})]:
            with self.subTest(key=key):
                self.f.remote.clear();self.f.remote.update(deepcopy(original));self.f.remote[key]=value
                self.assertEqual(self.client.get(self.f.base+'/sandbox-send-preflight').status_code,409)
    def test_unknown_provider_fields_or_customer_identity_changes_require_review(self):
        self.sent();self.f.remote['NewBusinessField']='changed'
        self.assertFalse(self.f.review()['postsend']['current_integrity']['valid'])
        self.f.remote.pop('NewBusinessField');self.r.customer['DisplayName']='Changed identity'
        self.assertFalse(self.f.review()['postsend']['current_integrity']['valid'])
    def test_local_r4_invalidates_current_integrity_but_preserves_historical_authorization(self):
        self.sent()
        with transaction(self.path) as db:
            q=one(db,'SELECT * FROM quotes WHERE id=?',(scope.QUOTE,));q.update(id='DISPOSABLE-r4',revision=4)
            from app.store import insert
            insert(db,'quotes',q)
        p=self.f.review()['postsend'];self.assertFalse(p['current_integrity']['valid'])
        self.assertTrue(p['historical_authorization']['authorization_consistent'])
        self.assertFalse(self.f.review()['can_send'])
    def test_encrypted_exchange_is_allowlisted_and_append_only(self):
        self.sent()
        with closing(connect(self.path)) as db:
            row=one(db,'SELECT * FROM sandbox_provider_exchanges WHERE kind=\'SEND\'')
            value=decrypt(row['ciphertext'])
            self.assertEqual(value['http_status'],200);self.assertEqual(value['intuit_tid'],'TEST-TRACE-ONLY')
            self.assertNotIn('SECRET',json.dumps(value));self.assertNotIn('TEST-TRACE',row['ciphertext'])
        for sql in ('UPDATE sandbox_provider_exchanges SET kind=\'SEND\'','DELETE FROM sandbox_provider_exchanges'):
            with self.assertRaises(sqlite3.IntegrityError):
                with transaction(self.path) as db:db.execute(sql)
    def test_http_error_and_malformed_json_retain_trace_without_retry(self):
        for malformed,status in ((False,400),(True,200)):
            with self.subTest(malformed=malformed):
                # A fresh isolated setup for each consumed-slot test.
                other=PostSendTests();other.setUp()
                try:
                    res=response({'Fault':{'Error':[{'code':'TEST','Message':'SECRET'}]}},status)
                    if malformed:res._content=b'NOT JSON SECRET'
                    with patch('app.sandbox_estimate_send.requests.post',return_value=res) as post:
                        self.assertEqual(other.client.post(other.f.base+'/sandbox-send',json=other.data).status_code,502)
                        self.assertEqual(other.client.post(other.f.base+'/sandbox-send',json=other.data).status_code,409)
                        self.assertEqual(post.call_count,1)
                    with closing(connect(other.path)) as db:
                        e=decrypt(one(db,'SELECT * FROM sandbox_provider_exchanges WHERE kind=\'SEND\'')['ciphertext'])
                        self.assertEqual(e['http_status'],status);self.assertEqual(e['intuit_tid'],'TEST-TRACE-ONLY')
                        self.assertNotIn('SECRET',json.dumps(e))
                finally:other.doCleanups()
    def test_timeout_or_evidence_storage_failure_consumes_slot(self):
        with patch('app.sandbox_estimate_send.requests.post',side_effect=requests.Timeout('SECRET')) as post:
            self.assertEqual(self.client.post(self.f.base+'/sandbox-send',json=self.data).status_code,502)
            self.assertEqual(post.call_count,1)
        with closing(connect(self.path)) as db:self.assertEqual(settings(db,delivery.SEND_SLOT)['state'],'Unknown')
        self.assertEqual(self.client.post(self.f.base+'/sandbox-send',json=self.data).status_code,409)
    def test_telemetry_storage_failure_leaves_claim_unknown_and_does_not_repeat_post(self):
        with patch('app.sandbox_estimate_send.requests.post',return_value=response({'Estimate':deepcopy(self.f.remote)})) as post,patch('app.sandbox_estimate_observation.retain_exchange',side_effect=sqlite3.OperationalError('SIMULATED')):
            self.assertEqual(self.client.post(self.f.base+'/sandbox-send',json=self.data).status_code,502)
            self.assertEqual(self.client.post(self.f.base+'/sandbox-send',json=self.data).status_code,409)
            self.assertEqual(post.call_count,0)
        with closing(connect(self.path)) as db:self.assertEqual(settings(db,delivery.SEND_SLOT)['state'],'Unknown')
    def test_post_response_retention_failure_never_retries_an_already_requested_send(self):
        original=observation.retain_exchange
        def fail_after_post(path,actor,kind,evidence):
            if kind=='SEND':raise sqlite3.OperationalError('SIMULATED')
            return original(path,actor,kind,evidence)
        with patch('app.sandbox_estimate_send.requests.post',return_value=response({'Estimate':deepcopy(self.f.remote)})) as post,patch('app.sandbox_estimate_observation.retain_exchange',side_effect=fail_after_post):
            self.assertEqual(self.client.post(self.f.base+'/sandbox-send',json=self.data).status_code,502)
            self.assertEqual(self.client.post(self.f.base+'/sandbox-send',json=self.data).status_code,409)
            self.assertEqual(post.call_count,1)
    def test_owner_retirement_does_not_erase_historical_identity_or_grant_send(self):
        self.sent()
        with transaction(self.path) as db:db.execute("UPDATE users SET active=0 WHERE id='owner'")
        p=delivery.preflight(self.path,scope.QUOTE)
        self.assertTrue(p['reconciliation']['historical_intact']);self.assertFalse(p['reconciliation']['valid'])
        self.assertTrue(p['postsend']['historical_authorization']['authorization_consistent'])
        self.assertFalse(p['can_send'])
    def test_raw_readback_one_get_no_record_changes_and_no_secret_headers(self):
        before=self.saved()
        raw=deepcopy(self.f.remote);raw.update(DeliveryInfo={'DeliveryType':'Email','DeliveryTime':'TEST','DeliveryErrorType':'TEST bounce'},MetaData={'LastUpdatedTime':'TEST'})
        raw['CustomerMemo']={'value':'PRIVATE'}
        with patch('app.sandbox_estimate_observation.requests.get',return_value=response({'Estimate':raw})) as get,patch('requests.post') as post:
            r=self.client.get(self.f.base+'/sandbox-provider-readback');self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(get.call_count,1);post.assert_not_called()
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
        data=r.json();self.assertEqual(data['operation'],'GET');self.assertEqual(data['http_status'],200)
        self.assertEqual(data['intuit_tid'],'TEST-TRACE-ONLY');self.assertNotIn('PRIVATE',json.dumps(data));self.assertNotIn('SECRET',json.dumps(data))
        self.assertEqual(data['sanitized_response']['Estimate']['DeliveryInfo']['DeliveryErrorType'],'TEST bounce')
        self.assertEqual(self.saved(),before);self.assertEqual(self.f.f.count('sandbox_provider_exchanges'),0)
    def test_raw_readback_denies_staff_wrong_quote_and_environment_before_provider_call(self):
        with patch('app.sandbox_estimate_observation.requests.get') as get:
            self.client.cookies.set('crownops_session','sales-token')
            self.assertEqual(self.client.get(self.f.base+'/sandbox-provider-readback').status_code,403)
            self.client.cookies.set('crownops_session','owner-token')
            self.assertEqual(self.client.get('/api/quotes/OTHER/sandbox-provider-readback').status_code,404)
            with patch.dict('os.environ',{'QBO_ENV':'production'}):
                self.assertEqual(self.client.get(self.f.base+'/sandbox-provider-readback').status_code,503)
            get.assert_not_called()

if __name__=='__main__':unittest.main()
