"""Owner-supplied rendered Oct24 bodies; real ingestion, disposable DB, no network."""
import base64
import copy
import json
from contextlib import closing
from pathlib import Path
import unittest
from unittest.mock import patch
from app.gmail_sync import _quote_fields, FEEDS, sync_gmail_readonly, match_inquiry_project
from app.store import connect, transaction
import test_template_quotes as fixture

FIXTURES=json.loads(Path(__file__).with_name('fixtures').joinpath('oct24_rendered_notifications.json').read_text())
DATES=['2026-11-10','2026-11-09','2026-11-11','2026-11-12']
EXPECTED=json.loads(Path(__file__).with_name('fixtures').joinpath('oct24_expected_fields.json').read_text())
EMPTY_TABLES=('quotes','outbound_messages','jobs','payment_links','deposit_links','payments','job_invoices')

class OriginalNotificationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.TemplateQuoteTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.path=self.fixture.path
        endpoint=next(r.endpoint for r in self.fixture.app.routes if r.path=='/api/gmail/sync-readonly')
        self.new_lead=dict(zip(endpoint.__code__.co_freevars,[c.cell_contents for c in endpoint.__closure__]))['new_lead']
        self.messages=[]
        self.network=patch('requests.sessions.Session.request',side_effect=AssertionError('External requests prohibited'));self.network.start();self.addCleanup(self.network.stop)
        self.socket=patch('socket.socket.connect',side_effect=AssertionError('External sockets prohibited'));self.socket.start();self.addCleanup(self.socket.stop)

    def sync(self,fixtures=None):
        self.messages=fixtures if fixtures is not None else FIXTURES
        def api(token,path,params=None):
            if path=='labels':return {'labels':[{'id':'quote','name':FEEDS['quote']}]}
            if path=='messages':return {'messages':[{'id':'OCT24-'+x['id']} for x in self.messages]}
            if path.startswith('messages/'):
                x=next(x for x in self.messages if 'OCT24-'+x['id']==path.split('/')[1])
                return {'id':'OCT24-'+x['id'],'internalDate':'1792814400000','payload':{'headers':[{'name':'Subject','value':x['subject']}],'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(x['body'].encode()).decode()}}}
            raise AssertionError(path)
        with patch('app.gmail_sync._api',side_effect=api):
            return sync_gmail_readonly(self.path,'MOCK-ONLY','sales',self.new_lead,feed_names=('quote',))

    def records(self,table):
        with closing(connect(self.path)) as db:return [dict(r) for r in db.execute('SELECT * FROM '+table)]

    def test_four_unchanged_bodies_create_one_client_four_services_and_preserve_every_field(self):
        result=self.sync();self.assertEqual(result['new_leads'],4);self.assertFalse(result['errors'])
        self.assertEqual(len(self.records('client_accounts')),1)
        leads={x['external_id']:x for x in self.records('leads')}
        self.assertEqual(len(leads),4);self.assertEqual({x['move_date'] for x in leads.values()},set(DATES))
        events={x['event_key']:x for x in self.records('integration_events')}
        for x,day in zip(FIXTURES,DATES):
            with self.subTest(fixture=x['id']):
                key='gmail:OCT24-'+x['id'];parsed=_quote_fields(x['body'],x['subject']);stored=leads[key]
                self.assertEqual(parsed['email'],'jonathanprobonas15@gmail.com');self.assertEqual(parsed['phone'],'5146064030');self.assertEqual(parsed['move_date'],day)
                self.assertEqual({k:parsed[k] for k in EXPECTED[x['id']]},EXPECTED[x['id']])
                for field in ('client_name','email','phone','move_date','pickup','dropoff','home_size','boxes','pickup_access','dropoff_access','pickup_walk','dropoff_walk','inventory','notes','photo_link','language'):
                    self.assertEqual(stored[field],parsed[field],field)
                payload=json.loads(events[key]['payload_json']);self.assertEqual(payload['body'],x['body']);self.assertEqual(payload['parsed'],parsed)
        for table in EMPTY_TABLES:self.assertEqual(self.records(table),[],table)
        drafts=self.records('intake_drafts');self.assertEqual(len(drafts),4)
        for d in drafts:
            self.assertIsNone(d['quote_id']);self.assertEqual(d['status'],'Needs details');self.assertTrue(json.loads(d['missing_json']))
        links=self.records('client_account_leads');self.assertEqual(len(links),4);self.assertEqual(len({x['client_id'] for x in links}),1)

    def test_exact_retries_leave_all_service_fields_events_and_client_unchanged(self):
        self.sync();before={t:self.records(t) for t in ('leads','client_accounts','client_account_leads','integration_events','tasks','intake_drafts')}
        retry=self.sync();self.assertEqual(retry['duplicates'],4);self.assertEqual(retry['new_leads'],0)
        self.assertEqual(before,{t:self.records(t) for t in before})

    def test_same_gmail_id_changed_body_is_not_reimported_or_overwritten(self):
        self.sync();before=self.records('integration_events');leads=self.records('leads')
        changed=copy.deepcopy(FIXTURES);changed[0]['body']=changed[0]['body'].replace('2026-11-10','2027-01-01')
        self.assertEqual(self.sync(changed)['duplicates'],4)
        self.assertEqual(self.records('integration_events'),before);self.assertEqual(self.records('leads'),leads)

    def test_french_address_conflict_is_blank_flagged_and_original_values_retained(self):
        p=_quote_fields(FIXTURES[1]['body'],FIXTURES[1]['subject'])
        self.assertEqual(p['pickup'],'');self.assertEqual(p['dropoff'],'')
        self.assertIn('H2X 1A1',p['source_contact_fields']['DépartAdresse'][0]);self.assertEqual(p['source_contact_fields']['Code postal'],['H4N 2B2'])
        self.assertTrue(p['parser_review']);self.sync()
        lead=next(x for x in self.records('leads') if x['move_date']=='2026-11-09')
        draft=next(x for x in self.records('intake_drafts') if x['lead_id']==lead['id'])
        self.assertIn(p['parser_review'][0],json.loads(draft['missing_json']))

    def test_calculator_values_remain_unapproved_french_classification_and_blank_photo(self):
        for i,language,rate in [(2,'English','$150/hr + tax'),(3,'French','155 $/h + tax')]:
            p=_quote_fields(FIXTURES[i]['body'],FIXTURES[i]['subject'])
            self.assertEqual(p['language'],language);self.assertFalse(p['calculator_source']['approved'])
            self.assertEqual(p['calculator_source']['values']['Hourly Rate'],rate)
            self.assertEqual(p['photo_link'],'');self.assertEqual(p['inventory'],'');self.assertEqual(p['heavy_items'],'')
            self.assertTrue(p['notes']);self.assertEqual(p['intake']['between_stops_minutes'],'')

    def test_missing_approved_pricing_is_an_explicit_blocker_not_a_notification_rate(self):
        with transaction(self.path) as db:db.execute('UPDATE pricing_versions SET approved=0')
        self.sync()
        for draft in self.records('intake_drafts'):
            self.assertIn('approved pricing setup',json.loads(draft['missing_json']))
            self.assertIsNone(draft['quote_id'])
            inputs=json.loads(draft['inputs_json']);self.assertNotIn('hourly_rate',inputs)
        self.assertEqual(self.records('quotes'),[])

    def test_blank_customer_contact_cannot_be_replaced_by_company_footer(self):
        x=FIXTURES[2];body=x['body'].replace('jonathanprobonas15@gmail.com','').replace('(514) 606-4030','')
        p=_quote_fields(body,x['subject']);self.assertEqual(p['email'],'');self.assertEqual(p['phone'],'')

    def test_conflicting_repeated_customer_emails_require_identity_review(self):
        x=copy.deepcopy(FIXTURES[2]);x['body']=x['body'].replace('jonathanprobonas15@gmail.comMoving Date','other@example.invalidMoving Date')
        p=_quote_fields(x['body'],x['subject']);self.assertTrue(p['contact_conflict'])
        self.sync([x]);self.assertEqual(self.records('leads'),[]);self.assertEqual(self.records('client_accounts'),[])
        self.assertIn('Conflicting contact',json.loads(self.records('integration_events')[0]['payload_json'])['matching']['reason'])

    def test_blank_name_is_not_replaced_with_a_fake_customer_name(self):
        x=copy.deepcopy(FIXTURES[2]);x['body']=x['body'].replace('NameTEST — MUSE — EN-CALCPhone','NamePhone')
        self.assertEqual(_quote_fields(x['body'],x['subject'])['client_name'],'')
        self.sync([x]);self.assertEqual(self.records('leads'),[])
        self.assertIn('client name',json.loads(self.records('integration_events')[0]['payload_json'])['validation_review']['message'])

    def test_conflicting_repeated_customer_phones_require_identity_review(self):
        x=copy.deepcopy(FIXTURES[2]);x['body']=x['body'].replace('(514) 606-4030Email','(514) 555-9999Email')
        p=_quote_fields(x['body'],x['subject']);self.assertTrue(p['contact_conflict'])
        self.sync([x]);self.assertEqual(self.records('leads'),[])

    def test_single_closed_service_requires_review_even_for_new_date(self):
        self.sync(FIXTURES[:1])
        with transaction(self.path) as db:db.execute("UPDATE leads SET stage='Closed'")
        p=_quote_fields(FIXTURES[1]['body'],FIXTURES[1]['subject'])
        with closing(connect(self.path)) as db:self.assertTrue(match_inquiry_project(db,p,'quote')['requires_review'])

    def probe(self,**changes):
        self.sync(FIXTURES[:2]);p=_quote_fields(FIXTURES[2]['body'],FIXTURES[2]['subject']);p.update(changes)
        with closing(connect(self.path)) as db:return match_inquiry_project(db,p,'quote')

    def test_missing_date_with_several_services_requires_review(self):
        self.assertTrue(self.probe(move_date='')['requires_review'])

    def test_new_date_with_phone_email_conflict_requires_review(self):
        self.assertTrue(self.probe(email='someone-else@example.invalid')['requires_review'])

    def test_email_only_cannot_use_the_new_multi_service_exception(self):
        self.assertTrue(self.probe(phone='')['requires_review'])

    def test_closed_service_blocks_new_multi_service_exception(self):
        self.sync(FIXTURES[:2])
        with transaction(self.path) as db:db.execute("UPDATE leads SET stage='Closed' WHERE move_date='2026-11-10'")
        p=_quote_fields(FIXTURES[2]['body'],FIXTURES[2]['subject'])
        with closing(connect(self.path)) as db:self.assertTrue(match_inquiry_project(db,p,'quote')['requires_review'])

    def test_existing_exact_date_selects_original_service_instead_of_creating_one(self):
        self.sync();p=_quote_fields(FIXTURES[2]['body'],FIXTURES[2]['subject'])
        with closing(connect(self.path)) as db:
            match=match_inquiry_project(db,p,'quote');self.assertFalse(match['requires_review']);self.assertEqual(match['lead']['move_date'],'2026-11-11')

    def test_unverified_or_changed_client_linkage_cannot_use_exception(self):
        self.sync(FIXTURES[:2])
        with transaction(self.path) as db:db.execute("UPDATE client_accounts SET email='different@example.invalid'")
        p=_quote_fields(FIXTURES[2]['body'],FIXTURES[2]['subject'])
        with closing(connect(self.path)) as db:self.assertTrue(match_inquiry_project(db,p,'quote')['requires_review'])

    def test_repeated_new_date_notification_preserves_existing_scope(self):
        self.sync();before=self.records('leads');x=copy.deepcopy(FIXTURES[2]);x['id']='SEPARATE-NOTIFICATION-SAME-SERVICE';x['body']=x['body'].replace('Elevator','Different unreviewed access')
        self.assertEqual(self.sync([x])['new_leads'],0);self.assertEqual(self.records('leads'),before)
        self.assertEqual(len(self.records('messages')),5)
