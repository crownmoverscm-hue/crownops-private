"""Company calendars, Montréal date boundaries and unchanged approved time."""
import unittest
from contextlib import closing
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from app.store import connect,transaction,insert,one,rows,now
from app.platform_common import put_setting
from app.main import sha
from app.payroll_periods import period_for,period_range
import test_worked_hours_costs as fixture

class PayrollPeriodTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.WorkedHoursCostsTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path=self.f.path;self.owner=self.f.owner
        with transaction(self.path) as db:
            insert(db,'users',dict(id='manager',email='manager@example.invalid',name='Manager',role='manager',active=1,password_hash='unused',created_at=now()))
            insert(db,'sessions',dict(token_hash=sha('manager-token'),user_id='manager',csrf='test-csrf',expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()))
        self.manager=self.f.staff('manager')
    def cfg(self,client=None,on=''):
        r=(client or self.owner).get('/api/payroll-periods',params={'on':on});self.assertEqual(r.status_code,200,r.text);return r.json()
    def save(self,days=7,anchor='2026-11-09',client=None,token=None):
        c=client or self.owner
        return c.post('/api/payroll-periods',json={'period_days':days,'period_anchor':anchor,'review_token':token or self.cfg(c)['review_token']})
    def test_weekly_and_biweekly_any_start_day_inclusive_and_no_gap(self):
        for days in (7,14):
            cfg={'approved':True,'period_days':days,'period_anchor':'2026-12-30'}
            self.assertEqual(period_for(cfg,'2026-12-30')['start'],'2026-12-30')
            end=period_range(cfg,'2026-12-30');self.assertEqual((end-datetime.fromisoformat('2026-12-30').date()).days,days-1)
            next_day=(end+timedelta(days=1)).isoformat();self.assertEqual(period_for(cfg,next_day)['start'],next_day)
            self.assertEqual(period_for(cfg,'2026-12-29')['end'],'2026-12-29')
        self.assertEqual(self.save(7,'2026-11-11').status_code,200) # Wednesday is valid.
        self.assertEqual(self.cfg(on='2026-11-17')['selected'],{'start':'2026-11-11','end':'2026-11-17'})
    def test_only_owner_and_manager_configure_and_stale_updates_fail(self):
        stale=self.cfg()['review_token'];self.assertEqual(self.save(client=self.manager).status_code,200)
        self.assertEqual(self.save(14,token=stale).status_code,409)
        for role in ('sales','dispatch','accounting','crew','lead'):
            c=self.f.staff(role);self.assertFalse(self.cfg(c)['can_configure']);self.assertEqual(self.save(client=c).status_code,403)
    def test_invalid_frequency_dates_and_forged_fields_rejected(self):
        for days in (0,1,13,15,True,'7',7.5):self.assertEqual(self.save(days).status_code,422)
        for day in ('','2026-02-30','not-a-date',None):self.assertEqual(self.save(anchor=day).status_code,422)
        self.assertEqual(self.owner.get('/api/payroll-periods?on=not-a-date').status_code,422)
        self.assertEqual(self.owner.post('/api/payroll-periods',json={'period_days':7,'period_anchor':'2026-11-09','approved':True}).status_code,422)
    def test_default_period_is_configured_and_custom_dates_still_work(self):
        self.assertFalse(self.cfg()['configured']);self.save(14,'2026-11-09')
        with patch('app.payroll_periods.business_day',return_value='2026-11-16'):
            c=self.cfg();self.assertEqual(c['selected'],{'start':'2026-11-09','end':'2026-11-22'})
            d=self.owner.get('/api/ux/payroll-summary').json();self.assertEqual((d['start'],d['end']),('2026-11-09','2026-11-22'))
            self.assertEqual(c['previous']['end'],'2026-11-08');self.assertEqual(c['next']['start'],'2026-11-23')
        d=self.owner.get('/api/ux/payroll-summary?start=2026-11-01&end=2026-11-30').json();self.assertEqual((d['start'],d['end']),('2026-11-01','2026-11-30'))
    def test_legacy_biweekly_anchor_preserved(self):
        with transaction(self.path) as db:put_setting(db,'payroll_policy',{'approved':True,'period_anchor':'2026-11-05'})
        c=self.cfg(on='2026-11-18');self.assertTrue(c['configured']);self.assertEqual(c['period_days'],14)
        self.assertEqual(c['selected'],{'start':'2026-11-05','end':'2026-11-18'})
    def test_changing_schedule_preserves_hours_rates_approvals_and_custom_export(self):
        self.f.team();self.f.submit();self.f.approve()
        with closing(connect(self.path)) as db:before=rows(db,'SELECT * FROM times ORDER BY id')
        csv_before=self.owner.get('/api/ux/payroll-hours/export.csv'+self.f.params);self.assertEqual(csv_before.status_code,200)
        self.assertEqual(self.save(7,client=self.manager).status_code,200);self.assertEqual(self.save(14,'2026-11-11').status_code,200)
        with closing(connect(self.path)) as db:self.assertEqual(rows(db,'SELECT * FROM times ORDER BY id'),before)
        self.assertEqual(self.owner.get('/api/ux/payroll-hours/export.csv'+self.f.params).text,csv_before.text)
    def test_montreal_service_date_selects_correct_period_at_midnight(self):
        self.save();self.f.team();self.f.submit(('E1',),hours=3)
        with transaction(self.path) as db:db.execute("UPDATE jobs SET start_at='2026-11-16T00:30:00+00:00' WHERE id='J1'")
        d=self.owner.get('/api/ux/payroll-summary?start=2026-11-09&end=2026-11-15').json();self.assertEqual(d['total_hours'],3)
        d=self.owner.get('/api/ux/payroll-summary?start=2026-11-16&end=2026-11-22').json();self.assertEqual(d['total_hours'],0)
    def test_dst_periods_always_have_seven_or_fourteen_dates(self):
        for anchor,day in [('2026-03-02','2026-03-08'),('2026-10-26','2026-11-01')]:
            c=period_for({'period_anchor':anchor,'period_days':7},day);self.assertEqual(c['start'],anchor);self.assertEqual(c['end'],day)
    def test_legacy_exports_use_new_weekly_boundaries_and_reject_wrong_start(self):
        self.save(7,'2026-11-09')
        for url in ('/api/payroll-export','/api/payroll-adjustments/export'):
            self.assertEqual(self.owner.get(url+'?period_start=2026-11-09').status_code,200)
            self.assertEqual(self.owner.get(url+'?period_start=2026-11-10').status_code,422)
            self.assertEqual(self.owner.get(url+'?period_start=2026-11-16').status_code,200)
        self.assertEqual(self.manager.post('/api/platform/settings/payroll_policy',json={'period_days':7,'period_anchor':'2026-11-11','approved':True,'review_note':'Fictional weekly schedule'}).status_code,200)
    def test_default_approval_and_export_use_same_configured_period_as_summary(self):
        self.f.team();self.f.submit();self.save(7,'2026-11-09')
        with patch('app.payroll_periods.business_day',return_value='2026-11-15'):
            report=self.owner.get('/api/ux/payroll-summary').json()
            r=self.owner.post('/api/ux/payroll-summary/approve',json={'review_token':report['review_token']});self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['approved'],2)
            export=self.owner.get('/api/ux/payroll-hours/export.csv');self.assertEqual(export.status_code,200,export.text)
            self.assertIn('2026-11-09-2026-11-15',export.headers['content-disposition'])

if __name__=='__main__':unittest.main()
