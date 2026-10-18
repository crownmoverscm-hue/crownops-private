"""Payroll completeness, private costs, and idempotent read-only accounting import."""
from contextlib import closing
from unittest.mock import patch
import csv
import io
import unittest
from app.store import connect, transaction, insert, one, now, dumps, initialize
from app.cost_tracking import stage_records
from app.main import sha
import test_job_planning as fixture

class WorkedHoursCostsTests(unittest.TestCase):
    setUp=fixture.JobPlanningTests.setUp
    staff=fixture.JobPlanningTests.staff
    params='?start=2026-11-01&end=2026-11-30'

    def team(self,status='Completed'):
        with transaction(self.path) as db:
            db.execute('UPDATE jobs SET status=? WHERE id=\'J1\'',(status,))
            for eid in ('E1','E2'):insert(db,'assignments',dict(job_id='J1',resource_type='employee',resource_id=eid))

    def submit(self,eids=('E1','E2'),hours=3,client=None,**extra):
        return (client or self.owner).post('/api/ux/jobs/J1/crew-hours',json=dict(employee_ids=list(eids),team_hours=hours,**extra))

    def approve(self):
        return self.owner.post('/api/ux/payroll-summary/approve',json={'start':'2026-11-01','end':'2026-11-30'})

    def manual(self,**extra):
        body=dict(date='2026-11-15',vendor='Fuel vendor',subtotal=100,tax=14.98,category='Fuel',job_id='J1',status='Paid',request_key='expense-unique-request-2026')
        body.update(extra);r=self.owner.post('/api/expenses',json=body);self.assertEqual(r.status_code,200,r.text);return r.json()

    def source(self,kind='Purchase',realm='123',environment='production',**extra):
        raw=dict(Id='10',TxnDate='2026-11-15',CurrencyRef={'value':'CAD'},TotalAmt=114.98,TxnTaxDetail={'TotalTax':14.98},EntityRef={'name':'Fuel vendor'},VendorRef={'name':'Fuel vendor'},Balance=0,SyncToken='0')
        raw.update(extra)
        with transaction(self.path) as db:stage_records(db,realm,environment,[(kind,raw)],'owner')
        return raw

    def review_list(self):return self.owner.get('/api/costs/quickbooks').json()['items']

    def review(self,s,action='create',**extra):
        return self.owner.post('/api/costs/quickbooks/'+s['id']+'/review',json=dict(version=s['version'],action=action,category='Fuel',job_id='J1',**extra))

    def test_crew_can_record_only_own_hours_and_lead_can_record_team(self):
        self.team('In progress');crew=self.staff('crew')
        d=crew.get('/api/ux/jobs/J1/crew-hours').json();self.assertTrue(d['own_only']);self.assertEqual([e['id'] for e in d['employees']],['E1'])
        self.assertEqual(self.submit(client=crew).status_code,403)
        self.assertEqual(self.submit(('E1',),client=crew).status_code,200)
        lead=self.staff('lead');self.assertFalse(lead.get('/api/ux/jobs/J1/crew-hours').json()['own_only'])
        self.assertEqual(self.submit(client=lead,hours=4).status_code,200)
        self.assertEqual(self.submit(client=self.staff('sales')).status_code,403)

    def test_missing_and_inactive_hours_remain_visible_without_invoice(self):
        self.team();self.submit(('E1',))
        with transaction(self.path) as db:db.execute("UPDATE employees SET active=0 WHERE id='E1'")
        d=self.owner.get('/api/ux/payroll-summary'+self.params).json();self.assertEqual(d['total_hours'],3);self.assertEqual(d['missing_entries'],1)
        self.assertFalse(next(e for e in d['employees'] if e['employee_id']=='E1')['active'])
        self.assertEqual(self.owner.get('/api/ux/payroll-hours/export.csv'+self.params).status_code,409)
        self.assertEqual(self.staff('crew').get('/api/ux/payroll-summary'+self.params).json()['missing_entries'],0)

    def test_approval_completed_only_and_pay_rate_snapshot_is_stable(self):
        self.team('In progress');self.submit()
        self.assertEqual(self.approve().json()['approved'],0)
        with transaction(self.path) as db:db.execute("UPDATE jobs SET status='Completed' WHERE id='J1'")
        self.assertEqual(self.approve().json()['approved'],2)
        with transaction(self.path) as db:db.execute('UPDATE employees SET hourly_cents=9900')
        report=self.owner.get('/api/ux/payroll-summary'+self.params).json();self.assertEqual(report['employees'][0]['gross_cents'],7500)
        r=self.owner.get('/api/ux/payroll-hours/export.csv'+self.params);self.assertEqual(r.status_code,200,r.text)
        records=list(csv.DictReader(io.StringIO(r.text)));self.assertEqual(len(records),2);self.assertEqual(records[0]['Approved hourly rate CAD'],'25.00')
        self.assertEqual(records[0]['Approved minutes'],'180')
        self.assertEqual(self.staff('sales').get('/api/ux/payroll-hours/export.csv'+self.params).status_code,403)
        for e in self.staff('sales').get('/api/times').json():self.assertNotIn('approved_hourly_cents',e)

    def test_edit_invalidates_approval_and_stale_edit_is_rejected(self):
        self.team();self.submit();self.approve()
        t=self.owner.get('/api/ux/payroll-summary'+self.params).json()['employees'][0]['details'][0]
        r=self.owner.patch('/api/ux/payroll-time/'+t['time_id'],json={'hours':4,'version':t['version']});self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.owner.patch('/api/ux/payroll-time/'+t['time_id'],json={'hours':5,'version':t['version']}).status_code,409)
        self.assertEqual(self.owner.get('/api/ux/payroll-hours/export.csv'+self.params).status_code,409)
        with closing(connect(self.path)) as db:
            row=one(db,'SELECT * FROM times WHERE id=?',(t['time_id'],));self.assertEqual(row['status'],'Submitted');self.assertIsNone(row['approved_hourly_cents'])

    def test_bad_hours_dates_and_pending_rates_do_not_partially_approve(self):
        self.team()
        for invalid in ('NaN','Infinity',True,-1,25,0.001):self.assertEqual(self.submit(hours=invalid).status_code,422)
        self.submit()
        with transaction(self.path) as db:db.execute("UPDATE employees SET hourly_cents=0 WHERE id='E2'")
        self.assertEqual(self.approve().status_code,409)
        with closing(connect(self.path)) as db:self.assertEqual(one(db,"SELECT count(*) n FROM times WHERE status='Approved'")['n'],0)
        for dates in ({'start':'2026-12-01','end':'2026-11-01'},{'start':'2020-01-01','end':'2026-11-01'}):
            self.assertEqual(self.owner.post('/api/ux/payroll-summary/approve',json=dates).status_code,422)

    def test_lead_can_keep_approved_hours_while_entering_missing_teammate(self):
        self.team();self.submit(('E1',),note='Keep this note')
        with closing(connect(self.path)) as db:tid=one(db,"SELECT id FROM times WHERE employee_id='E1'")['id']
        self.assertEqual(self.owner.post('/api/times/'+tid+'/approve').status_code,200)
        r=self.submit(client=self.staff('lead'));self.assertEqual(r.status_code,200,r.text)
        with closing(connect(self.path)) as db:
            t=one(db,'SELECT * FROM times WHERE id=?',(tid,));self.assertEqual(t['status'],'Approved');self.assertEqual(t['note'],'Keep this note')
        self.assertEqual(self.submit(client=self.staff('lead'),hours=4).status_code,409)

    def test_approval_checks_the_reviewed_snapshot(self):
        self.team();self.submit()
        before=self.owner.get('/api/ux/payroll-summary'+self.params).json()
        self.submit(hours=4)
        r=self.owner.post('/api/ux/payroll-summary/approve',json={'start':before['start'],'end':before['end'],'review_token':before['review_token']})
        self.assertEqual(r.status_code,409)
        self.assertEqual(self.owner.get('/api/ux/payroll-summary'+self.params).json()['approved_hours'],0)

    def test_exact_minutes_aggregate_before_rounding_and_migration_preserves_rows(self):
        self.team();self.submit(('E1',),hours=1/60)
        self.submit(('E2',),hours=1/60)
        d=self.owner.get('/api/ux/payroll-summary'+self.params).json();self.assertEqual(d['total_hours'],0.03)
        initialize(self.path);initialize(self.path)
        self.assertEqual(self.owner.get('/api/ux/payroll-summary'+self.params).json()['total_hours'],0.03)

    def test_import_deduplicates_and_changes_require_review(self):
        self.source();s=self.review_list()[0];self.assertEqual(self.review(s).status_code,200)
        self.source();self.assertEqual(len(self.review_list()),1);self.assertEqual(self.review_list()[0]['state'],'Imported')
        self.assertEqual(self.owner.get('/api/expenses').json()['total'],1)
        self.source(TotalAmt=229.96,TxnTaxDetail={'TotalTax':29.96},SyncToken='1');s=self.review_list()[0];self.assertEqual(s['state'],'Changed')
        self.assertEqual(self.owner.get('/api/expenses').json()['total_cents'],11498)
        r=self.review(s,'update',expense_version=s['expense']['version']);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.owner.get('/api/expenses').json()['total_cents'],22996)
        self.assertEqual(self.owner.get('/api/expenses').json()['total'],1)

    def test_matching_existing_expense_counts_it_once_and_rejects_stale_versions(self):
        existing=self.manual();self.source();s=self.review_list()[0]
        self.assertEqual(len(s['matches']),1);self.assertEqual(self.review(s).status_code,409)
        self.assertEqual(self.review(s,'match',expense_id=existing['id'],expense_version=0).status_code,409)
        r=self.review(s,'match',expense_id=existing['id'],expense_version=1);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.owner.get('/api/expenses').json()['total'],1)
        self.source(Id='11');s=next(s for s in self.review_list() if s['external_id']=='11')
        self.assertEqual(self.review(s,'match',expense_id=existing['id'],expense_version=1).status_code,409)

    def test_credits_currency_and_sandbox_cannot_be_counted_as_expenses(self):
        for i,kwargs in enumerate(({'Credit':True},{'CurrencyRef':{'value':'USD'}},{'TotalAmt':-10},{'TotalAmt':0},{'TxnTaxDetail':{}},{'environment':'sandbox'})):
            self.source(Id=str(i),**kwargs)
        for s in self.review_list():self.assertEqual(self.review(s).status_code,409)
        self.assertEqual(self.owner.get('/api/expenses').json()['total'],0)

    def test_all_private_cost_routes_deny_sales_and_crew(self):
        self.source();s=self.review_list()[0]
        for role in ('sales','crew','dispatch','accounting'):
            c=self.staff(role)
            for url in ('/api/costs/margins'+self.params,'/api/costs/quickbooks'):self.assertEqual(c.get(url).status_code,403)
            self.assertEqual(c.post('/api/costs/quickbooks/fetch',json={}).status_code,403)
            self.assertEqual(c.post('/api/costs/quickbooks/'+s['id']+'/review',json={}).status_code,403)

    def test_fetch_reads_purchase_and_bill_but_not_billpayment_and_rolls_back_failures(self):
        raw=self.source();
        with transaction(self.path) as db:db.execute('DELETE FROM expense_sources')
        statements=[]
        def query(path,statement):
            statements.append(statement)
            return {'Purchase':[raw]} if 'FROM Purchase' in statement else {'Bill':[{**raw,'Id':'20','Balance':114.98}]}
        with patch('app.cost_tracking.providers._qbo_connection',return_value=({},'123','host')),patch('app.cost_tracking.quickbooks_environment',return_value='production'),patch('app.cost_tracking.providers.qbo_query',side_effect=query),patch('app.providers.qbo_call') as write:
            r=self.owner.post('/api/costs/quickbooks/fetch',json={'start':'2026-11-01','end':'2026-11-30'});self.assertEqual(r.status_code,200,r.text);write.assert_not_called()
        self.assertEqual(len(statements),2);self.assertTrue(all('BillPayment' not in s for s in statements));self.assertEqual(len(self.review_list()),2)
        bill=next(s for s in self.review_list() if s['kind']=='Bill');self.assertEqual(bill['data']['status'],'Unpaid')
        before=self.review_list()
        with patch('app.cost_tracking.providers._qbo_connection',return_value=({},'123','host')),patch('app.cost_tracking.providers.qbo_query',side_effect=[{'Purchase':[{**raw,'Id':'99'}]},RuntimeError('offline')]):
            self.assertEqual(self.owner.post('/api/costs/quickbooks/fetch',json={}).status_code,502)
        self.assertEqual(self.review_list(),before)

    def test_margin_uses_issued_revision_approved_labour_and_tax_excluded_costs(self):
        self.team();self.submit();self.approve();self.manual()
        with transaction(self.path) as db:
            insert(db,'job_invoices',dict(id='INV',job_id='J1',number='INV-1',status='Draft',terms_json='{}',terms_approved_by='owner',terms_approved_at=now(),created_by='owner',created_at=now(),updated_at=now()))
            for revision,total in [(1,114975),(2,229950)]:
                insert(db,'invoice_revisions',dict(invoice_id='INV',revision=revision,document_json=dumps({'calculation':{'total_cents':total,'taxes':[{'amount_cents':total-100000*revision}]}}),issued_by='owner',issued_at=now()))
        d=self.owner.get('/api/costs/margins'+self.params).json()['items'][0]
        self.assertEqual(d['revenue_cents'],200000);self.assertEqual(d['approved_labour_cents'],15000);self.assertEqual(d['expense_cents'],10000);self.assertEqual(d['recorded_margin_cents'],175000)
        self.assertTrue(any('correction' in w for w in d['warnings']))
        self.source();s=self.review_list()[0];self.review(s,'match',expense_id=self.owner.get('/api/expenses').json()['items'][0]['id'],expense_version=1)
        self.source(TotalAmt=200,SyncToken='1')
        self.assertIsNone(self.owner.get('/api/costs/margins'+self.params).json()['items'][0]['recorded_margin_cents'])
