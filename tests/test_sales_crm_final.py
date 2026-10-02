import tempfile
import unittest
from pathlib import Path

from app.main import create_app
from app.pricing import calculate
from app.sales_final import DEFAULT_TEMPLATES, seed_sales_final_defaults
from app.store import connect
from app.platform_common import settings


def pricing():
    return {
        "id": "P1",
        "approved": 0,
        "rates_json": [{
            "Season / Date Range": "All year",
            "Start Month": 1, "Start Day": 1, "End Month": 12, "End Day": 31,
            "2 movers": 135, "3 movers": 175, "4 movers": 240, "5 movers": 300, "6 movers": 360,
        }],
        "settings_json": {
            "morning_service_minimum": 3,
            "minimum_billable_hours": 3,
            "normal_local_fuel": 35,
            "gas_per_truck": 35,
            "time_increment_hours": .25,
        },
    }


class FinalSalesCRMTests(unittest.TestCase):
    def test_launch_routes_exist(self):
        with tempfile.TemporaryDirectory() as td:
            app = create_app(Path(td) / "crownops.sqlite3")
            paths = {route.path for route in app.routes}
            for required in (
                "/api/ux/clients",
                "/api/ux/clients/{lid}",
                "/api/ux/clients/{lid}/new-service",
                "/api/ux/quote-templates",
                "/api/ux/rate-preview",
                "/api/ux/quotes/{qid}/prepare-delivery",
                "/api/ux/outbound/{mid}/send-reviewed",
                "/api/ux/quotes/{qid}/booking-exception",
                "/api/ux/jobs/{jid}/prepare-review-request",
            ):
                self.assertIn(required, paths)

    def test_english_and_french_long_distance_templates_are_seeded(self):
        ids = {x["id"] for x in DEFAULT_TEMPLATES}
        self.assertIn("long-en", ids)
        self.assertIn("long-fr", ids)
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "crownops.sqlite3"
            create_app(db_path)
            seed_sales_final_defaults(db_path)
            with connect(db_path) as db:
                saved = settings(db, "sales_quote_templates_v1", [])
            self.assertTrue(any(x["id"] == "long-en" for x in saved))
            self.assertTrue(any(x["id"] == "long-fr" for x in saved))

    def test_manager_rate_override_and_fixed_long_distance_transport(self):
        out = calculate({
            "move_date": "2026-10-15", "movers": 3, "trucks": 2,
            "hourly_rate_override": 175, "hours_low": 3, "hours_high": 5,
            "allowance_percent": 0, "transport_hours": 14, "transport_fixed_amount": 2450,
            "long_distance_km": 500, "long_distance_gas_per_km": .94,
            "minimum_key": "morning_service_minimum", "fuel_key": "normal_local_fuel",
            "materials_amount": 0, "other_amount": 0, "discount_amount": 0,
            "service_type": "Moving",
        }, pricing())
        self.assertEqual(out["hourly_rate_cents"], 17500)
        self.assertEqual(out["transport_cents"], 245000)
        self.assertEqual(out["fuel_cents"], 94000)
        self.assertEqual(out["low_cents"], 391500)
        self.assertEqual(out["high_cents"], 426500)

    def test_second_local_truck_adds_25_transport_and_second_35_gas(self):
        out = calculate({
            "move_date": "2026-10-15", "movers": 2, "trucks": 2,
            "hours_low": 3, "hours_high": 3, "allowance_percent": 0,
            "transport_hours": 1, "extra_truck_transport_fee": 25,
            "minimum_key": "morning_service_minimum", "fuel_key": "normal_local_fuel",
            "materials_amount": 0, "other_amount": 0, "discount_amount": 0,
            "service_type": "Moving",
        }, pricing())
        self.assertEqual(out["transport_cents"], 16000)
        self.assertEqual(out["fuel_cents"], 7000)

    def test_junk_removal_guide_and_extra_labour(self):
        out = calculate({
            "move_date": "2026-10-15", "movers": 2, "trucks": 1,
            "minimum_key": "morning_service_minimum", "fuel_key": "normal_local_fuel",
            "service_type": "Junk Removal", "junk_base_amount": 175,
            "junk_extra_half_hours": 2, "materials_amount": 0,
            "other_amount": 0, "discount_amount": 0,
        }, pricing())
        self.assertEqual(out["low_cents"], 33500)
        self.assertEqual(out["high_cents"], 33500)
        self.assertEqual(out["service_type"], "Junk Removal")


if __name__ == "__main__":
    unittest.main()
