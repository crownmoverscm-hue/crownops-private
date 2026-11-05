"""D01-mfa-require-2: mandatory MFA at the dedicated pilot startup boundary.

Fictional accounts only; no interactive enrollment, credentials or provider calls.
"""
import os
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from app.main import create_app, create_user
from app.store import initialize
from app.core_pilot import REQUIRED, create_pilot_app


class MandatoryPilotMFAStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='d01-require2-fictional-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'fictional.sqlite3'
        self.env = patch.dict(os.environ, {
            'CROWNOPS_PROFILE': 'core-v1', 'CROWNOPS_ENV': 'test',
            'CROWNOPS_ALLOW_EXTERNAL': '0', 'CROWNOPS_RUN_WORKER': '0',
            'CROWNOPS_REQUIRE_MFA': '0',
        })
        self.env.start(); self.addCleanup(self.env.stop)
        initialize(self.path)
        self.passwords = {}
        for role in ('owner', 'sales'):
            password = secrets.token_urlsafe(24)
            self.passwords[role] = password
            create_user(self.path, role + '@example.invalid', 'TEST ' + role, password, role=role)
        create_app(self.path)
        self.valid = {**REQUIRED, 'CROWNOPS_DB': str(self.path),
                      'CROWNOPS_PUBLIC_URL': 'https://core-pilot.example.invalid',
                      'CROWNOPS_SECRET_KEY': Fernet.generate_key().decode()}

    def test_01_exact_ten_setting_configuration_starts_locked_and_denies_password_only(self):
        self.assertEqual(REQUIRED['CROWNOPS_REQUIRE_MFA'], '1')
        self.assertEqual(len(self.valid), 10)
        with patch.dict(os.environ, self.valid):
            app = create_pilot_app()
            self.assertTrue(app.state.core_v1_pilot_entrypoint)
            self.assertTrue(app.state.core_v1_profile)
            self.assertTrue(app.state.core_v1_workers_disabled)
            with TestClient(app, base_url=self.valid['CROWNOPS_PUBLIC_URL']) as client:
                for role in ('owner', 'sales'):
                    response = client.post('/api/login', json={
                        'email': role + '@example.invalid', 'password': self.passwords[role]})
                    self.assertEqual(response.status_code, 401)
                self.assertEqual(client.post('/api/quotes', json={}).status_code, 403)
        print('D01_REQUIRE2_VALID settings=10 locked=true workers_disabled=true owner_sales_password_only=401 forbidden_quote=403')

    def test_02_missing_flag_refused_before_database_access_or_app_construction(self):
        with patch.dict(os.environ, self.valid), patch('app.main.create_app') as construct, patch('app.store.connect') as connect:
            os.environ.pop('CROWNOPS_REQUIRE_MFA', None)
            with self.assertRaisesRegex(RuntimeError, 'CROWNOPS_REQUIRE_MFA'):
                create_pilot_app()
            construct.assert_not_called(); connect.assert_not_called()
        print('D01_REQUIRE2_MISSING refused=true database_access=0 app_construction=0 fallback=0')

    def test_03_invalid_flags_refused_before_database_access_or_app_construction(self):
        for value in ('0', '', 'true', 'false', 'yes', '01', ' 1', '1 '):
            with self.subTest(value=value), patch.dict(os.environ, {**self.valid, 'CROWNOPS_REQUIRE_MFA': value}), patch('app.main.create_app') as construct, patch('app.store.connect') as connect:
                with self.assertRaisesRegex(RuntimeError, 'CROWNOPS_REQUIRE_MFA'):
                    create_pilot_app()
                construct.assert_not_called(); connect.assert_not_called()
        print('D01_REQUIRE2_INVALID variants=8 refused=true database_access=0 app_construction=0 fallback=0')


if __name__ == '__main__':
    unittest.main(verbosity=2)
