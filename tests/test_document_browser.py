"""Financial lists preserve receivables, actual delivery evidence, and role boundaries."""
from contextlib import closing
from datetime import date, timedelta
import json, unittest
from app.store import connect, transaction, one, insert, now, dumps
import test_launch_access as fixture

class DocumentBrowserTests(unittest.TestCase):
    staff=fixture.LaunchAccessTests.staff
    plan=fixture.LaunchAccessTests.plan
    invoice=fixture.LaunchAccessTests.invoice
    def setUp(self):fixture.LaunchAccessTests.setUp(self)
    def issued(self,due='2000-01-01'):
        crew=self.invoice()
        response=self.manager.patch('/api/documents/invoices/INV/due-date',json={'due_date':due,'version':1})
        self.assertEqual(response.status_code,200,response.text)
        r=crew.patch('/api/invoices/INV',json={'version':2,'service_hours':4,'quantities':{},'notes':'','reason':'Recorded completed service'})
        self.assertEqual(r.status_code,200,r.text)
        r=crew.post('/api/invoices/INV/issue',json={'version':3,'confirmed':True});self.assertEqual(r.status_code,200,r.text)
        return crew
    def record(self,client=None):return (client or self.owner).get('/api/invoices').json()[0]

    def test_overdue_and_verified_paid_use_issued_total_and_allocated_receipts(self):
        crew=self.issued();d=self.record(crew)
        self.assertEqual((d['billing_total_cents'],d['verified_paid_cents'],d['outstanding_cents']),(68500,30000,38500))
        self.assertEqual(d['display_state'],'Overdue');self.assertTrue(d['overdue']);self.assertEqual(d['email_status'],'Not sent')
        with transaction(self.path) as db:db.execute("UPDATE payments SET verified=0 WHERE id='P1'")
        d=self.record();self.assertEqual(d['verified_paid_cents'],0);self.assertEqual(d['outstanding_cents'],68500)

    def test_no_due_date_never_fabricates_overdue_and_draft_is_not_receivable(self):
        self.invoice();d=self.record();self.assertEqual(d['display_state'],'Draft');self.assertEqual(d['outstanding_cents'],0)
        self.assertEqual(d['verified_paid_cents'],0)
        r=self.owner.patch('/api/documents/invoices/INV/due-date',json={'due_date':'','version':1});self.assertEqual(r.status_code,200)
        crew=self.staff('crew');crew.patch('/api/invoices/INV',json={'version':2,'service_hours':4,'quantities':{},'reason':'Actual work checked'})
        crew.post('/api/invoices/INV/issue',json={'version':3,'confirmed':True})
        d=self.record();self.assertFalse(d['overdue']);self.assertEqual(d['display_state'],'Partial')

    def test_correction_draft_keeps_prior_issued_receivable_until_reissued(self):
        crew=self.issued();r=crew.post('/api/invoices/INV/correct',json={'version':4,'reason':'Correct actual service time'});self.assertEqual(r.status_code,200,r.text)
        crew.patch('/api/invoices/INV',json={'version':5,'service_hours':6,'quantities':{},'reason':'Corrected time checked'})
        d=self.record();self.assertEqual(d['display_state'],'Correction draft');self.assertEqual(d['billing_total_cents'],68500);self.assertEqual(d['outstanding_cents'],38500);self.assertTrue(d['overdue'])
        q=self.owner.get('/api/documents/quotes').json()[0];self.assertEqual(q['display_state'],'Invoiced')

    def test_due_date_is_explicit_versioned_and_manager_only(self):
        self.invoice()
        for role in ('crew','sales','dispatch','accounting'):
            self.assertEqual(self.staff(role).patch('/api/documents/invoices/INV/due-date',json={'due_date':'2026-11-30','version':1}).status_code,403)
        for value in ('tomorrow','2026-02-30','20261130',None):
            self.assertEqual(self.owner.patch('/api/documents/invoices/INV/due-date',json={'due_date':value,'version':1}).status_code,422)
        self.assertEqual(self.owner.patch('/api/documents/invoices/INV/due-date',json={'due_date':'2026-11-30','version':999}).status_code,409)
        r=self.manager.patch('/api/documents/invoices/INV/due-date',json={'due_date':'2026-11-30','version':1});self.assertEqual(r.status_code,200,r.text)

    def test_private_links_do_not_send_and_preserve_assigned_invoice_boundaries(self):
        crew=self.issued();url='/api/documents/invoices/INV/share';data={'confirmed':True,'version':4,'revision':1}
        for role in ('lead',):self.assertEqual(self.staff(role).post(url,json=data).status_code,403)
        self.assertEqual(crew.post(url,json={**data,'version':2}).status_code,409)
        r=crew.post(url,json=data);self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['sent']);self.assertIn('/invoice-access',r.json()['url'])
        self.assertEqual(crew.get('/api/documents/quotes').status_code,403)
        self.assertEqual(crew.post('/api/documents/quotes/Q1/share',json={'confirmed':True}).status_code,403)
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM outbound_messages').fetchone()[0],0)
        self.assertEqual(self.staff('lead').get('/api/documents/invoices/INV/print').status_code,403)

    def test_print_uses_saved_revision_and_escapes_client_text(self):
        self.issued();r=self.owner.get('/api/documents/invoices/INV/print');self.assertEqual(r.status_code,200,r.text);self.assertIn('2000-01-01',r.text);self.assertIn('Print / save as PDF',r.text)
        self.assertEqual(self.owner.patch('/api/documents/invoices/INV/due-date',json={'due_date':'2026-12-01','version':4}).status_code,409)
        with transaction(self.path) as db:db.execute("UPDATE quotes SET inputs_json=?,lead_snapshot_json=? WHERE id='Q1'",(dumps({'quote_title':'<script>alert(1)</script>'}),dumps({'client_name':'<img src=x onerror=alert(1)>','notes':'PRIVATE INTERNAL NOTE'})))
        r=self.owner.get('/api/documents/quotes/Q1/print');self.assertEqual(r.status_code,200,r.text);self.assertIn('&lt;script&gt;',r.text);self.assertNotIn('<img src=x',r.text);self.assertNotIn('PRIVATE INTERNAL NOTE',r.text)

    def test_quote_tracking_uses_confirmed_sends_views_and_expiry_without_truncating(self):
        with transaction(self.path) as db:
            db.execute("UPDATE quotes SET status='Approved',inputs_json=? WHERE id='Q1'",(dumps({'expiry_date':'2000-01-01'}),))
            insert(db,'quote_views',dict(id='V1',quote_id='Q1',lead_id='L1',session_hash='s',time_bucket=1,viewed_at=now()))
            for n in range(2,303):
                insert(db,'quotes',dict(id='Q'+str(n),lead_id='L1',revision=n,status='Draft',inputs_json='{}',result_json='{}',pricing_id='PRICE',lead_snapshot_json='{}',lead_version=1,created_by='owner',created_at=now()))
        data=self.owner.get('/api/documents/quotes').json();self.assertEqual(len(data),302)
        q=next(x for x in data if x['id']=='Q1');self.assertEqual(q['display_state'],'Expired');self.assertEqual(q['opens'],1);self.assertEqual(q['email_status'],'Not sent')
        self.assertEqual(self.owner.post('/api/documents/quotes/Q1/share',json={'confirmed':True}).status_code,409)
        with transaction(self.path) as db:
            db.execute("UPDATE quotes SET inputs_json='{}' WHERE id='Q1'")
            insert(db,'outbound_messages',dict(id='MAIL',lead_id='L1',event_key='quote-delivery:Q1:1',recipient='test@example.invalid',subject='test',body='test',category='Quote',status='Sent',created_by='owner',created_at=now(),sent_at=now()))
        q=next(x for x in self.owner.get('/api/documents/quotes').json() if x['id']=='Q1');self.assertEqual(q['display_state'],'Sent');self.assertIsNotNone(q['sent_at'])
