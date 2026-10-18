"""Only the signed-in account holder can disable their verified factor."""
from contextlib import closing
import time
import unittest
from app.security import totp
from app.platform_common import encrypt
from app.store import connect, transaction, insert, one, now
from app.main import sha
import test_account_access as fixture

class AuthenticatorDisableTests(unittest.TestCase):
    setUpClass=classmethod(fixture.AccountAccessTests.setUpClass.__func__)
    setUp=fixture.AccountAccessTests.setUp
    client=fixture.AccountAccessTests.client

    def factor(self):
        self.key='JBSWY3DPEHPK3PXP';self.uid=self.master['id']
        with transaction(self.path) as db:
            insert(db,'auth_factors',dict(user_id=self.uid,secret_ciphertext=encrypt({'secret':self.key}),enabled=1,last_step=0,created_at=now()))
            insert(db,'sessions',dict(token_hash=sha('second-device'),user_id=self.uid,csrf='other-csrf',expires_at='2099-01-01'))
        return totp(self.key,int(time.time()//30))

    def test_requires_password_and_fresh_factor_and_preserves_current_session(self):
        code=self.factor()
        self.assertEqual(self.owner.post('/api/security/mfa-disable',json={'password':'wrong','code':code}).status_code,401)
        self.assertEqual(self.owner.post('/api/security/mfa-disable',json={'password':fixture.PASSWORD,'code':'bad'}).status_code,401)
        result=self.owner.post('/api/security/mfa-disable',json={'password':fixture.PASSWORD,'code':code})
        self.assertEqual(result.status_code,200,result.text)
        self.assertFalse(self.owner.get('/api/security').json()['mfa_enabled'])
        self.assertEqual(self.owner.get('/api/me').status_code,200)
        with closing(connect(self.path)) as db:self.assertIsNone(one(db,'SELECT * FROM sessions WHERE token_hash=?',(sha('second-device'),)))
        login=self.public.post('/api/login',json={'email':'master@example.invalid','password':fixture.PASSWORD})
        self.assertEqual(login.status_code,200,login.text)

    def test_cannot_disable_someone_elses_factor(self):
        self.factor()
        result=self.client('CREW').post('/api/security/mfa-disable',json={'password':fixture.PASSWORD,'user_id':self.uid})
        self.assertEqual(result.status_code,200)
        self.assertTrue(self.owner.get('/api/security').json()['mfa_enabled'])

    def test_code_replay_rejected_and_missing_csrf_rejected(self):
        code=self.factor()
        with transaction(self.path) as db:db.execute('UPDATE auth_factors SET last_step=? WHERE user_id=?',(int(time.time()//30),self.uid))
        self.assertEqual(self.owner.post('/api/security/mfa-disable',json={'password':fixture.PASSWORD,'code':code}).status_code,401)
        self.owner.headers.pop('x-csrf-token')
        self.assertEqual(self.owner.post('/api/security/mfa-disable',json={'password':fixture.PASSWORD,'code':code}).status_code,403)

if __name__=='__main__':unittest.main()
