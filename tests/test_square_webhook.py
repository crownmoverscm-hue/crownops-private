"""Square payment webhook (POST /api/webhooks/square), exercised locally with signed synthetic
events. No request reaches Square: these prove CrownOps' side of the contract only. The live
sandbox delivery is a separate, blocked gate (docs/INTEGRATION_TEST_PLANS.md, SQ-*)."""
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tests'))
from test_independent_installations import CONFIGS  # noqa: E402

KEY = 'synthetic-square-signature-key'
URL = 'https://pilot.example.invalid/api/webhooks/square'
LOCATION = 'SYNTH-LOCATION'


def signed(body):
    return base64.b64encode(hmac.new(KEY.encode(), URL.encode() + body, hashlib.sha256).digest()).decode()


class SquareWebhook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); base = Path(cls.temp.name)
        cls.template = base / 'template.sqlite3'
        cls.env = {'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', 'CROWNOPS_UPLOADS': str(base / 'uploads'),
                   'CROWNOPS_SECRET_KEY': Fernet.generate_key().decode()}
        cfg = base / 'cfg.json'; cfg.write_text(json.dumps(CONFIGS['B']))
        run = subprocess.run([sys.executable, '-I', str(ROOT / 'tests' / 'installation_workflow.py'), str(cls.template), str(cfg)],
                             capture_output=True, text=True, env={**os.environ, **cls.env}, cwd=ROOT, timeout=300)
        assert run.returncode == 0, run.stderr[-1500:]
        cls.invoice = json.loads(run.stdout.strip().splitlines()[-1])['invoice_id']

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        import sqlite3
        self.db = Path(self.temp.name) / (self._testMethodName + '.sqlite3')  # each test starts from the same issued invoice
        with closing(sqlite3.connect(self.template)) as source, closing(sqlite3.connect(self.db)) as target:
            source.backup(target)
        env = patch.dict(os.environ, {**self.env, 'SQUARE_WEBHOOK_SIGNATURE_KEY': KEY, 'SQUARE_WEBHOOK_URL': URL, 'SQUARE_LOCATION_ID': LOCATION})
        env.start(); self.addCleanup(env.stop)
        from app.main import create_app
        from app.platform_common import invoice_current
        from app.store import connect, insert, now, transaction
        self.client = TestClient(create_app(self.db))
        with closing(connect(self.db)) as db:
            inv, calc = invoice_current(db, self.invoice)
        self.balance, self.revision = calc['balance_cents'], inv['revision']
        self.assertGreater(self.balance, 0)
        self.order = 'ORDER-' + self._testMethodName[-12:]
        with transaction(self.db) as db:
            db.execute("UPDATE payment_links SET status='Cancelled' WHERE invoice_id=?", (self.invoice,))
            insert(db, 'payment_links', dict(id='LINK-' + self._testMethodName[-20:], invoice_id=self.invoice, revision=self.revision,
                                             amount_cents=self.balance, provider='square', order_id=self.order, link_id='L', url='https://x.invalid',
                                             status='Active', created_at=now()))

    def event(self, event_id, payment=None, kind='payment.updated', obj=None):
        body = json.dumps({'event_id': event_id, 'type': kind, 'data': {'object': obj if obj is not None else {'payment': payment}}}).encode()
        return body

    def payment(self, pid, **changes):
        return {'id': pid, 'order_id': self.order, 'status': 'COMPLETED', 'location_id': LOCATION,
                'amount_money': {'amount': self.balance, 'currency': 'CAD'}, **changes}

    def post(self, body, signature=None):
        return self.client.post('/api/webhooks/square', content=body,
                                headers={'content-type': 'application/json', 'x-square-hmacsha256-signature': signature if signature is not None else signed(body)})

    def count(self, sql, *args):
        from app.store import connect
        with closing(connect(self.db)) as db:
            return db.execute(sql, args).fetchone()[0]

    def test_unsigned_wrongly_signed_or_unconfigured_events_change_nothing(self):
        body = self.event('EV-SIG', self.payment('PAY-SIG'))
        before = self.count('SELECT count(*) FROM payments')
        self.assertEqual(self.post(body, '').status_code, 401)
        self.assertEqual(self.post(body, signed(body + b' ')).status_code, 401)
        self.assertEqual(self.post(body + b' ', signed(body)).status_code, 401)  # body altered after signing
        with patch.dict(os.environ, {'SQUARE_WEBHOOK_SIGNATURE_KEY': ''}):
            self.assertEqual(self.post(body).status_code, 503)
        self.assertEqual(self.post(b'{not json', signed(b'{not json')).status_code, 422)
        self.assertEqual(self.count('SELECT count(*) FROM payments'), before)
        self.assertEqual(self.count("SELECT count(*) FROM bridge_events WHERE event_id='square:EV-SIG'"), 0)

    def test_completed_payment_is_applied_once_across_repeated_and_re_sent_events(self):
        body = self.event('EV-PAID', self.payment('PAY-PAID'))
        first = self.post(body).json()
        self.assertTrue(first['applied'])
        self.assertEqual(self.post(body).json().get('duplicate'), True)  # Square retries the same event
        again = self.post(self.event('EV-PAID-2', self.payment('PAY-PAID'))).json()  # new event id, same payment
        self.assertFalse(again['applied'])
        self.assertEqual(self.count("SELECT count(*) FROM payments WHERE reference='Square:PAY-PAID'"), 1)
        self.assertEqual(self.count("SELECT sum(amount_cents) FROM invoice_payment_allocations WHERE payment_id=?", first['payment_id']), self.balance)
        self.assertEqual(self.count("SELECT count(*) FROM payment_links WHERE order_id=? AND status='Paid'", self.order), 1)

    def test_amount_currency_or_location_mismatch_is_never_applied(self):
        cases = {'amount': {'amount_money': {'amount': self.balance - 1, 'currency': 'CAD'}},
                 'currency': {'amount_money': {'amount': self.balance, 'currency': 'USD'}},
                 'location': {'location_id': 'OTHER-LOCATION'}}
        for label, change in cases.items():
            with self.subTest(case=label):
                result = self.post(self.event('EV-MM-' + label, self.payment('PAY-MM-' + label, **change))).json()
                self.assertFalse(result['applied']); self.assertIn('mismatch', result['reason'])
                self.assertEqual(self.count("SELECT count(*) FROM payments WHERE reference=?", 'Square:PAY-MM-' + label), 0)
                self.assertEqual(self.count("SELECT count(*) FROM tasks WHERE title LIKE 'URGENT: reconcile Square payment mismatch%' AND legacy_id=?",
                                            'square:EV-MM-' + label), 1)

    def test_pending_payment_is_recorded_without_money_moving(self):
        result = self.post(self.event('EV-PENDING', self.payment('PAY-PENDING', status='APPROVED'))).json()
        self.assertFalse(result['applied'])
        self.assertEqual(self.count("SELECT count(*) FROM payments WHERE reference='Square:PAY-PENDING'"), 0)
        self.assertEqual(self.count("SELECT count(*) FROM bridge_events WHERE event_id='square:EV-PENDING'"), 1)

    def test_refunds_and_disputes_raise_a_finance_task_and_reverse_nothing(self):
        paid = self.post(self.event('EV-BEFORE-REFUND', self.payment('PAY-REFUNDED'))).json()
        self.assertTrue(paid['applied'])
        payments = self.count('SELECT count(*) FROM payments')
        for kind, obj in (('refund.created', {'refund': {'id': 'R1', 'payment_id': 'PAY-REFUNDED', 'status': 'PENDING'}}),
                          ('dispute.created', {'dispute': {'id': 'D1', 'disputed_payment': {'payment_id': 'PAY-REFUNDED'}}})):
            with self.subTest(kind=kind):
                result = self.post(self.event('EV-' + kind, kind=kind, obj=obj)).json()
                self.assertFalse(result['applied']); self.assertIn('finance reconciliation', result['reason'])
        self.assertEqual(self.count('SELECT count(*) FROM payments'), payments)
        self.assertEqual(self.count("SELECT count(*) FROM tasks WHERE title LIKE 'URGENT: Square refund/dispute%'"), 2)
        self.assertEqual(self.count("SELECT count(*) FROM payments WHERE reference='Square:PAY-REFUNDED' AND verified=1"), 1)

    def test_unknown_checkout_is_visible_to_operations_without_exposing_payment_details(self):
        from app.ops_status import collect
        from app.store import transaction

        with transaction(self.db) as db:
            db.execute("UPDATE payment_links SET status='Unknown' WHERE order_id=?", (self.order,))
        report = collect(self.db)
        self.assertEqual(report['square_checkouts_needing_review'], 1)
        finding = next(f for f in report['findings'] if f['code'] == 'square_checkout_unconfirmed')
        self.assertEqual(finding['level'], 'warning')
        self.assertEqual(finding['count'], 1)
        serialized = json.dumps(report)
        self.assertNotIn(self.order, serialized)
        self.assertNotIn(self.invoice, serialized)
        self.assertNotIn('https://x.invalid', serialized)

        with transaction(self.db) as db:
            db.execute("UPDATE payment_links SET status='Cancelled' WHERE order_id=?", (self.order,))
        resolved = collect(self.db)
        self.assertEqual(resolved['square_checkouts_needing_review'], 0)
        self.assertNotIn('square_checkout_unconfirmed', {f['code'] for f in resolved['findings']})

    def test_oversized_events_are_refused(self):
        body = b'{"event_id":"big","pad":"' + b'x' * 200_001 + b'"}'
        self.assertEqual(self.post(body).status_code, 413)


if __name__ == '__main__':
    unittest.main()
