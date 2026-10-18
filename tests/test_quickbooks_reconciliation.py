"""Disposable financial handoffs; all provider reads/sends are mocked."""
from contextlib import closing
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
import copy,json,os,unittest
from fastapi import HTTPException
from app.store import connect,transaction,one,rows,insert,now,dumps
from app.platform_common import invoice_current,put_setting,queue_message
from app.invoice_payments import quickbooks_payment_state,collection_payment_check,register_invoice_reconciliation,checked_quickbooks_link
from app.providers import process_mail
from app.worker import tick
import test_launch_access as fixture


class QuickBooksReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.LaunchAccessTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path=self.f.path
        if not any(r.path=='/api/invoices/{iid}/payment-state' for r in self.f.app.routes):
            # The production app registers this in main; keep the fixture independently useful.
            auth=next(r for r in self.f.app.routes if r.path=='/api/expenses').dependant.dependencies[0].call
            register_invoice_reconciliation(self.f.app,self.path,auth)
        with transaction(self.path) as db:db.execute("DELETE FROM payments WHERE id='P1'")
        self.crew,_=self.f.qbo_invoice()
        self.assertEqual(self.crew.get('/api/invoices/INV').json()['status'],'Issued')
        with transaction(self.path) as db:
            db.execute("UPDATE invoice_revisions SET issued_at=? WHERE invoice_id='INV'",((datetime.now(timezone.utc)-timedelta(days=8)).isoformat(),))
            put_setting(db,'automation_policy',{'enabled':True,'invoice_followup':True})
        self.remote={'Id':'102','TotalAmt':685,'Balance':685,'CurrencyRef':{'value':'CAD'},'CustomerRef':{'value':'101'},'InvoiceLink':'https://connect.intuit.com/test-invoice-link','LinkedTxn':[]}
        self.payments={}

    def payment(self,amount,pid='201',**changes):
        self.remote['Balance']=685-amount
        self.remote['LinkedTxn']=[{'TxnType':'Payment','TxnId':pid}]
        self.payments[pid]={'Id':pid,'TotalAmt':amount,'UnappliedAmt':0,'CurrencyRef':{'value':'CAD'},'CustomerRef':{'value':'101'},'TxnDate':'2026-10-16','SyncToken':'1',
            'Line':[{'Amount':amount,'LinkedTxn':[{'TxnType':'Invoice','TxnId':'102'}]}],**changes}

    def read(self,path,resource,params=None):
        if resource=='query':return {'QueryResponse':{'Invoice':[copy.deepcopy(self.remote)]}}
        if resource.startswith('payment/'):return {'Payment':copy.deepcopy(self.payments[resource.split('/')[1]])}
        raise AssertionError('Unexpected provider read: '+resource)

    def balance(self):
        with closing(connect(self.path)) as db:return invoice_current(db,'INV')[1]['balance_cents']

    def collections(self):
        with closing(connect(self.path)) as db:return rows(db,"SELECT * FROM outbound_messages WHERE category='Collection' AND status IN ('Draft','Approved','Sent')")

    def draft(self):
        tick(self.path)
        with closing(connect(self.path)) as db:return one(db,"SELECT * FROM outbound_messages WHERE category='Collection' AND status='Draft'")

    def approve(self,m):
        r=self.f.manager.post('/api/outbound/'+m['id']+'/approve',json={'reviewed':True});self.assertEqual(r.status_code,200,r.text)

    def send(self,m):
        with patch.dict(os.environ,{'CROWNOPS_ALLOW_EXTERNAL':'1','CROWNOPS_TEST_EMAIL_ALLOWLIST':'client@example.invalid'}),patch('app.providers.send_gmail',return_value='mock-only') as mail:
            result=process_mail(self.path,m['id'])
            return result,mail.call_count

    def test_paid_quickbooks_receipt_import_is_idempotent_and_does_not_move_money(self):
        self.payment(685)
        with patch('app.providers.qbo_read',side_effect=self.read),patch('app.providers.qbo_call') as write:
            first=quickbooks_payment_state(self.path,'INV',reconcile=True)
            again=quickbooks_payment_state(self.path,'INV',reconcile=True)
        self.assertEqual(first['status'],'Paid');self.assertTrue(first['verified']);self.assertFalse(first['can_collect'])
        self.assertEqual(first['reconciled_receipts'],1);self.assertEqual(again['reconciled_receipts'],0);self.assertEqual(self.balance(),0);write.assert_not_called()
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM payments').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM invoice_payment_allocations').fetchone()[0],1)
            payment=one(db,'SELECT * FROM payments')
            op=one(db,'SELECT * FROM provider_operations WHERE id=?',('qbo:Payment:'+payment['id']+':1',))
            self.assertEqual(op['status'],'Completed')

    def test_worker_paid_invoice_creates_no_false_balance_followup(self):
        self.payment(685)
        with patch('app.providers.qbo_read',side_effect=self.read):result=tick(self.path)
        self.assertEqual(result['drafts_created'],0);self.assertEqual(self.collections(),[]);self.assertEqual(self.balance(),0)

    def test_partial_payment_cancels_old_draft_and_creates_current_balance_draft(self):
        with patch('app.providers.qbo_read',side_effect=self.read):old=self.draft()
        self.assertIn('685.00',old['body']);self.payment(100)
        with patch('app.providers.qbo_read',side_effect=self.read):tick(self.path)
        current=self.collections();self.assertEqual(len(current),1);self.assertIn('585.00',current[0]['body']);self.assertNotEqual(current[0]['id'],old['id'])
        with closing(connect(self.path)) as db:self.assertEqual(one(db,'SELECT status FROM outbound_messages WHERE id=?',(old['id'],))['status'],'Cancelled')

    def test_fresh_send_check_blocks_old_approved_reminder_after_external_payment(self):
        with patch('app.providers.qbo_read',side_effect=self.read):old=self.draft()
        self.approve(old);self.payment(100)
        with patch('app.providers.qbo_read',side_effect=self.read):result,calls=self.send(old)
        self.assertFalse(result['sent']);self.assertEqual(calls,0);self.assertEqual(self.balance(),58500)

    def test_current_partial_balance_can_be_reviewed_and_sent_after_fresh_check(self):
        self.payment(100)
        with patch('app.providers.qbo_read',side_effect=self.read):current=self.draft()
        self.approve(current)
        with patch('app.providers.qbo_read',side_effect=self.read) as read:result,calls=self.send(current)
        self.assertTrue(result['sent']);self.assertEqual(calls,1);self.assertGreater(read.call_count,0)

    def test_provider_timeout_blocks_draft_and_final_send_without_exposing_error(self):
        with patch('app.providers.qbo_read',side_effect=self.read):old=self.draft()
        self.approve(old)
        with patch('app.providers.qbo_read',side_effect=TimeoutError('private-token-secret')):
            self.assertEqual(tick(self.path)['drafts_created'],0)
            state=quickbooks_payment_state(self.path,'INV',reconcile=True)
            result,calls=self.send(old)
        self.assertEqual(state['status'],'Unavailable');self.assertNotIn('secret',dumps(state));self.assertFalse(result['sent']);self.assertEqual(calls,0)

    def test_customer_currency_total_and_reference_mismatches_never_import(self):
        self.payment(100);original=copy.deepcopy(self.remote)
        for changes in ({'CustomerRef':{'value':'999'}},{'CurrencyRef':{'value':'USD'}},{'TotalAmt':1},{'Id':'999'},{'Balance':True}):
            self.remote={**original,**changes}
            with patch('app.providers.qbo_read',side_effect=self.read):state=quickbooks_payment_state(self.path,'INV',reconcile=True)
            self.assertEqual(state['status'],'Review required',changes);self.assertFalse(state['can_collect']);self.assertEqual(self.balance(),68500)

    def test_credit_or_balance_delta_without_real_payment_is_not_a_receipt(self):
        self.remote['Balance']=0
        with patch('app.providers.qbo_read',side_effect=self.read):state=quickbooks_payment_state(self.path,'INV',reconcile=True)
        self.assertEqual(state['status'],'Review required');self.assertEqual(self.balance(),68500)
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM payments').fetchone()[0],0)

    def test_posted_local_deposit_is_reused_without_second_receipt_or_allocation(self):
        self.payment(300)
        with transaction(self.path) as db:
            insert(db,'payments',dict(id='EXISTING',lead_id='L1',amount_cents=30000,method='Interac',purpose='Deposit',reference='LOCAL-DEPOSIT',verified=1,recorded_by='owner',created_at=now()))
            insert(db,'invoice_payment_allocations',dict(id='EXISTING-ALLOC',invoice_id='INV',payment_id='EXISTING',amount_cents=30000,allocated_by='owner',created_at=now()))
            insert(db,'external_records',dict(provider='quickbooks',kind='Payment',object_id='EXISTING',revision=1,external_id='201',amount_cents=30000,updated_at=now()))
        with patch('app.providers.qbo_read',side_effect=self.read):state=quickbooks_payment_state(self.path,'INV',reconcile=True)
        self.assertEqual(state['status'],'Partial');self.assertTrue(state['can_collect']);self.assertEqual(state['reconciled_receipts'],0);self.assertEqual(self.balance(),38500)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM payments').fetchone()[0],1);self.assertEqual(db.execute('SELECT count(*) FROM invoice_payment_allocations').fetchone()[0],1)

    def test_unmatched_local_receipt_blocks_duplicate_quickbooks_import(self):
        self.payment(300)
        with transaction(self.path) as db:insert(db,'payments',dict(id='LOCAL',lead_id='L1',amount_cents=30000,method='Interac',purpose='Deposit',reference='UNMATCHED',verified=1,recorded_by='owner',created_at=now()))
        with patch('app.providers.qbo_read',side_effect=self.read):state=quickbooks_payment_state(self.path,'INV',reconcile=True)
        self.assertEqual(state['status'],'Review required');self.assertIn('Match',state['reason'])
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM payments').fetchone()[0],1)

    def test_refund_or_removed_payment_blocks_collection_without_reversing_history(self):
        self.payment(685)
        with patch('app.providers.qbo_read',side_effect=self.read):quickbooks_payment_state(self.path,'INV',reconcile=True)
        self.remote['Balance']=685;self.remote['LinkedTxn']=[]
        with patch('app.providers.qbo_read',side_effect=self.read):state=collection_payment_check(self.path,'INV')
        self.assertEqual(state['status'],'Review required');self.assertFalse(state['can_collect']);self.assertEqual(self.balance(),0)
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM payments').fetchone()[0],1)

    def test_concurrent_invoice_revision_change_rolls_back_import(self):
        self.payment(100);changed=False
        def read(path,resource,params=None):
            nonlocal changed
            result=self.read(path,resource,params)
            if not changed:
                changed=True
                with transaction(self.path) as db:db.execute("UPDATE job_invoices SET version=version+1 WHERE id='INV'")
            return result
        with patch('app.providers.qbo_read',side_effect=read):state=quickbooks_payment_state(self.path,'INV',reconcile=True)
        self.assertEqual(state['status'],'Review required');self.assertIn('changed',state['reason']);self.assertEqual(self.balance(),68500)

    def test_wrong_payment_customer_and_split_service_payment_require_finance_review(self):
        self.payment(100,CustomerRef={'value':'999'})
        with patch('app.providers.qbo_read',side_effect=self.read):state=quickbooks_payment_state(self.path,'INV',reconcile=True)
        self.assertEqual(state['status'],'Review required')
        self.payment(100,TotalAmt=150,Line=[{'Amount':100,'LinkedTxn':[{'TxnType':'Invoice','TxnId':'102'}]},{'Amount':50,'LinkedTxn':[{'TxnType':'Invoice','TxnId':'999'}]}])
        with patch('app.providers.qbo_read',side_effect=self.read):state=quickbooks_payment_state(self.path,'INV',reconcile=True)
        self.assertEqual(state['status'],'Review required');self.assertEqual(self.balance(),68500)

    def test_pending_declined_or_refunded_payment_is_not_verified_as_a_receipt(self):
        for status in ('Pending','Declined','Refunded','Unknown'):
            self.payment(100,CreditCardPayment={'CreditChargeResponse':{'Status':status}})
            with patch('app.providers.qbo_read',side_effect=self.read):state=quickbooks_payment_state(self.path,'INV',reconcile=True)
            self.assertEqual(state['status'],'Review required',status);self.assertFalse(state['can_collect']);self.assertEqual(self.balance(),68500)

    def test_old_mapping_does_not_allow_collection_for_new_revision(self):
        with transaction(self.path) as db:db.execute("UPDATE job_invoices SET revision=2 WHERE id='INV'")
        with patch('app.providers.qbo_read') as read:state=collection_payment_check(self.path,'INV')
        self.assertTrue(state['mapped']);self.assertEqual(state['status'],'Review required');read.assert_not_called()

    def test_single_currency_cad_company_can_supply_omitted_transaction_currency(self):
        self.payment(100);self.remote.pop('CurrencyRef');self.payments['201'].pop('CurrencyRef')
        calls=[]
        def read(path,resource,params=None):
            calls.append(resource)
            if resource=='preferences':return {'Preferences':{'CurrencyPrefs':{'MultiCurrencyEnabled':False,'HomeCurrency':{'value':'CAD'}}}}
            return self.read(path,resource,params)
        with patch('app.providers.qbo_read',side_effect=read):
            state=quickbooks_payment_state(self.path,'INV',reconcile=True)
            self.assertEqual(state['status'],'Partial',state);self.assertEqual(self.balance(),58500)
            self.assertEqual(calls.count('preferences'),1)
            response=self.f.manager.get('/api/invoices/INV/quickbooks-payment-link');self.assertEqual(response.status_code,200,response.text)

    def test_missing_currency_never_assumes_cad_for_multi_currency_unknown_or_usd(self):
        self.remote.pop('CurrencyRef')
        for prefs in ({'MultiCurrencyEnabled':True,'HomeCurrency':{'value':'CAD'}},{'MultiCurrencyEnabled':False,'HomeCurrency':{'value':'USD'}},{},{'MultiCurrencyEnabled':'false','HomeCurrency':{'value':'CAD'}}):
            def read(path,resource,params=None):
                return {'Preferences':{'CurrencyPrefs':prefs}} if resource=='preferences' else self.read(path,resource,params)
            with self.subTest(prefs=prefs),patch('app.providers.qbo_read',side_effect=read):
                state=quickbooks_payment_state(self.path,'INV',reconcile=True)
                self.assertFalse(state['can_collect']);self.assertEqual(state['status'],'Review required');self.assertEqual(self.balance(),68500)

    def test_equal_balances_do_not_allow_checkout_when_remote_reduction_is_a_credit(self):
        with transaction(self.path) as db:
            insert(db,'payments',dict(id='LOCAL',lead_id='L1',amount_cents=30000,method='Cash',purpose='Deposit',reference='VERIFIED-LOCAL',verified=1,recorded_by='owner',created_at=now()))
            insert(db,'invoice_payment_allocations',dict(id='LOCAL-A',invoice_id='INV',payment_id='LOCAL',amount_cents=30000,allocated_by='owner',created_at=now()))
        self.remote.update(Balance=385,LinkedTxn=[{'TxnType':'CreditMemo','TxnId':'901'}])
        with patch('app.providers.qbo_read',side_effect=self.read),patch('app.providers.qbo_call') as write:
            response=self.f.manager.get('/api/invoices/INV/quickbooks-payment-link');self.assertEqual(response.status_code,409,response.text)
            state=quickbooks_payment_state(self.path,'INV');self.assertFalse(state['can_collect']);self.assertEqual(state['status'],'Review required');write.assert_not_called()

    def test_equal_balances_still_require_exact_receipt_match_before_checkout(self):
        self.payment(300)
        with transaction(self.path) as db:
            insert(db,'payments',dict(id='LOCAL',lead_id='L1',amount_cents=30000,method='Cash',purpose='Deposit',reference='VERIFIED-LOCAL',verified=1,recorded_by='owner',created_at=now()))
            insert(db,'invoice_payment_allocations',dict(id='LOCAL-A',invoice_id='INV',payment_id='LOCAL',amount_cents=30000,allocated_by='owner',created_at=now()))
        with patch('app.providers.qbo_read',side_effect=self.read):
            response=self.f.manager.get('/api/invoices/INV/quickbooks-payment-link');self.assertEqual(response.status_code,409,response.text)
            inv=self.crew.get('/api/invoices/INV').json()
            matched=self.f.manager.post('/api/invoices/INV/match-quickbooks-receipt',json={'confirmed':True,'version':inv['version'],'revision':inv['revision'],'payment_id':'LOCAL','quickbooks_payment_id':'201','note':'Compared the actual deposit receipt with the linked QuickBooks payment.'})
            self.assertEqual(matched.status_code,200,matched.text)
            response=self.f.manager.get('/api/invoices/INV/quickbooks-payment-link');self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['amount_cents'],38500)

    def test_unmapped_invoice_uses_local_pending_receipt_guards_without_provider_call(self):
        with transaction(self.path) as db:
            db.execute("DELETE FROM external_records WHERE kind='Invoice'");db.execute("DELETE FROM provider_operations WHERE kind='Invoice'")
        with patch('app.providers.qbo_read') as read:state=collection_payment_check(self.path,'INV')
        self.assertFalse(state['mapped']);self.assertTrue(state['can_collect']);read.assert_not_called()

    def test_read_state_is_nonmutating_and_crew_cannot_import_or_match(self):
        self.payment(100)
        with patch('app.providers.qbo_read',side_effect=self.read) as read:
            response=self.crew.get('/api/invoices/INV/payment-state')
            self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertTrue(response.json()['verified']);self.assertEqual(response.json()['remote_balance_cents'],58500)
            read.reset_mock()
            self.assertEqual(self.f.staff('lead').get('/api/invoices/INV/payment-state').status_code,403);read.assert_not_called()
            for url in ('reconcile-payments','match-quickbooks-receipt'):
                self.assertEqual(self.crew.post('/api/invoices/INV/'+url,json={'confirmed':True,'version':3,'revision':1}).status_code,403)
                self.assertEqual(self.f.staff('sales').post('/api/invoices/INV/'+url,json={'confirmed':True,'version':3,'revision':1}).status_code,403)
        self.assertEqual(self.balance(),68500)

    def test_manager_import_requires_current_version_then_is_retry_safe(self):
        self.payment(100);invoice=self.crew.get('/api/invoices/INV').json()
        body={'confirmed':True,'version':invoice['version'],'revision':invoice['revision']}
        with patch('app.providers.qbo_read',side_effect=self.read):
            stale=self.f.manager.post('/api/invoices/INV/reconcile-payments',json={**body,'version':999});self.assertEqual(stale.status_code,409)
            response=self.f.manager.post('/api/invoices/INV/reconcile-payments',json=body);self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['reconciled_receipts'],1)
            retry=self.f.manager.post('/api/invoices/INV/reconcile-payments',json=body);self.assertEqual(retry.status_code,409)
            response=self.f.manager.post('/api/invoices/INV/reconcile-payments',json={**body,'version':response.json()['version']})
            self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['reconciled_receipts'],0)

    def test_management_match_reuses_real_existing_deposit_and_prevents_second_post(self):
        self.payment(300)
        with transaction(self.path) as db:
            insert(db,'payments',dict(id='LOCAL',lead_id='L1',amount_cents=30000,method='Interac',purpose='Deposit',reference='BANK-RECEIPT',verified=1,recorded_by='owner',created_at=now()))
            insert(db,'invoice_payment_allocations',dict(id='LOCAL-ALLOC',invoice_id='INV',payment_id='LOCAL',amount_cents=30000,allocated_by='owner',created_at=now()))
        inv=self.crew.get('/api/invoices/INV').json()
        body={'confirmed':True,'version':inv['version'],'revision':inv['revision'],'payment_id':'LOCAL','quickbooks_payment_id':'201','note':'Compared bank reference BANK-RECEIPT with QBO Payment 201.'}
        with patch('app.providers.qbo_read',side_effect=self.read),patch('app.providers.qbo_call') as write:
            response=self.f.manager.post('/api/invoices/INV/match-quickbooks-receipt',json=body);self.assertEqual(response.status_code,200,response.text)
            retry=self.f.manager.post('/api/invoices/INV/match-quickbooks-receipt',json=body);self.assertEqual(retry.status_code,200,retry.text)
            state=quickbooks_payment_state(self.path,'INV',reconcile=True)
        write.assert_not_called();self.assertEqual(state['status'],'Partial');self.assertTrue(state['can_collect'])
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM payments').fetchone()[0],1);self.assertEqual(db.execute('SELECT count(*) FROM invoice_payment_allocations').fetchone()[0],1)
            op=one(db,"SELECT * FROM provider_operations WHERE id='qbo:Payment:LOCAL:1'");self.assertEqual(op['status'],'Completed')

    def test_match_wrong_amount_or_wrong_quickbooks_id_is_atomic_and_audited(self):
        self.payment(300)
        with transaction(self.path) as db:insert(db,'payments',dict(id='LOCAL',lead_id='L1',amount_cents=10000,method='Interac',purpose='Deposit',reference='WRONG',verified=1,recorded_by='owner',created_at=now()))
        inv=self.crew.get('/api/invoices/INV').json();body={'confirmed':True,'version':inv['version'],'revision':inv['revision'],'payment_id':'LOCAL','quickbooks_payment_id':'201','note':'Controlled mismatch test.'}
        with patch('app.providers.qbo_read',side_effect=self.read):
            for changes in ({},{'quickbooks_payment_id':'999'},{'version':999},{'note':''},{'quickbooks_payment_id':'invalid'}):
                response=self.f.manager.post('/api/invoices/INV/match-quickbooks-receipt',json={**body,**changes});self.assertIn(response.status_code,(409,422),response.text)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM external_records WHERE kind='Payment'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM invoice_payment_allocations').fetchone()[0],0)

    def test_invoice_mail_accepts_only_approved_immutable_billing_email_or_client(self):
        with transaction(self.path) as db:
            document=json.loads(one(db,"SELECT document_json FROM invoice_revisions WHERE invoice_id='INV'")['document_json'])
            document['terms']['customer']['email']='payer@example.invalid'
            db.execute("UPDATE invoice_revisions SET document_json=? WHERE invoice_id='INV'",(dumps(document),))
            m=queue_message(db,'L1','Invoice','Controlled invoice only.','Invoice','manager','invoice-delivery:INV:1',jid='J1')
            db.execute("UPDATE outbound_messages SET recipient='payer@example.invalid',status='Approved',approved_by='manager' WHERE id=?",(m['id'],))
        with patch.dict(os.environ,{'CROWNOPS_ALLOW_EXTERNAL':'1','CROWNOPS_TEST_EMAIL_ALLOWLIST':'payer@example.invalid'}),patch('app.providers.send_gmail',return_value='mock-only') as mail,patch('app.providers.qbo_read',side_effect=self.read):
            result=process_mail(self.path,m['id']);self.assertTrue(result['sent']);self.assertEqual(mail.call_count,1)
            with transaction(self.path) as db:db.execute("UPDATE outbound_messages SET recipient='stranger@example.invalid',status='Approved' WHERE id=?",(m['id'],))
            mail.reset_mock()
            with self.assertRaises(HTTPException) as failure:process_mail(self.path,m['id'])
            self.assertEqual(failure.exception.status_code,409);mail.assert_not_called()

    def test_invoice_guest_link_requires_exact_preauthenticated_scope(self):
        with patch('app.providers.qbo_read',side_effect=self.read) as read:
            for scope in (None,'OTHER'):
                with self.assertRaises(HTTPException) as failure:checked_quickbooks_link(self.path,'INV',None,invoice_scope=scope)
                self.assertEqual(failure.exception.status_code,403)
            read.assert_not_called()
            result=checked_quickbooks_link(self.path,'INV',None,invoice_scope='INV')
        self.assertEqual(result['invoice_id'],'INV');self.assertEqual(result['amount_cents'],68500)

    def test_preparation_reminder_has_confirmed_date_and_correct_project_link(self):
        from zoneinfo import ZoneInfo
        start=datetime.now(timezone.utc)+timedelta(days=1)
        with transaction(self.path) as db:
            db.execute("UPDATE jobs SET status='Confirmed',start_at=?,end_at=? WHERE id='J1'",(start.isoformat(),(start+timedelta(hours=5)).isoformat()))
            put_setting(db,'automation_policy',{'enabled':True,'preparation':True})
        result=tick(self.path);self.assertEqual(result['drafts_created'],1)
        with closing(connect(self.path)) as db:
            message=one(db,"SELECT * FROM outbound_messages WHERE event_key LIKE 'prep:%'")
            self.assertIn(start.astimezone(ZoneInfo('America/Toronto')).date().isoformat(),message['body'])
            self.assertIn('/portal?tab=details#access=',message['body']);self.assertIn('Parking',message['body'])
            invite=one(db,'SELECT * FROM portal_invites');self.assertEqual(invite['lead_id'],'L1')


if __name__=='__main__':unittest.main()
