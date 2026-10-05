import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from app.store import initialize, transaction, one, insert, dumps, now
from app.sales_crm_v6 import schema, calculate, stage_tentative_job, QUOTE_ROLES
from app.providers import branded_email_html


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

    def test_tv_protection_seed_is_35_dollars(self):
        td,path=self.make_db()
        try:
            with transaction(path) as db:
                item=one(db,"SELECT * FROM material_catalog WHERE code='TV_PROTECTION_PACKAGE'")
            self.assertIsNotNone(item)
            self.assertEqual(item["sell_cents"],3500)
        finally:
            td.cleanup()

    def test_dispatch_managers_can_use_quote_workflow(self):
        self.assertIn("dispatch", QUOTE_ROLES)

    def test_accepted_quote_stages_tentative_job_with_selected_start(self):
        td,path=self.make_db()
        try:
            with transaction(path) as db:
                insert(db,"pricing_versions",dict(id="PRICE-TEST",label="Test",rates_json="[]",settings_json="{}",approved=1,created_at=now()))
                insert(db,"leads",dict(id="LEAD-TEST",client_name="Test Client",email="test@example.com",phone="5145550000",
                    move_date="2026-10-15",pickup="Montreal",dropoff="Laval",created_at=now(),updated_at=now()))
                insert(db,"quotes",dict(id="QUOTE-TEST",lead_id="LEAD-TEST",revision=1,status="Accepted",
                    inputs_json=dumps({"service_type":"Moving","move_date":"2026-10-15","service_start":"13:00","deposit_required":300}),
                    result_json=dumps({"service_high":5,"transport_hours":1}),pricing_id="PRICE-TEST",
                    lead_snapshot_json=dumps({}),lead_version=1,created_by="test",created_at=now()))
                q=one(db,"SELECT * FROM quotes WHERE id='QUOTE-TEST'");lead=one(db,"SELECT * FROM leads WHERE id='LEAD-TEST'")
                job=stage_tentative_job(db,q,lead,"test")
            self.assertEqual(job["status"],"Tentative")
            local=datetime.fromisoformat(job["start_at"]).astimezone(ZoneInfo("America/Toronto"))
            self.assertEqual((local.hour,local.minute),(13,0))
        finally:
            td.cleanup()

    def test_branded_email_has_safe_html_and_quote_cta(self):
        html=branded_email_html({"subject":"Your Crown Movers estimate is ready","body":"Hi <Client>\nhttps://example.com/estimate"})
        self.assertIn("VIEW ESTIMATE",html)
        self.assertIn("&lt;Client&gt;",html)
        self.assertNotIn("<Client>",html)


if __name__=="__main__":
    unittest.main()
