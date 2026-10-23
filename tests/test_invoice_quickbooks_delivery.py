"""Assigned-job invoice emails use provider mocks and fictional recipients only."""
from contextlib import closing
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from app.store import connect, transaction, one, rows, insert, now, dumps
from app.providers import qbo_invoice_payload
import test_quickbooks_reconciliation as fixture


class InvoiceQuickBooksDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.QuickBooksReconciliationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path=self.f.path;self.client=self.f.crew;self.base='/api/invoices/INV/quickbooks-email';self.sends=0
        with closing(connect(self.path)) as db:
            self.doc=json.loads(one(db,"SELECT document_json FROM invoice_revisions WHERE invoice_id='INV' AND revision=1")['document_json'])
        body=qbo_invoice_payload(self.doc,'101','7','2','3')
        self.f.remote.update(body,Id='102',SyncToken='0',EmailStatus='NotSet',TotalAmt=685,Balance=685,TxnTaxDetail={'TotalTax':0},LinkedTxn=[])
        patches=[patch.dict('os.environ',{'QBO_ENV':'sandbox','CROWNOPS_ALLOW_QBO_SANDBOX_WRITES':'1','CROWNOPS_ALLOW_QBO_INVOICE_DELIVERY':'1','CROWNOPS_TEST_EMAIL_ALLOWLIST':'client@example.invalid'}),
            patch('app.providers.qbo_read',side_effect=self.read),patch('app.providers._qbo_connection',return_value=({},'123','https://example.invalid')),
            patch('app.providers.qbo_mapping',side_effect=lambda p,k:{'service_item_id':'7','tax_code_id':'2','discount_account_id':'3','deposit_account_id':'4'}[k]),
            patch('app.providers.qbo_send_invoice',side_effect=self.send)]
        for p in patches:p.start();self.addCleanup(p.stop)

    def read(self,path,resource,params=None):
        if resource=='invoice/102':return {'Invoice':deepcopy(self.f.remote)}
        return self.f.read(path,resource,params)

    def send(self,path,eid,recipient):
        self.sends+=1;self.f.remote.update(EmailStatus='EmailSent',SyncToken='1',DeliveryInfo={'DeliveryTime':now()})
        return {'Invoice':deepcopy(self.f.remote)}

    def review(self):
        r=self.client.get(self.base);self.assertEqual(r.status_code,200,r.text);return r.json()

    def deliver(self):
        p=self.review();return self.client.post(self.base+'/send',json={'confirm':True,'recipient':p['recipient'],'review_hash':p['review_hash']})

    def test_assigned_crew_can_send_once_and_status_is_visible(self):
        p=self.review();self.assertTrue(p['can_send']);self.assertEqual(p['balance_cents'],68500)
        r=self.deliver();self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['sent'])
        self.assertTrue(self.deliver().json()['duplicate']);self.assertEqual(self.sends,1)
        d=self.client.get('/api/invoices/INV/delivery-state').json();self.assertEqual(d['delivery_provider'],'QuickBooks');self.assertEqual(d['status'],'Sent');self.assertFalse(d['can_send'])
        with closing(connect(self.path)) as db:self.assertEqual(len(rows(db,'SELECT * FROM payments')),0)

    def test_unknown_send_is_not_retried_and_can_be_reconciled(self):
        with patch('app.providers.qbo_send_invoice',side_effect=TimeoutError) as send:
            self.assertEqual(self.deliver().status_code,502)
            self.assertEqual(self.deliver().status_code,409);self.assertEqual(send.call_count,1)
        self.f.remote['EmailStatus']='EmailSent'
        self.assertEqual(self.review()['state'],'Sent')
        with closing(connect(self.path)) as db:self.assertEqual(one(db,"SELECT status FROM provider_operations WHERE kind='InvoiceEmail'")['status'],'Completed')

    def test_changed_remote_line_due_date_total_or_customer_blocks_send(self):
        self.f.remote['Line'][0]['Description']='Wrong service'
        self.assertEqual(self.client.get(self.base).status_code,409);self.assertEqual(self.sends,0)
        self.f.remote['CustomerRef']={'value':'999'}
        self.assertEqual(self.client.get(self.base).status_code,409)

    def test_review_hash_changes_after_payment_or_provider_edit(self):
        p=self.review();self.f.remote['SyncToken']='10'
        r=self.client.post(self.base+'/send',json={'confirm':True,'recipient':p['recipient'],'review_hash':p['review_hash']})
        self.assertEqual(r.status_code,409);self.assertEqual(self.sends,0)

    def test_no_cross_sender_duplicate_email(self):
        r=self.deliver();self.assertEqual(r.status_code,200,r.text)
        with patch.dict('os.environ',{'CROWNOPS_ALLOW_EXTERNAL':'1'}),patch('app.providers.send_gmail') as gmail:
            self.assertEqual(self.client.post('/api/invoices/INV/deliver',json={'confirm':True}).status_code,409)
            gmail.assert_not_called()

    def test_existing_crownops_sent_email_blocks_quickbooks(self):
        from app.platform_common import queue_message
        with transaction(self.path) as db:
            message=queue_message(db,'L1','TEST','TEST','Invoice','owner','invoice-delivery:INV:1',jid='J1')
            db.execute("UPDATE outbound_messages SET status='Sent' WHERE id=?",(message['id'],))
        self.assertFalse(self.review()['can_send']);self.assertEqual(self.deliver().status_code,409)

    def test_role_restrictions_feature_flag_and_recipient_allowlist(self):
        with transaction(self.path) as db:db.execute("DELETE FROM assignments WHERE resource_id='E1'")
        self.assertEqual(self.client.get(self.base).status_code,403)
        with transaction(self.path) as db:insert(db,'assignments',{'job_id':'J1','resource_type':'employee','resource_id':'E1'})
        with patch.dict('os.environ',{'CROWNOPS_ALLOW_QBO_INVOICE_DELIVERY':'0'}):self.assertEqual(self.deliver().status_code,503)
        with patch.dict('os.environ',{'CROWNOPS_TEST_EMAIL_ALLOWLIST':''}):self.assertEqual(self.deliver().status_code,403)
        self.assertEqual(self.sends,0)

    def test_export_carries_friendly_message_due_date_and_not_internal_reference(self):
        doc=deepcopy(self.doc);doc['terms']['due_date']='2026-11-30';doc['terms']['approval_reference']='INTERNAL STAFF NOTE'
        body=qbo_invoice_payload(doc,'101','7','2','3')
        self.assertEqual(body['DueDate'],'2026-11-30');self.assertIn('thank you',body['CustomerMemo']['value']);self.assertNotIn('INTERNAL STAFF NOTE',body['CustomerMemo']['value'])

    def test_background_receipt_sync_is_scoped_idempotent_and_makes_no_provider_writes(self):
        from app.invoice_payments import poll_quickbooks_receipts
        with transaction(self.path) as db:
            db.execute("UPDATE provider_operations SET result_json=? WHERE id='qbo:invoice:INV:1'",(dumps({'realm_id':'123','environment':'sandbox'}),))
        self.f.payment(685)
        with patch.dict('os.environ',{'CROWNOPS_ALLOW_QBO_RECEIPT_SYNC':'1'}),patch('app.providers.qbo_call') as write:
            first=poll_quickbooks_receipts(self.path);second=poll_quickbooks_receipts(self.path)
            self.assertEqual(first['imported_receipts'],1);self.assertEqual(second['imported_receipts'],0);write.assert_not_called()
        with closing(connect(self.path)) as db:self.assertEqual(len(rows(db,'SELECT * FROM payments')),1)

    def test_background_sync_skips_other_company_and_disabled_feature(self):
        from app.invoice_payments import poll_quickbooks_receipts
        with patch.dict('os.environ',{'CROWNOPS_ALLOW_QBO_RECEIPT_SYNC':'0'}):self.assertFalse(poll_quickbooks_receipts(self.path)['enabled'])
        with transaction(self.path) as db:
            db.execute("UPDATE provider_operations SET result_json=? WHERE id='qbo:invoice:INV:1'",(dumps({'realm_id':'456','environment':'sandbox'}),))
        with patch.dict('os.environ',{'CROWNOPS_ALLOW_QBO_RECEIPT_SYNC':'1'}),patch('app.invoice_payments.quickbooks_payment_state') as read:
            self.assertEqual(poll_quickbooks_receipts(self.path)['checked_invoices'],0);read.assert_not_called()

    def test_export_links_the_accepted_estimate_without_creating_another_email(self):
        from cryptography.fernet import Fernet
        from app.providers import token_save
        remote=deepcopy(self.f.remote);remote.update(Id='77',TxnStatus='Accepted',ExpirationDate='2026-11-14')
        payload={k:remote[k] for k in ('CustomerRef','CurrencyRef','GlobalTaxCalculation','Line','CustomerMemo','BillEmail','ExpirationDate')}
        with patch.dict('os.environ',{'CROWNOPS_SECRET_KEY':Fernet.generate_key().decode()}):
            with transaction(self.path) as db:
                token_save(db,'quickbooks',{'access_token':'fictional','realm_id':'123'})
                db.execute("DELETE FROM external_records WHERE kind='Invoice'")
                db.execute("DELETE FROM provider_operations WHERE kind='Invoice'")
                insert(db,'qbo_estimate_deliveries',dict(quote_id='Q1',environment='sandbox',realm_id='123',external_id='77',basis='high',state='Accepted',source_hash='fixture',payload_json=dumps(payload),recipient='client@example.invalid',created_by='owner',created_at=now()))
            with patch('app.providers.qbo_read',return_value={'Estimate':remote}),patch('app.providers.qbo_call',return_value={'Invoice':self.f.remote}) as create:
                response=self.f.f.owner.post('/api/invoices/INV/quickbooks',json={'confirm':True})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(create.call_args.args[3]['LinkedTxn'],[{'TxnId':'77','TxnType':'Estimate'}])
        self.assertEqual(self.sends,0)


if __name__=='__main__':unittest.main()
