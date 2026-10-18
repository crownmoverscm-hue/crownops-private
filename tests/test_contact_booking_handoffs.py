"""Client contact corrections and durable booking emails, without external sends."""
from contextlib import closing
from datetime import datetime,timedelta
import json
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.store import connect,transaction,one,rows
from app.client_communications import quote_scope_current,pending_scope_changes,booking_confirmation_current,preparation_body
import test_client_journey as journey_fixture


class ContactBookingHandoffTests(unittest.TestCase):
    def setUp(self):
        self.f=journey_fixture.ClientJourneyTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path=self.f.path;self.sales=self.f.sales;self.portal=self.f.portal
        self.f.open_portal();self.qid=self.f.qid;self.lid=self.f.lid

    def project(self):return self.portal.get('/api/portal/project').json()

    def submit(self,fields):
        result=self.portal.post('/api/portal/changes',json={'version':self.project()['client']['version'],'fields':fields})
        self.assertEqual(result.status_code,200,result.text)
        return next(c for c in self.sales.get('/api/client-changes').json() if c['status']=='Pending' and json.loads(c['payload_json'])==fields)

    def review(self,change,**extra):
        return self.sales.post('/api/client-changes/'+change['id']+'/review',json={'decision':'Approve','lead_version':self.project()['client']['version'],'note':'Reviewed correction',**extra})

    def confirm(self):
        result=self.f.accept();self.assertEqual(result.status_code,200,result.text)
        result=self.sales.post('/api/crm/quotes/'+self.qid+'/manual-deposit',json={'method':'Cash','mode':'received','amount':300,'reference':'TEST recorded cash deposit'})
        self.assertEqual(result.status_code,200,result.text);self.assertTrue(result.json()['confirmed'])
        return result.json()['job_id']

    def booking(self,jid):
        with closing(connect(self.path)) as db:return one(db,'SELECT * FROM jobs WHERE id=?',(jid,))

    def prepare(self,jid,**extra):
        return self.sales.post('/api/jobs/'+jid+'/booking-confirmation',json={'version':self.booking(jid)['version'],**extra})

    def test_contact_review_preserves_accepted_quote_and_payment_access(self):
        self.assertEqual(self.f.accept().status_code,200)
        with closing(connect(self.path)) as db:original=one(db,'SELECT * FROM quotes WHERE id=?',(self.qid,))
        change=self.submit({'phone':'5145550199','client_name':'Corrected Client Name'})
        self.assertFalse(self.project()['quotes'][0]['stale'])
        self.assertEqual(self.review(change).status_code,200)
        project=self.project();self.assertEqual(project['client']['version'],2);self.assertFalse(project['quotes'][0]['stale'])
        with closing(connect(self.path)) as db:
            quote=one(db,'SELECT * FROM quotes WHERE id=?',(self.qid,));lead=one(db,'SELECT * FROM leads WHERE id=?',(self.lid,))
            self.assertEqual(quote,original);self.assertTrue(quote_scope_current(db,quote,lead));self.assertFalse(pending_scope_changes(db,self.lid))
        result=self.sales.post('/api/crm/quotes/'+self.qid+'/manual-deposit',json={'method':'Cash','mode':'received','amount':300,'reference':'TEST contact-corrected deposit'})
        self.assertEqual(result.status_code,200,result.text);self.assertTrue(result.json()['confirmed'])

    def test_pending_contact_allows_acceptance_but_scope_change_remains_guarded(self):
        self.submit({'phone':'5145550199'})
        self.assertEqual(self.f.accept().status_code,200)
        self.submit({'inventory':'Additional piano and furniture'})
        self.assertTrue(self.project()['quotes'][0]['stale'])
        with closing(connect(self.path)) as db:self.assertTrue(pending_scope_changes(db,self.lid))

    def test_contact_versions_are_contiguous_and_unknown_gaps_fail_closed(self):
        for phone in ('5145550199','5145550188'):
            self.assertEqual(self.review(self.submit({'phone':phone})).status_code,200)
            self.assertFalse(self.project()['quotes'][0]['stale'])
        with transaction(self.path) as db:db.execute('UPDATE leads SET version=version+1 WHERE id=?',(self.lid,))
        self.assertTrue(self.project()['quotes'][0]['stale'])
        self.assertEqual(self.review(self.submit({'client_name':'Another Correct Name'})).status_code,200)
        self.assertTrue(self.project()['quotes'][0]['stale'])

    def test_contact_review_still_requires_current_version_and_reconciliation(self):
        first=self.submit({'phone':'5145550199'});second=self.submit({'phone':'5145550188'})
        self.assertEqual(self.review(first).status_code,200)
        self.assertEqual(self.review(second,lead_version=1).status_code,409)
        self.assertEqual(self.review(second).status_code,409)
        self.assertEqual(self.review(second,reconcile=True).status_code,200)
        self.assertFalse(self.project()['quotes'][0]['stale'])

    def test_mixed_contact_and_scope_edit_does_not_bridge_quote_versions(self):
        change=self.submit({'phone':'5145550199','pickup':'New pickup address'})
        self.assertEqual(self.review(change).status_code,200)
        self.assertTrue(self.project()['quotes'][0]['stale']);self.assertEqual(self.f.accept().status_code,409)

    def test_booking_confirmation_survives_internal_versions_and_is_explicitly_recoverable(self):
        jid=self.confirm();first=self.prepare(jid);self.assertEqual(first.status_code,200,first.text)
        with transaction(self.path) as db:
            message=one(db,'SELECT * FROM outbound_messages WHERE id=?',(first.json()['mail_id'],))
            self.assertIsNone(message['source_version'])
            db.execute('UPDATE jobs SET version=version+1,notes=? WHERE id=?',('Internal crew instructions',jid))
            self.assertTrue(booking_confirmation_current(db,message))
        again=self.prepare(jid);self.assertEqual(again.json()['mail_id'],first.json()['mail_id'])
        with transaction(self.path) as db:db.execute("UPDATE outbound_messages SET status='Cancelled' WHERE id=?",(first.json()['mail_id'],))
        restored=self.prepare(jid);self.assertEqual(restored.status_code,200,restored.text);self.assertEqual(restored.json()['status'],'Draft');self.assertEqual(restored.json()['mail_id'],first.json()['mail_id'])

    def test_changed_client_time_replaces_draft_but_sent_current_email_is_not_resent(self):
        jid=self.confirm();first=self.prepare(jid).json();job=self.booking(jid)
        start=datetime.fromisoformat(job['start_at'])+timedelta(days=1,hours=1)
        end=datetime.fromisoformat(job['end_at'])+timedelta(days=1,hours=1)
        with transaction(self.path) as db:
            db.execute('UPDATE jobs SET start_at=?,end_at=?,version=version+1 WHERE id=?',(start.isoformat(),end.isoformat(),jid))
            old=one(db,'SELECT * FROM outbound_messages WHERE id=?',(first['mail_id'],));self.assertFalse(booking_confirmation_current(db,old))
        current=self.prepare(jid);self.assertEqual(current.status_code,200,current.text);self.assertNotEqual(current.json()['mail_id'],first['mail_id'])
        with transaction(self.path) as db:
            old=one(db,'SELECT * FROM outbound_messages WHERE id=?',(first['mail_id'],));self.assertEqual(old['status'],'Cancelled')
            message=one(db,'SELECT * FROM outbound_messages WHERE id=?',(current.json()['mail_id'],))
            self.assertIn('2026-11-16',message['body']);self.assertIn('10:00 AM',message['body']);self.assertTrue(booking_confirmation_current(db,message))
            db.execute("UPDATE outbound_messages SET status='Sent' WHERE id=?",(message['id'],))
        same=self.prepare(jid);self.assertTrue(same.json()['sent']);self.assertEqual(same.json()['mail_id'],current.json()['mail_id'])

    def test_unconfirmed_send_prevents_duplicate_confirmation(self):
        jid=self.confirm();message=self.prepare(jid).json()
        with transaction(self.path) as db:db.execute("UPDATE outbound_messages SET status='Unknown' WHERE id=?",(message['mail_id'],))
        self.assertEqual(self.prepare(jid).status_code,409)

    def test_internal_assignment_versions_do_not_cancel_reviewed_confirmation_delivery(self):
        from app.providers import process_mail
        jid=self.confirm();message=self.prepare(jid).json()
        with transaction(self.path) as db:
            db.execute("UPDATE outbound_messages SET status='Approved',approved_by='sales' WHERE id=?",(message['mail_id'],))
            db.execute('UPDATE jobs SET version=version+1 WHERE id=?',(jid,))
        with patch.dict('os.environ',{'CROWNOPS_ALLOW_EXTERNAL':'1','CROWNOPS_TEST_EMAIL_ALLOWLIST':'template@example.invalid'}),patch('app.providers.send_gmail',return_value='mock-confirmation') as send:
            result=process_mail(self.path,message['mail_id'])
        self.assertTrue(result['sent']);self.assertEqual(send.call_count,1)

    def test_actual_reschedule_blocks_stale_confirmation_before_provider_send(self):
        from app.providers import process_mail
        jid=self.confirm();message=self.prepare(jid).json();job=self.booking(jid)
        with transaction(self.path) as db:
            db.execute("UPDATE outbound_messages SET status='Approved',approved_by='sales' WHERE id=?",(message['mail_id'],))
            db.execute('UPDATE jobs SET start_at=?,version=version+1 WHERE id=?',((datetime.fromisoformat(job['start_at'])+timedelta(days=1)).isoformat(),jid))
        with patch.dict('os.environ',{'CROWNOPS_ALLOW_EXTERNAL':'1','CROWNOPS_TEST_EMAIL_ALLOWLIST':'template@example.invalid'}),patch('app.providers.send_gmail',return_value='must-not-send') as send:
            result=process_mail(self.path,message['mail_id'])
        self.assertFalse(result['sent']);self.assertEqual(send.call_count,0)

    def test_expired_confirmation_link_is_refreshed_for_review(self):
        jid=self.confirm();message=self.prepare(jid).json()
        with transaction(self.path) as db:
            db.execute("UPDATE outbound_messages SET status='Approved',approved_by='sales' WHERE id=?",(message['mail_id'],))
            db.execute("UPDATE portal_invites SET expires_at='2000-01-01'")
        refreshed=self.prepare(jid);self.assertEqual(refreshed.status_code,200,refreshed.text)
        self.assertEqual(refreshed.json()['mail_id'],message['mail_id']);self.assertEqual(refreshed.json()['status'],'Draft')

    def test_prepare_confirmation_requires_office_permission_and_current_job(self):
        jid=self.confirm();self.assertEqual(self.prepare(jid,version=0).status_code,409)
        crew=TestClient(self.f.app);self.addCleanup(crew.close);crew.cookies.set('crownops_session','crew-token');crew.headers['x-csrf-token']='crew-csrf'
        self.assertEqual(crew.post('/api/jobs/'+jid+'/booking-confirmation',json={'version':self.booking(jid)['version']}).status_code,403)

    def test_preparation_copy_has_actual_date_link_and_help_in_both_languages(self):
        jid=self.confirm();job=self.booking(jid)
        for language,greeting in (('English','Hi'),('French','Bonjour')):
            body=preparation_body({'client_name':'Fictional Client','language':language},job,'https://example.invalid/portal?tab=details#access=test','9:00-9:30 AM')
            self.assertTrue(body.startswith(greeting));self.assertIn('2026-11-15',body);self.assertIn('9:00-9:30 AM',body);self.assertIn('https://example.invalid/portal?',body);self.assertIn('514-606-4030',body)

    def test_quote_form_attached_to_manual_service_is_latest_inquiry_without_rewriting_type(self):
        from app.sales_crm_v6 import client_latest_activity
        with transaction(self.path) as db:
            client=one(db,'SELECT client_id FROM client_account_leads WHERE lead_id=?',(self.lid,))['client_id']
            db.execute("INSERT INTO integration_events (id,event_key,event_type,object_id,payload_json,status,created_at) VALUES ('ATTACHED-QUOTE','gmail:attached','gmail_quote',?,?, 'Imported','2026-12-01T12:00:00+00:00')",(self.lid,json.dumps({'subject':'New Quote Request','received_at':'2026-10-01T12:00:00+00:00','message_id':'FORM-COPY'})))
            db.execute("INSERT INTO messages (id,lead_id,kind,subject,body,language,author,created_at) VALUES ('FORM-COPY',?,'Incoming email','New Quote Request','Fictional form','English','sync','2026-12-01T12:00:00+00:00')",(self.lid,))
            activity=client_latest_activity(db,client)
            self.assertEqual(activity['label'],'Quote Form');self.assertEqual(activity['at'],'2026-10-01T12:00:00+00:00')
            self.assertEqual(one(db,'SELECT lead_type FROM leads WHERE id=?',(self.lid,))['lead_type'],'Moving')
            db.execute("INSERT INTO messages (id,lead_id,kind,subject,body,language,author,created_at) VALUES ('REAL-REPLY',?,'Incoming email','New Quote Request','A new client reply with the same subject','English','sync','2026-12-02T12:00:00+00:00')",(self.lid,))
            self.assertEqual(client_latest_activity(db,client)['label'],'Email')

    def test_separate_payer_email_uses_invoice_cta_and_neutral_contact_help(self):
        from app.client_communications import invoice_body,email_html
        for language,label in (('English','Open invoice'),('French','Consulter la facture')):
            body=invoice_body({'client_name':'Fictional Payer','language':language},{'number':'INV-TEST'},{'total_cents':10000,'deposit_cents':0,'balance_cents':10000},'https://example.invalid/invoice-access#access=secret')
            markup=email_html('Invoice',body)
            self.assertIn(label,markup);self.assertIn('info@crownmovers.ca',body);self.assertNotIn('Request a callback in your moving file',body)
            self.assertNotIn('open your moving file and request',markup)


if __name__=='__main__':unittest.main()
