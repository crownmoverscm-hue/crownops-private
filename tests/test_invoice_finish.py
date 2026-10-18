"""Real-service actuals, verified deposits, extras and uncertain email recovery."""
from contextlib import closing
from datetime import datetime,timezone,timedelta
from email.message import EmailMessage
from unittest.mock import patch
import base64,json,unittest
from app.store import connect,transaction,one,insert,audit,now,dumps
from app.platform_common import put_setting
import test_launch_access as fixture

class InvoiceFinishTests(unittest.TestCase):
    staff=fixture.LaunchAccessTests.staff
    plan=fixture.LaunchAccessTests.plan
    invoice=fixture.LaunchAccessTests.invoice
    def setUp(self):fixture.LaunchAccessTests.setUp(self)
    def save_actuals(self,crew,version=1,hours=4):
        r=crew.patch('/api/invoices/INV',json={'version':version,'service_hours':hours,'quantities':{},'notes':'','reason':'Actual service and used materials checked'})
        self.assertEqual(r.status_code,200,r.text);return r.json()
    def issue(self,crew,version=2):return crew.post('/api/invoices/INV/issue',json={'version':version,'confirmed':True})

    def test_draft_preparation_keeps_estimated_hours_separate_and_applies_verified_deposit(self):
        from app.invoices import validate_terms
        with transaction(self.path) as db:
            q=one(db,"SELECT * FROM quotes WHERE id='Q1'");r=json.loads(q['result_json']);r.update(service_low=3,hourly_rate_cents=15000,minimum_hours=3,transport_cents=5000,fuel_cents=3500,discount_cents=0,other_cents=0)
            db.execute("UPDATE quotes SET result_json=?,approved_by='owner',approved_at=? WHERE id='Q1'",(dumps(r),now()))
            db.execute("UPDATE jobs SET status='Completed' WHERE id='J1'")
            put_setting(db,'billing_policy',{'approved':True,'terms':{'issuer':{'name':'Crown','address':'Test address'},'minimum_hours':3,'taxes':[],'catalogue':[],'approval_reference':'Reviewed billing'}})
            insert(db,'assignments',dict(job_id='J1',resource_type='employee',resource_id='E1'))
        crew=self.staff('crew');response=crew.post('/api/jobs/J1/invoice-from-quote',json={});self.assertEqual(response.status_code,200,response.text)
        iid=response.json()['id'];doc=crew.get('/api/invoices/'+iid).json();self.assertIsNone(doc['actual']['service_hours']);self.assertEqual(doc['actual']['estimated_service_hours'],3)
        self.assertEqual(doc['calculation']['deposit_cents'],30000);self.assertFalse(doc['calculation']['actual_hours_recorded'])
        self.assertEqual(crew.post('/api/invoices/'+iid+'/issue',json={'version':1,'confirmed':True}).status_code,422)
        retry=crew.post('/api/jobs/J1/invoice-from-quote',json={});self.assertTrue(retry.json()['already_prepared'])
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM invoice_payment_allocations').fetchone()[0],1)

    def test_previous_prefilled_quote_hours_cannot_be_issued_without_actuals_save(self):
        crew=self.invoice()
        with transaction(self.path) as db:
            db.execute("UPDATE job_invoices SET actual_json=? WHERE id='INV'",(dumps({'service_hours':3,'quantities':{}}),))
            audit(db,'owner','Invoice prepared from accepted quote','job_invoices','INV',{'quote_id':'Q1'})
        self.assertTrue(crew.get('/api/invoices/INV').json()['actuals_need_review'])
        self.assertEqual(self.issue(crew,1).status_code,422)
        self.save_actuals(crew);self.assertEqual(self.issue(crew).status_code,200)

    def test_deposit_is_applied_once_and_final_invoice_and_payroll_use_actual_hours(self):
        crew=self.invoice();doc=self.save_actuals(crew,hours=4.5)
        self.assertEqual(doc['calculation']['deposit_cents'],30000);self.assertEqual(doc['calculation']['balance_cents'],46000)
        doc=self.save_actuals(crew,2,4.5);self.assertEqual(doc['calculation']['deposit_cents'],30000)
        self.assertEqual(self.issue(crew,3).status_code,200)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM invoice_payment_allocations').fetchone()[0],1)
            issued=json.loads(one(db,"SELECT document_json FROM invoice_revisions WHERE invoice_id='INV'")['document_json'])
            self.assertEqual(issued['calculation']['actual_service_hours'],4.5)

    def test_pending_commercial_change_blocks_confirmation_but_contact_change_does_not(self):
        with transaction(self.path) as db:
            insert(db,'client_changes',dict(id='CHANGE',lead_id='L1',payload_json=dumps({'pickup':'Different origin'}),base_version=1,created_at=now()))
        response=self.owner.put('/api/jobs/J1/plan',json=self.plan(confirm=True))
        self.assertEqual(response.status_code,409,response.text)
        with transaction(self.path) as db:db.execute("UPDATE client_changes SET payload_json=? WHERE id='CHANGE'",(dumps({'phone':'5145550198'}),))
        response=self.owner.put('/api/jobs/J1/plan',json=self.plan(confirm=True));self.assertEqual(response.status_code,200,response.text)

    def test_unverified_and_reversed_deposits_do_not_reduce_invoice(self):
        crew=self.invoice()
        with transaction(self.path) as db:
            db.execute("UPDATE payments SET verified=0 WHERE id='P1'")
            insert(db,'payments',dict(id='P2',lead_id='L1',amount_cents=10000,method='Cash',purpose='Deposit',reference='P2',verified=1,recorded_by='owner',created_at=now()))
            insert(db,'payments',dict(id='R2',lead_id='L1',amount_cents=-10000,method='Cash',purpose='Deposit',reference='Reversed',verified=1,reverses_id='P2',recorded_by='owner',created_at=now()))
        self.assertEqual(self.save_actuals(crew)['calculation']['deposit_cents'],0)

    def test_multiple_service_phases_need_explicit_deposit_allocation_and_management_review(self):
        crew=self.invoice()
        with transaction(self.path) as db:insert(db,'jobs',dict(id='J2',lead_id='L1',title='Separate packing',phase='Packing',start_at='2026-11-14T14:00:00+00:00',end_at='2026-11-14T19:00:00+00:00',created_by='owner',created_at=now()))
        doc=self.save_actuals(crew);self.assertEqual(doc['calculation']['deposit_cents'],0);self.assertTrue(doc['deposit_review']['review_required'])
        self.assertEqual(self.issue(crew).status_code,409)
        self.assertEqual(crew.post('/api/invoices/INV/deposit-review',json={'version':2,'reason':'Reserved for packing service'}).status_code,403)
        r=self.manager.post('/api/invoices/INV/allocate-payment',json={'payment_id':'P1','amount':300});self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.issue(crew,3).status_code,200)

    def test_excess_deposit_needs_audited_manager_credit_review(self):
        crew=self.invoice()
        with transaction(self.path) as db:db.execute("UPDATE payments SET amount_cents=80000 WHERE id='P1'")
        doc=self.save_actuals(crew);self.assertEqual(doc['calculation']['balance_cents'],0);self.assertEqual(doc['deposit_review']['unallocated_cents'],11500)
        self.assertEqual(self.issue(crew).status_code,409)
        review=self.manager.post('/api/invoices/INV/deposit-review',json={'version':2,'reason':'Remaining credit115.00 reviewed; arrange refund under management approval'})
        self.assertEqual(review.status_code,200,review.text);self.assertEqual(self.issue(crew,3).status_code,200)

    def extra(self,crew,key='material-request-001'):
        r=crew.post('/api/invoices/INV/extras',json={'version':2,'request_id':key,'name':'Wardrobe box','quantity':2,'unit_price':18,'reason':'Client requested two additional wardrobe boxes'})
        self.assertEqual(r.status_code,200,r.text);return r.json()

    def test_new_material_is_not_charged_until_manager_records_client_agreement(self):
        crew=self.invoice();before=self.save_actuals(crew);extra=self.extra(crew)
        self.assertEqual(crew.get('/api/invoices/INV').json()['calculation']['total_cents'],before['calculation']['total_cents'])
        self.assertEqual(self.issue(crew).status_code,409)
        body={'version':2,'decision':'Approve','unit_price':20,'agreement':'Client agreed to two boxes at20 dollars each','client_agreed':True}
        self.assertEqual(crew.post('/api/invoices/INV/extras/'+extra['id']+'/review',json=body).status_code,403)
        response=self.manager.post('/api/invoices/INV/extras/'+extra['id']+'/review',json=body);self.assertEqual(response.status_code,200,response.text)
        doc=crew.get('/api/invoices/INV').json();self.assertEqual(doc['calculation']['total_cents'],before['calculation']['total_cents']+4000)
        self.assertEqual(self.issue(crew,3).status_code,200)
        self.assertEqual(self.manager.post('/api/invoices/INV/extras/'+extra['id']+'/review',json={**body,'version':3}).status_code,409)

    def test_extras_reject_replayed_payloads_missing_agreement_and_other_crew(self):
        crew=self.invoice();self.save_actuals(crew);extra=self.extra(crew);self.assertEqual(self.extra(crew)['id'],extra['id'])
        r=self.staff('lead').get('/api/invoices/INV/extras');self.assertEqual(r.status_code,403)
        body={'version':2,'decision':'Approve','unit_price':20,'agreement':'Staff proposes a higher price'}
        self.assertEqual(self.manager.post('/api/invoices/INV/extras/'+extra['id']+'/review',json=body).status_code,422)
        r=self.manager.post('/api/invoices/INV/extras/'+extra['id']+'/review',json={**body,'decision':'Decline','agreement':'Client did not agree; item was not used'})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.issue(crew).status_code,200)

    def unknown(self):
        crew=self.invoice();self.save_actuals(crew);self.assertEqual(self.issue(crew).status_code,200)
        with transaction(self.path) as db:insert(db,'outbound_messages',dict(id='EMAIL',event_key='invoice-delivery:INV:1',lead_id='L1',job_id='J1',recipient='client@example.invalid',subject='Invoice test',body='Private invoice test body',category='Invoice',status='Unknown',created_by='crew',created_at=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()))
        with transaction(self.path) as db:
            audit(db,'crew','Email delivery attempt started','outbound_messages','EMAIL',{'recipient':'client@example.invalid'})
            db.execute("UPDATE audit SET created_at=? WHERE object_id='EMAIL' AND action='Email delivery attempt started'",((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),))
        return crew,{'version':3,'revision':1,'message_id':'EMAIL','confirmed':True,'note':'Checked the exact recipient and company Sent email'}

    def test_unknown_sent_email_can_be_proven_without_resending(self):
        crew,data=self.unknown()
        with patch('app.invoice_management.gmail_delivery_evidence',return_value={'provider_id':'SENT123','sent_at':now()}),patch('app.providers.send_gmail') as send:
            self.assertEqual(crew.post('/api/invoices/INV/resolve-delivery',json=data).status_code,403)
            r=self.manager.post('/api/invoices/INV/resolve-delivery',json=data);self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['sent']);send.assert_not_called()
        self.assertEqual(crew.get('/api/invoices/INV/delivery-state').json()['status'],'Sent')

    def test_unknown_absence_or_provider_outage_does_not_silently_permit_retry(self):
        _,data=self.unknown()
        with patch('app.invoice_management.gmail_delivery_evidence',return_value=None):
            self.assertEqual(self.manager.post('/api/invoices/INV/resolve-delivery',json=data).status_code,409)
            r=self.manager.post('/api/invoices/INV/resolve-delivery',json={**data,'confirmed_not_sent':True});self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['status'],'Draft')
        with closing(connect(self.path)) as db:self.assertIn('not delivered',one(db,"SELECT error FROM outbound_messages WHERE id='EMAIL'")['error'])

    def test_legacy_unknown_delivery_waits_from_first_observation_not_draft_creation(self):
        _,data=self.unknown()
        with transaction(self.path) as db:db.execute("DELETE FROM audit WHERE object_id='EMAIL' AND action='Email delivery attempt started'")
        with patch('app.invoice_management.gmail_delivery_evidence',return_value=None):
            response=self.manager.post('/api/invoices/INV/resolve-delivery',json={**data,'confirmed_not_sent':True})
            self.assertEqual(response.status_code,409,response.text)
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db,"SELECT status FROM outbound_messages WHERE id='EMAIL'")['status'],'Unknown')
            self.assertIsNotNone(one(db,"SELECT created_at FROM audit WHERE object_id='EMAIL' AND action='Untracked invoice delivery first observed'"))

    def test_gmail_evidence_requires_sent_label_exact_message_recipient_and_body(self):
        from app.invoice_management import gmail_delivery_evidence
        message={'id':'EMAIL','recipient':'client@example.invalid','subject':'Invoice test','body':'Exact private body'}
        mail=EmailMessage();mail['Message-ID']='<email@crownops.local>';mail['To']='client@example.invalid';mail['Subject']='Invoice test';mail.set_content(message['body'])
        remote={'labelIds':['SENT'],'internalDate':'1792230000000','raw':base64.urlsafe_b64encode(mail.as_bytes()).decode()}
        with patch('app.providers.provider_token',return_value={'access_token':'fake'}),patch('app.gmail_sync._api',side_effect=[{'messages':[{'id':'abc123'}]},remote]):
            self.assertEqual(gmail_delivery_evidence(self.path,message)['provider_id'],'abc123')
        mail.replace_header('To','someoneelse@example.invalid');remote['raw']=base64.urlsafe_b64encode(mail.as_bytes()).decode()
        from fastapi import HTTPException
        with patch('app.providers.provider_token',return_value={'access_token':'fake'}),patch('app.gmail_sync._api',side_effect=[{'messages':[{'id':'abc123'}]},remote]):
            with self.assertRaises(HTTPException):gmail_delivery_evidence(self.path,message)
