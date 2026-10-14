"""Owner-reviewed invitation delivery uses disposable accounts and mocked Gmail."""
import base64
from contextlib import closing
from email import policy
from email.parser import BytesParser
import os
import unittest
from unittest.mock import Mock, patch
from app.account_access import send_invitation_email
from app.main import PASSWORDS, sha
from app.providers import send_gmail
from app.store import connect, transaction, one, rows
import test_account_access as account_fixtures
from test_account_access import PUBLIC_URL, PASSWORD
from fastapi import HTTPException

EMAIL = 'welcome.staff@example.invalid'


class InvitationEmailTests(unittest.TestCase):
    setUp = account_fixtures.AccountAccessTests.setUp
    client = account_fixtures.AccountAccessTests.client
    add_gmail = account_fixtures.AccountAccessTests.add_gmail

    @classmethod
    def setUpClass(cls):cls.password_hash = PASSWORDS.hash(PASSWORD)

    def payload(self, **changes):
        data = {'name':'Test New Staff','email':EMAIL,'role':'sales','employee_id':None,
                'send_email':True,'request_id':'test-invitation-request-001'}
        data.update(changes);return data

    def ready(self):
        self.add_gmail();os.environ['CROWNOPS_AUTH_EMAIL_TEST_ALLOWLIST']=EMAIL

    def create_link(self):
        result = self.owner.post('/api/invitations',json=self.payload(send_email=False))
        self.assertEqual(result.status_code,200,result.text)
        return result.json()

    def test_owner_can_create_and_send_welcome_email_without_enabling_general_external_actions(self):
        self.ready()
        with patch('app.account_access.send_invitation_email',return_value='FAKE-MAIL') as send:
            response = self.owner.post('/api/invitations',json=self.payload())
        self.assertEqual(response.status_code,200,response.text)
        data = response.json()
        self.assertTrue(data['sent']);self.assertEqual(data['delivery_status'],'Completed')
        self.assertEqual(data['recipient'],EMAIL);self.assertEqual(send.call_count,1)
        token = data['url'].split('#invite=')[1]
        self.assertEqual(send.call_args.args[1],sha(token));self.assertEqual(send.call_args.args[2],token)
        self.assertIn(data['url'],data['email_preview']['body'])
        self.assertEqual(os.environ['CROWNOPS_ALLOW_EXTERNAL'],'0')
        with closing(connect(self.path)) as db:
            operation = one(db,"SELECT * FROM provider_operations WHERE kind='Staff invitation'")
            self.assertEqual(operation['status'],'Completed')
            self.assertNotIn(token,str(operation));self.assertNotIn(token,str(rows(db,'SELECT * FROM audit')))
        listing = self.owner.get('/api/account-access')
        self.assertNotIn(token,listing.text)
        self.assertEqual(listing.json()['invitations'][0]['email_status'],'Completed')

    def test_sending_preflight_blocks_disabled_email_and_unapproved_staging_recipient_before_creation(self):
        response = self.owner.post('/api/invitations',json=self.payload())
        self.assertEqual(response.status_code,503,response.text)
        self.ready();os.environ['CROWNOPS_AUTH_EMAIL_TEST_ALLOWLIST']='other@example.invalid'
        response = self.owner.post('/api/invitations',json=self.payload())
        self.assertEqual(response.status_code,403,response.text)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM invitations').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM provider_operations WHERE kind='Staff invitation'").fetchone()[0],0)

    def test_link_only_keeps_working_when_email_is_disabled_and_returns_the_welcome_preview(self):
        with patch('app.account_access.send_invitation_email') as send:
            data = self.create_link()
            send.assert_not_called()
        self.assertFalse(data['sent']);self.assertFalse(data['email_requested'])
        self.assertEqual(data['delivery_status'],'Not requested')
        self.assertIn('Samsung / Android',data['email_preview']['body'])
        self.assertIn('Windows',data['email_preview']['body'])

    def test_invalid_send_options_and_roles_are_rejected_without_creating_an_invitation(self):
        for change in ({'send_email':'yes'},{'request_id':'short'},{'role':['owner']},{'role':{'role':'owner'}}):
            result=self.owner.post('/api/invitations',json=self.payload(**change))
            self.assertEqual(result.status_code,422,result.text)
        self.assertEqual(self.owner.post('/api/account-access/invitation-preview',json={'role':['owner']}).status_code,422)
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM invitations').fetchone()[0],0)

    def test_retry_of_the_same_send_request_does_not_create_a_new_link_or_send_again(self):
        self.ready()
        with patch('app.account_access.send_invitation_email',return_value='FAKE-MAIL') as send:
            first = self.owner.post('/api/invitations',json=self.payload())
            repeat = self.owner.post('/api/invitations',json=self.payload())
        self.assertEqual(first.status_code,200);self.assertEqual(repeat.status_code,200)
        self.assertTrue(repeat.json()['sent']);self.assertIsNone(repeat.json()['url'])
        self.assertEqual(send.call_count,1)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM invitations').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM provider_operations WHERE kind='Staff invitation'").fetchone()[0],1)

    def test_changed_recipient_or_role_cannot_reuse_a_previous_send_request(self):
        self.ready()
        with patch('app.account_access.send_invitation_email',return_value='FAKE-MAIL') as send:
            self.assertEqual(self.owner.post('/api/invitations',json=self.payload()).status_code,200)
            for changes in ({'email':'wrong@example.invalid'},{'name':'Different Staff'},{'role':'dispatch'}):
                self.assertEqual(self.owner.post('/api/invitations',json=self.payload(**changes)).status_code,409)
            self.assertEqual(send.call_count,1)

    def test_concurrent_retry_sees_running_status_and_does_not_send_twice(self):
        self.ready();nested=[]
        def sending(*args):
            nested.append(self.owner.post('/api/invitations',json=self.payload()))
            return 'FAKE-MAIL'
        with patch('app.account_access.send_invitation_email',side_effect=sending) as send:
            response = self.owner.post('/api/invitations',json=self.payload())
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(nested[0].status_code,200)
        self.assertEqual(nested[0].json()['delivery_status'],'Running')
        self.assertFalse(nested[0].json()['sent']);self.assertEqual(send.call_count,1)

    def test_delivery_failure_is_unknown_does_not_claim_sent_and_does_not_retry(self):
        self.ready()
        with patch('app.account_access.send_invitation_email',side_effect=RuntimeError('provider-private-secret')) as send:
            response = self.owner.post('/api/invitations',json=self.payload())
            repeat = self.owner.post('/api/invitations',json=self.payload())
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json()['sent']);self.assertEqual(response.json()['delivery_status'],'Unknown')
        self.assertFalse(repeat.json()['sent']);self.assertEqual(send.call_count,1)
        self.assertNotIn('provider-private-secret',response.text)
        with closing(connect(self.path)) as db:
            self.assertNotIn('provider-private-secret',str(rows(db,'SELECT * FROM provider_operations')))
            self.assertEqual(db.execute('SELECT count(*) FROM invitations').fetchone()[0],1)

    def test_owner_can_email_a_created_link_and_one_link_is_sent_at_most_once(self):
        data = self.create_link();self.ready()
        token = data['url'].split('#invite=')[1]
        route = '/api/account-access/invitations/'+data['invitation_id']+'/send-email'
        with patch('app.account_access.send_invitation_email',return_value='FAKE-MAIL') as send:
            for request_id in ('test-existing-invite-001','test-existing-invite-002'):
                response = self.owner.post(route,json={'token':token,'request_id':request_id,'email':'wrong@example.invalid'})
                self.assertEqual(response.status_code,200,response.text)
                self.assertTrue(response.json()['sent']);self.assertEqual(response.json()['recipient'],EMAIL)
            self.assertEqual(send.call_count,1)

    def test_gmail_adapter_uses_pending_invitation_recipient_and_html_plus_plain_text(self):
        data = self.create_link();self.ready()
        token = data['url'].split('#invite=')[1]
        profile=Mock();profile.json.return_value={'emailAddress':'company@example.invalid'}
        response=Mock();response.json.return_value={'id':'FAKE-GMAIL'}
        with patch('app.providers.provider_token',return_value={'access_token':'FAKE-TOKEN'}) as provider,patch('app.account_access.requests.get',return_value=profile),patch('app.account_access.requests.post',return_value=response) as post:
            send_invitation_email(self.path,data['invitation_id'],token,PUBLIC_URL,'TEST-INVITE-MAIL')
            provider.assert_called_once_with(self.path,'gmail',require_external=False)
            message=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(post.call_args.kwargs['json']['raw']))
            self.assertEqual(message['To'],EMAIL);self.assertEqual(message['From'],'company@example.invalid')
            self.assertTrue(message.is_multipart())
            self.assertIn(token,message.get_body(preferencelist=('plain',)).get_content())
            html=message.get_body(preferencelist=('html',)).get_content()
            self.assertIn('Create my CrownOps account',html);self.assertIn('MacBook / Mac',html)
        with self.assertRaises(HTTPException):
            send_gmail(self.path,{'recipient':'client@example.invalid','subject':'Test','body':'Test','id':'TEST','category':'Test'})

    def test_wrong_token_used_expired_or_revoked_invitation_cannot_be_emailed(self):
        data=self.create_link();self.ready();token=data['url'].split('#invite=')[1]
        route='/api/account-access/invitations/'+data['invitation_id']+'/send-email'
        with patch('app.account_access.send_invitation_email') as send:
            self.assertEqual(self.owner.post(route,json={'token':'wrong','request_id':'test-invalid-token-001'}).status_code,404)
            with transaction(self.path) as db:db.execute('UPDATE invitations SET expires_at=?',('2000-01-01T00:00:00+00:00',))
            self.assertEqual(self.owner.post(route,json={'token':token,'request_id':'test-expired-token-001'}).status_code,409)
            with transaction(self.path) as db:db.execute('UPDATE invitations SET expires_at=?,used_at=?',('2099-01-01T00:00:00+00:00','2000-01-01T00:00:00+00:00'))
            self.assertEqual(self.owner.post(route,json={'token':token,'request_id':'test-used-token-001'}).status_code,409)
            with transaction(self.path) as db:db.execute('DELETE FROM invitations')
            self.assertEqual(self.owner.post(route,json={'token':token,'request_id':'test-revoked-token-001'}).status_code,404)
            send.assert_not_called()

    def test_preview_is_owner_only_reflects_reviewed_details_and_creates_no_invitation(self):
        response=self.owner.post('/api/account-access/invitation-preview',json={'name':'Test Preview','email':EMAIL,'role':'crew'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('Hi Test Preview',response.json()['body']);self.assertIn(EMAIL,response.json()['body'])
        self.assertIn('actual hours',response.json()['body']);self.assertNotIn('#invite=',response.text)
        self.assertEqual(self.client('SALES').post('/api/account-access/invitation-preview',json={'role':'sales'}).status_code,403)
        self.assertEqual(self.client('OWNER2').post('/api/account-access/invitation-preview',json={'role':'owner'}).status_code,403)
        with closing(connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM invitations').fetchone()[0],0)

    def test_sending_requires_owner_csrf_and_master_authority_for_owner_invitations(self):
        data=self.create_link();self.ready();token=data['url'].split('#invite=')[1]
        route='/api/account-access/invitations/'+data['invitation_id']+'/send-email'
        body={'token':token,'request_id':'test-authority-check-001'}
        self.assertEqual(self.public.post(route,json=body).status_code,401)
        self.assertEqual(self.client('CREW').post(route,json=body).status_code,403)
        self.owner.headers.pop('x-csrf-token')
        self.assertEqual(self.owner.post(route,json=body).status_code,403)
        self.owner.headers.update({'x-csrf-token':'test-csrf-'+self.master['id']})
        result=self.owner.post('/api/invitations',json=self.payload(email='next.owner@example.invalid',role='owner',send_email=False))
        self.assertEqual(result.status_code,200,result.text)
        owner_inv=result.json()
        self.assertEqual(self.client('OWNER2').post('/api/account-access/invitations/'+owner_inv['invitation_id']+'/send-email',json={'token':owner_inv['url'].split('#invite=')[1],'request_id':'test-owner-invite-001'}).status_code,403)


if __name__=='__main__':unittest.main()
