"""Square Sandbox permission boundaries and deposit checkout regression checks."""
import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from app.deposits import create_deposit_checkout
from app.platform_common import external_gate, quickbooks_gate, square_connection_status, square_deposits_enabled
from app.store import connect, insert, now, transaction


class SquareDepositPermissionTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0',
            'CROWNOPS_ALLOW_SQUARE_SANDBOX_DEPOSITS': '0',
            'CROWNOPS_SQUARE_SANDBOX_TEST_EMAILS': '', 'SQUARE_ENV': 'sandbox',
            'SQUARE_ACCESS_TOKEN': 'unit-test-token', 'SQUARE_LOCATION_ID': 'unit-test-location',
            'SQUARE_WEBHOOK_SIGNATURE_KEY': 'unit-test-signature',
            'SQUARE_WEBHOOK_URL': 'https://example.invalid/api/webhooks/square',
            'QBO_ENV': 'sandbox', 'CROWNOPS_ALLOW_QBO_SANDBOX_WRITES': '0',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'deposits.sqlite3'
        with transaction(self.path) as db:
            db.executescript('''
                CREATE TABLE leads(id TEXT PRIMARY KEY, email TEXT NOT NULL, version INTEGER);
                CREATE TABLE quotes(id TEXT PRIMARY KEY, lead_id TEXT REFERENCES leads(id),
                    status TEXT, lead_version INTEGER, inputs_json TEXT);
                CREATE TABLE payments(id TEXT PRIMARY KEY, lead_id TEXT, purpose TEXT,
                    amount_cents INTEGER, verified INTEGER);
                CREATE TABLE client_changes(id TEXT PRIMARY KEY, lead_id TEXT, status TEXT, payload_json TEXT DEFAULT '{}');
                CREATE TABLE jobs(id TEXT PRIMARY KEY,quote_id TEXT,status TEXT);
                CREATE TABLE deposit_links(id TEXT PRIMARY KEY, lead_id TEXT REFERENCES leads(id),
                    quote_id TEXT REFERENCES quotes(id), amount_cents INTEGER,
                    status TEXT DEFAULT 'Creating', link_id TEXT, order_id TEXT UNIQUE,
                    url TEXT, created_by TEXT, created_at TEXT);
                CREATE TABLE settings(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
                CREATE TABLE audit(id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT, object_type TEXT, object_id TEXT,
                    details_json TEXT, created_at TEXT);
            ''')
            insert(db, 'leads', {'id': 'TEST-LEAD', 'email': 'company.test@example.com', 'version': 1})
            insert(db, 'quotes', {'id': 'TEST-QUOTE', 'lead_id': 'TEST-LEAD', 'status': 'Accepted',
                'lead_version': 1, 'inputs_json': json.dumps({'deposit_required': 300})})

    def enable_company_test(self):
        os.environ['CROWNOPS_ALLOW_SQUARE_SANDBOX_DEPOSITS'] = '1'
        os.environ['CROWNOPS_SQUARE_SANDBOX_TEST_EMAILS'] = 'company.test@example.com'

    def checkout(self):
        return create_deposit_checkout(self.path, 'TEST-QUOTE', 'TEST-LEAD', 'unit-test')

    def assert_blocked(self, status=503):
        with patch('app.deposits.requests.post') as post:
            with self.assertRaises(HTTPException) as raised:
                self.checkout()
            self.assertEqual(raised.exception.status_code, status)
            post.assert_not_called()
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM deposit_links').fetchone()[0], 0)

    def test_permission_requires_sandbox_flag_and_exact_stored_email(self):
        cases = [
            ('sandbox', '0', 'company.test@example.com', 'company.test@example.com', False),
            ('sandbox', '1', '', 'company.test@example.com', False),
            ('sandbox', '1', '*', 'company.test@example.com', False),
            ('sandbox', '1', 'company.test@example.com', 'other@example.com', False),
            ('sandbox', '1', 'company.test@example.com', '', False),
            ('sandbox', '1', 'company.test@example.com', None, False),
            ('sandbox', '1', ' COMPANY.TEST@example.com ; second@example.com ', ' company.test@EXAMPLE.com ', True),
            (' SANDBOX ', '1', 'company.test@example.com', 'company.test@example.com', True),
            ('production', '1', 'company.test@example.com', 'company.test@example.com', False),
            ('invalid', '1', 'company.test@example.com', 'company.test@example.com', False),
        ]
        for env, flag, recipients, email, expected in cases:
            with self.subTest(env=env, flag=flag, recipients=recipients, email=email):
                os.environ.update(SQUARE_ENV=env, CROWNOPS_ALLOW_SQUARE_SANDBOX_DEPOSITS=flag,
                    CROWNOPS_SQUARE_SANDBOX_TEST_EMAILS=recipients)
                self.assertIs(square_deposits_enabled(email), expected)
        self.enable_company_test()
        os.environ['SQUARE_ENV'] = 'sandbox'
        for gate in (external_gate, quickbooks_gate):
            with self.assertRaises(HTTPException):
                gate()

    def test_default_production_invalid_and_unlisted_checkouts_do_not_write_or_call_square(self):
        self.assert_blocked()
        self.enable_company_test()
        with transaction(self.path) as db:
            db.execute("UPDATE leads SET email='other@example.com'")
        self.assert_blocked()
        with transaction(self.path) as db:
            db.execute("UPDATE leads SET email='company.test@example.com'")
        for env in ('production', 'invalid'):
            os.environ['SQUARE_ENV'] = env
            self.assert_blocked()
        os.environ['SQUARE_ENV'] = 'sandbox'
        for key in ('SQUARE_ACCESS_TOKEN', 'SQUARE_LOCATION_ID'):
            with patch.dict(os.environ, {key: ''}):
                self.assert_blocked()

    def test_company_test_creates_one_cad_checkout_at_sandbox_host(self):
        self.enable_company_test()
        os.environ['SQUARE_ENV'] = ' SANDBOX '
        response = Mock()
        response.json.return_value = {'payment_link': {'id': 'LINK-TEST', 'order_id': 'ORDER-TEST',
            'url': 'https://sandbox.square.link/u/unit-test'}}
        with patch('app.deposits.requests.post', return_value=response) as post:
            result = self.checkout()
            self.assertFalse(result['booking_confirmed'])
            self.assertEqual(post.call_args.args[0], 'https://connect.squareupsandbox.com/v2/online-checkout/payment-links')
            payload = post.call_args.kwargs['json']
            self.assertEqual(payload['quick_pay']['price_money'], {'amount': 30000, 'currency': 'CAD'})
            self.assertEqual(payload['quick_pay']['location_id'], 'unit-test-location')
            self.assertFalse(payload['checkout_options']['allow_tipping'])
            reused = self.checkout()
            self.assertTrue(reused['already_created'])
            self.assertEqual(reused['url'], result['url'])
            post.assert_called_once()
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT status FROM deposit_links').fetchone()[0], 'Active')

    def test_existing_estimate_and_payment_checks_still_block_checkout(self):
        self.enable_company_test()
        cases = [
            ("UPDATE quotes SET status='Approved'", "UPDATE quotes SET status='Accepted'"),
            ("UPDATE leads SET version=2", "UPDATE leads SET version=1"),
            ("INSERT INTO client_changes(id,lead_id,status) VALUES('CHANGE-TEST','TEST-LEAD','Pending')", 'DELETE FROM client_changes'),
            ("INSERT INTO payments VALUES('PAY-TEST','TEST-LEAD','Deposit',30000,0)", 'DELETE FROM payments'),
            ("INSERT INTO payments VALUES('PAY-TEST','TEST-LEAD','Deposit',30000,1)", 'DELETE FROM payments'),
        ]
        for setup, cleanup in cases:
            with self.subTest(setup=setup):
                with transaction(self.path) as db: db.execute(setup)
                self.assert_blocked(409)
                with transaction(self.path) as db: db.execute(cleanup)

    def test_unconfirmed_provider_request_is_not_retried(self):
        self.enable_company_test()
        with patch('app.deposits.requests.post', side_effect=RuntimeError('unit-test failure')) as post:
            for expected in (502, 409):
                with self.assertRaises(HTTPException) as raised: self.checkout()
                self.assertEqual(raised.exception.status_code, expected)
            post.assert_called_once()
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT status FROM deposit_links').fetchone()[0], 'Unknown')

    def test_general_permission_is_preserved_and_status_does_not_return_secrets(self):
        os.environ.update(CROWNOPS_ALLOW_EXTERNAL='1', SQUARE_ENV='production')
        self.assertTrue(square_deposits_enabled('other@example.com'))
        status = square_connection_status()
        self.assertTrue(status['deposit_creation_enabled'])
        self.assertTrue(status['configured'])
        serialized = json.dumps(status)
        for secret in ('unit-test-token', 'unit-test-location', 'unit-test-signature', 'example.invalid'):
            self.assertNotIn(secret, serialized)
        os.environ['SQUARE_ENV'] = 'invalid'
        self.assertFalse(square_deposits_enabled('other@example.com'))
        self.assertFalse(square_connection_status()['deposit_creation_enabled'])

    def test_status_distinguishes_configured_keys_from_enabled_test_permission(self):
        self.assertTrue(square_connection_status()['configured'])
        self.assertFalse(square_connection_status()['deposit_creation_enabled'])
        self.enable_company_test()
        status = square_connection_status()
        self.assertTrue(status['deposit_creation_enabled'])
        self.assertEqual(status['sandbox_test_recipient_count'], 1)
        self.assertFalse(status['external_actions_enabled'])
        self.assertNotIn('company.test@example.com', json.dumps(status))
        os.environ['CROWNOPS_SQUARE_SANDBOX_TEST_EMAILS'] = ''
        self.assertFalse(square_connection_status()['deposit_creation_enabled'])

    def test_existing_general_permission_can_still_create_a_production_checkout(self):
        os.environ.update(CROWNOPS_ALLOW_EXTERNAL='1', SQUARE_ENV=' PRODUCTION ')
        response = Mock()
        response.json.return_value = {'payment_link': {'id': 'LINK-TEST', 'order_id': 'ORDER-TEST',
            'url': 'https://square.link/u/unit-test'}}
        with patch('app.deposits.requests.post', return_value=response) as post:
            self.checkout()
            self.assertEqual(post.call_args.args[0], 'https://connect.squareup.com/v2/online-checkout/payment-links')


if __name__ == '__main__':
    unittest.main()
