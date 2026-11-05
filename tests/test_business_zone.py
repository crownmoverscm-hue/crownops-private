"""COM-3: company time zones, including daylight-saving transitions. Fictional data only.

Toronto is Crown's existing zone (behaviour must be identical); Vancouver, Halifax
(Atlantic, observes DST) and Regina (no DST) cover other Canadian moving companies.
Expected offsets are computed from the system tz database, never hardcoded, because
zone rules change (British Columbia's rules, for example).
"""
import json
import os
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.business_zone import BUSINESS_ZONE, DEFAULT_ZONE, set_business_zone, zone_name
from app.main import create_app, sha
from app.store import connect, insert, now, transaction

ZONES = ('America/Toronto', 'America/Vancouver', 'America/Halifax', 'America/Regina')
# Local wall times around the 2026 transitions (second Sunday of March, first Sunday of November).
WALL_TIMES = ['2026-03-08T01:30', '2026-03-08T02:30', '2026-03-08T03:30', '2026-07-01T09:00',
              '2026-11-01T00:30', '2026-11-01T01:30', '2026-11-01T02:30', '2026-12-31T23:30']


class ZoneTearDown(unittest.TestCase):
    def tearDown(self):
        set_business_zone(DEFAULT_ZONE)


class BusinessZoneEquivalenceTests(ZoneTearDown):
    """BUSINESS_ZONE must behave exactly like ZoneInfo for the configured zone."""

    def test_local_wall_times_resolve_like_zoneinfo_including_fold(self):
        for name in ZONES:
            set_business_zone(name); real = ZoneInfo(name)
            for text in WALL_TIMES:
                for fold in (0, 1):
                    with self.subTest(zone=name, wall=text, fold=fold):
                        ours = datetime.fromisoformat(text).replace(tzinfo=BUSINESS_ZONE, fold=fold)
                        theirs = datetime.fromisoformat(text).replace(tzinfo=real, fold=fold)
                        self.assertEqual(ours.utcoffset(), theirs.utcoffset())
                        self.assertEqual(ours.astimezone(timezone.utc), theirs.astimezone(timezone.utc))
                        self.assertEqual(ours.isoformat(), theirs.isoformat())

    def test_every_utc_hour_across_both_transitions_converts_like_zoneinfo(self):
        for name in ZONES:
            set_business_zone(name); real = ZoneInfo(name)
            for start in ('2026-03-07T00:00+00:00', '2026-10-31T00:00+00:00'):
                moment = datetime.fromisoformat(start)
                for hour in range(72):
                    at = moment + timedelta(hours=hour)
                    with self.subTest(zone=name, utc=at.isoformat()):
                        ours, theirs = at.astimezone(BUSINESS_ZONE), at.astimezone(real)
                        self.assertEqual(ours.replace(tzinfo=None), theirs.replace(tzinfo=None))
                        self.assertEqual((ours.utcoffset(), ours.fold), (theirs.utcoffset(), theirs.fold))

    def test_repeated_november_hour_is_distinguished_by_fold(self):
        set_business_zone('America/Toronto')
        first = datetime(2026, 11, 1, 1, 30, tzinfo=BUSINESS_ZONE, fold=0).astimezone(timezone.utc)
        second = datetime(2026, 11, 1, 1, 30, tzinfo=BUSINESS_ZONE, fold=1).astimezone(timezone.utc)
        self.assertEqual(second - first, timedelta(hours=1))

    def test_business_day_follows_company_zone(self):
        from app.platform_common import business_day
        moment = '2026-07-02T05:30:00+00:00'  # 01:30 in Toronto, 22:30 the previous day in Vancouver
        for name in ZONES:
            set_business_zone(name)
            with self.subTest(zone=name):
                expected = datetime.fromisoformat(moment).astimezone(ZoneInfo(name)).date().isoformat()
                self.assertEqual(business_day(moment), expected)
        set_business_zone('America/Toronto'); toronto = business_day(moment)
        set_business_zone('America/Vancouver'); self.assertNotEqual(business_day(moment), toronto)

    def test_booking_details_presents_scheduled_start_in_company_zone(self):
        from app.client_communications import booking_details
        quote = {'inputs_json': json.dumps({'arrival_window': '8:00 AM'})}
        for name in ZONES:
            set_business_zone(name)
            with self.subTest(zone=name):
                detail = booking_details({'start_at': '2026-11-01T13:00:00Z'}, quote)
                local = datetime(2026, 11, 1, 13, tzinfo=timezone.utc).astimezone(ZoneInfo(name))
                self.assertEqual(detail['date'], local.date().isoformat())
                self.assertEqual(detail['start_at'], local.isoformat(timespec='seconds'))


class CompanyZoneApplicationTests(ZoneTearDown):
    def make(self, timezone_name=None):
        env = patch.dict(os.environ, {'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', 'QBO_ENV': 'sandbox'})
        env.start(); self.addCleanup(env.stop)
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'zone.sqlite3'
        app = create_app(self.path)
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with transaction(self.path) as db:
            insert(db, 'users', dict(id='owner', email='owner@example.invalid', name='Owner', role='owner', password_hash='x', active=1, created_at=now()))
            insert(db, 'sessions', dict(token_hash=sha('owner-session'), user_id='owner', csrf='owner-csrf', expires_at=expires))
        self.client = TestClient(app); self.addCleanup(self.client.close)
        self.client.cookies.set('crownops_session', 'owner-session'); self.client.headers['x-csrf-token'] = 'owner-csrf'
        if timezone_name:
            version = self.client.get('/api/company/profile').json()['version']
            saved = self.client.put('/api/company/profile', json={'name': 'Fictional Coast Movers', 'phone': '604-555-0199',
                                    'email': 'office@coast.example.invalid', 'timezone': timezone_name, 'version': version})
            self.assertEqual(saved.status_code, 200, saved.text)
        return app

    def test_existing_installation_stays_on_toronto_with_original_et_label(self):
        self.make()
        self.assertEqual(zone_name(), 'America/Toronto')
        script = self.client.get('/business-zone.js')
        self.assertEqual(script.headers['cache-control'], 'no-store')
        self.assertEqual(script.text, 'window.CROWNOPS_TZ="America/Toronto";window.CROWNOPS_TZ_LABEL="ET";')

    def test_profile_zone_applies_immediately_and_on_restart(self):
        self.make('America/Vancouver')
        self.assertEqual(zone_name(), 'America/Vancouver')
        self.assertIn('"America/Vancouver"', self.client.get('/business-zone.js').text)
        set_business_zone(DEFAULT_ZONE)
        create_app(self.path)  # restart: zone is loaded from the stored profile
        self.assertEqual(zone_name(), 'America/Vancouver')

    def test_pages_load_the_zone_before_page_scripts_except_core_v1(self):
        static = Path(__file__).resolve().parent.parent / 'app' / 'static'
        for page in ('index.html', 'portal.html', 'request.html', 'approval.html', 'participate.html', 'invoice_access.html'):
            html = (static / page).read_text()
            self.assertLess(html.index('/business-zone.js'), html.index('<script src="/static/'), page)
        self.assertNotIn('business-zone.js', (static / 'core_v1.html').read_text())
        offenders = [p.name for p in static.glob('*.js') if "timeZone:'America/Toronto'" in p.read_text()]
        self.assertEqual(offenders, [])

    def test_accepted_estimate_hold_uses_company_wall_clock_across_dst(self):
        from app.sales_crm_v6 import ensure_tentative_job, quote_start_window
        self.make('America/Vancouver')
        for day in ('2026-03-08', '2026-07-15', '2026-11-01'):
            with self.subTest(day=day):
                qid, lid = 'Q-' + day, 'L-' + day
                inputs = {'move_date': day, 'arrival_window': '8:00 AM', 'movers': 2, 'trucks': 1, 'service_low': 3, 'service_high': 4}
                with transaction(self.path) as db:
                    insert(db, 'leads', dict(id=lid, client_name='Fictional', phone='6045550100', stage='Won', created_at=now(), updated_at=now()))
                    if not db.execute("SELECT 1 FROM pricing_versions WHERE id='PRICE-ZONE'").fetchone():
                        insert(db, 'pricing_versions', dict(id='PRICE-ZONE', label='Fictional', rates_json='{}', settings_json='{}', created_at=now()))
                    insert(db, 'quotes', dict(id=qid, lead_id=lid, revision=1, status='Accepted', inputs_json=json.dumps(inputs),
                                              result_json='{}', pricing_id='PRICE-ZONE', lead_snapshot_json='{}', lead_version=1,
                                              created_by='owner', created_at=now()))
                    q = dict(db.execute('SELECT * FROM quotes WHERE id=?', (qid,)).fetchone())
                    lead = dict(db.execute('SELECT * FROM leads WHERE id=?', (lid,)).fetchone())
                    job = ensure_tentative_job(db, q, lead, 'owner')
                start_text, _ = quote_start_window(inputs)
                expected = datetime.fromisoformat(day + 'T' + start_text + ':00').replace(tzinfo=ZoneInfo('America/Vancouver')).astimezone(timezone.utc)
                self.assertEqual(datetime.fromisoformat(job['start_at'].replace('Z', '+00:00')), expected)

    def test_imported_schedule_rows_record_the_company_zone(self):
        from app.business_zone import zone_name as zn
        import inspect
        from app import schedule_paste, schedule_importer, wix_sync
        for module in (schedule_paste, schedule_importer, wix_sync):
            self.assertNotIn("'America/Toronto'", inspect.getsource(module))
        set_business_zone('America/Halifax'); self.assertEqual(zn(), 'America/Halifax')


if __name__ == '__main__':
    unittest.main()
