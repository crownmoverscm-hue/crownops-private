"""Gmail diagnostics exercise content permissions and bound systemic failures."""
import json
import unittest
from unittest.mock import patch
import requests
from contextlib import closing
from app.store import connect,one
from app.gmail_sync import _api, gmail_read_failure, sync_gmail_readonly
import test_intake_resilience as fixture


def failure(status, reason=''):
    response=requests.Response()
    response.status_code=status
    response._content=json.dumps({'error':{'errors':[{'reason':reason}], 'message':'SECRET provider payload'}}).encode()
    return requests.HTTPError('SECRET', response=response)


class GmailAccessTests(unittest.TestCase):
    def test_error_classification_does_not_leak_provider_payload(self):
        for status,reason,code,retry in [
            (403,'insufficientPermissions','read_permission',False),
            (403,'ACCESS_TOKEN_SCOPE_INSUFFICIENT','read_permission',False),
            (403,'domainPolicy','domain_policy',False),
            (403,'dailyLimitExceeded','daily_quota',False),
            (403,'userRateLimitExceeded','rate_limit',True),
            (429,'','rate_limit',True),(401,'','authorization',False),
            (403,'private unknown value','access_blocked',False),
            (503,'','unavailable',True),(404,'','message_missing',False)]:
            with self.subTest(code=code,reason=reason):
                result=gmail_read_failure(failure(status,reason))
                self.assertEqual(result['code'],code)
                self.assertEqual(result['retryable'],retry)
                self.assertNotIn('SECRET',str(result))
                self.assertNotIn('private unknown value',str(result))

    def test_scope_failure_is_not_retried_but_rate_limits_back_off(self):
        for reason,count in [('insufficientPermissions',1),('userRateLimitExceeded',3)]:
            err=failure(403,reason)
            with patch('app.gmail_sync.requests.get',return_value=err.response) as get,patch('app.gmail_sync.time.sleep') as sleep:
                with self.assertRaises(requests.HTTPError):_api('secret','messages/x')
            self.assertEqual(get.call_count,count)
            self.assertEqual([c.args[0] for c in sleep.call_args_list],[1,2] if count==3 else [])

    def test_import_stops_on_first_systemic_failure_and_keeps_cursor(self):
        f=fixture.IntakeResilienceTests();f.setUp();self.addCleanup(f.doCleanups)
        f.message('one');f.message('two')
        calls=[]
        def gmail(token,path,params=None):
            calls.append(path)
            if path.startswith('messages/'):raise failure(403,'insufficientPermissions')
            return f.gmail(token,path,params)
        with patch('app.gmail_sync._api',side_effect=gmail):
            result=sync_gmail_readonly(f.path,'mock','owner',None,fluid=False)
        self.assertEqual(len(result['errors']),1)
        self.assertEqual(result['errors'][0]['code'],'read_permission')
        self.assertEqual(calls,['labels','messages','messages/one'])
        self.assertFalse(f.records('integration_events'))
        with closing(connect(f.path)) as db:
            self.assertIsNone(one(db,"SELECT * FROM settings WHERE key='gmail_cursor_quote'"))

    def diagnostic(self, empty=False, blocked=False):
        f=fixture.IntakeResilienceTests();f.setUp();self.addCleanup(f.doCleanups)
        f.message('sample')
        calls=[]
        def gmail(token,path,params=None):
            calls.append((path,params))
            if path=='profile':return {'emailAddress':'owner@example.invalid'}
            if path=='messages' and empty:return {'messages':[]}
            if path.startswith('messages/') and blocked:raise failure(403,'insufficientPermissions')
            return f.gmail(token,path,params)
        with patch('app.providers.provider_token',return_value={'access_token':'mock'}),patch('app.gmail_sync._api',side_effect=gmail):
            result=f.client.get('/api/gmail/test-readonly')
        return result,calls

    def test_read_access_test_fetches_full_message_content(self):
        result,calls=self.diagnostic()
        self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(result.json()['ok'])
        self.assertTrue(result.json()['message_content_verified'])
        self.assertIn(('messages/sample',{'format':'full'}),calls)

    def test_read_access_test_does_not_claim_empty_mailbox_verified(self):
        result,calls=self.diagnostic(empty=True)
        self.assertFalse(result.json()['ok'])
        self.assertFalse(result.json()['message_content_verified'])

    def test_list_access_does_not_mask_content_permission_failure(self):
        result,calls=self.diagnostic(blocked=True)
        self.assertEqual(result.status_code,502)
        self.assertIn('missing Gmail read permission',result.json()['detail'])
        self.assertNotIn('SECRET',result.text)
