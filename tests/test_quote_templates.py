"""COM-4b: quote templates and contractual wording per company. Fictional data only."""
import json
import os
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.business_zone import DEFAULT_ZONE, set_business_zone
from app.main import create_app, sha
from app.platform_common import put_setting
from app.store import connect, initialize, insert, now, transaction

CROWN_TERMS = ('0.60', '0,60', 'Crown')


class TemplateInstallation(unittest.TestCase):
    MODE = None

    def setUp(self):
        env = patch.dict(os.environ, {'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0'}); env.start(); self.addCleanup(env.stop)
        self.addCleanup(set_business_zone, DEFAULT_ZONE)
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 't.sqlite3'
        if self.MODE:
            initialize(self.path)
            with transaction(self.path) as db:
                put_setting(db, 'company_identity_mode', self.MODE)
        self.app = create_app(self.path)
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.c = {}
        with transaction(self.path) as db:
            for role in ('owner', 'sales'):
                insert(db, 'users', dict(id=role, email=role + '@example.invalid', name=role, role=role, password_hash='x', active=1, created_at=now()))
                insert(db, 'sessions', dict(token_hash=sha(role + '-s'), user_id=role, csrf=role + '-c', expires_at=expires))
        for role in ('owner', 'sales'):
            client = TestClient(self.app); client.cookies.set('crownops_session', role + '-s'); client.headers['x-csrf-token'] = role + '-c'
            self.c[role] = client; self.addCleanup(client.close)

    def templates(self):
        return {t['template_key']: t for t in self.c['owner'].get('/api/crm/templates/manage').json()}


class LegacyCrownTemplatesTests(TemplateInstallation):
    def test_crown_keeps_its_active_templates_and_material(self):
        t = self.templates()
        self.assertTrue(all(x['active'] for x in t.values()))
        self.assertIn('$0.60/lb per article', t['moving-en']['body']['liability'])
        with closing(connect(self.path)) as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM material_catalog WHERE code='TV_PROTECTION_PACKAGE'").fetchone())


class NewCompanyTemplatesTests(TemplateInstallation):
    MODE = 'unconfigured'

    def test_new_company_inherits_no_terms_and_cannot_quote_until_it_approves_its_own(self):
        t = self.templates()
        self.assertEqual(len(t), 8)
        for key, tpl in t.items():
            with self.subTest(template=key):
                self.assertFalse(tpl['active']); self.assertEqual(tpl['body']['liability'], ''); self.assertEqual(tpl['body']['fragile'], '')
                self.assertFalse(any(term in json.dumps(tpl, ensure_ascii=False) for term in CROWN_TERMS))
        with closing(connect(self.path)) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM material_catalog WHERE code='TV_PROTECTION_PACKAGE'").fetchone())
        self.assertEqual(self.c['sales'].get('/api/crm/templates').json(), [])  # nothing usable for estimates yet

    def test_only_managers_approve_and_approval_requires_owner_wording(self):
        tpl = self.templates()['moving-en']
        payload = {'title': 'Local move', 'liability': 'Released value per our signed agreement.', 'fragile': '', 'approved': True, 'updated_at': tpl['updated_at']}
        self.assertEqual(self.c['sales'].put('/api/crm/templates/moving-en', json=payload).status_code, 403)
        self.assertEqual(self.c['owner'].put('/api/crm/templates/moving-en', json={**payload, 'liability': ''}).status_code, 422)
        self.assertEqual(self.c['owner'].put('/api/crm/templates/moving-en', json={**payload, 'liability': '<script>x</script>'}).status_code, 422)
        saved = self.c['owner'].put('/api/crm/templates/moving-en', json=payload)
        self.assertEqual(saved.status_code, 200, saved.text); self.assertEqual(saved.json()['active'], 1)
        self.assertEqual(self.c['owner'].put('/api/crm/templates/moving-en', json=payload).status_code, 409)  # stale edit refused
        self.assertEqual([x['template_key'] for x in self.c['sales'].get('/api/crm/templates').json()], ['moving-en'])
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE action='Quote template approved'").fetchone()[0], 1)

    def test_status_reports_templates_and_terms(self):
        states = {i['key']: i['state'] for i in self.c['sales'].get('/api/company/setup-status').json()['items']}
        self.assertEqual((states['templates'], states['terms']), ('incomplete', 'incomplete'))
        tpl = self.templates()['moving-en']
        self.c['owner'].put('/api/crm/templates/moving-en', json={'title': 'Local move', 'liability': 'Our terms.', 'approved': True, 'updated_at': tpl['updated_at']})
        terms = self.c['owner'].post('/api/platform/settings/client_terms', json={'approved': True, 'version': 'v1', 'review_note': 'Fictional owner review', 'text': 'Fictional booking terms written by this company owner for testing.'})
        self.assertEqual(terms.status_code, 200, terms.text)
        states = {i['key']: i['state'] for i in self.c['sales'].get('/api/company/setup-status').json()['items']}
        self.assertEqual((states['templates'], states['terms']), ('approved', 'approved'))


if __name__ == '__main__':
    unittest.main()
