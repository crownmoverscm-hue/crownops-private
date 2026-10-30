"""Credential reissue tests: disposable users only; no live database or providers."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import secrets
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import wix_test_credential_reissue as reissue
from app.main import sha
from app.store import audit, connect, dumps, insert, now, one, rows, transaction
import test_wix_draft_connector as fixture


class SyntheticCredentialReissueTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.WixDraftConnectorTests('test_preflight_uses_actual_snapshot_and_never_calls_provider')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.path = self.f.path
        self.client = self.f.client
        prepared = self.client.post('/api/wix-drafts/test-access', json={'confirm': True})
        self.assertEqual(prepared.status_code, 200)
        self.initial = {a['email']: a['password'] for a in prepared.json()['accounts']}
        with closing(connect(self.path)) as db:
            self.accounts = reissue.inventory(db)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manifest_path = Path(self.temp.name) / 'verified.json'
        self.manifest = {'status': 'OWNER_REVIEWED_MUSE_METADATA',
                         'verification_reference': 'DISPOSABLE TEST - not live Muse metadata',
                         'accounts': [{k: a[k] for k in ('id', 'email', 'role', 'active')} for a in self.accounts]}
        self.manifest_path.write_text(dumps(self.manifest))
        p = patch.object(reissue, 'MANIFEST_PATH', self.manifest_path)
        p.start(); self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'CROWNOPS_WIX_TEST_REISSUE_ENABLED': '1'})
        p.start(); self.addCleanup(p.stop)
        self.new = [{'id': a['id'], 'password': secrets.token_urlsafe(30)} for a in self.accounts]
        for a in self.new:
            a['confirmation'] = a['password']
        self.new_by_id = {a['id']: a['password'] for a in self.new}

    def preflight(self):
        return self.client.get(reissue.PREFIX + '/preflight')

    def plan(self):
        p = self.preflight()
        self.assertEqual(p.status_code, 200)
        r = self.client.post(reissue.PREFIX + '/plan', json={'inventory_hash': p.json()['inventory_hash'],
            'confirm': True, 'reason': 'Disposable fixture access recovery; no provider action.'})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def execute(self, op, entries=None):
        return self.client.post(reissue.PREFIX + '/execute', json={'operation_id': op['operation_id'],
            'confirm': True, 'passwords': self.new if entries is None else entries})

    def count(self, table):
        with closing(connect(self.path)) as db:
            return db.execute('SELECT count(*) FROM ' + table).fetchone()[0]

    def metadata(self):
        with closing(connect(self.path)) as db:
            return [{k: a[k] for k in ('id','name','email','role','active','employee_id','created_at','mfa_enabled','mfa_present','mfa_created_at')}
                    for a in reissue.inventory(db)]

    def test_preflight_selects_only_five_safe_metadata_rows_and_never_mutates(self):
        before = [self.count(t) for t in ('users','audit','wix_test_credential_reissues','quotes','jobs','outbound_messages')]
        p = self.preflight()
        self.assertEqual(p.status_code, 200)
        self.assertEqual({a['email'] for a in p.json()['accounts']}, set(reissue.EXPECTED))
        self.assertEqual(before, [self.count(t) for t in ('users','audit','wix_test_credential_reissues','quotes','jobs','outbound_messages')])
        self.assertTrue('password_hash' not in p.text)
        self.assertTrue('secret_ciphertext' not in p.text)
        self.assertTrue('token_hash' not in p.text)

    def test_disabled_flag_blocks_plan_and_execution(self):
        op = self.plan()
        with patch.dict(os.environ, {'CROWNOPS_WIX_TEST_REISSUE_ENABLED':'0'}):
            self.assertFalse(self.preflight().json()['can_prepare'])
            self.assertEqual(self.execute(op).status_code, 503)

    def test_missing_or_unverified_manifest_cannot_be_bypassed_by_browser(self):
        self.manifest_path.write_text(dumps({'status':'AWAITING_MUSE_METADATA_AND_OWNER_REVIEW','accounts':[]}))
        p = self.preflight().json()
        self.assertFalse(p['verified_manifest'])
        r = self.client.post(reissue.PREFIX + '/plan', json={'inventory_hash':p['inventory_hash'], 'confirm':True, 'reason':'Disposable test reason'})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.count('wix_test_credential_reissues'),0)

    def test_roles_and_csrf_are_rechecked_for_all_actions(self):
        op = self.plan()
        for role in ('otherowner','manager','sales','crew','inactive'):
            self.f.login(role)
            denied_plan=self.client.post(reissue.PREFIX+'/plan',json={'confirm':True,'reason':'Disposable denied request','inventory_hash':'ignored'})
            for response in (self.preflight(),self.execute(op),denied_plan):
                self.assertIn(response.status_code, (401,403))
        self.f.login('owner')
        self.client.headers['x-csrf-token'] = 'wrong'
        self.assertEqual(self.execute(op).status_code,403)
        self.client.cookies.clear()
        self.assertEqual(self.preflight().status_code,401)

    def test_exact_staging_path_or_environment_mismatch_denied(self):
        with patch.dict(os.environ, {'CROWNOPS_ENV':'production'}):
            self.assertEqual(self.preflight().status_code,503)
        with patch.dict(os.environ, {'CROWNOPS_ENV':'staging'}):
            self.assertEqual(self.preflight().status_code,503)

    def test_approved_ids_roles_active_status_and_master_exclusion_fail_closed(self):
        for field,value in [('id','unexpected-id'),('role','sales'),('active',0)]:
            altered = json.loads(dumps(self.manifest))
            altered['accounts'][0][field] = value
            self.manifest_path.write_text(dumps(altered))
            p = self.preflight()
            self.assertIn(p.status_code,(200,409))
            if p.status_code == 200:
                self.assertFalse(p.json()['can_prepare'])
        self.manifest_path.write_text(dumps(self.manifest))
        with transaction(self.path) as db:
            db.execute("UPDATE users SET role='sales' WHERE id=?",(self.accounts[0]['id'],))
        self.assertEqual(self.preflight().status_code,409)

    def test_plan_is_idempotent_and_contains_no_credentials(self):
        first = self.plan(); second = self.plan()
        self.assertEqual(first['operation_id'],second['operation_id'])
        self.assertTrue(second['duplicate'])
        self.assertFalse(first['credentials_changed'])
        self.assertEqual(self.count('wix_test_credential_reissues'),1)

    def test_success_preserves_identity_roles_mfa_and_all_business_records(self):
        with transaction(self.path) as db:
            insert(db,'auth_factors',dict(user_id=self.accounts[1]['id'],secret_ciphertext='DISPOSABLE-OPAQUE-FIXTURE',enabled=0,created_at=now()))
            for a in self.accounts:
                insert(db,'sessions',dict(token_hash=sha(secrets.token_urlsafe(20)),user_id=a['id'],csrf=secrets.token_urlsafe(20),expires_at='2099-01-01T00:00:00+00:00'))
                insert(db,'account_resets',dict(token_hash=sha(secrets.token_urlsafe(20)),user_id=a['id'],expires_at='2099-01-01T00:00:00+00:00'))
        before = self.metadata()
        with closing(connect(self.path)) as db:
            business = {t: rows(db,'SELECT * FROM ' + t) for t in ('quotes','leads','jobs','payments','outbound_messages','wix_draft_reviews','wix_draft_slots')}
        op = self.plan()
        with patch('requests.Session.request',side_effect=AssertionError('No network permitted')),patch('requests.post',side_effect=AssertionError('No network permitted')):
            result = self.execute(op)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['state'],'Completed')
        self.assertEqual(before,self.metadata())
        with closing(connect(self.path)) as db:
            for a in self.accounts:
                self.assertEqual(db.execute('SELECT count(*) FROM sessions WHERE user_id=?',(a['id'],)).fetchone()[0],0)
                self.assertEqual(db.execute('SELECT count(*) FROM account_resets WHERE user_id=?',(a['id'],)).fetchone()[0],0)
            for t,expected in business.items():
                self.assertEqual(expected,rows(db,'SELECT * FROM ' + t))
            audit_text = dumps(rows(db,'SELECT * FROM audit')) + dumps(rows(db,'SELECT * FROM wix_test_credential_reissues')) + result.text
        for value in [*self.initial.values(), *self.new_by_id.values()]:
            self.assertTrue(value not in audit_text)
        manager = next(a for a in self.accounts if a['role']=='manager')
        inactive = next(a for a in self.accounts if not a['active'])
        client = TestClient(self.f.f.app); self.addCleanup(client.close)
        self.assertEqual(client.post('/api/login',json={'email':manager['email'],'password':self.initial[manager['email']]}).status_code,401)
        self.assertEqual(client.post('/api/login',json={'email':manager['email'],'password':self.new_by_id[manager['id']]}).status_code,200)
        self.assertEqual(client.post('/api/login',json={'email':inactive['email'],'password':self.new_by_id[inactive['id']]}).status_code,401)

    def test_duplicate_execution_is_terminal_and_does_not_revoke_new_sessions(self):
        op = self.plan()
        self.assertEqual(self.execute(op).status_code,200)
        with transaction(self.path) as db:
            insert(db,'sessions',dict(token_hash=sha(secrets.token_urlsafe(20)),user_id=self.accounts[0]['id'],csrf=secrets.token_urlsafe(20),expires_at='2099-01-01T00:00:00+00:00'))
        again = self.execute(op,[])
        self.assertEqual(again.status_code,200)
        self.assertTrue(again.json()['duplicate'])
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sessions WHERE user_id=?',(self.accounts[0]['id'],)).fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE action='Synthetic credential reissue completed; no messages or providers'").fetchone()[0],1)

    def test_unique_passwords_confirmations_and_exact_targets_required(self):
        op = self.plan()
        malformed = []
        for kind in ('missing','duplicate','short','confirmation','target','extra'):
            data = json.loads(dumps(self.new))
            if kind=='missing':data.pop()
            if kind=='duplicate':data[1]['password']=data[0]['password'];data[1]['confirmation']=data[0]['password']
            if kind=='short':data[0]['password']=data[0]['confirmation']='x'
            if kind=='confirmation':data[0]['confirmation']='different'
            if kind=='target':data[0]['id']='unrelated-user'
            if kind=='extra':data[0]['role']='owner'
            malformed.append(data)
        for data in malformed:
            self.assertEqual(self.execute(op,data).status_code,422)
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db,'SELECT state FROM wix_test_credential_reissues')['state'],'Planned')

    def test_stale_account_or_mfa_metadata_prevents_execution(self):
        op = self.plan()
        with transaction(self.path) as db:
            insert(db,'auth_factors',dict(user_id=self.accounts[0]['id'],secret_ciphertext='DISPOSABLE-OPAQUE-FIXTURE',enabled=1,created_at=now()))
        self.assertEqual(self.execute(op).status_code,409)

    def test_expiry_prevents_reset_and_requires_fresh_manifest_review(self):
        op = self.plan()
        future=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(timespec='seconds')
        with patch.object(reissue,'now',return_value=future):
            self.assertEqual(self.execute(op).status_code,409)

    def test_storage_failure_rolls_back_every_account_and_operation(self):
        op = self.plan()
        def fail(*args,**kwargs):
            raise OSError('Disposable storage fault')
        with patch.object(reissue,'audit',side_effect=fail):
            self.assertEqual(self.execute(op).status_code,503)
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db,'SELECT state FROM wix_test_credential_reissues')['state'],'Planned')
        manager=self.accounts[0]
        client=TestClient(self.f.f.app);self.addCleanup(client.close)
        self.assertEqual(client.post('/api/login',json={'email':manager['email'],'password':self.initial[manager['email']]}).status_code,200)

    def test_concurrent_execution_has_exactly_one_successful_mutation(self):
        from concurrent.futures import ThreadPoolExecutor
        op=self.plan()
        user={'id':'owner','csrf':'owner-csrf'}
        data={'operation_id':op['operation_id'],'confirm':True,'passwords':self.new}
        def run(_):return reissue.execute(self.path,user,data)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(run,range(2)))
        self.assertEqual(sum(bool(r['new_changes']) for r in results),1)

    def test_operation_cannot_be_deleted_or_reopened(self):
        op=self.plan()
        for sql in ('DELETE FROM wix_test_credential_reissues',"UPDATE wix_test_credential_reissues SET reason='changed'"):
            with self.assertRaises(sqlite3.IntegrityError):
                with transaction(self.path) as db:db.execute(sql)
        self.execute(op)
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.path) as db:db.execute("UPDATE wix_test_credential_reissues SET state='Planned',completed_at=NULL")

    def test_actual_owner_page_does_not_embed_credentials_or_storage(self):
        r=self.client.get('/wix-test-credential-reissue')
        self.assertEqual(r.status_code,200)
        self.assertIn('No staging reset is authorized yet',r.text)
        js=Path('app/static/wix_test_credential_reissue.js').read_text()
        self.assertTrue('localStorage' not in js and 'sessionStorage' not in js and 'console.' not in js)
        for secret in self.initial.values():
            self.assertTrue(secret not in r.text)

    def test_master_identity_can_never_be_a_reset_target(self):
        from fastapi import HTTPException
        from app.platform_common import put_setting
        with transaction(self.path) as db:
            put_setting(db,'master_owner',{'user_id':self.accounts[0]['id']})
            with self.assertRaises(HTTPException):
                reissue.confirmed_inventory(db,self.manifest)

    def test_production_registration_adds_no_routes_or_tables(self):
        from fastapi import FastAPI
        app=FastAPI();before=len(app.routes)
        with patch.dict(os.environ,{'CROWNOPS_ENV':'production'}):
            reissue.register(app,Path(self.temp.name)/'must-not-exist.sqlite',lambda:None)
        self.assertEqual(len(app.routes),before)
        self.assertFalse((Path(self.temp.name)/'must-not-exist.sqlite').exists())


    def test_malformed_sensitive_body_is_never_reflected(self):
        secret=secrets.token_urlsafe(30)
        for body in ([{'password':secret}],secret,{'password':secret}):
            r=self.client.post(reissue.PREFIX+'/execute',json=body)
            self.assertEqual(r.status_code,422)
            self.assertTrue(secret not in r.text)
