"""Client handoffs, email links, closed dates and role boundaries; no real messages/payments."""
from contextlib import closing
from datetime import datetime,timedelta,timezone
from email import policy
from email.parser import BytesParser
from unittest.mock import patch,Mock
from urllib.parse import urlsplit
import base64,json,os,unittest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.store import connect,transaction,one,rows,insert,now,dumps
from app.platform_common import put_setting
from app.availability import assert_dates_open
from app.automatic_intake import labour_suggestion,prepare_draft
from app.gmail_sync import _quote_fields
import test_template_quotes as template_fixture
import test_launch_access as role_fixture


class ClientJourneyTests(unittest.TestCase):
    def setUp(self):
        self.f=template_fixture.TemplateQuoteTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.app=self.f.app;self.path=self.f.path;self.sales=self.f.client
        self.quote=self.f.save().json();self.qid=self.quote['id'];self.lid=self.quote['lead_id']
        with transaction(self.path) as db:put_setting(db,'client_terms',{'approved':True,'version':'test-terms','text':'Fictional test terms only.'})
        self.portal=TestClient(self.app);self.addCleanup(self.portal.close)

    def finalize(self):
        r=self.sales.post('/api/crm/quotes/'+self.qid+'/finalize',json={});self.assertEqual(r.status_code,200,r.text);return r.json()

    def open_portal(self,url=None,client=None):
        url=url or self.finalize()['portal_url'];client=client or self.portal
        r=client.post('/api/portal/exchange',json={'token':urlsplit(url).fragment.removeprefix('access=')});self.assertEqual(r.status_code,200,r.text)
        client.headers['x-csrf-token']=r.json()['csrf'];return client

    def accept(self):return self.portal.post('/api/portal/quotes/'+self.qid+'/accept',json={'agree':True,'signed_name':'Fictional Client','terms_version':'test-terms'})

    def test_expired_quote_is_visible_but_cannot_be_accepted(self):
        with transaction(self.path) as db:
            q=one(db,'SELECT inputs_json FROM quotes WHERE id=?',(self.qid,));inputs=json.loads(q['inputs_json']);inputs['expiry_date']='2000-01-01'
            db.execute('UPDATE quotes SET inputs_json=? WHERE id=?',(dumps(inputs),self.qid))
        self.open_portal()
        self.assertTrue(self.portal.get('/api/portal/project').json()['quotes'][0]['expired'])
        self.assertEqual(self.accept().status_code,409)
        self.assertEqual(self.sales.post('/api/quotes/'+self.qid+'/accept',json={'reference':'Fictional acceptance'}).status_code,409)
        with transaction(self.path) as db:db.execute("UPDATE quotes SET status='Accepted' WHERE id=?",(self.qid,))
        self.assertFalse(self.portal.get('/api/portal/project').json()['quotes'][0]['expired'])
        self.assertTrue(self.accept().json()['already_accepted'])

    def test_repeated_finalize_preserves_the_email_link_and_one_draft(self):
        first=self.finalize();second=self.finalize();self.assertEqual(first['portal_url'],second['portal_url'])
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM portal_invites').fetchone()[0],1)
        self.open_portal(first['portal_url']);self.assertEqual(self.portal.get('/api/portal/project').status_code,200)

    def test_sent_quote_is_not_resent_or_its_link_revoked(self):
        first=self.finalize()
        with transaction(self.path) as db:db.execute("UPDATE outbound_messages SET status='Sent' WHERE id=?",(first['mail_id'],))
        second=self.finalize();self.assertTrue(second['sent']);self.assertEqual(first['portal_url'],second['portal_url']);self.open_portal(first['portal_url'])

    def test_expired_draft_link_is_replaced_before_sending(self):
        first=self.finalize()
        with transaction(self.path) as db:db.execute("UPDATE portal_invites SET expires_at='2000-01-01'")
        second=self.finalize();self.assertNotEqual(first['portal_url'],second['portal_url']);self.assertEqual(first['mail_id'],second['mail_id']);self.open_portal(second['portal_url'])

    def test_one_use_link_reopens_only_in_its_matching_active_session(self):
        url=self.finalize()['portal_url'];self.open_portal(url);self.open_portal(url)
        with TestClient(self.app) as other:self.assertEqual(other.post('/api/portal/exchange',json={'token':urlsplit(url).fragment.removeprefix('access=')}).status_code,401)

    def test_email_change_invalidates_existing_sessions_and_unused_invites(self):
        first=self.finalize()['portal_url'];self.open_portal(first)
        invite=self.sales.post('/api/leads/'+self.lid+'/portal-invite',json={}).json()['url']
        with transaction(self.path) as db:db.execute("UPDATE leads SET email='replacement@example.invalid' WHERE id=?",(self.lid,))
        self.assertEqual(self.portal.get('/api/portal/project').status_code,401)
        self.assertEqual(self.portal.post('/api/portal/exchange',json={'token':invite.split('#access=')[1]}).status_code,401)

    def test_callback_retries_are_idempotent_and_visible_to_sales(self):
        self.open_portal();data={'request_key':'callback-test-00001','phone':'5145550123','preferred_time':'Tomorrow after 2 pm ET','message':'Please explain packing.'}
        first=self.portal.post('/api/portal/callbacks',json=data);self.assertEqual(first.status_code,200,first.text)
        again=self.portal.post('/api/portal/callbacks',json=data);self.assertTrue(again.json()['duplicate'])
        self.assertEqual(self.portal.post('/api/portal/callbacks',json={**data,'phone':'5145550111'}).status_code,409)
        requests=self.sales.get('/api/callbacks').json();self.assertEqual(len(requests),1);self.assertEqual(requests[0]['lead_id'],self.lid)
        self.assertEqual(self.sales.post('/api/callbacks/'+requests[0]['id']+'/complete',json={}).status_code,200)
        self.assertEqual(self.portal.get('/api/portal/project').json()['callbacks'][0]['status'],'Completed')

    def test_changes_are_deduplicated_and_block_quote_acceptance_until_reviewed(self):
        self.open_portal();data={'version':1,'fields':{'inventory':'Extra wardrobe'}}
        self.assertEqual(self.portal.post('/api/portal/changes',json=data).status_code,200)
        self.assertTrue(self.portal.post('/api/portal/changes',json=data).json()['duplicate'])
        self.assertTrue(self.portal.get('/api/portal/project').json()['quotes'][0]['stale']);self.assertEqual(self.accept().status_code,409)
        self.assertEqual(self.portal.post('/api/portal/changes',json={'version':1,'fields':{'move_date':'not a date'}}).status_code,422)

    def test_acceptance_is_tentative_and_closed_date_blocks_acceptance(self):
        self.open_portal();r=self.accept();self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['booking_confirmed'])
        self.assertEqual(self.portal.get('/api/portal/project').json()['jobs'],[])
        with transaction(self.path) as db:db.execute("UPDATE jobs SET status='Confirmed' WHERE quote_id=?",(self.qid,))
        self.assertTrue(self.accept().json()['booking_confirmed'])

    def test_closed_date_is_visible_and_prevents_new_client_acceptance(self):
        self.open_portal();r=self.sales.put('/api/schedule/availability/2026-11-15',json={'status':'Fully booked','version':0,'reason':'Team capacity'})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.accept().status_code,409)
        data=self.portal.get('/api/portal/project').json()['quotes'][0]['availability'];self.assertFalse(data['available']);self.assertNotIn('reason',data)
        self.assertEqual(self.sales.put('/api/schedule/availability/2026-11-15',json={'status':'Open','version':0}).status_code,409)
        self.assertEqual(self.sales.put('/api/schedule/availability/2026-11-15',json={'status':'Open','version':1}).status_code,200)
        self.assertEqual(self.accept().status_code,200)

    def test_closing_date_keeps_existing_confirmed_clients_booking_and_payment_access(self):
        self.open_portal();self.assertEqual(self.accept().status_code,200)
        with transaction(self.path) as db:db.execute("UPDATE jobs SET status='Confirmed' WHERE quote_id=?",(self.qid,))
        self.sales.put('/api/schedule/availability/2026-11-15',json={'status':'Fully booked','version':0})
        q=self.portal.get('/api/portal/project').json()['quotes'][0]
        self.assertTrue(q['booking_confirmed']);self.assertEqual(q['availability']['status'],'Fully booked')
        self.assertTrue(self.accept().json()['booking_confirmed'])

    def test_revised_quote_supersedes_old_approval_and_cancels_old_draft(self):
        first=self.finalize();data={**self.f.data,'lead_id':self.lid,'lead_version':1,'draft_key':'revised-draft'}
        data.pop('new_client');data.pop('service_details');data['client_note']='Updated reviewed scope'
        second=self.f.save(data).json();self.qid=second['id'];latest=self.finalize();self.open_portal(latest['portal_url'])
        quotes=self.portal.get('/api/portal/project').json()['quotes'];self.assertEqual(len(quotes),1);self.assertEqual(quotes[0]['id'],self.qid)
        with closing(connect(self.path)) as db:self.assertEqual(one(db,'SELECT status FROM outbound_messages WHERE id=?',(first['mail_id'],))['status'],'Cancelled')

    def test_fresh_link_request_does_not_disclose_whether_a_client_exists_and_is_rate_limited(self):
        self.finalize()
        with patch.dict(os.environ,{'CROWNOPS_ALLOW_EXTERNAL':'1','CROWNOPS_TEST_EMAIL_ALLOWLIST':'template@example.invalid'}),patch('app.providers.send_gmail',return_value='mock-link') as send:
            found=self.portal.post('/api/portal/request-link',json={'email':'template@example.invalid'})
            absent=self.portal.post('/api/portal/request-link',json={'email':'unknown@example.invalid'})
            self.assertEqual(found.json(),absent.json());self.assertEqual(send.call_count,1)
            body=send.call_args.args[1]['body'];self.assertIn('/portal?tab=estimates#access=',body)
            for _ in range(5):self.portal.post('/api/portal/request-link',json={'email':'template@example.invalid'})
            self.assertEqual(send.call_count,3)

    def test_outbound_email_is_bilingual_multipart_and_escapes_user_content(self):
        from app.providers import send_gmail
        response=Mock(ok=True);response.json.return_value={'id':'mock-id'}
        msg={'id':'MAIL-TEST','recipient':'template@example.invalid','subject':'Your estimate','body':'Hi <script>alert(1)</script>,\n\nhttps://crownops.example.invalid/portal?tab=estimates#access=test','category':'Reply','event_key':'test'}
        profile=Mock();profile.json.return_value={'emailAddress':'office@example.invalid'}
        with patch('app.providers.provider_token',return_value={'access_token':'not-a-real-token'}),patch('app.providers.requests.get',return_value=profile),patch('app.providers.requests.post',return_value=response) as send:
            send_gmail(self.path,msg)
        mime=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(send.call_args.kwargs['json']['raw']))
        self.assertIsNotNone(mime.get_body(preferencelist=('plain',)));html=mime.get_body(preferencelist=('html',)).get_content()
        self.assertIn('Open my moving file',html);self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)

    def test_form_acknowledgment_and_callback_are_created_once_without_booking(self):
        data={'request_id':'public-form-test-0001','permission_to_contact':True,'callback_request':True,'preferred_time':'Afternoon',
              'lead':{'client_name':'Fictional Form Client','email':'form@example.invalid','phone':'5145550100','move_date':'2026-11-20','photo_link':'https://example.invalid/photos'}}
        first=self.portal.post('/api/request',json=data);self.assertEqual(first.status_code,200,first.text)
        second=self.portal.post('/api/request',json=data);self.assertEqual(first.json()['reference'],second.json()['reference'])
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM outbound_messages WHERE event_key='request-ack:public-form-test-0001'").fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM client_callbacks').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)


class AvailabilityRoleTests(unittest.TestCase):
    def setUp(self):
        self.f=role_fixture.LaunchAccessTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.path=self.f.path

    def test_only_scheduling_office_can_close_dates_and_existing_confirmed_work_is_kept(self):
        for role in ('owner','manager','sales','dispatch'):
            c=self.f.staff(role);r=c.put('/api/schedule/availability/2026-11-15',json={'status':'Fully booked','version':('owner','manager','sales','dispatch').index(role)})
            self.assertEqual(r.status_code,200,r.text)
        for role in ('crew','lead','accounting'):
            self.assertEqual(self.f.staff(role).put('/api/schedule/availability/2026-11-15',json={'status':'Open','version':4}).status_code,403)
            self.assertEqual(self.f.staff(role).get('/api/callbacks').status_code,403)
        self.assertEqual(self.f.owner.put('/api/jobs/J1/plan',json=self.f.plan()).status_code,409)
        with transaction(self.path) as db:db.execute("UPDATE jobs SET status='Confirmed' WHERE id='J1'")
        self.assertEqual(self.f.owner.put('/api/jobs/J1/plan',json=self.f.plan()).status_code,200)

    def test_multiday_closure_uses_montreal_day_and_excludes_midnight_end(self):
        self.f.owner.put('/api/schedule/availability/2026-11-16',json={'status':'Fully booked','version':0})
        with closing(connect(self.path)) as db:
            assert_dates_open(db,'2026-11-15T23:00:00-05:00','2026-11-16T00:00:00-05:00')
            with self.assertRaises(HTTPException):assert_dates_open(db,'2026-11-15T23:00:00-05:00','2026-11-16T00:01:00-05:00')

    def test_received_deposit_is_recorded_even_if_closed_date_prevents_confirmation(self):
        self.f.owner.put('/api/schedule/availability/2026-11-15',json={'status':'Fully booked','version':0})
        r=self.f.owner.post('/api/crm/quotes/Q1/manual-deposit',json={'method':'Cash','amount':300,'reference':'additional-test-payment','mode':'received'})
        self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['payment_recorded']);self.assertFalse(r.json()['confirmed'])
        with closing(connect(self.path)) as db:self.assertIsNotNone(one(db,"SELECT id FROM payments WHERE reference='additional-test-payment'"))

    def test_client_invoice_and_quickbooks_links_stay_project_scoped(self):
        crew,remote=self.f.qbo_invoice()
        invite=self.f.owner.post('/api/leads/L1/portal-invite',json={}).json()['url']
        with TestClient(self.f.app) as client:
            client.post('/api/portal/exchange',json={'token':invite.split('#access=')[1]})
            view=client.get('/api/portal/invoices/INV/document?download=false');self.assertEqual(view.status_code,200,view.text);self.assertIn('inline',view.headers['content-disposition'])
            with patch('app.providers.qbo_read',return_value=remote) as read:
                self.assertEqual(client.get('/api/portal/invoices/INV/quickbooks-payment-link').status_code,200)
                self.assertEqual(client.get('/api/portal/invoices/OTHER/quickbooks-payment-link').status_code,404)
                self.assertEqual(read.call_count,1)


class AutomaticDraftTests(unittest.TestCase):
    def test_complete_inquiry_prepares_a_real_draft_without_sending_or_booking(self):
        fixture=template_fixture.TemplateQuoteTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        lead={'client_name':'Draft Test','email':'draft@example.invalid','phone':'5145550100','move_date':'2026-11-15','pickup':'Test A','dropoff':'Test B','lead_type':'Form',
              'home_size':'Studio / 1.5','boxes':'10-20','pickup_access':'Ground floor / direct access','dropoff_access':'Ground floor / direct access','pickup_walk':'0-10 ft','dropoff_walk':'0-10 ft',
              'intake':{'local_area':'Yes','start_window':'morning','between_stops_minutes':'20','pickup_base_minutes':'15','return_base_minutes':'15'}}
        result=fixture.client.post('/api/leads',json=lead);self.assertEqual(result.status_code,200,result.text)
        with closing(connect(fixture.path)) as db:
            record=one(db,'SELECT * FROM intake_drafts');self.assertEqual(record['status'],'Draft ready for review',record)
            q=one(db,'SELECT * FROM quotes');self.assertEqual(q['status'],'Draft');self.assertEqual(q['lead_id'],result.json()['id'])
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],0);self.assertEqual(db.execute('SELECT count(*) FROM outbound_messages').fetchone()[0],0)

    def test_recovery_fills_only_untouched_forms_without_quotes_and_preserves_original(self):
        from app.automatic_intake import repair_imported_forms
        fixture=template_fixture.TemplateQuoteTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        body='Client Name: Recovered Client\nEmail: recovered@example.invalid\nPhone: 5145550100\nMoving Date: 2026-11-28\nPickup Address: Original Test Address'
        with transaction(fixture.path) as db:
            for lid,version in [('RECOVER',1),('EDITED',2)]:
                insert(db,'leads',dict(id=lid,client_name='Website inquiry',source='Gmail',lead_type='Form',version=version,created_at=now(),updated_at=now()))
                insert(db,'messages',dict(id='MSG-'+lid,lead_id=lid,kind='Incoming email',subject='New Crown Movers Quote Request - Recovered Client - 2026-11-28',body=body,author='test',created_at=now()))
        self.assertEqual(repair_imported_forms(fixture.path,'test'),1)
        with closing(connect(fixture.path)) as db:
            self.assertEqual(one(db,"SELECT client_name FROM leads WHERE id='RECOVER'")['client_name'],'Recovered Client')
            self.assertEqual(one(db,"SELECT client_name FROM leads WHERE id='EDITED'")['client_name'],'Website inquiry')
            self.assertEqual(one(db,"SELECT body FROM messages WHERE id='MSG-RECOVER'")['body'],body)
        self.assertEqual(repair_imported_forms(fixture.path,'test'),0)

    def test_new_production_schema_is_added_only_after_verified_recovery(self):
        from app.store import initialize
        from app.platform_common import settings
        fixture=template_fixture.TemplateQuoteTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        with transaction(fixture.path) as db:db.execute('DROP TABLE schedule_days')
        root=str(fixture.path.parent)
        env={'CROWNOPS_ENV':'production','RAILWAY_VOLUME_MOUNT_PATH':root,'CROWNOPS_UPLOADS':root+'/uploads','CROWNOPS_BACKUPS':root+'/backups','CROWNOPS_SECRET_KEY':'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA='}
        with patch.dict(os.environ,env):initialize(fixture.path)
        with closing(connect(fixture.path)) as db:
            record=settings(db,'verified_backup');self.assertTrue(record['restore_verified'])
            self.assertIsNotNone(one(db,"SELECT name FROM sqlite_master WHERE name='schedule_days'"))
        with patch.dict(os.environ,env),patch('app.release_recovery.before_manager_migration') as recover:
            initialize(fixture.path);recover.assert_not_called()

    def test_failed_recovery_leaves_the_existing_database_unchanged(self):
        from app.store import initialize
        fixture=template_fixture.TemplateQuoteTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        with transaction(fixture.path) as db:db.execute('DROP TABLE schedule_days')
        with patch.dict(os.environ,{'CROWNOPS_ENV':'production'}),patch('app.release_recovery.before_manager_migration',side_effect=RuntimeError('Controlled recovery failure')):
            with self.assertRaises(RuntimeError):initialize(fixture.path)
        with closing(connect(fixture.path)) as db:
            self.assertIsNone(one(db,"SELECT name FROM sqlite_master WHERE name='schedule_days'"));self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0],2)

    def test_parser_preserves_multiline_details_and_keeps_empty_answers_empty(self):
        body='CROWN_QUOTE_BUILDER_INPUT\nClient Name: Test Client\nEmail: test@example.invalid\nPhone: 5145550123\nMoving Date: 2026-11-28\nPickup Walking Distance:\nDrop-off Walking Distance: 10-20 ft\nAdditional Details: Elevator cannot be reserved.\nFourth floor.\nPhoto / Video Link:\nEND_CROWN_QUOTE_BUILDER_INPUT'
        fields=_quote_fields(body,'New Crown Movers Quote Request - Test Client - 2026-11-28')
        self.assertEqual(fields['pickup_walk'],'');self.assertEqual(fields['photo_link'],'');self.assertIn('Fourth floor.',fields['inventory'])

    def test_server_uses_actual_company_calculator_and_unknown_travel_stays_flagged(self):
        lead={'lead_type':'Form','home_size':'Studio / 1.5','boxes':'10-20','pickup_access':'Ground floor / direct access','dropoff_access':'Ground floor / direct access','pickup_walk':'0-10 ft','dropoff_walk':'0-10 ft','intake_json':dumps({'local_area':'Yes'})}
        result=labour_suggestion(lead);self.assertTrue(result['available'],result);self.assertFalse(result['can_apply']);self.assertIsNone(result['transport_hours'])


if __name__=='__main__':unittest.main()
