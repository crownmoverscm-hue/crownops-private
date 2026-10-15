"""Account lifecycle tests use disposable accounts and mocked email delivery."""
import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.account_access import recovery_email_status, send_account_email
from app.main import create_app, create_user, PASSWORDS, sha
from app.platform_common import encrypt, settings
from app.providers import send_gmail
from app.store import connect, transaction, one, rows, insert, now

PASSWORD = 'test-account-password-only'
NEW_PASSWORD = 'new-account-password-only'
PUBLIC_URL = 'https://crownops.example.invalid'


class AccountAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = PASSWORDS.hash(PASSWORD)

    def setUp(self):
        env = {'CROWNOPS_ENV':'test', 'CROWNOPS_ALLOW_EXTERNAL':'0',
            'CROWNOPS_ALLOW_AUTH_EMAIL':'0', 'CROWNOPS_AUTH_EMAIL_TEST_ALLOWLIST':'crew@example.invalid',
            'CROWNOPS_PUBLIC_URL':PUBLIC_URL, 'RAILWAY_PUBLIC_DOMAIN':'', 'CROWNOPS_SECURE_COOKIES':'0',
            'CROWNOPS_RUN_WORKER':'0', 'GOOGLE_CLIENT_ID':'', 'GOOGLE_CLIENT_SECRET':'',
            'CROWNOPS_SECRET_KEY':Fernet.generate_key().decode()}
        p = patch.dict(os.environ, env); p.start(); self.addCleanup(p.stop)
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'accounts.sqlite3'
        self.app = create_app(self.path)
        self.master = create_user(self.path, 'master@example.invalid', 'Test Master Owner', PASSWORD)
        with transaction(self.path) as db:
            insert(db, 'employees', {'id':'EMP-TEST','name':'Test Crew','role':'Mover','hourly_cents':2500})
            for user_id, role, active in [('OWNER2','owner',1),('CREW','crew',1),('SALES','sales',1),('DISPATCH','dispatch',1),('ACCOUNTING','accounting',1),('INACTIVE','crew',0)]:
                insert(db, 'users', {'id':user_id,'name':'Test '+user_id,'email':user_id.lower()+'@example.invalid',
                    'role':role,'active':active,'employee_id':'EMP-TEST' if role=='crew' else None,
                    'password_hash':self.password_hash,'created_at':now()})
        self.public = TestClient(self.app, base_url=PUBLIC_URL)
        self.addCleanup(self.public.close)
        self.owner = self.client(self.master['id'])

    def client(self, user_id):
        token = 'test-session-'+user_id
        csrf = 'test-csrf-'+user_id
        with transaction(self.path) as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (sha(token),))
            insert(db, 'sessions', {'token_hash':sha(token),'user_id':user_id,'csrf':csrf,
                'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(timespec='seconds')})
        client = TestClient(self.app, base_url=PUBLIC_URL)
        client.cookies.set('crownops_session', token)
        client.headers.update({'x-csrf-token':csrf})
        self.addCleanup(client.close)
        return client

    def invite(self, email='new.staff@example.invalid', role='crew'):
        result = self.owner.post('/api/invitations', json={'email':email,'name':'Test New Staff',
            'role':role,'employee_id':'EMP-TEST' if role=='crew' else None})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['url'].split('#invite=')[1]

    def reset_link(self, user_id='CREW'):
        response = self.owner.post('/api/users/'+user_id+'/reset-link', json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['sent'])
        return response.json()['url'].split('#reset=')[1]

    def add_gmail(self):
        os.environ.update({'CROWNOPS_ALLOW_AUTH_EMAIL':'1','GOOGLE_CLIENT_ID':'test-client', 'GOOGLE_CLIENT_SECRET':'test-secret'})
        with transaction(self.path) as db:
            insert(db, 'oauth_credentials', {'provider':'gmail','ciphertext':encrypt({'access_token':'test-token'}),'updated_at':now()})

    def access_payload(self, user_id, **changes):
        person = next(p for p in self.owner.get('/api/account-access').json()['accounts'] if p['id']==user_id)
        payload = {key:person[key] for key in ('role','employee_id')}
        payload.update(active=bool(person['active']), expected_revision=person['access_revision'])
        payload.update(changes)
        return payload

    def test_restore_reuses_the_account_password_authenticator_and_employee_records(self):
        import time
        from app.security import totp
        crew = self.client('CREW'); old_reset = self.reset_link()
        secret = 'JBSWY3DPEHPK3PXP'
        with transaction(self.path) as db:
            insert(db, 'auth_factors', {'user_id':'CREW','secret_ciphertext':encrypt({'secret':secret}),'enabled':1,'created_at':now()})
        self.owner.post('/api/users/CREW/disable', json={})
        with patch('app.account_access._send_account_message') as send:
            restored = self.owner.post('/api/users/CREW/access', json=self.access_payload('CREW',active=True))
            send.assert_not_called()
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertTrue(restored.json()['restored'])
        self.assertEqual(crew.get('/api/me').status_code, 401)
        self.assertEqual(self.public.post('/api/reset-password', json={'token':old_reset,'password':NEW_PASSWORD}).status_code, 401)
        login = {'email':'crew@example.invalid','password':PASSWORD}
        self.assertEqual(self.public.post('/api/login', json=login).status_code, 401)
        login['mfa_code'] = totp(secret,int(time.time()//30))
        response = self.public.post('/api/login', json=login)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['user']['id'], 'CREW')
        self.assertEqual(response.json()['user']['employee_id'], 'EMP-TEST')
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db,"SELECT * FROM users WHERE id='CREW'")['password_hash'], self.password_hash)
            self.assertEqual(one(db,"SELECT * FROM employees WHERE id='EMP-TEST'")['hourly_cents'], 2500)
            self.assertEqual(db.execute("SELECT count(*) FROM users WHERE email='crew@example.invalid'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE action='Access restored' AND object_id='CREW'").fetchone()[0], 1)

    def test_role_and_employee_reassignment_revokes_old_access_and_pending_links(self):
        crew = self.client('CREW'); old_reset = self.reset_link()
        with transaction(self.path) as db:
            insert(db, 'employees', {'id':'EMP-OTHER','name':'Test Other Employee','role':'Mover','hourly_cents':3000})
            insert(db, 'invitations', {'token_hash':sha('old-invite'),'name':'Test Crew','email':'crew@example.invalid',
                'role':'owner','expires_at':'2099-01-01T00:00:00+00:00','created_by':self.master['id']})
        response = self.owner.post('/api/users/CREW/access', json=self.access_payload('CREW',role='sales',employee_id='EMP-OTHER'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(crew.get('/api/me').status_code, 401)
        self.assertEqual(self.public.post('/api/reset-password', json={'token':old_reset,'password':NEW_PASSWORD}).status_code, 401)
        self.assertEqual(self.public.post('/api/accept-invitation', json={'token':'old-invite','password':NEW_PASSWORD}).status_code, 400)
        login = self.public.post('/api/login', json={'email':'crew@example.invalid','password':PASSWORD})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()['user']['role'], 'sales')
        self.assertEqual(login.json()['user']['employee_id'], 'EMP-OTHER')
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM employees').fetchone()[0], 2)

    def test_ordinary_owner_can_restore_and_change_staff_but_cannot_grant_or_manage_owners(self):
        secondary = self.client('OWNER2')
        response = secondary.post('/api/users/INACTIVE/access', json=self.access_payload('INACTIVE',active=True,role='dispatch'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(secondary.post('/api/users/SALES/access', json=self.access_payload('SALES',role='owner')).status_code, 403)
        self.assertEqual(secondary.post('/api/users/'+self.master['id']+'/access', json=self.access_payload(self.master['id'],role='sales')).status_code, 403)
        self.assertEqual(secondary.post('/api/users/OWNER2/access', json=self.access_payload('OWNER2',role='sales')).status_code, 422)
        with transaction(self.path) as db:
            insert(db, 'users', {'id':'OWNER3','name':'Test Other Owner','email':'owner3@example.invalid','role':'owner',
                'active':0,'password_hash':self.password_hash,'created_at':now()})
        self.assertEqual(secondary.post('/api/users/OWNER3/access', json=self.access_payload('OWNER3',active=True)).status_code, 403)

    def test_master_can_promote_demote_and_restore_other_owners_but_cannot_change_self(self):
        staff = self.client('SALES')
        self.assertEqual(self.owner.post('/api/users/SALES/access', json=self.access_payload('SALES',role='owner')).status_code, 200)
        self.assertEqual(staff.get('/api/me').status_code, 401)
        self.assertEqual(self.owner.post('/api/users/SALES/disable', json={}).status_code, 200)
        self.assertEqual(self.owner.post('/api/users/SALES/access', json=self.access_payload('SALES',active=True)).status_code, 200)
        self.assertEqual(self.owner.post('/api/users/SALES/access', json=self.access_payload('SALES',role='sales')).status_code, 200)
        payload = self.access_payload(self.master['id'],role='sales',active=False)
        self.assertEqual(self.owner.post('/api/users/'+self.master['id']+'/access', json=payload).status_code, 422)
        self.assertEqual(self.owner.get('/api/me').json()['user']['role'], 'owner')

    def test_access_edit_requires_owner_authentication_and_csrf(self):
        payload = self.access_payload('CREW',role='sales')
        for user_id in ('CREW','SALES','DISPATCH','ACCOUNTING'):
            self.assertEqual(self.client(user_id).post('/api/users/CREW/access', json=payload).status_code, 403)
        self.assertEqual(self.public.post('/api/users/CREW/access', json=payload).status_code, 401)
        self.owner.headers.pop('x-csrf-token')
        self.assertEqual(self.owner.post('/api/users/CREW/access', json=payload).status_code, 403)

    def test_stale_role_edit_cannot_overwrite_a_more_recent_removal_or_role_change(self):
        payload = self.access_payload('CREW',role='sales')
        self.owner.post('/api/users/CREW/disable', json={})
        self.assertEqual(self.owner.post('/api/users/CREW/access', json=payload).status_code, 409)
        first = self.access_payload('INACTIVE',role='sales')
        second = self.access_payload('INACTIVE',active=True)
        self.assertEqual(self.owner.post('/api/users/INACTIVE/access', json=first).status_code, 200)
        self.assertEqual(self.owner.post('/api/users/INACTIVE/access', json=second).status_code, 409)
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db,"SELECT active FROM users WHERE id='CREW'")['active'], 0)
            self.assertEqual(one(db,"SELECT role,active FROM users WHERE id='INACTIVE'"), {'role':'sales','active':0})

    def test_access_edit_validates_role_active_state_employee_and_revision(self):
        payload = self.access_payload('SALES')
        for changes in ({'role':[]},{'role':'manager'},{'active':'true'},{'active':1},
                        {'employee_id':[]},{'employee_id':'missing'},{'expected_revision':None},
                        {'expected_revision':'short'},{'role':'crew','employee_id':None}):
            with self.subTest(changes=changes):
                self.assertEqual(self.owner.post('/api/users/SALES/access', json={**payload,**changes}).status_code, 422)
        with transaction(self.path) as db:
            db.execute("UPDATE employees SET active=0 WHERE id='EMP-TEST'")
        self.assertEqual(self.owner.post('/api/users/SALES/access', json={**payload,'role':'crew','employee_id':'EMP-TEST'}).status_code, 422)
        self.assertEqual(self.owner.post('/api/users/MISSING/access', json=payload).status_code, 404)

    def test_employee_link_change_alone_ends_sessions_and_noop_keeps_them(self):
        crew = self.client('CREW')
        unchanged = self.owner.post('/api/users/CREW/access', json=self.access_payload('CREW'))
        self.assertEqual(unchanged.status_code, 200, unchanged.text)
        self.assertFalse(unchanged.json()['changed'])
        self.assertEqual(crew.get('/api/me').status_code, 200)
        with transaction(self.path) as db:
            insert(db, 'employees', {'id':'EMP-NEW','name':'Test New Employee','role':'Mover'})
        response = self.owner.post('/api/users/CREW/access', json=self.access_payload('CREW',employee_id='EMP-NEW'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(crew.get('/api/me').status_code, 401)

    def test_access_editor_rechecks_owner_authority_and_session_in_the_transaction(self):
        endpoint = next(route.endpoint for route in self.app.routes if route.path=='/api/users/{user_id}/access')
        actor = {'id':'OWNER2','role':'owner','csrf':'test-csrf-OWNER2'}
        self.client('OWNER2')
        payload = self.access_payload('CREW',role='sales')
        for change in ("UPDATE users SET role='sales' WHERE id='OWNER2'",
                       "UPDATE users SET role='owner',active=0 WHERE id='OWNER2'",
                       "UPDATE users SET role='owner',active=1 WHERE id='OWNER2'"):
            with transaction(self.path) as db:
                db.execute(change)
                if "active=1" in change:
                    db.execute("DELETE FROM sessions WHERE user_id='OWNER2'")
            with self.assertRaises(HTTPException) as error:
                endpoint('CREW',payload,u=actor)
            self.assertEqual(error.exception.status_code, 403)

    def test_existing_removed_email_directs_owner_to_restore_instead_of_duplicate_signup(self):
        response = self.owner.post('/api/invitations', json={'email':'inactive@example.invalid','name':'Test Existing','role':'sales'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('restore access', response.json()['detail'])

    def test_master_owner_is_persisted_and_cannot_be_removed_or_managed_by_another_owner(self):
        self.assertTrue(self.master['is_master_owner'])
        restarted = TestClient(create_app(self.path), base_url=PUBLIC_URL)
        self.addCleanup(restarted.close)
        restarted.cookies.set('crownops_session', 'test-session-'+self.master['id'])
        self.assertTrue(restarted.get('/api/me').json()['user']['is_master_owner'])
        with closing(connect(self.path)) as db:
            self.assertEqual(settings(db, 'master_owner')['user_id'], self.master['id'])
        secondary = self.client('OWNER2')
        for target, status in [(self.master['id'],403),('OWNER2',422)]:
            self.assertEqual(secondary.post('/api/users/'+target+'/disable', json={}).status_code, status)
        self.assertEqual(secondary.post('/api/users/'+self.master['id']+'/reset-link', json={}).status_code, 403)
        self.assertEqual(secondary.post('/api/invitations', json={'email':'owner3@example.invalid','name':'Test','role':'owner'}).status_code, 403)
        self.assertEqual(self.owner.post('/api/users/'+self.master['id']+'/disable', json={}).status_code, 422)
        self.assertEqual(self.owner.post('/api/users/OWNER2/disable', json={}).status_code, 200)

    def test_existing_first_owner_is_remembered_on_upgrade(self):
        with transaction(self.path) as db:
            db.execute("DELETE FROM settings WHERE key='master_owner'")
            db.execute('UPDATE users SET created_at=? WHERE id=?', ('2000-01-01T00:00:00+00:00',self.master['id']))
        create_app(self.path)
        with closing(connect(self.path)) as db:
            self.assertEqual(settings(db, 'master_owner')['user_id'], self.master['id'])
        self.assertEqual(self.public.post('/api/setup-owner', json={}).status_code, 409)

    def test_empty_install_requires_private_setup_code_and_closes_after_the_master_owner_is_created(self):
        path = Path(self.folder.name) / 'new-install.sqlite3'
        client = TestClient(create_app(path), base_url=PUBLIC_URL)
        self.addCleanup(client.close)
        payload = {'setup_code':'incorrect','name':'Test First Owner','email':'first@example.invalid',
            'password':PASSWORD,'confirm_password':PASSWORD}
        with patch('app.main.SETUP_TOKEN_HASH', sha('test-private-setup-code')):
            self.assertEqual(client.post('/api/setup-owner', json=payload).status_code, 401)
            payload['setup_code'] = 'test-private-setup-code'
            payload['confirm_password'] = 'mismatch'
            self.assertEqual(client.post('/api/setup-owner', json=payload).status_code, 422)
            payload['confirm_password'] = PASSWORD
            self.assertEqual(client.post('/api/setup-owner', json=payload).status_code, 200)
            self.assertEqual(client.post('/api/setup-owner', json=payload).status_code, 409)
        login = client.post('/api/login', json={'email':'first@example.invalid','password':PASSWORD})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertTrue(login.json()['user']['is_master_owner'])
        with self.assertRaises(HTTPException):
            create_user(path, 'race@example.invalid', 'Test Concurrent Setup', PASSWORD, initial_only=True)
        with closing(connect(path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 1)

    def test_team_access_and_all_account_mutations_require_owner_role_and_csrf(self):
        for role in ('CREW','SALES','DISPATCH','ACCOUNTING'):
            client = self.client(role)
            for route in ('/api/account-access',):
                self.assertEqual(client.get(route).status_code, 403)
            self.assertEqual(client.post('/api/users/CREW/disable', json={}).status_code, 403)
            self.assertEqual(client.post('/api/users/CREW/reset-link', json={}).status_code, 403)
        self.assertEqual(self.public.get('/api/account-access').status_code, 401)
        self.owner.headers.pop('x-csrf-token')
        self.assertEqual(self.owner.post('/api/users/CREW/disable', json={}).status_code, 403)

    def test_private_invitation_sets_staff_password_once_and_ignores_role_tampering(self):
        token = self.invite()
        payload = {'token':token,'password':NEW_PASSWORD,'confirm_password':'mismatch','role':'owner'}
        self.assertEqual(self.public.post('/api/accept-invitation', json=payload).status_code, 422)
        payload['confirm_password'] = NEW_PASSWORD
        result = self.public.post('/api/accept-invitation', json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse(self.public.cookies.get('crownops_session'))
        self.assertEqual(self.public.post('/api/accept-invitation', json=payload).status_code, 400)
        login = self.public.post('/api/login', json={'email':'new.staff@example.invalid','password':NEW_PASSWORD})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()['user']['role'], 'crew')
        self.assertEqual(login.json()['user']['employee_id'], 'EMP-TEST')
        self.assertFalse(login.json()['user']['is_master_owner'])

    def test_new_invite_invalidates_previous_and_owner_can_revoke_a_pending_link(self):
        old = self.invite(); new = self.invite()
        self.assertEqual(self.public.post('/api/accept-invitation', json={'token':old,'password':PASSWORD}).status_code, 400)
        listing = self.owner.get('/api/account-access')
        self.assertNotIn(new, listing.text)
        self.assertEqual(len(listing.json()['invitations']), 1)
        invitation_id = listing.json()['invitations'][0]['id']
        self.assertEqual(self.owner.post('/api/account-access/invitations/'+invitation_id+'/revoke', json={}).status_code, 200)
        self.assertEqual(self.public.post('/api/accept-invitation', json={'token':new,'password':PASSWORD}).status_code, 400)
        self.assertEqual(self.owner.get('/api/account-access').json()['invitations'], [])

    def test_owner_invitation_cannot_be_revoked_by_a_secondary_owner(self):
        self.invite('next.owner@example.invalid', role='owner')
        invitation_id = self.owner.get('/api/account-access').json()['invitations'][0]['id']
        secondary = self.client('OWNER2')
        self.assertEqual(secondary.post('/api/account-access/invitations/'+invitation_id+'/revoke', json={}).status_code, 403)
        self.assertEqual(self.owner.post('/api/account-access/invitations/'+invitation_id+'/revoke', json={}).status_code, 200)

    def test_removed_access_cancels_sessions_resets_and_invites_but_retains_employee_records(self):
        crew = self.client('CREW'); token = self.reset_link()
        with transaction(self.path) as db:
            insert(db, 'invitations', {'token_hash':sha('obsolete-invite'),'name':'Test Crew','email':'crew@example.invalid',
                'role':'crew','employee_id':'EMP-TEST','expires_at':'2099-01-01T00:00:00+00:00','created_by':self.master['id']})
        self.assertEqual(crew.get('/api/me').status_code, 200)
        self.assertEqual(self.owner.post('/api/users/CREW/disable', json={}).status_code, 200)
        self.assertEqual(crew.get('/api/me').status_code, 401)
        self.assertEqual(self.public.post('/api/reset-password', json={'token':token,'password':NEW_PASSWORD}).status_code, 401)
        self.assertEqual(self.public.post('/api/login', json={'email':'crew@example.invalid','password':PASSWORD}).status_code, 401)
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db,'SELECT * FROM users WHERE id=?',('CREW',))['active'], 0)
            self.assertEqual(one(db,'SELECT * FROM employees WHERE id=?',('EMP-TEST',))['hourly_cents'], 2500)
            self.assertEqual(db.execute("SELECT count(*) FROM account_resets WHERE user_id='CREW'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM sessions WHERE user_id='CREW'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM invitations WHERE email='crew@example.invalid'").fetchone()[0], 0)

    def test_forgot_password_returns_same_message_for_known_unknown_and_inactive_accounts(self):
        with patch('app.account_access.recovery_email_status', return_value={'available':True}), patch('app.account_access.send_account_email', return_value='TEST-MAIL') as send:
            responses = [self.public.post('/api/forgot-password', json={'email':email}) for email in
                ['CREW@EXAMPLE.INVALID','unknown@example.invalid','inactive@example.invalid']]
        self.assertEqual([r.status_code for r in responses], [200,200,200])
        self.assertEqual(responses[0].json(), responses[1].json())
        self.assertEqual(responses[0].json(), responses[2].json())
        self.assertEqual(send.call_count, 1)
        body = send.call_args.args[3]
        token = re.search(r'#reset=([^\s]+)', body).group(1)
        self.assertIn(PUBLIC_URL+'/#reset=', body)
        self.assertNotIn(token, responses[0].text)
        with closing(connect(self.path)) as db:
            reset = one(db, 'SELECT * FROM account_resets')
            self.assertEqual(reset['token_hash'], sha(token))
            persisted = str(rows(db, 'SELECT * FROM audit'))+str(rows(db, 'SELECT * FROM provider_operations'))
            self.assertNotIn(token, persisted)

    def test_email_recovery_is_off_by_default_and_requires_https_credentials_and_staging_allowlist(self):
        self.assertEqual(self.public.get('/api/recovery-status').json(), {'available':False})
        for email in ('crew@example.invalid','unknown@example.invalid'):
            self.assertEqual(self.public.post('/api/forgot-password', json={'email':email}).status_code, 503)
        self.add_gmail()
        self.assertTrue(recovery_email_status(self.path, PUBLIC_URL)['available'])
        self.assertFalse(recovery_email_status(self.path, 'http://example.invalid')['available'])
        os.environ['CROWNOPS_AUTH_EMAIL_TEST_ALLOWLIST'] = ''
        self.assertFalse(recovery_email_status(self.path, PUBLIC_URL)['available'])
        os.environ['CROWNOPS_ENV'] = 'production'
        self.assertTrue(recovery_email_status(self.path, PUBLIC_URL)['available'])
        os.environ['CROWNOPS_ALLOW_AUTH_EMAIL'] = '0'
        self.assertFalse(recovery_email_status(self.path, PUBLIC_URL)['available'])

    def test_recovery_throttles_unknown_accounts_and_case_variants_without_trusting_forwarded_headers(self):
        with patch('app.account_access.recovery_email_status', return_value={'available':True}), patch('app.account_access.deliver_recovery'):
            for email in ('unknown@example.invalid','UNKNOWN@EXAMPLE.INVALID','Unknown@Example.invalid'):
                self.assertEqual(self.public.post('/api/forgot-password', json={'email':email}).status_code, 200)
            self.assertEqual(self.public.post('/api/forgot-password', json={'email':'unknown@example.invalid'}).status_code, 429)
            for index in range(7):
                self.assertEqual(self.public.post('/api/forgot-password', json={'email':f'other{index}@example.invalid'}, headers={'x-forwarded-for':f'192.0.2.{index}'}).status_code, 200)
            self.assertEqual(self.public.post('/api/forgot-password', json={'email':'last@example.invalid'}, headers={'x-forwarded-for':'192.0.2.99'}).status_code, 429)

    def test_reset_is_one_use_revokes_old_signins_and_sends_notification_without_the_password(self):
        crew = self.client('CREW'); token = self.reset_link()
        self.assertEqual(self.public.post('/api/reset-password', json={'token':token,'password':NEW_PASSWORD,'confirm_password':'different'}).status_code, 422)
        with patch('app.account_access.recovery_email_allowed', return_value=True), patch('app.account_access.send_account_email', return_value='TEST-NOTICE') as send:
            reset = self.public.post('/api/reset-password', json={'token':token,'password':NEW_PASSWORD,'confirm_password':NEW_PASSWORD})
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertFalse(self.public.cookies.get('crownops_session'))
        self.assertEqual(crew.get('/api/me').status_code, 401)
        self.assertEqual(send.call_count, 1)
        self.assertNotIn(NEW_PASSWORD, send.call_args.args[3])
        self.assertNotIn(token, send.call_args.args[3])
        self.assertEqual(self.public.post('/api/reset-password', json={'token':token,'password':PASSWORD}).status_code, 401)
        self.assertEqual(self.public.post('/api/login', json={'email':'crew@example.invalid','password':PASSWORD}).status_code, 401)
        self.assertEqual(self.public.post('/api/login', json={'email':'crew@example.invalid','password':NEW_PASSWORD}).status_code, 200)

    def test_expired_reset_and_reset_for_an_inactive_account_are_rejected(self):
        token = self.reset_link()
        with transaction(self.path) as db:
            db.execute('UPDATE account_resets SET expires_at=?', ('2000-01-01T00:00:00+00:00',))
        self.assertEqual(self.public.post('/api/reset-password', json={'token':token,'password':PASSWORD}).status_code, 401)
        with transaction(self.path) as db:
            insert(db, 'account_resets', {'token_hash':sha('inactive-token'),'user_id':'INACTIVE','expires_at':'2099-01-01T00:00:00+00:00'})
        self.assertEqual(self.public.post('/api/reset-password', json={'token':'inactive-token','password':PASSWORD}).status_code, 401)

    def test_reset_preserves_authenticator_and_does_not_auto_sign_in(self):
        with transaction(self.path) as db:
            insert(db, 'auth_factors', {'user_id':'CREW','secret_ciphertext':encrypt({'secret':'JBSWY3DPEHPK3PXP'}),'enabled':1,'created_at':now()})
        token = self.reset_link()
        self.assertEqual(self.public.post('/api/reset-password', json={'token':token,'password':NEW_PASSWORD}).status_code, 200)
        self.assertFalse(self.public.cookies.get('crownops_session'))
        self.assertEqual(self.public.post('/api/login', json={'email':'crew@example.invalid','password':NEW_PASSWORD}).status_code, 401)
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db,"SELECT * FROM auth_factors WHERE user_id='CREW'")['enabled'], 1)

    def test_latest_recovery_link_replaces_an_older_link_and_delivery_failure_is_not_retried(self):
        old = self.reset_link()
        with patch('app.account_access.recovery_email_status', return_value={'available':True}), patch('app.account_access.send_account_email', side_effect=RuntimeError('private-provider-secret')) as send:
            response = self.public.post('/api/forgot-password', json={'email':'crew@example.invalid'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(send.call_count, 1)
        self.assertNotIn('private-provider-secret', response.text)
        self.assertEqual(self.public.post('/api/reset-password', json={'token':old,'password':NEW_PASSWORD}).status_code, 401)
        with closing(connect(self.path)) as db:
            operation = one(db, 'SELECT * FROM provider_operations')
            self.assertEqual(operation['status'], 'Unknown')
            self.assertNotIn('private-provider-secret', str(operation))
            self.assertEqual(db.execute('SELECT count(*) FROM account_resets').fetchone()[0], 1)

    def test_account_email_uses_registered_recipient_and_keeps_general_outbound_actions_disabled(self):
        self.add_gmail()
        profile = Mock(); profile.json.return_value = {'emailAddress':'company@example.invalid'}
        response = Mock(); response.json.return_value = {'id':'TEST-GMAIL-ID'}
        with patch('app.providers.provider_token', return_value={'access_token':'test-token'}) as token, patch('app.account_access.requests.get', return_value=profile), patch('app.account_access.requests.post', return_value=response) as post:
            self.assertEqual(send_account_email(self.path, {'id':'CREW','email':'wrong@example.invalid'}, PUBLIC_URL,
                'Example recovery notice', 'Test account notice', 'TEST-NOTICE'), 'TEST-GMAIL-ID')
            token.assert_called_once_with(self.path, 'gmail', require_external=False)
            message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(post.call_args.kwargs['json']['raw']))
            self.assertEqual(message['To'], 'crew@example.invalid')
            self.assertEqual(message['From'], 'company@example.invalid')
            with self.assertRaises(RuntimeError):
                send_account_email(self.path, {'id':'INACTIVE'}, PUBLIC_URL, 'Test', 'Test', 'TEST')
            self.assertEqual(post.call_count, 1)
        with self.assertRaises(HTTPException):
            send_gmail(self.path, {'recipient':'client@example.invalid','subject':'Test','body':'Test','id':'TEST','category':'Test'})

    def test_removed_accounts_do_not_receive_recovery_emails_or_new_reset_tokens(self):
        self.owner.post('/api/users/CREW/disable', json={})
        with patch('app.account_access.recovery_email_status', return_value={'available':True}), patch('app.account_access.send_account_email') as send:
            self.assertEqual(self.public.post('/api/forgot-password', json={'email':'crew@example.invalid'}).status_code, 200)
            send.assert_not_called()
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM account_resets WHERE user_id='CREW'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
