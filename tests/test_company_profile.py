"""Company identity (COM-1): client communications use the installation's company profile.

Fictional data only. No provider account is contacted; Gmail is mocked.
"""
import base64
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.main import create_app, sha
from app.store import connect, insert, now, transaction

ROOT = Path(__file__).resolve().parent.parent
LEGACY_MARKERS = re.compile(r'Crown Movers|crownmovers|514-606-4030|5146064030|Montréal time|heure de Montréal|\bCrown\b')
NEW_COMPANY = {
    'name': 'Fictional Haulers Ltd', 'short_name': 'Fictional Haulers', 'phone': '(604) 555-0142',
    'email': 'office@fictional-haulers.example.invalid', 'timezone': 'America/Vancouver',
    'local_time_label': 'Vancouver time', 'local_time_label_fr': 'heure de Vancouver',
}
# Modules that compose client-facing messages. Brand text belongs only in app/company_profile.py.
OUTBOUND_MODULES = ['client_communications', 'sales_crm_v6', 'workflows', 'extensions', 'sales_journey',
                    'review_features', 'conversations', 'providers', 'worker', 'invoice_quickbooks_delivery']


class CompanyProfileApiTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', 'QBO_ENV': 'sandbox'})
        env.start(); self.addCleanup(env.stop)
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'company.sqlite3'
        self.app = create_app(self.path)
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.clients = {}
        with transaction(self.path) as db:
            for role in ('owner', 'manager', 'sales', 'crew'):
                insert(db, 'users', dict(id=role, email=role + '@example.invalid', name=role.title(), role=role,
                                         password_hash='unused', active=1, created_at=now()))
                insert(db, 'sessions', dict(token_hash=sha(role + '-session'), user_id=role, csrf=role + '-csrf', expires_at=expires))
        for role in ('owner', 'manager', 'sales', 'crew'):
            c = TestClient(self.app); c.cookies.set('crownops_session', role + '-session'); c.headers['x-csrf-token'] = role + '-csrf'
            self.clients[role] = c; self.addCleanup(c.close)
        self.public = TestClient(self.app); self.addCleanup(self.public.close)

    def save(self, role='owner', **changes):
        current = self.clients['owner'].get('/api/company/profile').json()
        return self.clients[role].put('/api/company/profile', json={**NEW_COMPANY, **changes, 'version': current['version']})

    def test_existing_installation_keeps_legacy_identity_until_configured(self):
        profile = self.clients['sales'].get('/api/company/profile').json()
        self.assertFalse(profile['configured'])
        self.assertEqual((profile['name'], profile['phone'], profile['email']), ('Crown Movers', '514-606-4030', 'info@crownmovers.ca'))

    def test_only_owner_or_manager_can_change_profile_and_change_is_audited(self):
        self.assertEqual(self.save('sales').status_code, 403)
        self.assertEqual(self.save('crew').status_code, 403)
        self.assertEqual(self.clients['crew'].get('/api/company/profile').status_code, 403)
        saved = self.save('manager')
        self.assertEqual(saved.status_code, 200, saved.text)
        body = saved.json()
        self.assertTrue(body['configured']); self.assertEqual(body['version'], 1)
        self.assertEqual(body['phone_tel'], '+16045550142'); self.assertEqual(body['email'], NEW_COMPANY['email'])
        with closing(connect(self.path)) as db:
            row = db.execute("SELECT actor,action FROM audit WHERE action='Company profile updated'").fetchone()
        self.assertEqual(tuple(row), ('manager', 'Company profile updated'))

    def test_stale_version_is_rejected_without_overwriting(self):
        self.assertEqual(self.save().status_code, 200)
        stale = self.clients['owner'].put('/api/company/profile', json={**NEW_COMPANY, 'name': 'Overwrite Attempt', 'version': 0})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.clients['owner'].get('/api/company/profile').json()['name'], NEW_COMPANY['name'])

    def test_invalid_profiles_are_refused_and_nothing_is_stored(self):
        cases = [{'email': 'not-an-email'}, {'timezone': 'Mars/Olympus'}, {'phone': ''}, {'phone': '12'},
                 {'name': '<script>x</script>'}, {'name': ''}, {'name': 'x' * 121}, {'unexpected': 'field'}]
        for change in cases:
            with self.subTest(change=change):
                self.assertEqual(self.save(**change).status_code, 422)
        self.assertFalse(self.clients['owner'].get('/api/company/profile').json()['configured'])

    def test_public_profile_exposes_only_client_contact_details(self):
        self.save()
        public = self.public.get('/api/company/public-profile')
        self.assertEqual(public.status_code, 200)
        self.assertEqual(set(public.json()), {'name', 'phone', 'phone_tel', 'email'})

    def test_public_request_acknowledgement_uses_configured_company(self):
        self.save()
        data = {'request_id': 'company-profile-ack-0001', 'permission_to_contact': True,
                'lead': {'client_name': 'Fictional Client', 'email': 'client@example.invalid', 'phone': '6045550100', 'move_date': '2026-12-01'}}
        self.assertEqual(self.public.post('/api/request', json=data).status_code, 200)
        with closing(connect(self.path)) as db:
            subject, body = db.execute("SELECT subject,body FROM outbound_messages WHERE event_key='request-ack:company-profile-ack-0001'").fetchone()
        self.assertIn(NEW_COMPANY['name'], subject + body); self.assertIn(NEW_COMPANY['phone'], body)
        self.assertIsNone(LEGACY_MARKERS.search(subject + body), subject + body)

    def test_sent_email_from_name_and_html_use_configured_company(self):
        self.save()
        from app.providers import send_gmail
        response = Mock(ok=True); response.json.return_value = {'id': 'mock-id'}
        profile = Mock(); profile.json.return_value = {'emailAddress': 'office@example.invalid'}
        msg = {'id': 'MAIL-COMPANY', 'recipient': 'client@example.invalid', 'subject': 'Your estimate', 'category': 'Reply', 'event_key': 'test',
               'body': 'Hi Client,\n\nhttps://crownops.example.invalid/invoice-access#access=test'}
        with patch('app.providers.provider_token', return_value={'access_token': 'not-a-real-token'}), \
                patch('app.providers.requests.get', return_value=profile), \
                patch('app.providers.requests.post', return_value=response) as send:
            send_gmail(self.path, msg)
        mime = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(send.call_args.kwargs['json']['raw']))
        self.assertIn(NEW_COMPANY['name'], str(mime['From']))
        html = mime.get_body(preferencelist=('html',)).get_content()
        self.assertIn('tel:+16045550142', html); self.assertIn(NEW_COMPANY['name'], html)
        self.assertIsNone(LEGACY_MARKERS.search(html + str(mime['From'])))


class CompanyTemplateTests(unittest.TestCase):
    def setUp(self):
        from app.company_profile import validate_profile
        self.company = {**validate_profile(NEW_COMPANY), 'configured': True}
        self.lead = {'client_name': 'Fictional Client', 'language': 'English', 'email': 'client@example.invalid'}
        self.job = {'start_at': '2026-12-01T16:00:00+00:00'}

    def assert_clean(self, text):
        self.assertIsNone(LEGACY_MARKERS.search(text), text)
        self.assertIn(self.company['name'], text)

    def test_preparation_reminder_uses_company_name_phone_and_time_zone(self):
        from app.client_communications import preparation_body
        for language in ('English', 'French'):
            with self.subTest(language=language):
                body = preparation_body({**self.lead, 'language': language}, self.job, 'https://example.invalid/portal', company=self.company)
                self.assert_clean(body); self.assertIn(self.company['phone'], body)
                from zoneinfo import ZoneInfo
                start = datetime.fromisoformat(self.job['start_at'])
                expected = start.astimezone(ZoneInfo('America/Vancouver')).strftime('%I:%M %p').lstrip('0')
                montreal = start.astimezone(ZoneInfo('America/Toronto')).strftime('%I:%M %p').lstrip('0')
                self.assertNotEqual(expected, montreal)
                self.assertIn('arrival: ' + expected if language == 'English' else 'prévue : ' + expected, body)
                self.assertIn('Vancouver', body)

    def test_invoice_email_and_html_use_company_contact(self):
        from app.client_communications import email_html, invoice_body
        calc = {'total_cents': 100000, 'deposit_cents': 0, 'balance_cents': 100000}
        for language in ('English', 'French'):
            with self.subTest(language=language):
                body = invoice_body({**self.lead, 'language': language}, {'number': 'INV-1'}, calc, 'https://example.invalid/invoice-access#a', company=self.company)
                self.assert_clean(body); self.assertIn(self.company['email'], body)
                html = email_html('Invoice', body, self.company)
                self.assert_clean(html); self.assertIn('tel:+16045550142', html)

    def test_booking_confirmation_uses_company_contact(self):
        from app.sales_crm_v6 import confirmation
        quote = {'inputs_json': '{"move_date":"2026-12-01","language":"French"}'}
        body = confirmation({**self.lead, 'language': 'French'}, quote, None, self.company)
        self.assert_clean(body); self.assertIn(self.company['email'], body)

    def test_quickbooks_invoice_memo_uses_company(self):
        from app.providers import qbo_invoice_payload
        doc = {'id': 'DOC-1', 'revision': 1, 'service_date': '2026-12-01', 'language': 'English',
               'terms': {'customer': {'name': 'Fictional Client', 'email': 'client@example.invalid'}},
               'calculation': {'lines': [], 'discount_cents': 0}}
        memo = qbo_invoice_payload(doc, 'C1', 'ITEM', 'TAX', '', self.company)['CustomerMemo']['value']
        self.assert_clean(memo)

    def test_legacy_default_output_is_unchanged_for_existing_installation(self):
        from app.client_communications import preparation_body
        body = preparation_body(self.lead, self.job, 'https://example.invalid/portal')
        self.assertIn('Your Crown Movers service is coming up!', body)
        self.assertIn('(Montréal time)', body); self.assertIn('call 514-606-4030.', body)
        self.assertTrue(body.endswith('Thank you,\nThe Crown Movers team'))


class CompanyBrandGuardTests(unittest.TestCase):
    def test_outbound_modules_do_not_hardcode_another_company(self):
        offenders = []
        for name in OUTBOUND_MODULES:
            for number, line in enumerate((ROOT / 'app' / (name + '.py')).read_text().splitlines(), 1):
                if re.search(r'Crown Movers|crownmovers\.ca|514-606-4030|5146064030', line) and 'COM-4' not in line:
                    offenders.append(f'{name}.py:{number}')
        self.assertEqual(offenders, [], 'Client-facing brand text must come from app/company_profile.py')


class CompanyProfileCliTests(unittest.TestCase):
    def test_provisioning_command_stores_profile_and_doctor_reports_it(self):
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / 'new-company.sqlite3')
            env = {**os.environ, 'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0'}
            base = [sys.executable, str(ROOT / 'manage.py'), '--db', db]
            bad = subprocess.run(base + ['company-profile', '--name', 'X Movers', '--phone', '604-555-0100', '--email', 'bad', '--timezone', 'America/Vancouver'],
                                 capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
            self.assertNotEqual(bad.returncode, 0); self.assertIn('valid company email', bad.stderr)
            ok = subprocess.run(base + ['company-profile', '--name', 'X Movers', '--phone', '604-555-0100',
                                        '--email', 'office@x-movers.example.invalid', '--timezone', 'America/Vancouver'],
                                capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
            self.assertEqual(ok.returncode, 0, ok.stderr); self.assertIn('"configured": true', ok.stdout)
            doctor = subprocess.run(base + ['doctor'], capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
            self.assertIn('"company_profile_configured": true', doctor.stdout)


if __name__ == '__main__':
    unittest.main()
