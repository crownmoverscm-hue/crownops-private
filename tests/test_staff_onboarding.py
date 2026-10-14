"""Role guidance and email content checks do not send email."""
import unittest
from app.staff_onboarding import invitation_content, ROLE_LABELS, ROLE_STEPS

URL = 'https://crownops.example.invalid'


class StaffOnboardingContentTests(unittest.TestCase):
    def test_each_role_gets_its_own_guidance_and_all_device_instructions(self):
        for role in ROLE_LABELS:
            with self.subTest(role=role):
                content = invitation_content({'name':'Test Team Member','email':'staff@example.invalid','role':role},URL,URL+'/#invite=TEST-PRIVATE-TOKEN')
                self.assertIn('Hi Test Team Member',content['body'])
                self.assertIn('staff@example.invalid',content['body'])
                for sentence in ROLE_STEPS[role]:self.assertIn(sentence,content['body'])
                for device in ('Samsung / Android','iPhone / iPad','MacBook / Mac','Windows'):
                    self.assertIn(device,content['body']);self.assertIn(device,content['html'])
                for instruction in ('48 hours','12 characters','Confirm password','Create account','Forgot your password?','Help & install','Internet is required'):
                    self.assertIn(instruction,content['body'])
                self.assertIn('Regular app address: '+URL+'/',content['body'])
                self.assertIn('Create my CrownOps account',content['html'])

    def test_preview_has_no_real_private_link_and_html_escapes_personal_details(self):
        content = invitation_content({'name':'<script>alert(1)</script> & Test','email':'test@example.invalid','role':'sales'},URL)
        self.assertNotIn('#invite=',content['body']);self.assertNotIn('#invite=',content['html'])
        self.assertIn('private signup link will be added',content['body'])
        self.assertNotIn('<script>',content['html'])
        self.assertIn('&lt;script&gt;',content['html'])
        self.assertIn('max-width:620px',content['html'])


if __name__=='__main__':unittest.main()
