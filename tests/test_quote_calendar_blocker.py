"""Disposable DB reproduction: no production rates, terms or providers."""
import copy
from unittest.mock import patch
from app.store import transaction, connect, one
from app.quote_test_fixture import rate, DAY
from fastapi import HTTPException
from test_template_quotes import TemplateQuoteTests

class QuoteCalendarBlockerTests(TemplateQuoteTests):
    def setUp(self):
        super().setUp()
        self.data['hourly_rate']=100
        with transaction(self.path) as db:
            db.execute('UPDATE annual_rate_periods SET active=0')
            db.execute('UPDATE pricing_versions SET approved=0')

    # Do not inherit the parent suite's successful-pricing assumptions.
    def test_missing_calendar_no_partial_save(self):
        r=self.save();self.assertEqual(r.status_code,422,r.text)
        self.assertIn('Draft was not saved',r.json()['detail'])
        for table in ('quotes','leads','client_accounts'):self.assertEqual(self.count(table),0)

    def test_fixture_save_reload_and_duplicate_retry_without_terms(self):
        with patch.dict('os.environ',self.fixture_env()):
            r=self.save();self.assertEqual(r.status_code,200,r.text)
            q=r.json();self.assertTrue(q['result']['test_only']);self.assertIn('TEST ONLY',q['inputs']['quote_title'])
            self.assertEqual(self.save().json()['id'],q['id'])
            loaded=self.client.get('/api/crm/quotes/'+q['id']);self.assertEqual(loaded.status_code,200,loaded.text)
            self.assertEqual(loaded.json()['quote']['inputs']['client_note'],self.data['client_note'])
            self.assertEqual(self.count('quotes'),1)
            self.assertEqual(self.count('outbound_messages'),0);self.assertEqual(self.count('jobs'),0)

    def test_missing_calendar_setup_opens_without_price_or_side_effect(self):
        r=self.client.get('/api/crm/quote-setup?move_date=2026-11-15')
        self.assertEqual(r.status_code,200,r.text)
        self.assertIsNone(r.json()['suggested_hourly_rate'])
        self.assertIn('Draft was not saved',r.json()['pricing_error'])
        self.assertEqual(self.count('quotes'),0)

    @staticmethod
    def fixture_env():
        return {'CROWNOPS_QUOTE_TEST_FIXTURE':DAY,'CROWNOPS_ENV':'staging','QBO_ENV':'sandbox',
          'QBO_SANDBOX_REALM_ID':'9341458310238189','CROWNOPS_PUBLIC_URL':'https://crownops-quote-acceptance-staging.up.railway.app'}

    def test_fixture_rejects_production_wrong_host_and_realm(self):
        for change in ({'CROWNOPS_ENV':'production'},{'QBO_ENV':'production'},{'QBO_SANDBOX_REALM_ID':'wrong'},{'CROWNOPS_PUBLIC_URL':'https://crownops-private-production.up.railway.app'}):
            with self.subTest(change=change),patch.dict('os.environ',{**self.fixture_env(),**change}):
                with self.assertRaises(HTTPException):rate(DAY)
        with patch.dict('os.environ',self.fixture_env()):self.assertIsNone(rate('2026-11-16'))

    def test_fixture_rejects_real_client_and_rolls_back(self):
        d=copy.deepcopy(self.data);d['new_client']['client_name']='Real customer'
        with patch.dict('os.environ',self.fixture_env()):self.assertEqual(self.save(d).status_code,422)
        for table in ('quotes','leads','client_accounts'):self.assertEqual(self.count(table),0)

# Only the four new cases belong to this subclass; inherited cases run in their own suite.
for name in tuple(TemplateQuoteTests.__dict__):
    if name.startswith('test_'):setattr(QuoteCalendarBlockerTests,name,None)
