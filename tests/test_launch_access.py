"""Launch-role migration, private expense access, and assigned crew invoice delivery."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import os
import sqlite3
import tempfile
import unittest

from app.main import create_app, sha
from app.store import connect, initialize, transaction, insert, one, rows, now, dumps
from app.platform_common import put_setting, settings
from app.backups import make_backup, verify_backup, restore_archive
import test_job_planning as fixture


class LaunchAccessTests(unittest.TestCase):
    staff=fixture.JobPlanningTests.staff
    plan=fixture.JobPlanningTests.plan

    def setUp(self):
        fixture.JobPlanningTests.setUp(self)
        with transaction(self.path) as db:
            insert(db,'users',dict(id='manager',name='Test Manager',email='manager@example.invalid',role='manager',employee_id='E2',password_hash='unused',created_at=now()))
            expiry=one(db,"SELECT expires_at FROM sessions WHERE user_id='owner'")['expires_at']
            insert(db,'sessions',dict(token_hash=sha('manager-token'),user_id='manager',csrf='test-csrf',expires_at=expiry))
        self.manager=self.staff('manager')

    def test_manager_has_admin_access_and_personal_assignments_without_role_switching(self):
        for url in ('/api/account-access','/api/hosting-status','/api/schema','/api/platform/settings','/api/ux/employee-directory','/api/expenses'):
            r=self.manager.get(url);self.assertEqual(r.status_code,200,(url,r.text))
        r=self.manager.put('/api/jobs/J1/plan',json=self.plan(auto_confirm=True))
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.manager.get('/api/me').json()['user']['role'],'manager')
        self.assertEqual([j['id'] for j in self.manager.get('/api/ux/final/schedule?scope=mine').json()],['J1'])
        self.assertTrue(self.manager.get('/api/me').json()['user']['can_plan_jobs'])
        self.assertTrue(all('hourly_cents' in e for e in self.manager.get('/api/employees').json()))
        r=self.manager.post('/api/invitations',json={'role':'crew','name':'Test New Crew','email':'new@example.invalid'})
        self.assertEqual(r.status_code,200,r.text)
        r=self.owner.post('/api/invitations',json={'role':'manager','name':'Test New Manager','email':'newmanager@example.invalid','employee_id':'E2'})
        self.assertEqual(r.status_code,200,r.text)
        preview=self.owner.post('/api/account-access/invitation-preview',json={'role':'manager','name':'Manager','email':'m@example.invalid'})
        self.assertEqual(preview.status_code,200,preview.text)

    def test_manager_authority_is_revoked_on_next_request_and_master_owner_stays_protected(self):
        with transaction(self.path) as db:
            put_setting(db,'master_owner',{'user_id':'owner'})
        self.assertEqual(self.manager.post('/api/users/owner/disable',json={}).status_code,403)
        with transaction(self.path) as db:db.execute("UPDATE users SET role='sales' WHERE id='manager'")
        self.assertEqual(self.manager.get('/api/expenses').status_code,403)
        self.assertEqual(self.manager.get('/api/account-access').status_code,403)

    def test_mfa_status_includes_managers(self):
        status=self.owner.get('/api/hosting-status').json()
        check=next(c for c in status['checks'] if c['key']=='mfa')
        self.assertFalse(check['ready']);self.assertIn('2',check['detail'])

    def expense(self,**changes):
        data={'request_key':'test-expense-key-0001','date':'2026-10-16','vendor':'Test fuel vendor','category':'Fuel',
              'subtotal':'100.00','tax':'14.98','status':'Paid','job_id':'J1','vehicle_id':'V1',
              'receipt_url':'https://example.invalid/private/receipt','description':'Fuel receipt','reference':'TEST-001'}
        data.update(changes);return data

    def test_expenses_create_retry_update_void_and_summary_keep_audit_history(self):
        r=self.manager.post('/api/expenses',json=self.expense());self.assertEqual(r.status_code,200,r.text)
        e=r.json();self.assertEqual(e['total_cents'],11498)
        retry=self.manager.post('/api/expenses',json=self.expense());self.assertEqual(retry.json()['id'],e['id'])
        self.assertEqual(self.manager.post('/api/expenses',json=self.expense(subtotal='200')).status_code,409)
        self.assertEqual(self.owner.get('/api/expenses').json()['total'],1)
        update=self.expense(subtotal='150',version=1);update.pop('request_key')
        self.assertEqual(self.owner.put('/api/expenses/'+e['id'],json=update).status_code,200)
        self.assertEqual(self.manager.put('/api/expenses/'+e['id'],json=update).status_code,409)
        self.assertEqual(self.manager.post('/api/expenses/'+e['id']+'/void',json={'version':2,'reason':'Duplicate external receipt'}).status_code,200)
        summary=self.owner.get('/api/expenses').json();self.assertEqual(summary['total'],1);self.assertEqual(summary['total_cents'],0)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE object_type='expenses'").fetchone()[0],3)
            self.assertEqual(one(db,'SELECT status FROM expenses WHERE id=?',(e['id'],))['status'],'Voided')

    def test_all_expense_endpoints_reject_every_non_admin_and_signed_out_request(self):
        eid=self.owner.post('/api/expenses',json=self.expense()).json()['id']
        for role in ('sales','dispatch','accounting','crew','lead'):
            client=self.staff(role)
            for url in ('/api/expenses','/api/expenses/export.csv','/api/expenses/'+eid):
                self.assertEqual(client.get(url).status_code,403,(role,url))
            self.assertEqual(client.post('/api/expenses',json=self.expense()).status_code,403)
            self.assertEqual(client.put('/api/expenses/'+eid,json=self.expense(version=1)).status_code,403)
            self.assertEqual(client.post('/api/expenses/'+eid+'/void',json={'version':1,'reason':'test'}).status_code,403)
        from fastapi.testclient import TestClient
        with TestClient(self.app) as public:self.assertEqual(public.get('/api/expenses').status_code,401)

    def test_expense_validation_csv_and_paging(self):
        for change in ({'subtotal':'NaN'},{'tax':'1.001'},{'tax':True},{'date':'2026-02-30'},{'receipt_url':'javascript:alert(1)'},{'job_id':'missing'},{'vendor':''},{'extra':'no'}):
            self.assertEqual(self.manager.post('/api/expenses',json=self.expense(**change)).status_code,422,change)
        self.manager.post('/api/expenses',json=self.expense(vendor=' =SUM(1,2)'))
        response=self.manager.get('/api/expenses/export.csv');self.assertEqual(response.status_code,200)
        self.assertIn("'=SUM",response.text);self.assertIn('114.98',response.text)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(self.manager.get('/api/expenses?offset=1&limit=1').json()['items'],[])
        self.assertEqual(self.manager.get('/api/expenses?limit=1000').status_code,422)
        self.assertEqual(self.manager.get('/api/expenses?start=bad').status_code,422)

    def invoice(self):
        from app.invoices import validate_terms
        terms=validate_terms({'issuer':{'name':'Crown Movers','address':'Test company address'},'customer':{'name':'Test move','email':'client@example.invalid'},
           'hourly_rate':150,'minimum_hours':3,'transport_amount':50,'fuel_amount':35,'discount_amount':0,'taxes':[],
           'catalogue':[],'approval_reference':'Approved test billing'})
        with transaction(self.path) as db:
            db.execute("UPDATE jobs SET status='Completed' WHERE id='J1'")
            db.execute("UPDATE leads SET email='client@example.invalid',notifications_suppressed=0 WHERE id='L1'")
            insert(db,'assignments',dict(job_id='J1',resource_type='employee',resource_id='E1'))
            insert(db,'job_invoices',dict(id='INV',job_id='J1',number='TEST-INVOICE',terms_json=dumps(terms),actual_json='{}',terms_approved_by='manager',terms_approved_at=now(),created_by='crew',created_at=now(),updated_at=now()))
        return self.staff('crew')

    def test_ordinary_assigned_crew_can_finalize_and_deliver_once_and_see_status(self):
        crew=self.invoice()
        r=crew.patch('/api/invoices/INV',json={'version':1,'service_hours':4,'quantities':{},'notes':'Test actual hours','reason':'Actual service completed'})
        self.assertEqual(r.status_code,200,r.text)
        r=crew.post('/api/invoices/INV/issue',json={'version':2,'confirmed':True})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(crew.get('/api/invoices/INV/delivery-state').json()['status'],'Not sent')
        with patch.dict(os.environ,{'CROWNOPS_ALLOW_EXTERNAL':'1','CROWNOPS_TEST_EMAIL_ALLOWLIST':'client@example.invalid'}),patch('app.providers.send_gmail',return_value='mock-provider-id') as send:
            response=crew.post('/api/invoices/INV/deliver',json={'confirm':True})
            self.assertEqual(response.status_code,200,response.text);self.assertTrue(response.json()['sent'])
            again=crew.post('/api/invoices/INV/deliver',json={'confirm':True})
            self.assertEqual(again.status_code,200,again.text);self.assertTrue(again.json()['duplicate']);self.assertEqual(send.call_count,1)
        state=crew.get('/api/invoices/INV/delivery-state').json();self.assertEqual(state['status'],'Sent');self.assertFalse(state['can_send'])
        self.assertIsNotNone(state['sent_at'])
        for url in ('/api/invoices/INV','/api/jobs/J1/invoice','/api/invoices/INV/delivery-state'):
            self.assertEqual(self.staff('lead').get(url).status_code,403,url)
        self.assertEqual(self.staff('lead').post('/api/jobs/J1/invoice-from-quote',json={}).status_code,403)
        self.assertEqual(self.manager.get('/api/invoices/INV').status_code,200)

    def test_legacy_sent_invoice_mail_blocks_a_second_delivery(self):
        crew=self.invoice()
        crew.patch('/api/invoices/INV',json={'version':1,'service_hours':4,'quantities':{},'notes':'','reason':'Actual hours'})
        crew.post('/api/invoices/INV/issue',json={'version':2,'confirmed':True})
        with transaction(self.path) as db:
            insert(db,'outbound_messages',dict(id='MAIL',event_key='invoice-mail:INV:1',lead_id='L1',job_id='J1',subject='Invoice',body='Original invoice email',recipient='client@example.invalid',category='Invoice',status='Sent',provider_id='original',created_by='crew',created_at=now(),sent_at=now()))
        with patch.dict(os.environ,{'CROWNOPS_ALLOW_EXTERNAL':'1'}),patch('app.providers.send_gmail') as send:
            r=crew.post('/api/invoices/INV/deliver',json={'confirm':True});self.assertEqual(r.status_code,200,r.text)
            self.assertTrue(r.json()['duplicate']);send.assert_not_called()

    def qbo_invoice(self):
        crew=self.invoice()
        # This fixture tests a no-deposit invoice; deposit reconciliation has its own coverage.
        with transaction(self.path) as db:db.execute("DELETE FROM payments WHERE id='P1'")
        crew.patch('/api/invoices/INV',json={'version':1,'service_hours':4,'quantities':{},'notes':'','reason':'Actual hours'})
        crew.post('/api/invoices/INV/issue',json={'version':2,'confirmed':True})
        with transaction(self.path) as db:
            insert(db,'external_records',dict(provider='quickbooks',kind='Customer',object_id='L1',external_id='101',updated_at=now()))
            insert(db,'external_records',dict(provider='quickbooks',kind='Invoice',object_id='INV',revision=1,external_id='102',amount_cents=68500,updated_at=now()))
            insert(db,'provider_operations',dict(id='qbo:invoice:INV:1',provider='quickbooks',object_id='INV',kind='Invoice',status='Completed',created_at=now(),updated_at=now()))
        return crew,{'QueryResponse':{'Invoice':[{'Id':'102','TotalAmt':685,'Balance':685,'CurrencyRef':{'value':'CAD'},'CustomerRef':{'value':'101'},'InvoiceLink':'https://connect.intuit.com/test-invoice-link'}]}}

    def test_quickbooks_payment_link_checks_assignment_remote_invoice_and_balance_without_charging(self):
        crew,remote=self.qbo_invoice()
        with patch('app.providers.qbo_read',return_value=remote) as read:
            r=crew.get('/api/invoices/INV/quickbooks-payment-link');self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['amount_cents'],68500);self.assertFalse(r.json()['paid'])
            self.assertEqual(r.headers['cache-control'],'no-store')
            self.assertEqual(read.call_args.args[2]['include'],'invoiceLink')
            read.reset_mock()
            self.assertEqual(self.staff('lead').get('/api/invoices/INV/quickbooks-payment-link').status_code,403)
            read.assert_not_called()
        for change in ({'Balance':1},{'TotalAmt':1},{'Id':'999'},{'CustomerRef':{'value':'999'}},{'CurrencyRef':{'value':'USD'}},{'InvoiceLink':'https://connect.intuit.com.evil.invalid/pay'},{'InvoiceLink':'javascript:alert(1)'},{'InvoiceLink':''}):
            bad={'QueryResponse':{'Invoice':[{**remote['QueryResponse']['Invoice'][0],**change}]}}
            with patch('app.providers.qbo_read',return_value=bad):
                self.assertEqual(crew.get('/api/invoices/INV/quickbooks-payment-link').status_code,409,change)
        with patch('app.providers.qbo_read',side_effect=RuntimeError('provider unavailable')):
            self.assertEqual(crew.get('/api/invoices/INV/quickbooks-payment-link').status_code,502)

    def test_crew_can_explicitly_include_a_checked_quickbooks_payment_link_and_pending_receipts_block_it(self):
        crew,remote=self.qbo_invoice()
        with patch.dict(os.environ,{'CROWNOPS_ALLOW_EXTERNAL':'1','QBO_ENV':'sandbox','CROWNOPS_TEST_EMAIL_ALLOWLIST':'client@example.invalid'}),patch('app.providers.qbo_read',return_value=remote),patch('app.providers.send_gmail',return_value='mock-provider-id') as send:
            r=crew.post('/api/invoices/INV/deliver',json={'confirm':True,'include_quickbooks_payment':True})
            self.assertEqual(r.status_code,200,r.text)
            self.assertIn('https://connect.intuit.com/test-invoice-link',send.call_args.args[1]['body'])
            self.assertIn('TEST ONLY',send.call_args.args[1]['body'])
        r=crew.post('/api/invoices/INV/collect',json={'amount':10,'method':'Cash','reference':'TEST-CASH','request_id':'payment-link-test-001'})
        self.assertEqual(r.status_code,200,r.text)
        with patch('app.providers.qbo_read') as read:
            self.assertEqual(crew.get('/api/invoices/INV/quickbooks-payment-link').status_code,409)
            read.assert_not_called()

    def test_backup_restore_preserves_manager_expense_and_audit_history(self):
        eid=self.manager.post('/api/expenses',json=self.expense()).json()['id']
        archive=self.path.parent/'launch-backup.zip'
        make_backup(self.path,archive,self.path.parent/'uploads')
        self.assertTrue(verify_backup(archive)['restore_verified'])
        restored=self.path.parent/'restored'/'crownops.sqlite3';restore_archive(archive,restored)
        with closing(connect(restored)) as db:
            self.assertEqual(one(db,"SELECT role,employee_id FROM users WHERE id='manager'"),{'role':'manager','employee_id':'E2'})
            self.assertEqual(one(db,'SELECT total_cents FROM expenses WHERE id=?',(eid,))['total_cents'],11498)
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE object_type='expenses'").fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])


class ManagerMigrationTests(unittest.TestCase):
    def legacy_database(self,path):
        legacy=Path('app/schema.sql').read_text().replace("'owner','manager'","'owner'")
        with sqlite3.connect(path) as db:
            db.executescript(legacy);db.executescript(Path('app/platform_schema.sql').read_text())
            db.execute("INSERT INTO users VALUES ('OWNER','test@example.invalid','Original','owner','preserved-password',NULL,1,'2026-01-01')")

    def test_production_migration_first_makes_a_restore_verified_recovery_copy(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'crownops.sqlite3';self.legacy_database(path)
            env={'CROWNOPS_ENV':'production','RAILWAY_VOLUME_MOUNT_PATH':td,'CROWNOPS_SECRET_KEY':'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=','CROWNOPS_UPLOADS':td+'/uploads','CROWNOPS_BACKUPS':td+'/backups'}
            with patch.dict(os.environ,env):initialize(path)
            with closing(connect(path)) as db:
                record=settings(db,'verified_backup',{})
                self.assertTrue(record['restore_verified'])
                self.assertIn("'manager'",one(db,"SELECT sql FROM sqlite_master WHERE name='users'")['sql'])
            archive=Path(td)/'backups'/record['filename'];self.assertTrue(verify_backup(archive)['restore_verified'])
            import zipfile
            with zipfile.ZipFile(archive) as z:
                before=Path(td)/'before.sqlite3';before.write_bytes(z.read('database.sqlite3'))
            with closing(connect(before)) as db:
                self.assertNotIn("'manager'",one(db,"SELECT sql FROM sqlite_master WHERE name='users'")['sql'])
            with patch.dict(os.environ,env):initialize(path)
            self.assertEqual(len(list((Path(td)/'backups').glob('*.zip'))),1)

    def test_failed_recovery_stops_production_before_the_role_migration(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'crownops.sqlite3';self.legacy_database(path)
            with patch.dict(os.environ,{'CROWNOPS_ENV':'production'}),patch('app.release_recovery.before_manager_migration',side_effect=RuntimeError('Recovery unavailable')):
                with self.assertRaisesRegex(RuntimeError,'Recovery unavailable'):initialize(path)
            with closing(connect(path)) as db:
                self.assertNotIn("'manager'",one(db,"SELECT sql FROM sqlite_master WHERE name='users'")['sql'])
                self.assertEqual(one(db,"SELECT password_hash FROM users WHERE id='OWNER'")['password_hash'],'preserved-password')

    def test_legacy_staff_migration_keeps_accounts_sessions_factors_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'legacy.sqlite3'
            legacy=Path('app/schema.sql').read_text().replace("'owner','manager'","'owner'")
            db=sqlite3.connect(path);db.executescript(legacy)
            db.executescript(Path('app/platform_schema.sql').read_text())
            db.execute("INSERT INTO users VALUES ('OWNER','test@example.invalid','Original','owner','original-password-hash',NULL,1,'2026-01-01')")
            db.execute("INSERT INTO sessions VALUES ('session-hash','OWNER','csrf-value','2099-01-01')")
            db.execute("INSERT INTO auth_factors (user_id,secret_ciphertext,enabled,created_at) VALUES ('OWNER','original-encrypted-factor',1,'2026-01-01')")
            db.commit();db.close()
            initialize(path);initialize(path)
            with transaction(path) as db:
                self.assertEqual(one(db,"SELECT password_hash FROM users WHERE id='OWNER'")['password_hash'],'original-password-hash')
                self.assertEqual(one(db,"SELECT csrf FROM sessions WHERE user_id='OWNER'")['csrf'],'csrf-value')
                insert(db,'users',dict(id='MANAGER',name='Manager',email='m@example.invalid',role='manager',employee_id='WORKER',password_hash='manager-hash',created_at=now()))
                insert(db,'auth_factors',dict(user_id='MANAGER',secret_ciphertext='test-cipher',enabled=1,created_at=now()))
            initialize(path)
            with closing(connect(path)) as db:
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
                self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0],2)
                self.assertEqual(one(db,"SELECT secret_ciphertext FROM auth_factors WHERE user_id='OWNER'")['secret_ciphertext'],'original-encrypted-factor')
                self.assertEqual(one(db,"SELECT employee_id FROM users WHERE id='MANAGER'")['employee_id'],'WORKER')
