"""New evening inquiries remain actionable on the Montreal business day."""
from contextlib import closing
from datetime import datetime,timezone
from unittest.mock import patch
import unittest
from app.platform_common import business_day,add_task
from app.gmail_sync import _review_task
from app.store import transaction,connect,one
import test_launch_access as fixture

class BusinessDayTests(unittest.TestCase):
    def test_utc_midnight_and_daylight_saving_keep_the_local_service_day(self):
        for timestamp,expected in [('2026-10-18T00:30:00+00:00','2026-10-17'),('2026-03-08T04:30:00+00:00','2026-03-07'),('2026-11-01T03:30:00+00:00','2026-10-31'),('2026-11-01T06:30:00+00:00','2026-11-01')]:
            self.assertEqual(business_day(timestamp),expected)

    def test_inquiry_followup_and_ambiguous_call_are_due_on_the_current_local_day(self):
        f=fixture.LaunchAccessTests();f.setUp();self.addCleanup(f.doCleanups)
        instant=datetime(2026,10,18,0,30,tzinfo=timezone.utc)
        with patch('app.platform_common.datetime') as clock:
            clock.now.return_value=instant
            with transaction(f.path) as db:
                add_task(db,'L1','Callback received this evening',key='evening-callback')
                _review_task(db,'evening-call','Match incoming voicemail / missed call to the correct client service')
        with closing(connect(f.path)) as db:
            self.assertEqual(one(db,"SELECT due_date FROM tasks WHERE legacy_id='evening-callback'")['due_date'],'2026-10-17')
            self.assertEqual(one(db,"SELECT due_date FROM tasks WHERE legacy_id='gmail-match:evening-call'")['due_date'],'2026-10-17')
