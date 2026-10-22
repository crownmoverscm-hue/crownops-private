"""Fictional QuickBooks contract tests. No provider, mailbox or payment is used."""
from contextlib import closing
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import json
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.store import connect, transaction, one, rows, dumps, now
from app.platform_common import put_setting
from app.estimate_delivery import poll_estimates
import test_template_quotes as fixture


class EstimateDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TemplateQuoteTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path=self.f.path;self.client=self.f.client
        self.f.data.update(expiry_date='2026-11-14',transport_mode='local_amount',fixed_transport=35,
                           materials=[{'code':'CUSTOM_BOX','name':'Packing box','qty':3,'unit_price':12,'price_override':True}],discount_amount=10)
        self.q=self.f.save().json();self.qid=self.q['id'];self.base='/api/quotes/'+self.qid+'/quickbooks-delivery'
        with transaction(self.path) as db:put_setting(db,'client_terms',{'approved':True,'version':'TEST-1','text':'TEST — MUSE. Fictional booking terms.'})
        self.remote=None;self.send_count=0;self.create_count=0
        mocks=[patch.dict('os.environ',{'CROWNOPS_ALLOW_QBO_ESTIMATE_DELIVERY':'1','CROWNOPS_ALLOW_QBO_SANDBOX_WRITES':'1',
            'QBO_ENV':'sandbox','CROWNOPS_TEST_EMAIL_ALLOWLIST':'template@example.invalid'}),
            patch('app.estimate_delivery._qbo_connection',return_value=({},'123','https://example.invalid')),
            patch('app.estimate_delivery.qbo_mapping',side_effect=lambda path,key:{'service_item_id':'1','tax_code_id':'2','discount_account_id':'3'}[key]),
            patch('app.estimate_delivery.qbo_customer',return_value='10'),
            patch('app.estimate_delivery.qbo_call',side_effect=self.create),
            patch('app.estimate_delivery.qbo_read',side_effect=self.read),
            patch('app.providers.qbo_send_estimate',side_effect=self.send)]
        for mock in mocks:mock.start();self.addCleanup(mock.stop)

    def create(self,path,method,resource,body,key):
        self.create_count+=1;self.remote=deepcopy(body)
        net=sum(Decimal(str(x['Amount']))*(-1 if x['DetailType']=='DiscountLineDetail' else 1) for x in body['Line'] if 'Amount' in x)
        gst=(net*Decimal('.05')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        qst=(net*Decimal('.09975')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        self.remote.update(Id='88',SyncToken='0',TotalAmt=float(net+gst+qst),TxnTaxDetail={'TotalTax':float(gst+qst),
            'TaxLine':[{'Amount':float(gst),'TaxLineDetail':{'TaxRateRef':{'value':'5'}}},{'Amount':float(qst),'TaxLineDetail':{'TaxRateRef':{'value':'9'}}}]})
        return {'Estimate':deepcopy(self.remote)}

    def read(self,path,resource):
        if resource.startswith('customer/'):
            return {'Customer':{'DisplayName':'TEST Template Client | '+self.q['lead_id'][-12:],'PrimaryEmailAddr':{'Address':'template@example.invalid'}}}
        if resource.startswith('taxrate/'):return {'TaxRate':{'Name':'TPS' if resource.endswith('/5') else 'TVQ'}}
        return {'Estimate':deepcopy(self.remote)}

    def send(self,path,eid,recipient):
        self.send_count+=1;self.remote['EmailStatus']='EmailSent';self.remote['SyncToken']='1'
        return {'Estimate':deepcopy(self.remote)}

    def prepare(self,basis='high'):
        r=self.client.post(self.base+'/prepare',json={'basis':basis,'confirm':True})
        self.assertEqual(r.status_code,200,r.text);return r.json()

    def deliver(self):
        self.prepare();r=self.client.get(self.base);self.assertEqual(r.status_code,200,r.text);preview=r.json()
        data={'confirm':True,'recipient':preview['recipient'],'review_hash':preview['review_hash']}
        r=self.client.post(self.base+'/send',json=data);self.assertEqual(r.status_code,200,r.text);return data

    def test_itemized_preview_and_send_acceptance_creates_one_unconfirmed_job(self):
        data=self.deliver();self.assertEqual(self.send_count,1)
        descriptions='\n'.join(x.get('Description','') for x in self.remote['Line'])
        for expected in ('Packing box','Transportation Fee','Fictional booking terms','100 Test Street'):
            self.assertIn(expected,descriptions)
        self.assertEqual(self.remote['ExpirationDate'],'2026-11-14')
        self.assertEqual(self.remote['Line'][0]['SalesItemLineDetail']['Qty'],4)
        self.assertEqual(self.remote['Line'][1]['Amount'],35)
        self.assertEqual(self.client.get(self.base).json()['taxes'][0]['name'],'TPS')
        self.assertTrue(self.client.post(self.base+'/send',json=data).json()['duplicate']);self.assertEqual(self.send_count,1)
        self.remote.update(TxnStatus='Accepted',AcceptedBy='TEST — MUSE Client',AcceptedDate='2026-10-21')
        first=self.client.post(self.base+'/sync');self.assertEqual(first.status_code,200,first.text)
        second=self.client.post(self.base+'/sync');self.assertEqual(second.json()['job_id'],first.json()['job_id'])
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db,'SELECT status FROM quotes WHERE id=?',(self.qid,))['status'],'Accepted')
            jobs=rows(db,'SELECT * FROM jobs');self.assertEqual(len(jobs),1);self.assertEqual(jobs[0]['status'],'Tentative')
            self.assertEqual(len(rows(db,'SELECT * FROM assignments')),0)
            self.assertEqual(len(rows(db,'SELECT * FROM payments')),0)

    def test_closed_date_keeps_acceptance_without_booking(self):
        self.deliver()
        with transaction(self.path) as db:
            db.execute("INSERT INTO schedule_days(day,status,reason,updated_by,updated_at) VALUES('2026-11-15','Fully booked','TEST','sales',?)",(now(),))
        self.remote['TxnStatus']='Accepted'
        r=self.client.post(self.base+'/sync');self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['state'],'Accepted');self.assertIsNone(r.json()['job_id']);self.assertIn('fully booked',r.json()['message'])
        with closing(connect(self.path)) as db:self.assertEqual(len(rows(db,'SELECT * FROM jobs')),0)

    def test_unknown_send_is_never_retried_and_can_reconcile(self):
        self.prepare();p=self.client.get(self.base).json()
        data={'confirm':True,'recipient':p['recipient'],'review_hash':p['review_hash']}
        with patch('app.providers.qbo_send_estimate',side_effect=TimeoutError) as send:
            self.assertEqual(self.client.post(self.base+'/send',json=data).status_code,502)
            self.assertEqual(self.client.post(self.base+'/send',json=data).status_code,409)
            self.assertEqual(send.call_count,1)
        self.remote['EmailStatus']='EmailSent'
        self.assertEqual(self.client.post(self.base+'/sync').json()['state'],'Sent')

    def test_changed_scope_recipient_or_remote_blocks_send(self):
        self.prepare();p=self.client.get(self.base).json();data={'confirm':True,'recipient':p['recipient'],'review_hash':p['review_hash']}
        self.assertEqual(self.client.post(self.base+'/send',json={**data,'recipient':'other@example.invalid'}).status_code,409)
        self.remote['Line'][0]['Description']='Changed service'
        self.assertEqual(self.client.post(self.base+'/send',json=data).status_code,409);self.assertEqual(self.send_count,0)

    def test_changed_terms_block_send_and_changed_tax_blocks_acceptance(self):
        self.prepare();p=self.client.get(self.base).json()
        with transaction(self.path) as db:put_setting(db,'client_terms',{'approved':True,'version':'TEST-2','text':'Changed terms'})
        self.assertEqual(self.client.post(self.base+'/send',json={'confirm':True,'recipient':p['recipient'],'review_hash':p['review_hash']}).status_code,409)
        self.assertEqual(self.send_count,0)

    def test_legacy_export_and_other_sender_do_not_duplicate(self):
        self.prepare();self.prepare();self.assertEqual(self.create_count,1)
        self.assertEqual(self.client.post('/api/quotes/'+self.qid+'/quickbooks',json={'basis':'high','confirm':True}).status_code,409)
        self.assertEqual(self.client.post('/api/crm/quotes/'+self.qid+'/finalize',json={'send_now':True}).status_code,409)

    def test_roles_feature_gate_and_allowlist(self):
        crew=TestClient(self.f.app);self.addCleanup(crew.close)
        crew.cookies.set('crownops_session','crew-token');crew.headers['x-csrf-token']='crew-csrf'
        for suffix in ('/prepare','/send','/sync'):
            self.assertEqual(crew.post(self.base+suffix,json={'confirm':True}).status_code,403)
        self.assertEqual(crew.get(self.base).status_code,403)
        with patch.dict('os.environ',{'CROWNOPS_ALLOW_QBO_ESTIMATE_DELIVERY':'0'}):
            self.assertEqual(self.client.post(self.base+'/prepare',json={'basis':'high','confirm':True}).status_code,503)
        self.prepare();p=self.client.get(self.base).json()
        with patch.dict('os.environ',{'CROWNOPS_TEST_EMAIL_ALLOWLIST':''}):
            self.assertEqual(self.client.post(self.base+'/send',json={'confirm':True,'recipient':p['recipient'],'review_hash':p['review_hash']}).status_code,403)
        self.assertEqual(self.send_count,0)

    def test_company_switch_or_remote_change_cannot_book(self):
        self.deliver();self.remote['TxnStatus']='Accepted'
        with patch('app.estimate_delivery._qbo_connection',return_value=({},'456','https://example.invalid')):
            self.assertEqual(self.client.post(self.base+'/sync').status_code,409)
        self.remote['CustomerRef']={'value':'999'}
        self.assertEqual(self.client.post(self.base+'/sync').status_code,409)
        with closing(connect(self.path)) as db:self.assertEqual(len(rows(db,'SELECT * FROM jobs')),0)

    def test_poll_reads_acceptance_without_sending_mail(self):
        self.deliver();self.remote['TxnStatus']='Accepted';poll_estimates(self.path)
        self.assertEqual(self.send_count,1)
        with closing(connect(self.path)) as db:self.assertEqual(len(rows(db,'SELECT * FROM jobs')),1)

    def test_remote_tax_change_after_send_blocks_booking(self):
        self.deliver();self.remote['TxnStatus']='Accepted'
        self.remote['TotalAmt']+=1;self.remote['TxnTaxDetail']['TotalTax']+=1
        self.assertEqual(self.client.post(self.base+'/sync').status_code,409)
        with closing(connect(self.path)) as db:self.assertEqual(len(rows(db,'SELECT * FROM jobs')),0)

    def test_decline_is_visible_and_never_creates_job(self):
        self.deliver();self.remote['TxnStatus']='Rejected'
        self.assertEqual(self.client.post(self.base+'/sync').json()['state'],'Rejected')
        listing=self.client.get('/api/documents/quotes').json()
        self.assertEqual(next(q for q in listing if q['id']==self.qid)['display_state'],'Declined')
        with closing(connect(self.path)) as db:self.assertEqual(len(rows(db,'SELECT * FROM jobs')),0)

    def test_expired_or_newer_quote_cannot_be_automatically_booked(self):
        self.deliver();self.remote['TxnStatus']='Accepted'
        with patch('app.estimate_delivery.expired_quote',return_value=True):
            self.assertEqual(self.client.post(self.base+'/sync').status_code,409)
        with closing(connect(self.path)) as db:self.assertEqual(len(rows(db,'SELECT * FROM jobs')),0)

    def test_only_one_service_job_when_another_revision_was_scheduled(self):
        self.deliver();self.remote['TxnStatus']='Accepted'
        with transaction(self.path) as db:
            db.execute("INSERT INTO jobs(id,lead_id,title,phase,start_at,end_at,status,created_by,created_at) VALUES('OTHER-JOB',?,'TEST existing service','Moving','2026-11-15T14:00:00+00:00','2026-11-15T18:00:00+00:00','Tentative','sales',?)",(self.q['lead_id'],now()))
        result=self.client.post(self.base+'/sync');self.assertEqual(result.status_code,200,result.text)
        self.assertIsNone(result.json()['job_id']);self.assertIn('already has a job',result.json()['message'])
        with closing(connect(self.path)) as db:self.assertEqual(len(rows(db,'SELECT * FROM jobs')),1)


if __name__=='__main__':unittest.main()
