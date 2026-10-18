"""Invoice email handoffs stay scoped to one issued invoice, without real sends."""
from contextlib import closing
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from urllib.parse import urlsplit
import json
import os
import unittest
from fastapi.testclient import TestClient
from app.store import connect,transaction,one,dumps
from app.invoice_access import invoice_access_link
from app.client_communications import email_html
import test_launch_access as role_fixture


class InvoiceGuestAccessTests(unittest.TestCase):
    def setUp(self):
        public=patch.dict(os.environ,{'CROWNOPS_PUBLIC_URL':'https://crownops.example.invalid'});public.start();self.addCleanup(public.stop)
        self.f=role_fixture.LaunchAccessTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path=self.f.path;self.crew=self.f.invoice();self.owner=self.f.owner
        with transaction(self.path) as db:
            inv=one(db,"SELECT * FROM job_invoices WHERE id='INV'")
            terms=json.loads(inv['terms_json']);terms['customer'].update(name='Fictional Billing Contact',email='payer@example.invalid')
            db.execute("UPDATE job_invoices SET terms_json=? WHERE id='INV'",(dumps(terms),))
        edited=self.crew.patch('/api/invoices/INV',json={'version':1,'service_hours':4,'quantities':{},'notes':'Fictional actual work','reason':'Service completed'})
        self.assertEqual(edited.status_code,200,edited.text)
        issued=self.crew.post('/api/invoices/INV/issue',json={'version':edited.json()['version'],'confirmed':True})
        self.assertEqual(issued.status_code,200,issued.text)
        self.guest=TestClient(self.f.app);self.addCleanup(self.guest.close)

    def invoice(self):
        with closing(connect(self.path)) as db:return one(db,"SELECT * FROM job_invoices WHERE id='INV'")

    def link(self,recipient='payer@example.invalid'):
        with transaction(self.path) as db:return invoice_access_link(db,self.invoice(),recipient,'owner','https://example.invalid')

    def exchange(self,link=None,guest=None):
        return (guest or self.guest).post('/api/invoice-access/exchange',json={'token':urlsplit(link or self.link()).fragment.removeprefix('access=')})

    def send(self,**extra):
        return self.crew.post('/api/invoices/INV/deliver',json={'confirm':True,**extra})

    def test_guest_exchange_is_one_use_and_reading_is_invoice_scoped(self):
        link=self.link();opened=self.exchange(link);self.assertEqual(opened.status_code,200,opened.text)
        state=self.guest.get('/api/invoice-access');self.assertEqual(state.status_code,200,state.text)
        self.assertEqual(state.json()['number'],'TEST-INVOICE');self.assertEqual(state.json()['customer'],'Fictional Billing Contact')
        self.assertEqual(state.json()['calculation']['balance_cents'],38500)
        self.assertEqual(state.json()['payment_state']['status'],'Not linked')
        self.assertNotIn('photos',state.json());self.assertNotIn('pickup',state.json())
        document=self.guest.get('/api/invoice-access/document');self.assertEqual(document.status_code,200,document.text);self.assertIn('TEST-INVOICE',document.text)
        for path in ('/api/portal/project','/api/portal/uploads/FILE-OTHER','/api/client-projects','/api/invoices/INV'):
            self.assertEqual(self.guest.get(path).status_code,401,path)
        other=TestClient(self.f.app);self.addCleanup(other.close)
        self.assertEqual(self.exchange(link,other).status_code,401)
        self.assertEqual(self.guest.get('/api/invoice-access').status_code,200)
        repeated=self.exchange(link);self.assertEqual(repeated.status_code,200,repeated.text);self.assertTrue(repeated.json()['existing_session'])
        self.assertEqual(self.guest.get('/api/invoice-access/document/OTHER').status_code,404)

    def test_invalid_expired_invites_and_expired_sessions_are_rejected(self):
        for token in ('bad',None,{},'x'*200):
            self.assertEqual(self.guest.post('/api/invoice-access/exchange',json={'token':token}).status_code,401)
        link=self.link()
        with transaction(self.path) as db:db.execute("UPDATE invoice_access_invites SET expires_at='2000-01-01'")
        self.assertEqual(self.exchange(link).status_code,401)
        self.assertEqual(self.exchange().status_code,200)
        with transaction(self.path) as db:db.execute("UPDATE invoice_access_sessions SET expires_at='2000-01-01'")
        self.assertEqual(self.guest.get('/api/invoice-access').status_code,401)

    def test_invoice_correction_revokes_previous_guest_session_and_unused_invite(self):
        self.assertEqual(self.exchange().status_code,200);unused=self.link()
        result=self.owner.post('/api/invoices/INV/correct',json={'version':self.invoice()['version'],'reason':'Review a fictional billing correction'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.guest.get('/api/invoice-access').status_code,409)
        self.assertEqual(self.guest.get('/api/invoice-access/document').status_code,409)
        self.assertEqual(self.exchange(unused).status_code,409)

    def test_billing_recipient_delivery_sends_invoice_only_link_once(self):
        with patch.dict(os.environ,{'CROWNOPS_ALLOW_EXTERNAL':'1','CROWNOPS_TEST_EMAIL_ALLOWLIST':'payer@example.invalid,client@example.invalid'}),patch('app.providers.send_gmail',return_value='mock-payer-mail') as send:
            sent=self.send();self.assertEqual(sent.status_code,200,sent.text);self.assertTrue(sent.json()['sent'])
            message=send.call_args.args[1];self.assertEqual(message['recipient'],'payer@example.invalid')
            self.assertIn('/invoice-access#access=',message['body']);self.assertNotIn('/portal',message['body'])
            self.assertIn('Open invoice',email_html(message['subject'],message['body']))
            retry=self.send();self.assertEqual(retry.status_code,200,retry.text);self.assertTrue(retry.json()['duplicate']);self.assertEqual(send.call_count,1)
        link=next(line for line in message['body'].splitlines() if '/invoice-access#access=' in line)
        self.assertEqual(self.exchange(link).status_code,200)
        self.assertEqual(self.guest.get('/api/invoice-access').json()['number'],'TEST-INVOICE')

    def test_saved_client_can_be_selected_and_unsaved_recipient_is_refused(self):
        with patch.dict(os.environ,{'CROWNOPS_ALLOW_EXTERNAL':'1','CROWNOPS_TEST_EMAIL_ALLOWLIST':'payer@example.invalid,client@example.invalid'}),patch('app.providers.send_gmail',return_value='mock-client-mail') as send:
            invalid=self.send(recipient='unexpected@example.invalid');self.assertEqual(invalid.status_code,422,invalid.text);self.assertEqual(send.call_count,0)
            selected=self.send(recipient='client@example.invalid');self.assertEqual(selected.status_code,200,selected.text)
            self.assertEqual(send.call_args.args[1]['recipient'],'client@example.invalid')
            link=next(line for line in send.call_args.args[1]['body'].splitlines() if '/invoice-access#access=' in line)
        self.assertEqual(self.exchange(link).status_code,200)

    def test_recipient_tampering_invalidates_access(self):
        link=self.link()
        with transaction(self.path) as db:db.execute("UPDATE invoice_access_invites SET recipient='unexpected@example.invalid'")
        self.assertEqual(self.exchange(link).status_code,401)
        self.assertEqual(self.exchange().status_code,200)
        with transaction(self.path) as db:db.execute("UPDATE invoice_access_sessions SET recipient='unexpected@example.invalid'")
        self.assertEqual(self.guest.get('/api/invoice-access').status_code,401)

    def test_french_invoice_email_has_friendly_scoped_cta_and_contact_help(self):
        with transaction(self.path) as db:db.execute("UPDATE leads SET language='French' WHERE id='L1'")
        with patch.dict(os.environ,{'CROWNOPS_ALLOW_EXTERNAL':'1','CROWNOPS_TEST_EMAIL_ALLOWLIST':'payer@example.invalid'}),patch('app.providers.send_gmail',return_value='mock-french-payer-mail') as send:
            response=self.send();self.assertEqual(response.status_code,200,response.text)
            message=send.call_args.args[1];self.assertTrue(message['body'].startswith('Bonjour'))
            self.assertIn('Consulter la facture',email_html(message['subject'],message['body']))
            self.assertIn('info@crownmovers.ca',message['body']);self.assertNotIn('Demandez un rappel dans votre dossier',message['body'])

    def test_expired_invoice_link_recovers_only_to_saved_recipient_and_is_idempotent(self):
        link=self.link();token=urlsplit(link).fragment.removeprefix('access=')
        with transaction(self.path) as db:db.execute("UPDATE invoice_access_invites SET expires_at='2000-01-01'")
        fixed=datetime.now(timezone.utc)
        with patch.dict(os.environ,{'CROWNOPS_ALLOW_EXTERNAL':'1','CROWNOPS_TEST_EMAIL_ALLOWLIST':'payer@example.invalid'}),patch('app.invoice_access.datetime') as clock,patch('app.providers.send_gmail',return_value='mock-recovery') as send:
            clock.now.return_value=fixed
            data={'token':token,'email':'unexpected@example.invalid','recipient':'unexpected@example.invalid'}
            first=self.guest.post('/api/invoice-access/request-link',json=data)
            second=self.guest.post('/api/invoice-access/request-link',json=data)
            absent=self.guest.post('/api/invoice-access/request-link',json={'token':'x'*45})
            self.assertEqual(first.status_code,200,first.text);self.assertEqual(first.json(),second.json());self.assertEqual(first.json(),absent.json())
            self.assertEqual(send.call_count,1);message=send.call_args.args[1]
            self.assertEqual(message['recipient'],'payer@example.invalid');self.assertEqual(message['category'],'Invoice access')
            fresh=next(line for line in message['body'].splitlines() if '/invoice-access#access=' in line)
            self.assertNotEqual(fresh,link);self.assertNotIn('/portal',message['body'])
        self.assertEqual(self.exchange(fresh).status_code,200)
        self.assertEqual(self.guest.get('/api/invoice-access').status_code,200)

    def test_used_invoice_token_can_request_a_fresh_link_on_another_device(self):
        link=self.link();self.assertEqual(self.exchange(link).status_code,200)
        other=TestClient(self.f.app);self.addCleanup(other.close)
        token=urlsplit(link).fragment.removeprefix('access=')
        with patch.dict(os.environ,{'CROWNOPS_ALLOW_EXTERNAL':'1','CROWNOPS_TEST_EMAIL_ALLOWLIST':'payer@example.invalid'}),patch('app.providers.send_gmail',return_value='mock-used-link-recovery') as send:
            result=other.post('/api/invoice-access/request-link',json={'token':token})
            self.assertEqual(result.status_code,200,result.text);self.assertEqual(send.call_count,1)
            fresh=next(line for line in send.call_args.args[1]['body'].splitlines() if '/invoice-access#access=' in line)
        self.assertEqual(self.exchange(fresh,other).status_code,200)

    def test_invoice_recovery_remains_a_draft_when_external_actions_are_disabled(self):
        token=urlsplit(self.link()).fragment.removeprefix('access=')
        with patch('app.providers.send_gmail') as send:
            result=self.guest.post('/api/invoice-access/request-link',json={'token':token})
            self.assertEqual(result.status_code,200,result.text);self.assertEqual(send.call_count,0)
        with closing(connect(self.path)) as db:
            message=one(db,"SELECT * FROM outbound_messages WHERE category='Invoice access'")
            self.assertEqual(message['status'],'Draft');self.assertEqual(message['recipient'],'payer@example.invalid')

    def test_corrected_billing_recipient_blocks_old_invoice_recovery(self):
        token=urlsplit(self.link()).fragment.removeprefix('access=')
        corrected=self.owner.post('/api/invoices/INV/correct',json={'version':self.invoice()['version'],'reason':'Approved change to billing recipient'})
        self.assertEqual(corrected.status_code,200,corrected.text)
        with transaction(self.path) as db:
            inv=one(db,"SELECT * FROM job_invoices WHERE id='INV'");terms=json.loads(inv['terms_json']);terms['customer']['email']='replacement@example.invalid'
            db.execute("UPDATE job_invoices SET terms_json=? WHERE id='INV'",(dumps(terms),))
        inv=self.invoice();edited=self.crew.patch('/api/invoices/INV',json={'version':inv['version'],'service_hours':4,'quantities':{},'notes':'Same completed work','reason':'Confirm actual work for corrected recipient'})
        self.assertEqual(edited.status_code,200,edited.text)
        issued=self.crew.post('/api/invoices/INV/issue',json={'version':edited.json()['version'],'confirmed':True});self.assertEqual(issued.status_code,200,issued.text)
        with patch.dict(os.environ,{'CROWNOPS_ALLOW_EXTERNAL':'1','CROWNOPS_TEST_EMAIL_ALLOWLIST':'payer@example.invalid,replacement@example.invalid'}),patch('app.providers.send_gmail') as send:
            result=self.guest.post('/api/invoice-access/request-link',json={'token':token,'email':'replacement@example.invalid'})
            self.assertEqual(result.status_code,200,result.text);self.assertEqual(send.call_count,0)
        with closing(connect(self.path)) as db:self.assertIsNone(one(db,"SELECT id FROM outbound_messages WHERE category='Invoice access'"))

    def test_guest_view_rechecks_revision_after_provider_lookup(self):
        self.assertEqual(self.exchange().status_code,200);inv=self.invoice()
        def changed(*args,**kwargs):
            with transaction(self.path) as db:db.execute("UPDATE job_invoices SET status='Draft',revision=revision+1,version=version+1 WHERE id='INV'")
            return {'version':inv['version'],'revision':inv['revision'],'status':'Paid','mapped':True}
        with patch('app.invoice_payments.quickbooks_payment_state',side_effect=changed):
            self.assertEqual(self.guest.get('/api/invoice-access').status_code,409)

    def test_guest_payment_link_rechecks_revision_after_provider_lookup(self):
        self.assertEqual(self.exchange().status_code,200);inv=self.invoice()
        def changed(*args,**kwargs):
            with transaction(self.path) as db:db.execute("UPDATE job_invoices SET status='Draft',revision=revision+1,version=version+1 WHERE id='INV'")
            return {'version':inv['version'],'revision':inv['revision'],'url':'https://connect.intuit.com/must-not-open'}
        with patch('app.invoice_payments.checked_quickbooks_link',side_effect=changed):
            self.assertEqual(self.guest.get('/api/invoice-access/payment-link').status_code,409)

    def test_project_payment_check_is_scoped_before_any_provider_read(self):
        portal=TestClient(self.f.app);self.addCleanup(portal.close)
        link=self.owner.post('/api/leads/L1/portal-invite',json={}).json()['url']
        opened=portal.post('/api/portal/exchange',json={'token':urlsplit(link).fragment.removeprefix('access=')})
        self.assertEqual(opened.status_code,200,opened.text)
        with patch('app.invoice_payments.quickbooks_payment_state') as state:
            self.assertEqual(portal.get('/api/portal/invoices/OTHER/payment-state').status_code,404)
            self.assertEqual(state.call_count,0)
        response=portal.get('/api/portal/invoices/INV/payment-state')
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(response.json()['status'],'Not linked');self.assertEqual(response.json()['local_balance_cents'],38500)
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM payments').fetchone()[0],1)

    def test_project_payment_check_rejects_invoice_changed_during_lookup(self):
        portal=TestClient(self.f.app);self.addCleanup(portal.close)
        link=self.owner.post('/api/leads/L1/portal-invite',json={}).json()['url']
        portal.post('/api/portal/exchange',json={'token':urlsplit(link).fragment.removeprefix('access=')})
        inv=self.invoice()
        def changed(*args,**kwargs):
            with transaction(self.path) as db:db.execute("UPDATE job_invoices SET version=version+1 WHERE id='INV'")
            return {'revision':inv['revision'],'version':inv['version'],'status':'Paid'}
        with patch('app.invoice_payments.quickbooks_payment_state',side_effect=changed):
            self.assertEqual(portal.get('/api/portal/invoices/INV/payment-state').status_code,409)

    def test_invoice_guest_logout_requires_csrf_and_removes_access(self):
        opened=self.exchange();self.assertEqual(opened.status_code,200,opened.text)
        self.assertEqual(self.guest.post('/api/invoice-access/logout',json={}).status_code,403)
        self.guest.headers['x-csrf-token']=opened.json()['csrf']
        self.assertEqual(self.guest.post('/api/invoice-access/logout',json={}).status_code,200)
        self.assertEqual(self.guest.get('/api/invoice-access').status_code,401)


if __name__=='__main__':unittest.main()
