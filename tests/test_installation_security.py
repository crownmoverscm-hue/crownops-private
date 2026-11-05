"""Installation-level security for separately deployed companies (SEC-3). Fictional data only."""
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
OWNER = {'name': 'Fictional Owner', 'email': 'owner@example.invalid', 'password': 'fictional-owner-pass-123', 'confirm_password': 'fictional-owner-pass-123'}


def digest(code):
    return hashlib.sha256(code.encode()).hexdigest()


class WebSetupCodeTests(unittest.TestCase):
    def client(self, env):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        patcher = patch.dict(os.environ, {'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', **env}); patcher.start(); self.addCleanup(patcher.stop)
        os.environ.pop('CROWNOPS_SETUP_CODE_HASH', None) if 'CROWNOPS_SETUP_CODE_HASH' not in env else None
        c = TestClient(create_app(Path(temp.name) / 'install.sqlite3')); self.addCleanup(c.close)
        return c

    def test_no_shared_setup_hash_is_built_in(self):
        import app.main as main
        self.assertEqual(main.SETUP_TOKEN_HASH, '')
        self.assertNotIn('a2f107444f141c2a3d61af99ec02063038c77b60c0f639ef6bf7f5304fa21bb1', (ROOT / 'app' / 'main.py').read_text())

    def test_web_setup_is_closed_without_a_per_installation_hash(self):
        c = self.client({})
        r = c.post('/api/setup-owner', json={**OWNER, 'setup_code': 'any-guess'})
        self.assertEqual(r.status_code, 503)
        self.assertTrue(c.get('/api/health').json()['setup_required'])

    def test_malformed_hash_keeps_setup_closed(self):
        c = self.client({'CROWNOPS_SETUP_CODE_HASH': 'not-a-sha256'})
        self.assertEqual(c.post('/api/setup-owner', json={**OWNER, 'setup_code': 'not-a-sha256'}).status_code, 503)

    def test_another_installations_code_cannot_claim_this_owner(self):
        c = self.client({'CROWNOPS_SETUP_CODE_HASH': digest('install-b-private-code')})
        self.assertEqual(c.post('/api/setup-owner', json={**OWNER, 'setup_code': 'install-a-private-code'}).status_code, 401)
        self.assertEqual(c.post('/api/setup-owner', json={**OWNER, 'setup_code': 'install-b-private-code'}).status_code, 200)
        self.assertEqual(c.post('/api/setup-owner', json={**OWNER, 'setup_code': 'install-b-private-code'}).status_code, 409)


class SetupCodeCliTests(unittest.TestCase):
    def test_generator_prints_code_and_matching_hash_and_doctor_reports_it(self):
        with tempfile.TemporaryDirectory() as td:
            env = {k: v for k, v in os.environ.items() if k != 'CROWNOPS_SETUP_CODE_HASH'}
            base = [sys.executable, str(ROOT / 'manage.py'), '--db', str(Path(td) / 'x.sqlite3')]
            out = subprocess.run(base + ['setup-code'], capture_output=True, text=True, env=env, cwd=ROOT, timeout=120).stdout.splitlines()
            code = out[1].strip(); stored = out[3].strip().split('=', 1)[1]
            self.assertGreaterEqual(len(code), 30); self.assertEqual(stored, digest(code))
            doctor = subprocess.run(base + ['doctor'], capture_output=True, text=True, env={**env, 'CROWNOPS_SETUP_CODE_HASH': stored}, cwd=ROOT, timeout=120)
            self.assertIn('"web_setup_enabled": true', doctor.stdout)
            self.assertNotIn(code, doctor.stdout)


if __name__ == '__main__':
    unittest.main()
