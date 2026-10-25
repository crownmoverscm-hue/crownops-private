"""Company-read verification never implies a complete financial walkthrough."""
from contextlib import closing
from unittest.mock import patch
import unittest
from cryptography.fernet import Fernet
from app.store import transaction, connect, one
from app.providers import token_save
import test_launch_access as fixture


class QuickBooksStatusTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.LaunchAccessTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path=self.f.path;self.sales=self.f.staff('sales')
        p=patch.dict('os.environ',{'CROWNOPS_SECRET_KEY':Fernet.generate_key().decode(),'QBO_ENV':'sandbox','QBO_CLIENT_ID':'fictional','QBO_CLIENT_SECRET':'fictional'});p.start();self.addCleanup(p.stop)
        with transaction(self.path) as db:token_save(db,'quickbooks',{'access_token':'fictional-only','refresh_token':'fictional-only','realm_id':'123'})

    def state(self):
        r=self.sales.get('/api/connections');self.assertEqual(r.status_code,200,r.text);return r.json()['quickbooks']

    def test_saved_grant_is_not_a_verified_account(self):
        q=self.state();self.assertFalse(q['account_verified']);self.assertIn('not checked',q['state']);self.assertFalse(q['end_to_end_verified'])

    def test_read_check_shows_company_mode_and_timestamp_without_financial_access(self):
        with patch('app.providers._qbo_connection',return_value=({},'123','host')),patch('app.providers.qbo_read',return_value={'CompanyInfo':{'Id':'1','CompanyName':'TEST — MUSE Company'}}) as read:
            r=self.sales.get('/api/quickbooks/test-connection');self.assertTrue(r.json()['ok']);self.assertEqual(read.call_count,1)
        q=self.state();self.assertTrue(q['account_verified']);self.assertEqual(q['company_name'],'TEST — MUSE Company');self.assertEqual(q['environment'],'sandbox');self.assertTrue(q['checked_at']);self.assertFalse(q['end_to_end_verified'])
        self.assertNotIn('fictional-only',str(q));self.assertEqual(self.sales.get('/api/expenses').status_code,403)

    def test_failed_check_keeps_credentials_and_does_not_show_verified(self):
        with patch('app.providers._qbo_connection',return_value=({},'123','host')),patch('app.providers.qbo_read',side_effect=TimeoutError('SECRET PROVIDER DETAILS')):
            r=self.sales.get('/api/quickbooks/test-connection');self.assertFalse(r.json()['ok']);self.assertNotIn('SECRET PROVIDER',r.text)
        self.assertFalse(self.state()['account_verified'])
        with closing(connect(self.path)) as db:self.assertIsNotNone(one(db,"SELECT provider FROM oauth_credentials WHERE provider='quickbooks'"))

    def test_expected_sandbox_company_and_country_are_read_only_and_visible(self):
        with patch.dict('os.environ',{'QBO_SANDBOX_REALM_ID':'123'}),patch('app.providers._qbo_connection',return_value=({},'123','host')),patch('app.providers.qbo_read',return_value={'CompanyInfo':{'Id':'1','Country':'CA','CompanyName':'TEST Canadian Company'}}) as read,patch('app.providers.qbo_call') as write:
            r=self.sales.get('/api/quickbooks/test-connection')
            self.assertTrue(r.json()['ok']);self.assertEqual(r.json()['realm_id'],'123')
            self.assertEqual(r.json()['country'],'CA');read.assert_called_once_with(self.path,'companyinfo/123');write.assert_not_called()
            q=self.state();self.assertTrue(q['account_verified']);self.assertEqual(q['realm_id'],'123');self.assertEqual(q['country'],'CA')

    def test_wrong_sandbox_company_never_reaches_provider(self):
        with patch.dict('os.environ',{'QBO_SANDBOX_REALM_ID':'999'}),patch('app.providers._qbo_connection',return_value=({},'123','host')),patch('app.providers.qbo_read') as read:
            r=self.sales.get('/api/quickbooks/test-connection')
            self.assertFalse(r.json()['ok']);self.assertEqual(r.json()['state'],'Wrong company');read.assert_not_called()
            self.assertFalse(self.state()['account_verified'])

    def test_missing_or_wrong_country_is_not_verified(self):
        for country in ('','US'):
            with self.subTest(country=country),patch.dict('os.environ',{'QBO_SANDBOX_REALM_ID':'123'}),patch('app.providers._qbo_connection',return_value=({},'123','host')),patch('app.providers.qbo_read',return_value={'CompanyInfo':{'Id':'1','Country':country,'CompanyName':'TEST'}}):
                r=self.sales.get('/api/quickbooks/test-connection')
                self.assertFalse(r.json()['ok']);self.assertEqual(r.json()['state'],'Canadian company not verified')
                self.assertFalse(self.state()['account_verified'])

    def test_old_account_check_cannot_prove_country_for_new_sandbox_pin(self):
        with patch('app.providers._qbo_connection',return_value=({},'123','host')),patch('app.providers.qbo_read',return_value={'CompanyInfo':{'Id':'1','CompanyName':'TEST'}}):self.sales.get('/api/quickbooks/test-connection')
        with patch.dict('os.environ',{'QBO_SANDBOX_REALM_ID':'123'}):
            self.assertFalse(self.state()['account_verified'])

    def test_company_switch_does_not_reuse_previous_verification(self):
        with patch('app.providers._qbo_connection',return_value=({},'123','host')),patch('app.providers.qbo_read',return_value={'CompanyInfo':{'Id':'1','CompanyName':'TEST Company'}}):self.sales.get('/api/quickbooks/test-connection')
        with transaction(self.path) as db:token_save(db,'quickbooks',{'access_token':'fictional-only','realm_id':'456'})
        q=self.state();self.assertFalse(q['account_verified']);self.assertEqual(q['company_name'],'')


if __name__=='__main__':unittest.main()
