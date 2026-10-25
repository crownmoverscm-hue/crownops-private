"""Owner fee policy with fictional labour rates; no provider or business approval."""
from contextlib import closing
from copy import deepcopy
from decimal import Decimal
import json
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from app import local_pricing_policy as policy
from app.pricing import calculate as legacy_calculate
from app.sales_crm_v6 import calculate, service_minimum_hours
from app.quebec_taxes import taxable_move
from app.store import connect
import test_sales_crm_v6 as fixture


class LocalPricingPolicyTests(unittest.TestCase):
    def setUp(self):
        self.flag=patch.dict('os.environ', {'CROWNOPS_LOCAL_PRICING_POLICY':policy.VERSION})
        self.flag.start(); self.addCleanup(self.flag.stop)
        self.td,self.path=fixture.SalesCrmV6Tests().make_db(); self.addCleanup(self.td.cleanup)
        self.inputs={'service_type':'Moving','language':'English','move_date':'2026-11-15',
                     'start_window':'afternoon','small_afternoon_job':True,'movers':2,'trucks':1,
                     'hourly_rate':100,'hours_low':.75,'hours_high':1,'minimum_hours':3,
                     'transport_mode':'local_amount','fixed_transport':50,'materials':[]}

    def quote(self, **changes):
        with closing(connect(self.path)) as db:
            return calculate(db,{**self.inputs, **changes})

    def test_one_hour_minimum_and_separate_fee_lines(self):
        r=self.quote()
        self.assertEqual(r['minimum_hours'],1)
        self.assertEqual(r['service_low'],1)
        self.assertEqual(r['fuel_cents'],3599)
        self.assertEqual(r['transport_cents'],5000)
        self.assertEqual(r['low_cents'],18599)
        self.assertEqual([x['total_cents'] for x in r['line_items']], [10000,5000,3599])
        self.assertEqual(taxable_move(r['low_cents'])['total_cents'],21384)

    def test_additional_service_not_capped_at_one_hour(self):
        for hours in (1,1.25,2):
            with self.subTest(hours=hours):
                r=self.quote(hours_low=hours,hours_high=hours)
                self.assertEqual(r['service_low'],hours)
                self.assertEqual(r['service_low_cents'],round(hours*10000))

    def test_fuel_once_per_truck_not_per_hour(self):
        r=self.quote(trucks=2,hours_high=4)
        self.assertEqual(r['fuel_cents'],7198)
        fuel=r['line_items'][-1]
        self.assertEqual((fuel['qty'],fuel['unit_cents'],fuel['total_cents']),(2,3599,7198))

    def test_longer_route_no_automatic_mileage_fee_manual_override_supported(self):
        for km in (0,40,41,150):
            self.assertEqual(self.quote(distance_km=km)['fuel_cents'],3599)
        r=self.quote(distance_km=100,local_fuel_rate=94)
        self.assertEqual(r['fuel_cents'],9400)
        self.assertFalse(r['local_pricing_policy']['automatic_mileage_surcharge'])

    def test_afternoon_eligibility_is_explicit_and_does_not_change_regular_minimum(self):
        self.assertEqual(self.quote(start_window='morning',small_afternoon_job=False)['minimum_hours'],3)
        self.assertEqual(self.quote(small_afternoon_job=False)['minimum_hours'],3)
        with self.assertRaises(HTTPException):self.quote(start_window='morning')
        self.assertEqual(service_minimum_hours({'small_afternoon_job':True,'start_window':'afternoon'},'Packing')[0],Decimal('1.5'))
        with self.assertRaises(HTTPException):
            service_minimum_hours({'small_afternoon_job':True,'start_window':'afternoon'},'Long Distance')

    def test_disabled_flag_preserves_previous_defaults(self):
        with patch.dict('os.environ',{'CROWNOPS_LOCAL_PRICING_POLICY':''}):
            r=self.quote()
            self.assertEqual((r['minimum_hours'],r['fuel_cents']),(1.5,3500))

    def test_french_labels_and_snapshot_stay_stable_after_flag_change(self):
        r=self.quote(language='French')
        self.assertEqual(r['line_items'][-1]['name'],'Frais de carburant')
        saved=json.dumps(r,sort_keys=True)
        with patch.dict('os.environ',{'CROWNOPS_LOCAL_PRICING_POLICY':''}):self.quote()
        self.assertEqual(json.dumps(r,sort_keys=True),saved)
        self.assertEqual(r['local_pricing_policy']['version'],policy.VERSION)

    def test_legacy_calculator_matches_local_policy_and_explicit_override(self):
        pricing={'id':'TEST','approved':False,'rates_json':[],
                 '_annual_override':{'rate_2_cents':10000,'label':'TEST fictional'},
                 'settings_json':{'afternoon_micro_minimum':1.5,'normal_local_fuel':35,'time_increment_hours':.25}}
        inputs={'movers':2,'trucks':2,'hours_low':.75,'hours_high':1,'transport_hours':.5,
                'minimum_key':'afternoon_micro_minimum','fuel_key':'normal_local_fuel'}
        r=legacy_calculate(inputs,pricing)
        self.assertEqual((r['minimum_hours'],r['fuel_cents'],r['transport_cents']),(1,7198,5000))
        self.assertEqual(legacy_calculate({**inputs,'local_fuel_rate':94},pricing)['fuel_cents'],18800)
        self.assertFalse(r['pricing_approved'])
