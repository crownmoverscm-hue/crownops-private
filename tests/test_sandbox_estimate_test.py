"""Create-only policy contracts. All provider responses are simulated."""
from contextlib import closing
from copy import deepcopy
import json
import os
import unittest
from unittest.mock import patch
from app.store import connect, transaction, insert, now, one
from app.platform_common import put_setting, settings
from app.main import sha
from app import sandbox_estimate_test as test_gate
from test_template_quotes import TemplateQuoteTests as Fixture


class SandboxEstimateTest(unittest.TestCase):
    def setUp(self):
        self.f=Fixture();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path,self.client=self.f.path,self.f.client
        self.env={'CROWNOPS_QUOTE_TEST_FIXTURE':'2026-11-15','CROWNOPS_ENV':'staging','QBO_ENV':'sandbox',
            'QBO_SANDBOX_REALM_ID':'9341458310238189','CROWNOPS_PUBLIC_URL':'https://crownops-quote-acceptance-staging.up.railway.app',
            'CROWNOPS_DB':'/data/crownops-quote-acceptance.sqlite3','RAILWAY_SERVICE_ID':'5de22c3a-ea7e-4fa6-bc96-2f9472431c7c',
            'RAILWAY_ENVIRONMENT_ID':'53900324-c678-40b0-91f0-b6560316a5d2',
            **{k:'0' for k in ('CROWNOPS_ALLOW_EXTERNAL','CROWNOPS_ALLOW_QBO_SANDBOX_WRITES','CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES','CROWNOPS_ALLOW_QBO_ESTIMATE_DELIVERY','CROWNOPS_ALLOW_QBO_INVOICE_DELIVERY','CROWNOPS_ALLOW_QBO_RECEIPT_SYNC','CROWNOPS_GMAIL_QBO_SANDBOX_WORKFLOW','CROWNOPS_RUN_WORKER')}}
        self.f.data.update(hourly_rate=100,transport_mode='local_amount',fixed_transport=150,local_fuel_rate=35.99,expiry_date='2026-11-14')
        with patch.dict(os.environ,self.env):
            r=self.f.save();self.assertEqual(r.status_code,200,r.text);self.q=r.json()
        self.qid=self.q['id'];self.env['CROWNOPS_QBO_TEST_CREATE_QUOTE']=self.qid
        env=patch.dict(os.environ,self.env);env.start();self.addCleanup(env.stop)
        with transaction(self.path) as db:
            insert(db,'users',dict(id='owner',email='owner@example.invalid',name='Test Owner',role='owner',password_hash='unused',active=1,created_at=now()))
            insert(db,'sessions',dict(token_hash=sha('owner-token'),user_id='owner',csrf='owner-csrf',expires_at='2099-01-01T00:00:00+00:00'))
            put_setting(db,'quickbooks_mappings_sandbox',{'service_item_id':'1','tax_code_id':'2'})
        self.client.cookies.set('crownops_session','owner-token');self.client.headers['x-csrf-token']='owner-csrf'
        self.remote=None;self.writes=[];self.bad_country=False;self.fail_resource=None
        self.existing=True
        self.patches=[patch('app.sandbox_estimate_test._qbo_connection',return_value=({'access_token':'test'},'9341458310238189','https://sandbox-quickbooks.api.intuit.com')),
            patch('app.sandbox_estimate_test.qbo_read',side_effect=self.read),
            patch('app.sandbox_estimate_test.qbo_query',side_effect=lambda *a:{'Customer':[self.customer()]} if self.existing else {}),
            patch('app.sandbox_estimate_test._post',side_effect=self.post)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.base='/api/quotes/'+self.qid+'/quickbooks-delivery'

    def customer(self):return {'Id':'10','DisplayName':'TEST Template Client | '+self.q['lead_id'][-12:],'PrimaryEmailAddr':{'Address':'template@example.invalid'}}
    def read(self,path,resource):
        if resource.startswith('companyinfo/'):return {'CompanyInfo':{'Id':'1','CompanyName':'Sandbox Company CA 5634','Country':'US' if self.bad_country else 'CA'}}
        if resource.startswith('item/'):return {'Item':{'Id':'1','Active':True,'Type':'Service'}}
        if resource.startswith('taxcode/'):return {'TaxCode':{'Id':'2','Active':True,'SalesTaxRateList':{'TaxRateDetail':[{'TaxRateRef':{'value':'5'}},{'TaxRateRef':{'value':'9'}}]}}}
        if resource.startswith('taxrate/'):return {'TaxRate':{'RateValue':5 if resource.endswith('/5') else 9.975}}
        if resource.startswith('estimate/'):return {'Estimate':deepcopy(self.remote)}
        if resource.startswith('customer/'):return {'Customer':self.customer()}
        raise AssertionError(resource)
    def post(self,path,resource,body,key):
        self.writes.append(resource)
        if self.fail_resource==resource:raise TimeoutError('fictional provider timeout')
        if resource=='customer':return {'Customer':self.customer()}
        self.remote=deepcopy(body);self.remote.update(Id='88',SyncToken='0',TotalAmt=558.77,
            TxnTaxDetail={'TotalTax':72.78,'TaxLine':[{'Amount':24.30},{'Amount':48.48}]})
        return {'Estimate':deepcopy(self.remote)}
    def review(self):
        r=self.client.get(self.base);self.assertEqual(r.status_code,200,r.text);return r.json()
    def create(self,**extra):
        p=self.review();return self.client.post(self.base+'/prepare',json={'confirm':True,'review_hash':p['review_hash'],**extra})

    def test_preflight_exact_amounts_parent_history_and_separate_terms(self):
        p=self.review();self.assertEqual(p['total_cents'],55877);self.assertEqual(p['service_id'],self.q['lead_id'])
        self.assertEqual(p['revisions'][0]['id'],self.qid);self.assertEqual(p['terms']['version'],'TEST-ONLY-2026-10-26')
        self.assertEqual(self.writes,[])
        with closing(connect(self.path)) as db:self.assertFalse((settings(db,'client_terms',{}) or {}).get('approved'))

    def test_identical_edit_retains_id_and_changed_edit_has_same_parent(self):
        data=deepcopy(self.f.data);data.pop('new_client');data.pop('service_details')
        data.update(lead_id=self.q['lead_id'],lead_version=self.q['lead_version'],source_quote_id=self.qid,draft_key='reopen-same')
        same=self.client.post('/api/crm/quotes',json=data);self.assertEqual(same.status_code,200,same.text)
        self.assertEqual(same.json()['id'],self.qid);self.assertEqual(self.f.count('quotes'),1)
        data.update(client_note='Changed fictional note',draft_key='reopen-changed')
        changed=self.client.post('/api/crm/quotes',json=data);self.assertEqual(changed.status_code,200,changed.text)
        self.assertNotEqual(changed.json()['id'],self.qid);self.assertEqual(changed.json()['lead_id'],self.q['lead_id'])
        family=self.client.get('/api/crm/quotes/'+changed.json()['id']).json()['quote_family']
        self.assertEqual([q['revision'] for q in family['revisions']],[1,2]);self.assertEqual(self.f.count('leads'),1)

    def test_one_estimate_readback_retry_reuses_id_no_sending_or_booking(self):
        r=self.create();self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['external_id'],'88')
        again=self.client.post(self.base+'/prepare',json={'confirm':True});self.assertEqual(again.status_code,200,again.text)
        self.assertEqual(self.writes,['estimate'])
        self.assertNotIn('Accept this estimate',self.remote['CustomerMemo']['value'])
        self.assertEqual(self.remote['Line'][0]['SalesItemLineDetail']['Qty'],3)
        self.assertEqual(self.remote['EmailStatus'],'NotSet')
        for table in ('jobs','outbound_messages','payments'):self.assertEqual(self.f.count(table),0)
        self.assertEqual(self.client.post(self.base+'/send',json={'confirm':True}).status_code,409)
        self.assertEqual(self.client.post(self.base+'/sync',json={}).status_code,409)
        self.assertEqual(self.client.post('/api/quotes/'+self.qid+'/accept',json={'reference':'TEST'}).status_code,409)

    def test_missing_customer_requires_explicit_confirmation_and_creates_only_once(self):
        self.existing=False
        self.assertEqual(self.create().status_code,409);self.assertEqual(self.writes,[])
        r=self.create(confirm_customer_create=True);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.writes,['customer','estimate'])
        self.assertEqual(self.client.post(self.base+'/prepare',json={'confirm':True}).status_code,200)
        self.assertEqual(self.writes,['customer','estimate'])

    def test_estimate_timeout_consumes_attempt(self):
        self.fail_resource='estimate';self.assertEqual(self.create().status_code,502)
        self.assertEqual(self.client.post(self.base+'/prepare',json={'confirm':True}).status_code,409)
        self.assertEqual(self.writes,['estimate'])

    def test_customer_timeout_consumes_attempt(self):
        self.existing=False;self.fail_resource='customer';self.assertEqual(self.create(confirm_customer_create=True).status_code,502)
        self.assertEqual(self.client.post(self.base+'/prepare',json={'confirm':True,'confirm_customer_create':True}).status_code,409)
        self.assertEqual(self.writes,['customer'])

    def test_wrong_country_blocks_before_mutation(self):
        self.bad_country=True;r=self.client.get(self.base);self.assertEqual(r.status_code,409,r.text);self.assertEqual(self.writes,[])

    def test_wrong_service_environment_database_and_broad_gates_block(self):
        changes=({'CROWNOPS_ENV':'production'},{'QBO_ENV':'production'},{'QBO_SANDBOX_REALM_ID':'123'},
            {'RAILWAY_SERVICE_ID':'wrong'},{'RAILWAY_ENVIRONMENT_ID':'wrong'},{'CROWNOPS_DB':'/data/production.sqlite3'},
            {'CROWNOPS_ALLOW_QBO_ESTIMATE_DELIVERY':'1'},{'CROWNOPS_ALLOW_QBO_SANDBOX_WRITES':'1'}, {'CROWNOPS_RUN_WORKER':'1'})
        for change in changes:
            with self.subTest(change=change),patch.dict(os.environ,change):
                self.assertEqual(self.client.get(self.base).status_code,503)
        self.assertEqual(self.writes,[])

    def test_roles_cannot_execute_one_estimate_owner_permission(self):
        for role in ('sales','crew'):
            self.client.cookies.set('crownops_session',role+'-token');self.client.headers['x-csrf-token']=role+'-csrf'
            self.assertEqual(self.client.post(self.base+'/prepare',json={'confirm':True}).status_code,403)
            self.assertEqual(self.client.get('/api/quotes/'+self.qid+'/sandbox-test-preflight').status_code,403)
        self.assertEqual(self.writes,[])

    def test_changed_review_hash_is_rejected(self):
        self.assertEqual(self.client.post(self.base+'/prepare',json={'confirm':True,'review_hash':'wrong'}).status_code,409)
        self.assertEqual(self.writes,[])

    def test_existing_export_on_any_revision_blocks(self):
        with transaction(self.path) as db:
            insert(db,'external_records',dict(provider='quickbooks',kind='Estimate',object_id=self.qid,external_id='OLD',updated_at=now()))
        self.assertEqual(self.client.get(self.base).status_code,409);self.assertEqual(self.writes,[])

    def test_unapproved_business_terms_still_required_outside_test_quote(self):
        with patch.dict(os.environ,{'CROWNOPS_QBO_TEST_CREATE_QUOTE':''}):
            from app.estimate_delivery import source
            from fastapi import HTTPException
            with closing(connect(self.path)) as db:
                with self.assertRaises(HTTPException) as cm:source(db,self.qid)
                self.assertEqual(cm.exception.status_code,409)

    def test_readback_mismatch_does_not_create_replacement(self):
        original=self.post
        def bad(*args):
            result=original(*args)
            if args[1]=='estimate':self.remote['TotalAmt']=559.77
            return result
        with patch('app.sandbox_estimate_test._post',side_effect=bad):self.assertEqual(self.create().status_code,502)
        with closing(connect(self.path)) as db:self.assertEqual(settings(db,test_gate.SLOT)['external_id'],'88')
        self.assertEqual(self.client.post(self.base+'/prepare',json={'confirm':True}).status_code,409)
        self.assertEqual(self.writes,['estimate'])
