"""Pasted schedule imports preserve uncertainty and never create financial state."""
import csv
import io
import unittest
from contextlib import closing
from app.schedule_paste import parse_paste
from app.store import connect, rows
import test_job_planning as fixture


def sheet(records):
    header=['', 'Movers', 'Services', 'Client Name | Email', 'Phone Number', 'REFERRAL', 'KEY NOTES', 'GO!', 'Status', 'Start date', 'Start Time', 'Leave Hodge by', 'Q', 'Deposit', 'Paid?', 'N', 'Hours of Service', 'Workers', 'Starting Address']
    out=io.StringIO();w=csv.writer(out,delimiter='\t');w.writerow(header)
    for extra in records:
        row=['']*25
        for col,value in {1:'3',2:'Moving',3:'Example Client | client@example.invalid',8:'In progress',9:'10/19/2026',10:'9:00 AM',13:'300',18:'100 Example Street',19:'200 Example Street',**extra}.items():row[col]=value
        w.writerow(row)
    return out.getvalue()


class SchedulePasteTests(unittest.TestCase):
    setUp=fixture.JobPlanningTests.setUp
    staff=fixture.JobPlanningTests.staff

    def preview(self,text):
        r=self.owner.post('/api/schedule-imports/paste-preview',json={'text':text})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def commit(self,text,p):
        return self.owner.post('/api/schedule-imports/paste',json={'text':text,'preview_token':p['preview_token']})

    def test_only_in_progress_and_missing_fields_are_preserved(self):
        text=sheet([{}, {8:'Not started'}, {3:'Undated client',9:'',2:'',10:'Afternoon',17:'Person + ??',6:'Line one\nLine two'}])
        p=self.preview(text)
        self.assertEqual(p['summary'],dict(selected=2,new=2,skipped=0,undated=1,missing_service=1,emails_sent=0,payments_created=0))
        self.assertEqual(p['events'][1]['notes_raw'],'Line one\nLine two')
        self.assertIsNone(p['events'][1]['start_time'])
        self.assertEqual(self.commit(text,p).status_code,200)
        events=self.owner.get('/api/schedule-imports/events').json()
        self.assertEqual(len(events),2)
        self.assertEqual(events[0]['deposit_raw'],'300')
        with closing(connect(self.path)) as db:
            self.assertEqual(len(rows(db,'SELECT id FROM jobs')),1)
            self.assertEqual(len(rows(db,'SELECT id FROM payments')),1)
            self.assertEqual(rows(db,'SELECT job_id FROM assignments'),[])
            self.assertEqual(rows(db,'SELECT id FROM messages'),[])

    def test_repeat_reordered_and_changed_rows_never_overwrite(self):
        text=sheet([{}, {9:'10/20/2026'}, {}]);p=self.preview(text)
        self.assertEqual(p['summary']['new'],2)
        self.assertEqual(self.commit(text,p).json()['imported'],2)
        second=sheet([{9:'10/20/2026'}, {6:'Changed notes'}, {}]);p2=self.preview(second)
        self.assertEqual(p2['summary']['new'],0)
        self.assertEqual(self.commit(second,p2).json()['imported'],0)
        self.assertTrue(any('differs' in f for f in p2['events'][1]['flags']))

    def test_existing_working_job_matches_local_date_and_phone(self):
        p=self.preview(sheet([{3:'Renamed Client',4:'+1 (514) 555-0100',9:'11/15/2026'}]))
        self.assertEqual(p['events'][0]['action'],'Skip existing job')

    def test_preview_is_read_only_and_commit_requires_fresh_preview(self):
        text=sheet([{}]);p=self.preview(text)
        self.assertEqual(self.owner.get('/api/schedule-imports/events').json(),[])
        self.assertEqual(self.commit(text,{'preview_token':'wrong'}).status_code,409)
        self.assertEqual(self.commit(sheet([{6:'changed'}]),p).status_code,409)
        self.assertEqual(self.commit(text,p).status_code,200)
        self.assertEqual(self.commit(text,p).status_code,409)
        self.assertEqual(len(self.owner.get('/api/schedule-imports/events').json()),1)

    def test_role_and_input_validation(self):
        text=sheet([{}])
        for role in ('crew','sales','accounting','dispatch'):
            c=self.staff(role)
            self.assertEqual(c.post('/api/schedule-imports/paste-preview',json={'text':text}).status_code,403)
            self.assertEqual(c.post('/api/schedule-imports/paste',json={'text':text}).status_code,403)
        for invalid in ('bad headings',sheet([{9:'02/30/2026'}]),sheet([{3:''}]),sheet([{8:'Not started'}]),'x'*1_000_001):
            self.assertEqual(self.owner.post('/api/schedule-imports/paste-preview',json={'text':invalid}).status_code,422)

    def test_multiday_phases_remain_separate(self):
        p=self.preview(sheet([{}, {2:'Packing'}, {9:'10/20/2026'}]))
        self.assertEqual(p['summary']['new'],3)


if __name__=='__main__':unittest.main()
