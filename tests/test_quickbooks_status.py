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

    def test_company_switch_does_not_reuse_previous_verification(self):
        with patch('app.providers._qbo_connection',return_value=({},'123','host')),patch('app.providers.qbo_read',return_value={'CompanyInfo':{'Id':'1','CompanyName':'TEST Company'}}):self.sales.get('/api/quickbooks/test-connection')
        with transaction(self.path) as db:token_save(db,'quickbooks',{'access_token':'fictional-only','realm_id':'456'})
        q=self.state();self.assertFalse(q['account_verified']);self.assertEqual(q['company_name'],'')


if __name__=='__main__':unittest.main()
