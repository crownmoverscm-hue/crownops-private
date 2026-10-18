"""Generated client email uses the connected or explicitly reviewed company identity."""
import base64
from email import policy
from email.parser import BytesParser
from unittest.mock import Mock,patch
import unittest
from app.store import transaction
from app.platform_common import put_setting
from app.providers import send_gmail
import test_launch_access as fixture

class EmailIdentityTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.LaunchAccessTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.path=self.f.path
        self.msg={'recipient':'client@example.invalid','subject':'Fictional estimate','body':'Hi, this is a fictional test.','id':'MIME-TEST','category':'Reply'}
    def send(self,profile_email='office@example.invalid'):
        profile=Mock();profile.json.return_value={'emailAddress':profile_email};response=Mock(ok=True);response.json.return_value={'id':'fake-mail'}
        with patch('app.providers.provider_token',return_value={'access_token':'fake-token'}),patch('app.providers.requests.get',return_value=profile),patch('app.providers.requests.post',return_value=response) as send:
            send_gmail(self.path,self.msg)
            return BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(send.call_args.kwargs['json']['raw']))
    def identity(self,**changes):
        cfg={'connected_mailbox':'office@example.invalid','send_as':'info@example.invalid','reply_to':'help@example.invalid','approved':True};cfg.update(changes)
        with transaction(self.path) as db:put_setting(db,'company_communications',cfg)
    def test_default_sender_is_confirmed_from_gmail_not_from_client_input(self):
        self.msg['from']='attacker@example.invalid';self.msg['reply_to']='attacker@example.invalid'
        mime=self.send();self.assertEqual(str(mime['From']),'Crown Movers <office@example.invalid>');self.assertEqual(str(mime['Reply-To']),'office@example.invalid')
    def test_approved_company_alias_and_reply_address_are_used(self):
        self.identity();mime=self.send();self.assertEqual(str(mime['From']),'Crown Movers <info@example.invalid>');self.assertEqual(str(mime['Reply-To']),'help@example.invalid')
    def test_unapproved_alias_is_not_used(self):
        self.identity(approved=False);mime=self.send();self.assertEqual(str(mime['From']),'Crown Movers <office@example.invalid>')
    def test_changed_connected_account_invalid_sender_or_header_injection_does_not_send(self):
        for values,email in [({},'invalid'),({},'other@example.invalid'),({'send_as':'info@example.invalid\r\nBcc:other@example.invalid'},'office@example.invalid')]:
            self.identity(**values)
            with self.subTest(values=values,email=email),patch('app.providers.requests.post') as post:
                with self.assertRaises(ValueError):self.send(email)
                post.assert_not_called()
    def test_only_management_can_approve_valid_company_sender_settings(self):
        body={'connected_mailbox':'office@example.invalid','send_as':'info@example.invalid','reply_to':'help@example.invalid','approved':True}
        self.assertEqual(self.f.staff('sales').patch('/api/ux/company-communications',json=body).status_code,403)
        self.assertEqual(self.f.manager.patch('/api/ux/company-communications',json={**body,'send_as':'not-an-address'}).status_code,422)
        self.assertEqual(self.f.manager.patch('/api/ux/company-communications',json=body).status_code,200)
