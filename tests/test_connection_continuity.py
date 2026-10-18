"""Connection renewal uses disposable credentials and mocked provider calls."""
from contextlib import closing
import os
import time
import unittest
from unittest.mock import Mock, patch
import requests
from cryptography.fernet import Fernet
from app.providers import provider_token, token_save, maintain_connections
from app.platform_common import decrypt, settings
from app.store import connect, transaction, one, insert, now
import test_job_planning as fixture

class ConnectionContinuityTests(unittest.TestCase):
    setUp=fixture.JobPlanningTests.setUp
    staff=fixture.JobPlanningTests.staff

    def credentials(self,provider='quickbooks'):
        env=patch.dict(os.environ,{'CROWNOPS_SECRET_KEY':Fernet.generate_key().decode()})
        env.start();self.addCleanup(env.stop)
        with transaction(self.path) as db:
            token_save(db,provider,{'access_token':'old-access','refresh_token':'old-refresh','expires_in':0,'realm_id':'123'})

    def saved(self,provider='quickbooks'):
        with closing(connect(self.path)) as db:
            row=one(db,'SELECT * FROM oauth_credentials WHERE provider=?',(provider,))
            return decrypt(row['ciphertext']) if row else None

    def test_rotated_refresh_token_saved_and_reused_after_restart(self):
        self.credentials()
        response=Mock(status_code=200);response.json.return_value={'access_token':'fresh-access','refresh_token':'rotated-refresh','expires_in':3600}
        with patch('app.providers.requests.post',return_value=response) as post:
            maintain_connections(self.path)
            self.assertEqual(self.saved()['refresh_token'],'rotated-refresh')
            self.assertEqual(provider_token(self.path,'quickbooks',False)['realm_id'],'123')
            self.assertEqual(post.call_count,1)
        status=self.owner.get('/api/connections').json()
        self.assertEqual(status['connection_health']['quickbooks']['state'],'connected')
        self.assertNotIn('rotated-refresh',str(status))

    def test_gmail_missing_rotated_token_preserves_existing_grant(self):
        self.credentials('gmail')
        response=Mock(status_code=200);response.json.return_value={'access_token':'fresh','expires_in':3600}
        with patch('app.providers.requests.post',return_value=response):maintain_connections(self.path)
        self.assertEqual(self.saved('gmail')['refresh_token'],'old-refresh')

    def test_timeout_keeps_connection_and_next_check_recovers(self):
        self.credentials()
        with patch('app.providers.requests.post',side_effect=requests.Timeout('PRIVATE DETAILS')):maintain_connections(self.path)
        self.assertEqual(self.saved()['refresh_token'],'old-refresh')
        status=self.owner.get('/api/connections').json()
        self.assertEqual(status['connection_health']['quickbooks']['state'],'retrying')
        self.assertNotIn('PRIVATE DETAILS',str(status))
        response=Mock(status_code=200);response.json.return_value={'access_token':'fresh','expires_in':3600}
        with patch('app.providers.requests.post',return_value=response):maintain_connections(self.path)
        self.assertEqual(self.owner.get('/api/connections').json()['connection_health']['quickbooks']['state'],'connected')

    def test_revoked_grant_is_reported_without_deleting_or_retrying_forever(self):
        self.credentials()
        response=Mock(status_code=400);response.json.return_value={'error':'invalid_grant'}
        with patch('app.providers.requests.post',return_value=response) as post:
            maintain_connections(self.path);maintain_connections(self.path)
            self.assertEqual(post.call_count,1)
        self.assertIsNotNone(self.saved())
        self.assertEqual(self.owner.get('/api/connections').json()['connection_health']['quickbooks']['state'],'reconnect_required')

    def test_only_owner_disconnects_and_worker_cannot_reconnect(self):
        self.credentials()
        with transaction(self.path) as db:
            db.execute("UPDATE users SET role='manager' WHERE id='dispatch'")
            insert(db,'oauth_states',dict(state_hash='pending',provider='quickbooks',user_id='owner',expires_at='2099-01-01'))
        for user in ('dispatch','sales','crew','accounting'):
            self.assertEqual(self.staff(user).post('/api/disconnect/quickbooks').status_code,403)
        self.assertEqual(self.owner.post('/api/disconnect/quickbooks').status_code,200)
        with patch('app.providers.requests.post') as post:
            maintain_connections(self.path);post.assert_not_called()
        self.assertIsNone(self.saved())
        with closing(connect(self.path)) as db:self.assertIsNone(one(db,'SELECT * FROM oauth_states'))

if __name__=='__main__':unittest.main()
