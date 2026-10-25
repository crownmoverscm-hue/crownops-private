"""Disposable-db contracts only: no Gmail, Intuit, email or payment requests."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import html
import json
import unittest
from unittest.mock import patch
from app.gmail_sync import _quote_fields, _clean_html
from app.main import sha
from app.store import connect, transaction, insert, rows, now, dumps, one
from app.quebec_taxes import taxable_move
from app.sales_crm_v6 import rate_for
import test_template_quotes as fixture


def synthetic_form(fr=False,day='2026-11-15'):
    # Synthetic regression fixtures, NOT the missing original tester bodies.
    pairs=[('Nom complet' if fr else 'Client Name','TEST — MUSE — Recovery'),
           ('Courriel' if fr else 'Email Address','recovery@example.invalid'),
           ('Numéro de téléphone' if fr else 'Phone Number','(514) 555-0123'),
           ('Date du déménagement' if fr else 'Move Date',day),
           ('Adresse de départ' if fr else 'Pickup Address','100 Test Street'),
           ('Adresse de destination' if fr else 'Destination Address','200 Test Street'),
           ('Taille du logement' if fr else 'Home Size','2 bedrooms'),
           ('Boîtes' if fr else 'Boxes','25'),('Inventaire' if fr else 'Inventory','Sofa and bed'),
           ('Commentaires' if fr else 'Additional Details','TEST only'),
           ('Accès au départ' if fr else 'Pickup Access','Elevator'),
           ('Accès à destination' if fr else 'Drop-off Access','1 flight'),
           ('Distance de marche au départ' if fr else 'Walk at Pickup','10 m'),
           ('Distance de marche à destination' if fr else 'Walk at Drop-off','20 m')]
    return '<table>'+''.join('<tr><th>'+k+':</th><td><a title="quoted > 999999" href="https://example.invalid">'+html.escape(v)+'</a></td></tr>' for k,v in pairs)+'</table>'


class ParserContractTests(unittest.TestCase):
    def test_four_synthetic_formats_keep_visible_contact_and_move_fields(self):
        for subject,fr,escaped in [('Quick Quote Form EN',False,False),('Form Submission from Free Quote FR Page',True,False),
                                  ('New Instant Moving Estimate Request - Crown Movers',False,True),
                                  ('New Instant Moving Estimate Request - Crown Movers FR',True,True)]:
            with self.subTest(subject=subject):
                body=synthetic_form(fr)
                data=_quote_fields(html.escape(body) if escaped else body,subject)
                self.assertEqual(data['email'],'recovery@example.invalid')
                self.assertEqual(data['phone'],'5145550123')
                for key in ('client_name','move_date','pickup','dropoff','home_size','boxes','inventory','pickup_access','dropoff_access','pickup_walk','dropoff_walk'):
                    self.assertTrue(data[key],key)
                self.assertEqual(data['language'],'French' if fr else 'English')
                self.assertEqual(data['inventory'],'Sofa and bed')

    def test_hidden_content_is_not_a_contact_and_cleaning_is_idempotent(self):
        body='<style>Email: wrong@example.invalid</style><p>Email: visible@example.invalid</p>'
        cleaned=_clean_html(body)
        self.assertNotIn('wrong',cleaned)
        self.assertEqual(_clean_html(cleaned),cleaned)

    def test_quebec_taxes_do_not_compound(self):
        self.assertEqual(taxable_move(10000),{'subtotal_cents':10000,'gst_cents':500,'qst_cents':998,'tax_cents':1498,'total_cents':11498})
        self.assertEqual(taxable_move(0)['total_cents'],0)


class LaunchWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TemplateQuoteTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path,self.client=self.f.path,self.f.client
        with transaction(self.path) as db:
            insert(db,'users',dict(id='launch-owner',name='Owner',email='owner@example.invalid',role='owner',password_hash='unused',active=1,created_at=now()))
            insert(db,'sessions',dict(token_hash=sha('launch-owner-token'),user_id='launch-owner',csrf='launch-csrf',expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
        self.client.cookies.set('crownops_session','launch-owner-token');self.client.headers['x-csrf-token']='launch-csrf'

    def counts(self):
        with closing(connect(self.path)) as db:
            return {t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('leads','client_accounts','quotes','messages','invitations','material_catalog','jobs','outbound_messages')}

    def stored_form(self,eid='FORM-1',body=None,day='2026-11-15'):
        with transaction(self.path) as db:
            insert(db,'integration_events',dict(id=eid,event_key='gmail:'+eid,event_type='gmail_quote',object_id=eid,
                payload_json=dumps({'subject':'Quick Quote Form EN','body':body if body is not None else synthetic_form(day=day),'parsed':{'email':'','phone':''}}),status='Needs review',created_at=now()))

    def test_recovery_preview_is_read_only_apply_returns_ids_and_retries_do_not_duplicate(self):
        self.stored_form();self.stored_form('FORM-2',day='2026-11-16');before=self.counts()
        req={'event_ids':['FORM-1','FORM-2']}
        preview=self.client.post('/api/intake/recovery/preview',json=req)
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(before,self.counts())
        self.assertTrue(all(x['status']=='Ready' for x in preview.json()['items']))
        apply={**req,'confirm':True,'preview_hash':preview.json()['preview_hash']}
        result=self.client.post('/api/intake/recovery/apply',json=apply)
        self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(all(x['service_id'] and x['client_id'] for x in result.json()['items']))
        self.assertEqual(self.counts()['client_accounts'],1);self.assertEqual(self.counts()['leads'],2)
        # Missing arrival/driving details must be explicit, never guessed prices.
        self.assertTrue(all(x['missing'] for x in result.json()['items']))
        after=self.counts()
        self.assertEqual(self.client.post('/api/intake/recovery/apply',json=apply).status_code,409)
        self.assertEqual(after,self.counts());self.assertEqual(after['outbound_messages'],0);self.assertEqual(after['jobs'],0)
        with closing(connect(self.path)) as db:
            payload=json.loads(one(db,'SELECT payload_json FROM integration_events WHERE id=?',('FORM-1',))['payload_json'])
            self.assertEqual(payload['body'],synthetic_form())

    def test_recovery_bad_contact_reports_field_and_does_not_create_shell(self):
        self.stored_form(body='Name: TEST only\nEmail: not-an-email\nPhone: unknown')
        r=self.client.post('/api/intake/recovery/preview',json={'event_ids':['FORM-1']}).json()
        self.assertEqual(r['items'][0]['status'],'Blocked');self.assertIn('email or phone',r['items'][0]['failing_fields'][0])
        self.assertEqual(self.counts()['leads'],0)

    def test_manual_review_uses_fresh_parse_not_stale_empty_parsed_fields(self):
        from app.gmail_sync import _review_details
        self.stored_form()
        with closing(connect(self.path)) as db:
            payload,match=_review_details(db,one(db,'SELECT * FROM integration_events WHERE id=?',('FORM-1',)))
            self.assertEqual(payload['parsed']['email'],'recovery@example.invalid');self.assertFalse(match['requires_review'])

    def test_previews_never_create_invitations_materials_or_messages(self):
        before=self.counts()
        staff=self.client.post('/api/launch/staff-invitations/preview',json={'rows':[{'name':'TEST Sales','email':'test@example.invalid','role':'sales'}]})
        self.assertEqual(staff.status_code,200,staff.text);self.assertEqual(staff.json()['valid_rows'],1)
        mat=self.client.post('/api/launch/materials/preview',json={'rows':[{'code':'TEST_BOX','name':'Test box','category':'Boxes','sell_price':'12.50','cost_price':'4.00','taxable':True,'active':True}]})
        self.assertEqual(mat.status_code,200,mat.text);self.assertEqual(mat.json()['items'][0]['values']['sell_cents'],1250)
        self.assertEqual(before,self.counts())

    def test_duplicate_staff_missing_crew_mapping_and_blank_prices_are_flagged(self):
        r=self.client.post('/api/launch/staff-invitations/preview',json={'rows':[{'name':'A','email':'crew@example.invalid','role':'crew'},{'name':'B','email':'crew@example.invalid','role':'crew'}]}).json()
        self.assertEqual(r['invalid_rows'],2)
        r=self.client.post('/api/launch/materials/preview',json={'rows':[{'code':'TEST','name':'Box','category':'Boxes','taxable':True,'active':True}]}).json()
        self.assertEqual(r['invalid_rows'],1)

    def test_setup_previews_owner_only_and_recovery_denies_crew(self):
        self.client.cookies.set('crownops_session','sales-token');self.client.headers['x-csrf-token']='sales-csrf'
        for route in ('/api/launch/staff-invitations/preview','/api/launch/materials/preview','/api/intake/recovery/preview'):
            self.assertEqual(self.client.post(route,json={}).status_code,403)

    def test_2027_never_silently_inherits_yearless_2026_prices(self):
        from fastapi import HTTPException
        with closing(connect(self.path)) as db:
            with self.assertRaises(HTTPException) as error:rate_for(db,'2027-11-15',2)
            self.assertIn('effective-dated',error.exception.detail)
            self.assertEqual(str(rate_for(db,'2026-11-15',2)[0]),'135')

    def test_migrated_future_rates_are_not_owner_approval(self):
        from fastapi import HTTPException
        with transaction(self.path) as db:
            db.execute("UPDATE annual_rate_periods SET start_date='2027-11-01',end_date='2027-11-30',approved_by='migration' WHERE id='TEST-RATE'")
            with self.assertRaises(HTTPException):rate_for(db,'2027-11-15',2)
            db.execute("UPDATE annual_rate_periods SET approved_by='launch-owner' WHERE id='TEST-RATE'")
            self.assertEqual(str(rate_for(db,'2027-11-15',2)[0]),'135')

    def test_signed_webhook_retries_share_client_and_return_actual_draft_status(self):
        import hashlib,hmac,time
        secret='TEST-ONLY-'+('x'*40)
        def submit(event,**changes):
            lead={'client_name':'TEST Webhook','email':'WEBHOOK@example.invalid','move_date':'2026-11-15',**changes}
            body=json.dumps({'event_id':event,'lead':lead}).encode();stamp=str(int(time.time()))
            signature=hmac.new(secret.encode(),stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
            return self.client.post('/api/webhooks/intake',content=body,headers={'content-type':'application/json','x-crown-timestamp':stamp,'x-crown-signature':signature})
        with patch.dict('os.environ',{'CROWNOPS_WEBHOOK_SECRET':secret}):
            first=submit('test-form-1');self.assertEqual(first.status_code,200,first.text)
            repeat=submit('test-form-1');self.assertTrue(repeat.json()['duplicate'])
            second=submit('test-form-2',move_date='2026-11-16');self.assertEqual(second.status_code,200,second.text)
            self.assertEqual(first.json()['client_id'],second.json()['client_id'])
            self.assertEqual(self.counts()['leads'],2);self.assertEqual(self.counts()['client_accounts'],1)
            self.assertIsNone(first.json()['quote_id']);self.assertTrue(first.json()['missing'])
            self.assertEqual(submit('bad-date',move_date='2026-02-30').status_code,422)
            self.assertEqual(submit('bad-stage',stage='Booked').status_code,422)
            self.assertEqual(submit('test-form-1',pickup='Changed').status_code,409)
