"""Two recovery systems: disposable databases, no live accounts or provider calls."""
from contextlib import closing
from pathlib import Path
import os
import secrets
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import synthetic_recovery_guard as guard
from app import account_access
from app.main import sha
from app.store import connect, transaction, insert, rows, now
import test_wix_test_credential_reissue as fixture


class SyntheticRecoveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.SyntheticCredentialReissueTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.path, self.client = self.f.path, self.f.client
        self.active = [a for a in self.f.accounts if a['active']]

    def snapshot(self):
        with closing(connect(self.path)) as db:
            return {t:rows(db,'SELECT * FROM '+t) for t in ('users','sessions','auth_factors','account_resets','audit','wix_test_credential_reissues')}

    def seed_grant(self, account):
        token=secrets.token_urlsafe(40)
        with transaction(self.path) as db:
            insert(db,'account_resets',dict(token_hash=sha(token),user_id=account['id'],expires_at='2099-01-01T00:00:00+00:00'))
        return token

    def test_missing_enable_flag_blocks_both_synthetic_mutations_without_grants_or_changes(self):
        before=self.snapshot()
        with patch.dict(os.environ):
            os.environ.pop('CROWNOPS_WIX_TEST_REISSUE_ENABLED',None)
            p=self.f.preflight().json()
            self.assertFalse(p['can_prepare']);self.assertFalse(p['can_execute'])
            self.assertEqual(self.client.post('/api/wix-drafts/credential-reissue/plan',json={'confirm':True,'reason':'Disposable review only','inventory_hash':p['inventory_hash']}).status_code,503)
            self.assertEqual(self.f.execute({'operation_id':'WIX-REISSUE-'+'a'*32}).status_code,503)
        self.assertEqual(before,self.snapshot())

    def test_legacy_private_grants_blocked_for_targets_even_for_owner_and_manager(self):
        before=self.snapshot()
        for actor in ('owner','manager','otherowner'):
            self.f.f.login(actor)
            for a in self.active:
                self.assertIn(self.client.post('/api/users/'+a['id']+'/reset-link',json={}).status_code,(403,503))
        self.assertEqual(before,self.snapshot())

    def test_existing_legacy_grant_cannot_reset_any_active_synthetic_target(self):
        with TestClient(self.client.app) as public:
            for a in self.f.accounts:
                token=self.seed_grant(a)
                before=self.snapshot()
                r=public.post('/api/reset-password',json={'token':token,'password':secrets.token_urlsafe(30)})
                self.assertEqual(r.status_code,503 if a['active'] else 401)
                self.assertEqual(before,self.snapshot())

    def test_email_recovery_cannot_issue_target_grants_or_send(self):
        before=self.snapshot()
        with patch('app.account_access.recovery_email_allowed',return_value=True),patch('app.account_access.send_account_email') as send:
            for a in self.f.accounts:
                account_access.deliver_recovery(self.path,a['email'],'https://example.invalid')
            send.assert_not_called()
        self.assertEqual(before,self.snapshot())

    def test_team_metadata_distinguishes_target_management_from_reset_permission(self):
        accounts=self.client.get('/api/account-access').json()['accounts']
        subset=[a for a in accounts if a['id'] in guard.TARGETS]
        self.assertEqual(len(subset),5)
        self.assertTrue(all(a['can_manage'] for a in subset))
        self.assertTrue(all(a['general_recovery_blocked_reason'] for a in subset))
        self.f.f.login('sales')
        users=self.client.get('/api/users').json()
        self.assertTrue(all(not a['can_manage'] for a in users))

    def test_existing_general_recovery_for_unrelated_disposable_account_still_one_use(self):
        r=self.client.post('/api/users/crew/reset-link',json={})
        self.assertEqual(r.status_code,200);self.assertFalse(r.json()['sent'])
        token=r.json()['url'].split('#reset=')[1]
        with TestClient(self.client.app) as public,patch('app.security.notify_password_reset') as notice:
            data={'token':token,'password':secrets.token_urlsafe(30)}
            self.assertEqual(public.post('/api/reset-password',json=data).status_code,200)
            self.assertEqual(public.post('/api/reset-password',json=data).status_code,401)
            notice.assert_called_once()

    def test_grants_and_operation_ids_are_not_interchangeable(self):
        op=self.f.plan()
        token=self.seed_grant(self.active[0])
        with TestClient(self.client.app) as public:
            self.assertEqual(public.post('/api/reset-password',json={'token':op['operation_id'],'password':secrets.token_urlsafe(30)}).status_code,401)
        payload={'operation_id':token,'confirm':True,'passwords':self.f.new}
        self.assertEqual(self.client.post('/api/wix-drafts/credential-reissue/execute',json=payload).status_code,422)
        with patch.dict(os.environ,{'CROWNOPS_WIX_TEST_REISSUE_ENABLED':'0'}):
            self.assertEqual(self.f.execute(op).status_code,503)

    def test_legacy_grant_issuance_requires_session_csrf_and_permitted_role(self):
        before=self.snapshot()
        for actor in ('sales','crew','inactive'):
            self.f.f.login(actor)
            self.assertIn(self.client.post('/api/users/crew/reset-link',json={}).status_code,(401,403))
        self.f.f.login('owner');self.client.headers.pop('x-csrf-token')
        self.assertEqual(self.client.post('/api/users/crew/reset-link',json={}).status_code,403)
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/api/users/crew/reset-link',json={}).status_code,401)
        self.assertEqual(before,self.snapshot())

    def test_guard_does_not_change_production_or_other_staging_policy(self):
        a=self.active[0]
        with patch.dict(os.environ,{'CROWNOPS_ENV':'production'}):
            self.assertFalse(guard.restricted(self.path,a))
        with patch.dict(os.environ,{'CROWNOPS_ENV':'staging'}),patch('app.quote_staging.require_isolated',side_effect=__import__('fastapi').HTTPException(503,'Other environment')):
            self.assertFalse(guard.restricted(self.path,a))

    def test_private_link_ui_uses_explicit_backend_restriction(self):
        js=(Path(__file__).resolve().parents[1]/'app/static/account_access.js').read_text()
        self.assertIn('person.general_recovery_blocked_reason',js)
        self.assertIn('disabled title=',js)
        self.assertIn('Private reset link — restricted test account',js)

    def test_general_reset_audit_failure_rolls_back_credentials_sessions_and_grant(self):
        r=self.client.post('/api/users/crew/reset-link',json={})
        self.assertEqual(r.status_code,200)
        token=r.json()['url'].split('#reset=')[1]
        before=self.snapshot()
        with TestClient(self.client.app) as public,patch('app.security.audit',side_effect=RuntimeError('Disposable audit failure')):
            with self.assertRaises(Exception):
                public.post('/api/reset-password',json={'token':token,'password':secrets.token_urlsafe(30)})
        self.assertEqual(before,self.snapshot())

    def test_real_quote_scope_is_exact_and_partial_misconfiguration_fails_closed(self):
        from app import quote_staging as q
        from fastapi import HTTPException
        env={'CROWNOPS_ENV':'staging','QBO_ENV':'sandbox','RAILWAY_SERVICE_ID':q.SERVICE,
             'RAILWAY_ENVIRONMENT_ID':q.ENVIRONMENT,'CROWNOPS_DB':q.DATABASE,
             'QBO_SANDBOX_REALM_ID':q.REALM,'CROWNOPS_PUBLIC_URL':'https://'+q.HOST}
        with patch.dict(os.environ,env):
            self.assertTrue(guard.restricted(q.DATABASE,self.active[0]))
            with self.assertRaises(HTTPException):guard.restricted(self.path,self.active[0])
            with patch.dict(os.environ,{'RAILWAY_ENVIRONMENT_ID':'wrong'}):
                with self.assertRaises(HTTPException):guard.restricted(q.DATABASE,self.active[0])
