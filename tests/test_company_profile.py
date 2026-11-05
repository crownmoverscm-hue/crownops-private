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
                    'review_features', 'conversations', 'providers', 'worker', 'invoice_quickbooks_delivery',
                    'operations', 'ux_features', 'staff_onboarding', 'account_access', 'estimate_delivery',
                    'portal', 'invoice_access', 'client_participation', 'document_browser', 'main', 'invoices', 'deposits']


class CompanyFixture(unittest.TestCase):
    """Shared fictional installation with one signed-in user per role (no tests of its own)."""

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


class CompanyProfileApiTests(CompanyFixture):

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


class ConfiguredCompanyDefaultsTests(CompanyFixture):
    """A configured company never inherits Crown's legal identity, tax numbers or mailboxes."""

    def test_billing_form_prefills_own_identity_and_no_foreign_tax_registrations(self):
        legacy = self.clients['owner'].get('/api/platform/settings').json()['billing_policy']['terms']
        self.assertIn('Crown Movers', legacy['issuer']['name'])  # existing installation unchanged
        self.assertEqual([t['registration'] for t in legacy['taxes']], ['737446534RT001', '1227197265TQ001'])
        self.save()
        terms = self.clients['owner'].get('/api/platform/settings').json()['billing_policy']['terms']
        self.assertEqual(terms['issuer'], {'name': NEW_COMPANY['name'], 'address': '', 'email': NEW_COMPANY['email'], 'phone': NEW_COMPANY['phone']})
        self.assertEqual(terms['taxes'], [])
        self.assertIsNone(LEGACY_MARKERS.search(str(terms)))

    def test_staff_invitation_preview_uses_company_name(self):
        self.save()
        preview = self.clients['owner'].post('/api/account-access/invitation-preview',
                                             json={'name': 'Fictional Mover', 'email': 'mover@example.invalid', 'role': 'crew'})
        self.assertEqual(preview.status_code, 200, preview.text)
        content = preview.json()
        self.assertIn(NEW_COMPANY['name'], content['subject'])
        self.assertIsNone(LEGACY_MARKERS.search(content['subject'] + content['body'] + content['html']))

    def test_new_installation_with_profile_does_not_seed_another_companys_mailboxes(self):
        from app.company_profile import store_profile
        from app.platform_common import settings
        from app.store import initialize
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'fresh.sqlite3'
            initialize(path)
            with transaction(path) as db:
                store_profile(db, {**NEW_COMPANY, 'version': 0}, 'test')
            create_app(path)
            with closing(connect(path)) as db:
                comms = settings(db, 'company_communications')
            self.assertEqual(comms['send_as'], ''); self.assertFalse(comms['approved'])
            self.assertIsNone(LEGACY_MARKERS.search(str(comms)))


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

    def test_quickbooks_estimate_memo_uses_company(self):
        from app.estimate_delivery import estimate_payload
        import inspect
        source = inspect.getsource(estimate_payload)
        self.assertIn("c['name']", source); self.assertIn("c['phone']", source)
        self.assertIsNone(re.search(r'Crown(?!Ops)|514-606-4030', source))  # CrownOps is the product name

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


class NewInstallationFailClosedTests(CompanyFixture):
    """A new company without a profile can capture work but cannot speak or invoice as anyone."""

    def setUp(self):
        super().setUp()
        from app.platform_common import put_setting
        with transaction(self.path) as db:
            put_setting(db, 'company_identity_mode', 'unconfigured')

    def test_identity_mode_is_recorded_once_from_existing_data(self):
        from app.store import initialize
        from app.platform_common import settings
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'CROWNOPS_ENV': 'staging'}):
            fresh, existing = Path(td) / 'fresh.sqlite3', Path(td) / 'existing.sqlite3'
            initialize(fresh)
            with patch.dict(os.environ, {'CROWNOPS_ENV': 'test'}):
                initialize(existing)
            with transaction(existing) as db:
                insert(db, 'users', dict(id='u1', email='u1@example.invalid', name='U', role='owner', password_hash='x', active=1, created_at=now()))
            initialize(existing)
            initialize(fresh)  # repeated startups never flip the decision
            with closing(connect(fresh)) as a, closing(connect(existing)) as b:
                self.assertEqual(settings(a, 'company_identity_mode'), 'unconfigured')
                self.assertEqual(settings(b, 'company_identity_mode'), 'legacy-crown')

    def test_profile_reports_not_ready_without_crown_identity(self):
        profile = self.clients['owner'].get('/api/company/profile').json()
        self.assertFalse(profile['ready']); self.assertEqual(profile['mode'], 'unconfigured')
        self.assertIsNone(LEGACY_MARKERS.search(str(profile)))
        self.assertIsNone(LEGACY_MARKERS.search(self.public.get('/api/company/public-profile').text))

    def test_public_inquiry_and_callback_are_saved_but_no_message_is_prepared(self):
        data = {'request_id': 'unconfigured-form-0001', 'permission_to_contact': True, 'callback_request': True,
                'lead': {'client_name': 'Fictional Client', 'email': 'client@example.invalid', 'phone': '6045550100'}}
        self.assertEqual(self.public.post('/api/request', json=data).status_code, 200)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM leads WHERE email='client@example.invalid'").fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM client_callbacks').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM outbound_messages').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM portal_invites').fetchone()[0], 0)

    def test_staff_cannot_prepare_client_messages_until_profile_exists(self):
        from fastapi import HTTPException
        from app.platform_common import queue_message
        with transaction(self.path) as db:
            insert(db, 'leads', dict(id='L1', client_name='Fictional', email='c@example.invalid', phone='6045550100',
                                     stage='New Lead', created_at=now(), updated_at=now()))
        with transaction(self.path) as db, self.assertRaises(HTTPException) as refused:
            queue_message(db, 'L1', 'Subject', 'Body', 'Reply', 'owner', 'k1')
        self.assertEqual(refused.exception.status_code, 409)
        self.assertIn('company profile', refused.exception.detail)
        self.save()
        with transaction(self.path) as db:
            self.assertEqual(queue_message(db, 'L1', 'Subject', 'Body', 'Reply', 'owner', 'k1')['recipient'], 'c@example.invalid')

    def test_email_send_worker_and_billing_defaults_fail_closed(self):
        from app.providers import send_gmail
        from app.worker import tick
        with patch('app.providers.provider_token', return_value={'access_token': 'not-a-real-token'}), \
                patch('app.providers.requests.get') as get, patch('app.providers.requests.post') as post:
            get.return_value.json.return_value = {'emailAddress': 'office@example.invalid'}
            with self.assertRaises(ValueError):
                send_gmail(self.path, {'id': 'M1', 'recipient': 'c@example.invalid', 'subject': 's', 'body': 'b', 'category': 'Reply', 'event_key': 'k'})
            post.assert_not_called()
        self.assertEqual(tick(self.path)['drafts_created'], 0)
        terms = self.clients['owner'].get('/api/platform/settings').json()['billing_policy']['terms']
        self.assertEqual(terms['issuer']['name'], ''); self.assertEqual(terms['taxes'], [])
        self.assertIsNone(LEGACY_MARKERS.search(str(terms)))

    def test_templates_refuse_an_unready_profile(self):
        from fastapi import HTTPException
        from app.client_communications import invoice_body
        with closing(connect(self.path)) as db:
            from app.company_profile import company_profile
            unready = company_profile(db)
        with self.assertRaises(HTTPException):
            invoice_body({'client_name': 'C', 'language': 'English'}, {'number': 'I'}, {'total_cents': 1, 'deposit_cents': 0, 'balance_cents': 1}, 'https://x.invalid', company=unready)


class ClientPagesAndDocumentsTests(CompanyFixture):
    """COM-2b server side: pages, documents and review links follow the company profile."""

    def page(self, lang='en'):
        return self.public.get('/portal/missing?lang=' + lang, headers={'accept': 'text/html'})

    def test_legacy_error_page_keeps_crown_contact(self):
        html = self.page().text
        self.assertIn('Call Crown Movers at (514) 606-4030', html); self.assertIn('href="tel:+15146064030"', html)

    def test_configured_error_page_uses_company_contact(self):
        self.save()
        for lang in ('en', 'fr'):
            with self.subTest(lang=lang):
                html = self.page(lang).text
                self.assertIn(NEW_COMPANY['name'], html); self.assertIn('href="tel:+16045550142"', html)
                self.assertIsNone(LEGACY_MARKERS.search(html), html)

    def test_unconfigured_error_page_has_no_call_button_or_crown(self):
        from app.platform_common import put_setting
        with transaction(self.path) as db:
            put_setting(db, 'company_identity_mode', 'unconfigured')
        html = self.page().text
        self.assertNotIn('tel:', html); self.assertIsNone(LEGACY_MARKERS.search(html))

    def test_portal_link_request_reply_uses_company(self):
        self.save()
        reply = self.public.post('/api/portal/request-link', json={'email': 'nobody@example.invalid'})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertIn(NEW_COMPANY['name'], reply.json()['message']); self.assertIn(NEW_COMPANY['phone'], reply.json()['message'])
        self.assertIsNone(LEGACY_MARKERS.search(reply.text))

    def test_review_profiles_default_to_crown_only_for_legacy_install(self):
        from app.client_participation import policy
        from app.platform_common import put_setting
        with closing(connect(self.path)) as db:
            self.assertEqual([p['id'] for p in policy(db)['profiles']], ['main', 'french'])
        with transaction(self.path) as db:
            put_setting(db, 'company_identity_mode', 'unconfigured')
        with closing(connect(self.path)) as db:
            self.assertEqual(policy(db)['profiles'], [])
        self.save()
        with closing(connect(self.path)) as db:
            self.assertEqual(policy(db)['profiles'], [])

    def test_invoice_document_footer_uses_stamped_company_and_legacy_otherwise(self):
        from app.invoices import invoice_html
        import inspect
        from app import invoices
        self.assertIn("doc['company']={'name':company['name'],'phone':company['phone']}", inspect.getsource(invoices))
        doc_src = inspect.getsource(invoice_html)
        self.assertIn("document.get('company')", doc_src); self.assertIn('LEGACY_INVOICE_FOOTER', doc_src)

    def test_quote_email_export_is_not_signed_by_a_person_for_other_companies(self):
        from app.main import quote_document
        from app.company_profile import validate_profile
        company = {**validate_profile(NEW_COMPANY), 'configured': True, 'ready': True, 'mode': 'configured'}
        q = {'result_json': '{"movers":2,"trucks":1,"hourly_rate_cents":10000,"service_low":2,"service_high":3,"transport_cents":0,"fuel_cents":0,"materials_cents":0,"other_cents":0,"discount_cents":0,"low_cents":20000,"high_cents":30000,"minimum_hours":3}'}
        for language in ('English', 'French'):
            subject, body = quote_document(q, {'client_name': 'C', 'language': language}, company)
            self.assertNotIn('Jonathan', body); self.assertIsNone(LEGACY_MARKERS.search(subject + body))
            self.assertTrue(body.endswith(NEW_COMPANY['name'] + '\n' + NEW_COMPANY['phone']))
        legacy_subject, legacy_body = quote_document(q, {'client_name': 'C', 'language': 'English'})
        self.assertIn('Jonathan here!', legacy_body); self.assertTrue(legacy_body.endswith('Jonathan\nCrown Movers\n514-606-4030'))
