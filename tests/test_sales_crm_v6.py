import tempfile
import unittest

from fastapi import HTTPException
from pathlib import Path

from app.store import initialize, transaction, one
from app.sales_crm_v6 import schema, calculate, quote_start_window, quoted_block_hours, minimum_trucks_for_movers, service_minimum_hours


class SalesCrmV6Tests(unittest.TestCase):
    def make_db(self):
        td=tempfile.TemporaryDirectory()
        path=Path(td.name)/"crownops.sqlite3"
        initialize(path);schema(path)
        with transaction(path) as db:
            db.execute("""INSERT INTO annual_rate_periods
              (id,label,start_date,end_date,rate_2_cents,rate_3_cents,rate_4_cents,rate_5_cents,rate_6_cents,active,version,note,approved_by,created_at)
              VALUES ('RATE-TEST','Fall 2026','2026-09-01','2026-12-31',13500,17500,22000,26000,31500,1,1,'test','test','2026-01-01T00:00:00+00:00')""")
        return td,path

    def test_templates_include_blank_long_distance_english_and_french(self):
        td,path=self.make_db()
        try:
            with transaction(path) as db:
                en=one(db,"SELECT * FROM crm_quote_templates WHERE template_key='long-en'")
                fr=one(db,"SELECT * FROM crm_quote_templates WHERE template_key='long-fr'")
            self.assertIsNotNone(en);self.assertIsNotNone(fr)
            self.assertIn("blank", en["name"].lower())
            self.assertIn("vierge", fr["name"].lower())
        finally:
            td.cleanup()

    def test_local_two_truck_pricing_uses_extra_truck_transport_and_fuel(self):
        td,path=self.make_db()
        try:
            with transaction(path) as db:
                r=calculate(db,{"service_type":"Moving","language":"English","move_date":"2026-10-15",
                    "movers":3,"trucks":2,"hours_low":3,"hours_high":4,"minimum_hours":3,
                    "transport_hours":1,"distance_km":0,"distance_rate":0.94,"materials":[],
                    "other_amount":0,"discount_amount":0})
            self.assertEqual(r["hourly_rate_cents"],17500)
            self.assertEqual(r["transport_cents"],20000)  # 1 x 175 + extra truck $25
            self.assertEqual(r["fuel_cents"],7000)        # $35 x 2 trucks
            self.assertEqual(r["low_cents"],79500)
            self.assertEqual(r["high_cents"],97000)
        finally:
            td.cleanup()

    def test_minimum_trucks_follow_crown_crew_rules(self):
        self.assertEqual(minimum_trucks_for_movers(2),1)
        self.assertEqual(minimum_trucks_for_movers(3),1)
        self.assertEqual(minimum_trucks_for_movers(4),2)
        self.assertEqual(minimum_trucks_for_movers(5),2)
        self.assertEqual(minimum_trucks_for_movers(6),3)

        td,path=self.make_db()
        try:
            with transaction(path) as db:
                with self.assertRaises(HTTPException) as ctx:
                    calculate(db,{"service_type":"Moving","language":"English","move_date":"2026-10-15",
                        "movers":5,"trucks":1,"hours_low":3,"hours_high":4,"minimum_hours":3,
                        "transport_hours":1,"distance_km":0,"distance_rate":0.94,"materials":[],
                        "other_amount":0,"discount_amount":0})
                self.assertIn("at least 2 truck",str(ctx.exception.detail))

                with self.assertRaises(HTTPException) as ctx:
                    calculate(db,{"service_type":"Moving","language":"English","move_date":"2026-10-15",
                        "movers":6,"trucks":2,"hours_low":3,"hours_high":4,"minimum_hours":3,
                        "transport_hours":1,"distance_km":0,"distance_rate":0.94,"materials":[],
                        "other_amount":0,"discount_amount":0})
                self.assertIn("at least 3 truck",str(ctx.exception.detail))
        finally:
            td.cleanup()

    def test_small_afternoon_minimum_is_limited_to_local_afternoon_jobs(self):
        minimum,small=service_minimum_hours({"minimum_hours":1,"start_window":"morning","small_afternoon_job":False},"Moving")
        self.assertEqual(float(minimum),3.0);self.assertFalse(small)

        minimum,small=service_minimum_hours({"minimum_hours":3,"start_window":"afternoon","small_afternoon_job":True},"Moving")
        self.assertEqual(float(minimum),1.5);self.assertTrue(small)

        with self.assertRaises(HTTPException):
            service_minimum_hours({"start_window":"morning","small_afternoon_job":True},"Moving")
        with self.assertRaises(HTTPException):
            service_minimum_hours({"start_window":"afternoon","small_afternoon_job":True},"Long Distance")

    def test_afternoon_small_job_calculation_uses_one_point_five_hour_minimum(self):
        td,path=self.make_db()
        try:
            with transaction(path) as db:
                r=calculate(db,{"service_type":"Moving","language":"English","move_date":"2026-10-15",
                    "start_window":"afternoon","small_afternoon_job":True,
                    "movers":2,"trucks":1,"hours_low":1,"hours_high":2,"minimum_hours":3,
                    "transport_hours":1,"distance_km":0,"distance_rate":0.94,"materials":[],
                    "other_amount":0,"discount_amount":0})
            self.assertEqual(r["minimum_hours"],1.5)
            self.assertEqual(r["service_low"],1.5)
            self.assertTrue(r["small_afternoon_job"])
        finally:
            td.cleanup()

    def test_fixed_long_distance_transport_hides_transport_hours_and_uses_094_per_km_per_truck(self):
        td,path=self.make_db()
        try:
            with transaction(path) as db:
                r=calculate(db,{"service_type":"Long Distance","language":"English","move_date":"2026-10-15",
                    "movers":3,"trucks":2,"hours_low":4,"hours_high":5,"minimum_hours":3,
                    "transport_hours":14,"fixed_transport":2450,"distance_km":540,"distance_rate":0.94,
                    "materials":[],"other_amount":0,"discount_amount":0})
            self.assertEqual(r["transport_hours"],0)
            self.assertEqual(r["transport_cents"],245000)
            self.assertEqual(r["fuel_cents"],101520)
            self.assertEqual(r["distance_rate_cents"],94)
        finally:
            td.cleanup()

    def test_junk_removal_tiers_and_extra_labour(self):
        td,path=self.make_db()
        try:
            with transaction(path) as db:
                r=calculate(db,{"service_type":"Junk Removal","junk_tier":"quarter","junk_extra_half_hours":2})
            self.assertEqual(r["low_cents"],56000)
            self.assertEqual(r["high_cents"],56000)
            self.assertEqual(len(r["line_items"]),2)
        finally:
            td.cleanup()

    def test_quote_start_windows_and_block_hours_follow_sold_timing(self):
        self.assertEqual(quote_start_window({"start_window":"morning"}),("09:00","9:00-9:30 AM"))
        self.assertEqual(quote_start_window({"start_window":"afternoon"}),("13:00","1:00-4:00 PM"))
        self.assertEqual(quote_start_window({"start_window":"custom","start_time":"11:15"}),("11:15","11:15 AM"))
        self.assertEqual(float(quoted_block_hours({"service_type":"Moving","hours_high":4,"minimum_hours":3,"transport_hours":1})),5.0)

    def test_french_quote_lines_follow_selected_quote_language(self):
        td,path=self.make_db()
        try:
            with transaction(path) as db:
                r=calculate(db,{"service_type":"Moving","language":"French","move_date":"2026-10-15",
                    "movers":2,"trucks":1,"hours_low":3,"hours_high":4,"minimum_hours":3,
                    "transport_hours":1,"distance_km":0,"distance_rate":0.94,"materials":[],
                    "other_amount":0,"discount_amount":0})
            self.assertEqual(r["language"],"French")
            self.assertIn("demenageurs",r["line_items"][0]["name"])
            self.assertEqual(r["line_items"][1]["name"],"Frais de transport")
            self.assertEqual(r["line_items"][2]["name"],"Frais de carburant")
        finally:
            td.cleanup()

    def test_tv_protection_seed_is_35_dollars(self):
        td,path=self.make_db()
        try:
            with transaction(path) as db:
                item=one(db,"SELECT * FROM material_catalog WHERE code='TV_PROTECTION_PACKAGE'")
            self.assertIsNotNone(item)
            self.assertEqual(item["sell_cents"],3500)
        finally:
            td.cleanup()


if __name__=="__main__":
    unittest.main()
