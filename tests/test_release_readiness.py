import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.main import create_app
from app.store import initialize, transaction
from app.platform_common import put_setting, quickbooks_gate, quickbooks_write_enabled
from app.providers import qbo_invoice_payload, qbo_mapping, qbo_read


class EnvMixin:
    KEYS = (
        "CROWNOPS_ENV",
        "CROWNOPS_ALLOW_EXTERNAL",
        "CROWNOPS_ALLOW_QBO_SANDBOX_WRITES",
        "CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES",
        "QBO_ENV",
        "QBO_SERVICE_ITEM_ID",
    )

    def setUp(self):
        self.old_env = {k: os.environ.get(k) for k in self.KEYS}
        os.environ["CROWNOPS_ENV"] = "test"
        os.environ["CROWNOPS_ALLOW_EXTERNAL"] = "0"
        os.environ["QBO_ENV"] = "sandbox"
        os.environ.pop("CROWNOPS_ALLOW_QBO_SANDBOX_WRITES", None)
        os.environ.pop("CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES", None)

    def tearDown(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class ReleaseReadinessTests(EnvMixin, unittest.TestCase):
    def test_application_starts_and_critical_routes_exist(self):
        with tempfile.TemporaryDirectory() as td:
            app = create_app(Path(td) / "crownops.sqlite3")
            routes = {(route.path, ",".join(sorted(getattr(route, "methods", ())))) for route in app.routes}
            paths = {path for path, _ in routes}
            for required in (
                "/api/health",
                "/api/connections",
                "/api/connect/{provider}",
                "/api/oauth/{provider}/callback",
                "/api/quickbooks/setup",
                "/api/quickbooks/test-connection",
                "/api/invoices/{iid}/quickbooks",
                "/api/quotes/{qid}/quickbooks",
                "/api/payments/{pid}/quickbooks",
            ):
                self.assertIn(required, paths)

    def test_quickbooks_sandbox_writes_are_off_by_default(self):
        self.assertFalse(quickbooks_write_enabled())
        with self.assertRaises(HTTPException) as ctx:
            quickbooks_gate()
        self.assertEqual(ctx.exception.status_code, 503)
        os.environ["CROWNOPS_ALLOW_QBO_SANDBOX_WRITES"] = "1"
        self.assertTrue(quickbooks_write_enabled())
        quickbooks_gate()

    def test_quickbooks_production_needs_two_explicit_conditions(self):
        os.environ["QBO_ENV"] = "production"
        os.environ["CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES"] = "1"
        os.environ["CROWNOPS_ENV"] = "test"
        self.assertFalse(quickbooks_write_enabled())
        os.environ["CROWNOPS_ENV"] = "production"
        self.assertTrue(quickbooks_write_enabled())

    def test_saved_quickbooks_mapping_beats_environment_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "crownops.sqlite3"
            initialize(db_path)
            os.environ["QBO_SERVICE_ITEM_ID"] = "env-item"
            with transaction(db_path) as db:
                put_setting(db, "quickbooks_mappings", {"service_item_id": "saved-item"})
            self.assertEqual(qbo_mapping(db_path, "service_item_id"), "saved-item")

    def test_invoice_payload_uses_reviewed_item_tax_and_discount_refs(self):
        doc = {
            "service_date": "2026-10-02",
            "terms": {
                "testimonial_credit_mode": "before_tax",
                "testimonial_credit_cents": 0,
                "customer": {"email": "sandbox@example.com"},
                "approval_reference": "Sandbox only",
            },
            "calculation": {
                "lines": [
                    {"amount_cents": 15000, "description": "Moving service", "quantity": 1, "unit_cents": 15000}
                ],
                "discount_cents": 1000,
            },
        }
        body = qbo_invoice_payload(doc, "customer-1", "item-7", "tax-3", "account-9")
        self.assertEqual(body["CustomerRef"]["value"], "customer-1")
        self.assertEqual(body["Line"][0]["SalesItemLineDetail"]["ItemRef"]["value"], "item-7")
        self.assertEqual(body["Line"][0]["SalesItemLineDetail"]["TaxCodeRef"]["value"], "tax-3")
        self.assertEqual(body["Line"][1]["DiscountLineDetail"]["DiscountAccountRef"]["value"], "account-9")
        self.assertEqual(body["CurrencyRef"]["value"], "CAD")

    def test_read_only_qbo_call_does_not_need_global_external_switch(self):
        response = Mock()
        response.ok = True
        response.json.return_value = {"CompanyInfo": {"CompanyName": "Sandbox Company"}}
        with patch("app.providers.provider_token", return_value={"access_token": "token", "realm_id": "12345"}), \
             patch("app.providers.requests.get", return_value=response) as get:
            result = qbo_read("/tmp/unused.sqlite3", "companyinfo/12345")
        self.assertEqual(result["CompanyInfo"]["CompanyName"], "Sandbox Company")
        self.assertTrue(get.called)


if __name__ == "__main__":
    unittest.main()
