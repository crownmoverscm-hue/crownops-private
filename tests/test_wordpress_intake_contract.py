"""Synthetic intake contracts only; no original website submissions or providers."""
from contextlib import closing
import hashlib
import hmac
import html
import json
import time
import unittest
from unittest.mock import patch

from app.gmail_sync import _quote_fields
from app.store import connect
import test_template_quotes as fixture
from test_launch_workflow import synthetic_form


class FrenchBlankFieldTests(unittest.TestCase):
    def test_blank_french_answers_do_not_consume_following_accented_labels(self):
        body = '''Nom complet: TEST Blank fields
Courriel: blank@example.invalid
Téléphone:
Date du déménagement: 2026-11-15
Taille du logement:
Accès au départ: Ascenseur
Nombre approximatif de boîtes:
Accès à destination: Rez-de-chaussée
Distance de marche au départ:
Distance de marche à destination: 20 m
'''
        for content in (body, '<div>' + html.escape(body).replace('\n', '<br>') + '</div>'):
            with self.subTest(html=content != body):
                parsed = _quote_fields(content, 'Quick Quote Form FR')
                for key in ('phone', 'home_size', 'boxes', 'pickup_walk'):
                    self.assertEqual(parsed[key], '', key)
                self.assertEqual(parsed['move_date'], '2026-11-15')
                self.assertEqual(parsed['pickup_access'], 'Ascenseur')
                self.assertEqual(parsed['dropoff_access'], 'Rez-de-chaussée')
                self.assertEqual(parsed['dropoff_walk'], '20 m')


class SyntheticWebsiteHarnessTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.TemplateQuoteTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.client, self.path = self.f.client, self.f.path
        self.secret = 'DISPOSABLE-TEST-ONLY-' + 'x' * 40

    def submit(self, event, lead):
        # This is an in-process ASGI request, not an HTTP website submission.
        raw = json.dumps({'event_id': event, 'lead': lead}).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(self.secret.encode(), timestamp.encode() + b'.' + raw, hashlib.sha256).hexdigest()
        return self.client.post('/api/webhooks/intake', content=raw, headers={
            'content-type': 'application/json', 'x-crown-timestamp': timestamp,
            'x-crown-signature': signature})

    def test_four_formats_and_four_retries_create_one_contact_four_services_no_downstream(self):
        formats = [('Quick Quote Form EN', False, False),
                   ('Form Submission from Free Quote FR Page', True, False),
                   ('New Instant Moving Estimate Request - Crown Movers', False, True),
                   ('New Instant Moving Estimate Request - Crown Movers FR', True, True)]
        client_ids, service_ids = set(), set()
        with patch.dict('os.environ', {'CROWNOPS_WEBHOOK_SECRET': self.secret}), \
             patch('requests.sessions.Session.request', side_effect=AssertionError('External requests forbidden')):
            for index, (subject, french, escaped) in enumerate(formats):
                body = synthetic_form(french, day=f'2026-11-{15 + index}')
                parsed = _quote_fields(html.escape(body) if escaped else body, subject)
                # The WordPress bridge must map to this canonical contract;
                # actual WS Form field IDs and original bodies are not supplied.
                lead = {key: parsed[key] for key in (
                    'client_name', 'email', 'phone', 'language', 'move_date', 'pickup', 'dropoff',
                    'home_size', 'boxes', 'inventory', 'pickup_access', 'dropoff_access',
                    'pickup_walk', 'dropoff_walk')}
                first = self.submit('synthetic-form-' + str(index), lead)
                self.assertEqual(first.status_code, 200, first.text)
                repeat = self.submit('synthetic-form-' + str(index), lead)
                self.assertEqual(repeat.status_code, 200, repeat.text)
                self.assertTrue(repeat.json()['duplicate'])
                self.assertEqual(first.json()['lead_id'], repeat.json()['lead_id'])
                self.assertEqual(first.json()['messages_sent'], 0)
                self.assertIsNone(first.json()['quote_id'])
                self.assertTrue(first.json()['missing'])
                client_ids.add(first.json()['client_id'])
                service_ids.add(first.json()['lead_id'])
        self.assertEqual(len(client_ids), 1)
        self.assertEqual(len(service_ids), 4)
        with closing(connect(self.path)) as db:
            for table, expected in (('client_accounts', 1), ('leads', 4), ('intake_events', 4),
                                    ('quotes', 0), ('outbound_messages', 0), ('jobs', 0),
                                    ('payment_links', 0), ('deposit_links', 0), ('payments', 0),
                                    ('job_invoices', 0)):
                self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], expected, table)


if __name__ == '__main__':
    unittest.main()
