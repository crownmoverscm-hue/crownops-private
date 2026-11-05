"""COM-4: company pricing policy. Fictional data only; no provider is contacted."""
import json
import os
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pricing_golden_scenarios as golden  # noqa: E402

from app.main import create_app, sha  # noqa: E402
from app.platform_common import put_setting, settings  # noqa: E402
from app.store import connect, insert, now, transaction  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'crown_pricing_golden.json'
COMPANY_POLICY = {
    'truck_minimums': [[6, 3]], 'default_minimum_hours': '2', 'moving_packing_minimum_hours': '2.5',
    'small_afternoon_minimum_hours': '2', 'local_fuel_per_truck': '50.00', 'extra_truck_fee': '40.00',
    'long_distance_km_rate': '1.10', 'long_distance_km_buffer': '1.10', 'long_distance_transport_multiplier': '1.20',
    'junk_tiers': {'single': ['One item', '150'], 'quarter': ['Quarter load', '300'], 'half': ['Half load', '600'],
                   'three_quarter': ['Three-quarter load', '900'], 'full': ['Full load', '1200']},
    'junk_extra_labour': '70', 'default_deposit': '200', 'limited_liability_text': 'Released value per company terms',
    'approved': True,
}


class CrownPricingPreservationTests(unittest.TestCase):
    def test_crown_calculations_match_pre_com4_golden_outputs_exactly(self):
        expected = json.loads(FIXTURE.read_text())
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'CROWNOPS_ENV': 'test'}):
            actual = json.loads(json.dumps(golden.compute_all(golden.golden_database(td)), sort_keys=True, ensure_ascii=False))
        self.assertEqual(sorted(actual), sorted(expected))
        for name in expected:
            with self.subTest(scenario=name):
                self.assertEqual(actual[name], expected[name])
        self.assertNotIn('pricing_policy_version', actual['local_2_movers_default'])

    def test_legacy_testimonial_disclosure_text_is_unchanged(self):
        from app.pricing_policy import LEGACY_POLICY, disclosure_text, testimonial_credit_cents
        self.assertEqual(testimonial_credit_cents(LEGACY_POLICY), 5000)
        self.assertEqual(disclosure_text(LEGACY_POLICY), 'The customer received a $50 moving discount for participating in this testimonial.')


class CompanyPricingTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', 'QBO_ENV': 'sandbox'})
        env.start(); self.addCleanup(env.stop)
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = golden.golden_database(temp.name)
        with transaction(self.path) as db:
            put_setting(db, 'company_identity_mode', 'unconfigured')
        self.app = create_app(self.path)
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.clients = {}
        with transaction(self.path) as db:
            for role in ('owner', 'sales'):
                insert(db, 'users', dict(id=role, email=role + '@example.invalid', name=role, role=role, password_hash='x', active=1, created_at=now()))
                insert(db, 'sessions', dict(token_hash=sha(role + '-s'), user_id=role, csrf=role + '-c', expires_at=expires))
        for role in ('owner', 'sales'):
            c = TestClient(self.app); c.cookies.set('crownops_session', role + '-s'); c.headers['x-csrf-token'] = role + '-c'
            self.clients[role] = c; self.addCleanup(c.close)

    def calc(self, data):
        from app.sales_crm_v6 import calculate
        with closing(connect(self.path)) as db:
            return calculate(db, {**golden.BASE, **data})

    def save(self, role='owner', **changes):
        version = self.clients['owner'].get('/api/company/pricing-policy').json()['version']
        return self.clients[role].put('/api/company/pricing-policy', json={**COMPANY_POLICY, **changes, 'version': version})

    def test_new_company_cannot_estimate_or_inherit_crown_pricing(self):
        with self.assertRaises(HTTPException) as refused:
            self.calc({})
        self.assertEqual(refused.exception.status_code, 409); self.assertIn('pricing policy', refused.exception.detail)
        status = self.clients['sales'].get('/api/company/pricing-policy').json()
        self.assertFalse(status['configured']); self.assertIsNone(status['policy'])
        setup = self.clients['sales'].get('/api/crm/quote-setup').json()
        self.assertFalse(setup['pricing_configured']); self.assertEqual(setup['defaults'], {}); self.assertEqual(setup['long_distance_presets'], [])

    def test_new_installation_is_not_seeded_with_crown_rates(self):
        from app.seed import seed_pricing
        from app.store import initialize
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'CROWNOPS_ENV': 'staging'}):
            path = Path(td) / 'fresh.sqlite3'
            initialize(path); seed_pricing(path)
            from app.review_features import seed_review_defaults
            seed_review_defaults(path)
            with closing(connect(path)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM pricing_versions').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT count(*) FROM annual_rate_periods').fetchone()[0], 0)

    def test_policy_requires_manager_complete_values_and_explicit_approval(self):
        self.assertEqual(self.save('sales').status_code, 403)
        bad = [{'approved': 'yes'}, {'local_fuel_per_truck': ''}, {'truck_minimums': [[9, 1]]}, {'long_distance_km_buffer': '0.5'},
               {'junk_tiers': {'single': ['x', '1']}}, {'limited_liability_text': '<b>x</b>'}, {'surprise': 1}]
        for change in bad:
            with self.subTest(change=change):
                self.assertEqual(self.save(**change).status_code, 422)
        self.assertFalse(self.clients['owner'].get('/api/company/pricing-policy').json()['configured'])
        stale = self.clients['owner'].put('/api/company/pricing-policy', json={**COMPANY_POLICY, 'version': 5})
        self.assertEqual(stale.status_code, 409)

    def test_company_policy_prices_with_its_own_values_and_is_audited(self):
        saved = self.save(); self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()['version'], 1)
        r = self.calc({})  # 2 movers, rate 135, 2.5 h minimum floor vs 3 h default request
        # service 135 x 3 h + transport 135 x 1 h + company fuel 50 x 1 truck
        self.assertEqual((r['low_cents'], r['fuel_cents'], r['pricing_policy_version']), (59000, 5000, 1))
        four_one = self.calc({'movers': 4, 'trucks': 1})  # Crown requires 2 trucks for 4 movers; this company does not
        self.assertEqual(four_one['trucks'], 1)
        two_trucks = self.calc({'trucks': 2})
        self.assertEqual(two_trucks['truck_fee_cents'], 4000)
        junk = self.calc({'service_type': 'Junk Removal', 'junk_tier': 'half', 'junk_extra_half_hours': 1})
        self.assertEqual(junk['low_cents'], 67000)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE action='Pricing policy approved'").fetchone()[0], 1)

    def test_saved_estimates_keep_their_snapshot_after_policy_changes(self):
        self.assertEqual(self.save().status_code, 200)
        first = self.calc({})
        with transaction(self.path) as db:
            insert(db, 'leads', dict(id='L1', client_name='Fictional', phone='5145550100', stage='Quoted', created_at=now(), updated_at=now()))
            insert(db, 'pricing_versions', dict(id='PRICE-SNAP', label='Fictional', rates_json='{}', settings_json='{}', created_at=now()))
            insert(db, 'quotes', dict(id='Q1', lead_id='L1', revision=1, status='Approved', inputs_json=json.dumps(golden.BASE),
                                      result_json=json.dumps(first), pricing_id='PRICE-SNAP', lead_snapshot_json='{}', lead_version=1,
                                      created_by='owner', created_at=now()))
        self.assertEqual(self.save(local_fuel_per_truck='90.00').status_code, 200)
        self.assertNotEqual(self.calc({})['low_cents'], first['low_cents'])
        with closing(connect(self.path)) as db:
            stored = json.loads(db.execute("SELECT result_json FROM quotes WHERE id='Q1'").fetchone()[0])
        self.assertEqual(stored, first); self.assertEqual(stored['pricing_policy_version'], 1)

    def test_testimonial_credit_must_be_set_by_the_company(self):
        from app.pricing_policy import pricing_policy, testimonial_credit_cents
        self.save()
        with closing(connect(self.path)) as db, self.assertRaises(HTTPException):
            testimonial_credit_cents(pricing_policy(db))
        self.assertEqual(self.save(testimonial_credit='25').status_code, 200)
        with closing(connect(self.path)) as db:
            self.assertEqual(testimonial_credit_cents(pricing_policy(db)), 2500)


class TimezoneChangeSafeguardTests(unittest.TestCase):
    def test_timezone_cannot_change_once_scheduling_data_exists(self):
        from app.company_profile import store_profile
        from app.business_zone import DEFAULT_ZONE, set_business_zone
        self.addCleanup(set_business_zone, DEFAULT_ZONE)
        profile = {'name': 'Fictional Movers', 'phone': '514-555-0100', 'email': 'office@example.invalid', 'timezone': 'America/Toronto'}
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'CROWNOPS_ENV': 'test'}):
            path = golden.golden_database(td)
            with transaction(path) as db:
                store_profile(db, {**profile, 'version': 0}, 'owner')
                store_profile(db, {**profile, 'timezone': 'America/Halifax', 'version': 1}, 'owner')  # no bookings yet: allowed
                insert(db, 'leads', dict(id='L', client_name='F', phone='5145550100', stage='Won', created_at=now(), updated_at=now()))
                insert(db, 'jobs', dict(id='J', lead_id='L', title='Move', phase='Move', start_at='2026-11-20T14:00:00+00:00',
                                        end_at='2026-11-20T18:00:00+00:00', status='Tentative', created_by='owner', created_at=now()))
            with transaction(path) as db, self.assertRaises(HTTPException) as refused:
                store_profile(db, {**profile, 'timezone': 'America/Vancouver', 'version': 2}, 'owner')
            self.assertEqual(refused.exception.status_code, 409)
            with transaction(path) as db:  # other fields can still change
                self.assertEqual(store_profile(db, {**profile, 'timezone': 'America/Halifax', 'name': 'Renamed Movers', 'version': 2}, 'owner')['name'], 'Renamed Movers')


if __name__ == '__main__':
    unittest.main()
