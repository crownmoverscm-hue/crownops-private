"""Synthetic coverage for the visible current contact card; no customer data."""
import base64
from contextlib import closing
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.main import create_app
from app.gmail_sync import _quote_fields,_clean_html,_known_quote_notification,FEEDS,sync_gmail_readonly
from app.store import connect

FIXTURE=json.loads(Path(__file__).with_name('fixtures').joinpath('current_styled_quote_synthetic.json').read_text())
TABLES=('client_accounts','client_account_leads','leads','integration_events','messages','tasks','intake_drafts','quotes','outbound_messages','jobs','assignments','payment_links','deposit_links','payments','job_invoices','audit')


class CurrentStyledMailTests(unittest.TestCase):
    def setUp(self):
        p=patch.dict(os.environ,{'CROWNOPS_ENV':'test','CROWNOPS_ALLOW_EXTERNAL':'0','CROWNOPS_RUN_WORKER':'0'},clear=True);p.start();self.addCleanup(p.stop)
        folder=tempfile.TemporaryDirectory(prefix='crownops-current-mail-',dir='/tmp');self.addCleanup(folder.cleanup)
        self.path=Path(folder.name)/'inquiries.sqlite3';app=create_app(self.path)
        endpoint=next(r.endpoint for r in app.routes if r.path=='/api/gmail/sync-readonly')
        self.new_lead=dict(zip(endpoint.__code__.co_freevars,[c.cell_contents for c in endpoint.__closure__]))['new_lead']
        self.mock_calls=[]
        for target in ('requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo'):
            p=patch(target,side_effect=AssertionError('Network is not allowed'));p.start();self.addCleanup(p.stop)

    def records(self):
        with closing(connect(self.path)) as db:return {t:[dict(x) for x in db.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in TABLES}

    def sync(self,fixtures=None):
        fixtures=fixtures or [FIXTURE]
        def api(token,path,params=None):
            self.mock_calls.append(path)
            if path=='labels':return {'labels':[{'id':'quote','name':FEEDS['quote']}]}
            if path=='messages':return {'messages':[{'id':x['id']} for x in fixtures]}
            if path.startswith('messages/'):
                x=next(x for x in fixtures if x['id']==path.split('/')[1])
                return {'id':x['id'],'internalDate':'1793422800000','payload':{'mimeType':'text/html','headers':[{'name':'Subject','value':x['subject']}],
                    'body':{'data':base64.urlsafe_b64encode(x['html'].encode()).decode()}}}
            raise AssertionError('Unexpected mock operation')
        with patch('app.gmail_sync._api',side_effect=api):return sync_gmail_readonly(self.path,'MOCK-ONLY','synthetic-current-mail',self.new_lead,feed_names=('quote',))

    def test_synthetic_current_layout_recovers_fields_and_records_review_without_downstream(self):
        parsed=_quote_fields(FIXTURE['html'],FIXTURE['subject'])
        expected={'client_name':'TEST Current Layout','email':'current-layout@example.invalid','phone':'5145550140','move_date':'2026-11-29',
            'pickup':'TEST pickup, Montreal','dropoff':'TEST destination, Laval','notes':'TEST ONLY. Two chairs and a table. Access and box count not supplied.'}
        self.assertEqual({k:parsed[k] for k in expected},expected)
        self.assertEqual(_quote_fields(_clean_html(FIXTURE['html']),FIXTURE['subject']),parsed)
        result=self.sync();self.assertEqual(result['new_leads'],1);r=self.records()
        self.assertEqual(len(r['client_accounts']),1);self.assertEqual(len(r['leads']),1)
        for field,value in expected.items():self.assertEqual(r['leads'][0][field],value)
        payload=json.loads(r['integration_events'][0]['payload_json'])
        self.assertEqual(payload['body'],_clean_html(FIXTURE['html']));self.assertEqual(payload['parsed'],parsed)
        draft=r['intake_drafts'][0];missing=json.loads(draft['missing_json'])
        for reason in ('approved pricing setup','box count','pickup access','destination access'):self.assertIn(reason,missing)
        self.assertEqual(draft['status'],'Needs details');self.assertIsNone(draft['quote_id'])
        for t in ('quotes','outbound_messages','jobs','assignments','payment_links','deposit_links','payments','job_invoices'):self.assertEqual(r[t],[],t)

    def test_retry_and_same_id_changed_content_cannot_duplicate_or_overwrite(self):
        self.sync();before=self.records();retry=self.sync()
        self.assertEqual(retry['duplicates'],1);self.assertEqual(before,self.records())
        changed=copy.deepcopy(FIXTURE);changed['html']=changed['html'].replace('TEST pickup, Montreal','Different unreviewed address')
        self.assertEqual(self.sync([changed])['duplicates'],1);self.assertEqual(before,self.records())

    def test_cropped_summary_does_not_invent_unseen_fields(self):
        x=copy.deepcopy(FIXTURE);x['html']=x['html'].split('<h2>Move Summary</h2>',1)[0]+'<h2>Move Summary</h2></body></html>'
        parsed=_quote_fields(x['html'],x['subject'])
        self.assertEqual(parsed['phone'],'5145550140');self.assertEqual(parsed['move_date'],'2026-11-29')
        for field in ('pickup','dropoff','notes','boxes','inventory'):self.assertEqual(parsed[field],'')
        self.assertTrue(any('subject' in s for s in parsed['parser_review']))
        self.sync([x]);self.assertTrue(json.loads(self.records()['intake_drafts'][0]['missing_json']))

    def test_conflicting_subject_and_body_dates_remain_blank_and_reviewed(self):
        x=copy.deepcopy(FIXTURE);x['html']=x['html'].replace('2026-11-29','2026-11-30')
        p=_quote_fields(x['html'],x['subject']);self.assertEqual(p['move_date'],'')
        self.assertTrue(any('conflicts' in r for r in p['parser_review']))
        self.sync([x]);self.assertIn('service date',json.loads(self.records()['intake_drafts'][0]['missing_json']))

    def test_two_conflicting_body_dates_do_not_fall_back_to_subject(self):
        x=copy.deepcopy(FIXTURE);x['html']=x['html'].replace('<p><strong>Pickup Address:', '<p>Moving Date: 2026-11-30</p><p><strong>Pickup Address:')
        p=_quote_fields(x['html'],x['subject']);self.assertEqual(p['move_date'],'')
        self.assertTrue(any('Conflicting values' in note for note in p['parser_review']))

    def test_blank_contact_is_not_replaced_by_footer(self):
        x=copy.deepcopy(FIXTURE);x['html']=x['html'].replace('current-layout@example.invalid','').replace('5145550140','')
        x['html']+='<p>Name: Company office</p><p>Email: office@example.invalid</p><p>Phone: 5145550199</p>'
        p=_quote_fields(x['html'],x['subject']);self.assertEqual(p['email'],'');self.assertEqual(p['phone'],'')
        self.sync([x]);self.assertEqual(self.records()['leads'],[])

    def test_conflicting_repeated_contacts_do_not_create_customer_records(self):
        x=copy.deepcopy(FIXTURE);x['html']=x['html'].replace('>5145550140</a>','>5145550199</a>')
        p=_quote_fields(x['html'],x['subject']);self.assertTrue(p['contact_conflict'])
        self.sync([x]);self.assertEqual(self.records()['leads'],[]);self.assertEqual(self.records()['client_accounts'],[])

    def test_same_label_system_or_unknown_mail_is_preserved_for_review_not_parsed_as_lead(self):
        messages=[{'id':'SYNTHETIC-SYSTEM-01','subject':'[Crown Movers] Your site has updated','html':'<p>Name: TEST software notice</p><p>Email: robot@example.invalid</p><p>Phone: 5145550140</p>'},
            {'id':'SYNTHETIC-UNKNOWN-01','subject':'Unclassified notification','html':'<p>Name: TEST unknown</p><p>Email: unknown@example.invalid</p>'}]
        with patch('app.gmail_sync._quote_fields',side_effect=AssertionError('Unclassified mail must not be parsed as a customer')):
            result=self.sync(messages)
        self.assertEqual(result['new_leads'],0);self.assertEqual(result['needs_review'],2)
        r=self.records();self.assertEqual(r['leads'],[]);self.assertEqual(r['client_accounts'],[])
        for event in r['integration_events']:
            self.assertEqual(event['status'],'Needs review');p=json.loads(event['payload_json'])
            self.assertIn('classification_review',p);self.assertNotIn('parsed',p)
            self.assertEqual(p['body'],_clean_html(next(m['html'] for m in messages if m['id']==p['gmail_id'])))

    def test_previously_accepted_subject_families_remain_recognized_without_replay(self):
        old=json.loads(Path(__file__).with_name('fixtures').joinpath('oct24_rendered_notifications.json').read_text())
        for x in old:self.assertTrue(_known_quote_notification(x['subject'],x['body']))
        subject='New Crown Movers Quote Request - TEST canonical - 2026-11-29'
        self.assertTrue(_known_quote_notification(subject,'CROWN_QUOTE_BUILDER_INPUT\nEND_CROWN_QUOTE_BUILDER_INPUT'))
