import os
import tempfile
import json
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import create_app, sha
from app.store import connect, initialize, transaction, insert, one, dumps, now
from app.platform_common import put_setting, quickbooks_gate, quickbooks_write_enabled, settings, stamp_hash
from app.providers import qbo_customer, qbo_invoice_payload, qbo_mapping, qbo_read
from app.backups import make_backup, restore_archive
from app.security import validate_deployment
from app.operations import hos_dispatch_status, hos_cycle_capacity_errors
from app.gmail_sync import promote_pending_call_events


class EnvMixin:
    KEYS = (
        "CROWNOPS_ENV",
        "CROWNOPS_ALLOW_EXTERNAL",
        "CROWNOPS_ALLOW_QBO_SANDBOX_WRITES",
        "CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES",
        "QBO_ENV",
        "QBO_SERVICE_ITEM_ID",
        "CROWNOPS_SECRET_KEY",
        "CROWNOPS_SCAN_COMMAND",
        "CROWNOPS_UPLOADS_ENABLED",
        "CROWNOPS_UPLOADS",
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
                "/api/gmail/test-readonly",
                "/api/ux/action-assignees",
                "/api/ux/actions/{kind}/{action_id}/claim",
                "/api/ux/actions/{kind}/{action_id}/assign",
                "/api/ux/actions/{kind}/{action_id}/workspace",
                "/api/ux/actions/{kind}/{action_id}/outcome",
                "/api/invoices/{iid}/quickbooks",
                "/api/quotes/{qid}/quickbooks",
                "/api/payments/{pid}/quickbooks",
            ):
                self.assertIn(required, paths)

    def test_today_action_center_filters_current_work_by_owner_and_groups_gmail_payments(self):
        with tempfile.TemporaryDirectory() as td:
            db_path=Path(td)/"crownops.sqlite3"
            app=create_app(db_path)
            now_utc=datetime.now(timezone.utc)
            local_day=datetime.now(timezone.utc).astimezone(ZoneInfo('America/Toronto')).date()
            today_local=local_day.isoformat()
            tomorrow=(local_day+timedelta(days=1)).isoformat()
            expiry=(now_utc+timedelta(days=1)).isoformat(timespec="seconds")
            with transaction(db_path) as db:
                insert(db,"users",dict(id="USER-OWNER-TODAY",email="owner.today@example.invalid",name="Owner Today",role="owner",
                    password_hash="unused-test-hash",active=1,created_at=now()))
                insert(db,"users",dict(id="USER-SALES-TODAY",email="sales.today@example.invalid",name="Sales Today",role="sales",
                    password_hash="unused-test-hash",active=1,created_at=now()))
                insert(db,"sessions",dict(token_hash=sha("owner-today-session"),user_id="USER-OWNER-TODAY",csrf="csrf-owner",expires_at=expiry))
                insert(db,"sessions",dict(token_hash=sha("sales-today-session"),user_id="USER-SALES-TODAY",csrf="csrf-sales",expires_at=expiry))
                insert(db,"leads",dict(id="LEAD-TODAY-ASSIGNED",client_name="Assigned Client",assigned_to="USER-SALES-TODAY",
                    created_at=now(),updated_at=now()))
                insert(db,"leads",dict(id="LEAD-TODAY-OPEN",client_name="Open Client",
                    created_at=now(),updated_at=now()))
                insert(db,"tasks",dict(id="TASK-TODAY-MINE",lead_id="LEAD-TODAY-ASSIGNED",title="Call assigned client",
                    due_date=today_local,owner="USER-SALES-TODAY",status="Open",historical=0,created_at=now()))
                insert(db,"tasks",dict(id="TASK-TODAY-OPEN",lead_id="LEAD-TODAY-OPEN",title="Call unclaimed client",
                    due_date=today_local,owner="",status="Open",historical=0,created_at=now()))
                insert(db,"tasks",dict(id="TASK-TODAY-FUTURE",lead_id="LEAD-TODAY-OPEN",title="Future follow-up",
                    due_date=tomorrow,owner="",status="Open",historical=0,created_at=now()))
                insert(db,"tasks",dict(id="TASK-TODAY-HISTORY",lead_id="LEAD-TODAY-OPEN",title="Imported history",
                    due_date=today_local,owner="",status="Open",historical=1,created_at=now()))
                insert(db,"tasks",dict(id="TASK-TODAY-DONE",lead_id="LEAD-TODAY-OPEN",title="Already done",
                    due_date=today_local,owner="",status="Done",historical=0,created_at=now()))
                for n in (1,2):
                    insert(db,"integration_events",dict(id=f"GMAIL-PAY-TODAY-{n}",event_key=f"gmail:pay:today:{n}",
                        event_type="gmail_bank_payment",object_id=f"gmail-pay-{n}",
                        payload_json=dumps({"parsed":{"payer":f"Payer {n}","amount":"300.00"}}),
                        status="Needs review",created_at=now()))
            owner=TestClient(app);owner.cookies.set("crownops_session","owner-today-session")
            team=owner.get("/api/ux/today?view=team")
            self.assertEqual(team.status_code,200,team.text)
            team_body=team.json()
            self.assertEqual(team_body["view_counts"],{"unclaimed":2,"mine":0,"team":3})
            self.assertEqual(team_body["stats"]["attention"],3)
            self.assertEqual(team_body["stats"]["open_followups"],2)
            self.assertEqual(team_body["stats"]["unverified_payments"],1)
            self.assertEqual(len([x for x in team_body["attention"] if x["type"]=="gmail-payment-batch"]),1)
            self.assertFalse(any(x["id"] in ("TASK-TODAY-FUTURE","TASK-TODAY-HISTORY","TASK-TODAY-DONE") for x in team_body["attention"]))

            unclaimed=owner.get("/api/ux/today?view=unclaimed").json()
            self.assertEqual(unclaimed["stats"]["attention"],2)
            self.assertTrue(all(x["ownership"]=="unclaimed" for x in unclaimed["attention"]))

            sales=TestClient(app);sales.cookies.set("crownops_session","sales-today-session")
            mine=sales.get("/api/ux/today?view=mine")
            self.assertEqual(mine.status_code,200,mine.text)
            mine_body=mine.json()
            self.assertEqual(mine_body["stats"]["attention"],1)
            self.assertEqual(mine_body["attention"][0]["id"],"TASK-TODAY-MINE")
            self.assertEqual(mine_body["attention"][0]["owner_name"],"Sales Today")

    def test_action_claim_and_manager_reassignment_keep_client_work_together(self):
        with tempfile.TemporaryDirectory() as td:
            db_path=Path(td)/"crownops.sqlite3"
            app=create_app(db_path)
            expiry=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(timespec="seconds")
            local_day=datetime.now(timezone.utc).astimezone(ZoneInfo('America/Toronto')).date().isoformat()
            with transaction(db_path) as db:
                for user_id,email,name,role,active in (
                    ("USER-ACTION-OWNER","action.owner@example.invalid","Action Owner","owner",1),
                    ("USER-ACTION-A","action.a@example.invalid","Agent A","sales",1),
                    ("USER-ACTION-B","action.b@example.invalid","Agent B","sales",1),
                    ("USER-ACTION-CREW","action.crew@example.invalid","Crew User","crew",1),
                    ("USER-ACTION-OFF","action.off@example.invalid","Inactive Agent","sales",0),
                ):
                    insert(db,"users",dict(id=user_id,email=email,name=name,role=role,password_hash="unused-test-hash",active=active,created_at=now()))
                for token,user_id,csrf in (
                    ("action-owner-session","USER-ACTION-OWNER","csrf-owner"),
                    ("action-a-session","USER-ACTION-A","csrf-a"),
                    ("action-b-session","USER-ACTION-B","csrf-b"),
                ):
                    insert(db,"sessions",dict(token_hash=sha(token),user_id=user_id,csrf=csrf,expires_at=expiry))
                insert(db,"leads",dict(id="LEAD-ACTION",client_name="Action Client",assigned_to="",created_at=now(),updated_at=now()))
                insert(db,"tasks",dict(id="TASK-ACTION-1",lead_id="LEAD-ACTION",title="Return client call",
                    due_date=local_day,owner="",status="Open",historical=0,created_at=now()))
                insert(db,"tasks",dict(id="TASK-ACTION-2",lead_id="LEAD-ACTION",title="Send estimate",
                    due_date=local_day,owner="",status="Open",historical=0,created_at=now()))
                insert(db,"messages",dict(id="MSG-ACTION",lead_id="LEAD-ACTION",kind="Incoming email",
                    subject="Client reply",body="Please call me.",language="English",author="sync",created_at=now()))
                insert(db,"integration_events",dict(id="GMAIL-PAY-ACTION",event_key="gmail:pay:action",
                    event_type="gmail_bank_payment",object_id="gmail-pay-action",
                    payload_json=dumps({"parsed":{"payer":"Action Payer","amount":"300.00"}}),
                    status="Needs review",created_at=now()))

            agent_a=TestClient(app);agent_a.cookies.set("crownops_session","action-a-session");agent_a.headers.update({"x-csrf-token":"csrf-a"})
            claimed=agent_a.post("/api/ux/actions/task/TASK-ACTION-1/claim")
            self.assertEqual(claimed.status_code,200,claimed.text)
            with transaction(db_path) as db:
                lead=one(db,"SELECT assigned_to FROM leads WHERE id='LEAD-ACTION'")
                self.assertEqual(lead["assigned_to"],"USER-ACTION-A")
                task_owners={r[0] for r in db.execute("SELECT owner FROM tasks WHERE lead_id='LEAD-ACTION' AND status!='Done'").fetchall()}
                self.assertEqual(task_owners,{"USER-ACTION-A"})
                inbox=one(db,"SELECT claimed_by,resolved_at FROM inbox_claims WHERE message_id='MSG-ACTION'")
                self.assertEqual(inbox["claimed_by"],"USER-ACTION-A")
                self.assertIsNone(inbox["resolved_at"])

            agent_b=TestClient(app);agent_b.cookies.set("crownops_session","action-b-session");agent_b.headers.update({"x-csrf-token":"csrf-b"})
            conflict=agent_b.post("/api/ux/actions/task/TASK-ACTION-1/claim")
            self.assertEqual(conflict.status_code,409,conflict.text)
            forbidden=agent_a.post("/api/ux/actions/task/TASK-ACTION-1/assign",json={"user_id":"USER-ACTION-B"})
            self.assertEqual(forbidden.status_code,403,forbidden.text)

            owner=TestClient(app);owner.cookies.set("crownops_session","action-owner-session");owner.headers.update({"x-csrf-token":"csrf-owner"})
            reassigned=owner.post("/api/ux/actions/task/TASK-ACTION-1/assign",json={"user_id":"USER-ACTION-B"})
            self.assertEqual(reassigned.status_code,200,reassigned.text)
            with transaction(db_path) as db:
                self.assertEqual(one(db,"SELECT assigned_to FROM leads WHERE id='LEAD-ACTION'")["assigned_to"],"USER-ACTION-B")
                task_owners={r[0] for r in db.execute("SELECT owner FROM tasks WHERE lead_id='LEAD-ACTION' AND status!='Done'").fetchall()}
                self.assertEqual(task_owners,{"USER-ACTION-B"})
                self.assertEqual(one(db,"SELECT claimed_by FROM inbox_claims WHERE message_id='MSG-ACTION'")["claimed_by"],"USER-ACTION-B")
                audit_count=db.execute("SELECT count(*) FROM audit WHERE action='Action reassigned'").fetchone()[0]
                self.assertGreaterEqual(audit_count,1)

            batch_claim=agent_a.post("/api/ux/actions/gmail-payment-batch/gmail-payment-review/claim")
            self.assertEqual(batch_claim.status_code,200,batch_claim.text)
            batch_conflict=agent_b.post("/api/ux/actions/gmail-payment-batch/gmail-payment-review/claim")
            self.assertEqual(batch_conflict.status_code,409,batch_conflict.text)
            batch_assign=owner.post("/api/ux/actions/gmail-payment-batch/gmail-payment-review/assign",json={"user_id":"USER-ACTION-B"})
            self.assertEqual(batch_assign.status_code,200,batch_assign.text)
            mine=agent_b.get("/api/ux/today?view=mine")
            self.assertEqual(mine.status_code,200,mine.text)
            ids={x["id"] for x in mine.json()["attention"]}
            self.assertIn("TASK-ACTION-1",ids)
            self.assertIn("TASK-ACTION-2",ids)
            self.assertIn("gmail-payment-review",ids)

            assignees=owner.get("/api/ux/action-assignees")
            self.assertEqual(assignees.status_code,200,assignees.text)
            assignee_ids={x["id"] for x in assignees.json()}
            self.assertIn("USER-ACTION-A",assignee_ids)
            self.assertIn("USER-ACTION-B",assignee_ids)
            self.assertNotIn("USER-ACTION-CREW",assignee_ids)
            self.assertNotIn("USER-ACTION-OFF",assignee_ids)

    def test_action_workspace_shows_call_context_and_records_next_outcomes(self):
        with tempfile.TemporaryDirectory() as td:
            db_path=Path(td)/"crownops.sqlite3"
            app=create_app(db_path)
            expiry=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(timespec="seconds")
            local_day=datetime.now(timezone.utc).astimezone(ZoneInfo('America/Toronto')).date()
            follow_day=(local_day+timedelta(days=2)).isoformat()
            with transaction(db_path) as db:
                insert(db,"users",dict(id="USER-WORKSPACE",email="workspace@example.invalid",name="Workspace Agent",role="sales",
                    password_hash="unused-test-hash",active=1,created_at=now()))
                insert(db,"sessions",dict(token_hash=sha("workspace-session"),user_id="USER-WORKSPACE",csrf="csrf-workspace",expires_at=expiry))
                insert(db,"leads",dict(id="LEAD-WORKSPACE",client_name="Workspace Client",email="client@example.invalid",
                    phone="5145557788",move_date="2026-11-15",pickup="100 Test Street",dropoff="200 Test Street",
                    stage="New Lead",assigned_to="USER-WORKSPACE",created_at=now(),updated_at=now()))
                insert(db,"calls",dict(id="CALL-WORKSPACE",legacy_id=None,lead_id="LEAD-WORKSPACE",
                    payload_json=dumps({"phone":"5145557788","caller":"Workspace Client","call_time":"10/06/2026 13:15:44",
                        "occurred_at":"2026-10-06T17:15:44+00:00","subject":"Missed call from Workspace Client","voicemail":False,"attachments":[]}),
                    created_at="2026-10-06T17:15:44+00:00"))
                insert(db,"tasks",dict(id="TASK-WORKSPACE",legacy_id=None,lead_id="LEAD-WORKSPACE",title="Return missed call",
                    due_date=local_day.isoformat(),owner="USER-WORKSPACE",status="Open",historical=0,
                    raw_json=dumps({"call_id":"CALL-WORKSPACE"}),created_at=now()))
                insert(db,"tasks",dict(id="TASK-WORKSPACE-OTHER",legacy_id=None,lead_id="LEAD-WORKSPACE",title="Confirm inventory",
                    due_date=local_day.isoformat(),owner="USER-WORKSPACE",status="Open",historical=0,raw_json="{}",created_at=now()))
            client=TestClient(app);client.cookies.set("crownops_session","workspace-session");client.headers.update({"x-csrf-token":"csrf-workspace"})

            workspace=client.get("/api/ux/actions/task/TASK-WORKSPACE/workspace")
            self.assertEqual(workspace.status_code,200,workspace.text)
            body=workspace.json()
            self.assertEqual(body["client"]["client_name"],"Workspace Client")
            self.assertEqual(body["owner"]["name"],"Workspace Agent")
            self.assertEqual(body["action"]["call"]["phone"],"5145557788")
            self.assertEqual(body["action"]["call"]["occurred_at"],"2026-10-06T17:15:44+00:00")
            self.assertFalse(body["action"]["call"]["voicemail"])
            self.assertEqual(len(body["open_tasks"]),2)

            later=client.post("/api/ux/actions/task/TASK-WORKSPACE/outcome",json={
                "outcome":"followup","followup_date":follow_day,"followup_title":"Call client after review","note":"Client asked for time."
            })
            self.assertEqual(later.status_code,200,later.text)
            next_task=later.json()["followup_task_id"]
            self.assertTrue(next_task)
            with transaction(db_path) as db:
                self.assertEqual(one(db,"SELECT status FROM tasks WHERE id='TASK-WORKSPACE'")["status"],"Done")
                follow=one(db,"SELECT * FROM tasks WHERE id=?",(next_task,))
                self.assertEqual(follow["due_date"],follow_day)
                self.assertEqual(follow["owner"],"USER-WORKSPACE")
                self.assertEqual(follow["title"],"Call client after review")
                recorded=one(db,"SELECT * FROM action_outcomes WHERE action_id='TASK-WORKSPACE' ORDER BY created_at DESC LIMIT 1")
                self.assertEqual(recorded["outcome"],"followup")
                self.assertEqual(recorded["followup_task_id"],next_task)

            lost=client.post(f"/api/ux/actions/task/{next_task}/outcome",json={"outcome":"lost","note":"Client is no longer moving."})
            self.assertEqual(lost.status_code,200,lost.text)
            with transaction(db_path) as db:
                self.assertEqual(one(db,"SELECT stage FROM leads WHERE id='LEAD-WORKSPACE'")["stage"],"Lost")
                remaining=db.execute("SELECT count(*) FROM tasks WHERE lead_id='LEAD-WORKSPACE' AND status!='Done' AND historical=0").fetchone()[0]
                self.assertEqual(remaining,0)
                outcomes=db.execute("SELECT count(*) FROM action_outcomes WHERE lead_id='LEAD-WORKSPACE'").fetchone()[0]
                self.assertEqual(outcomes,2)

    def test_action_workspace_email_outcomes_resolve_email_and_update_stage(self):
        with tempfile.TemporaryDirectory() as td:
            db_path=Path(td)/"crownops.sqlite3"
            app=create_app(db_path)
            expiry=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(timespec="seconds")
            with transaction(db_path) as db:
                insert(db,"users",dict(id="USER-EMAIL-OUTCOME",email="email.outcome@example.invalid",name="Email Agent",role="sales",
                    password_hash="unused-test-hash",active=1,created_at=now()))
                insert(db,"sessions",dict(token_hash=sha("email-outcome-session"),user_id="USER-EMAIL-OUTCOME",csrf="csrf-email-outcome",expires_at=expiry))
                insert(db,"leads",dict(id="LEAD-EMAIL-OUTCOME",client_name="Email Client",email="emailclient@example.invalid",
                    assigned_to="USER-EMAIL-OUTCOME",stage="New Lead",created_at=now(),updated_at=now()))
                insert(db,"messages",dict(id="MSG-EMAIL-OUTCOME",lead_id="LEAD-EMAIL-OUTCOME",kind="Incoming email",
                    subject="Question about my move",body="Can you send me the estimate again?",language="English",author="sync",created_at=now()))
                insert(db,"inbox_claims",dict(message_id="MSG-EMAIL-OUTCOME",claimed_by="USER-EMAIL-OUTCOME",claimed_at=now(),resolved_at=None))
            client=TestClient(app);client.cookies.set("crownops_session","email-outcome-session");client.headers.update({"x-csrf-token":"csrf-email-outcome"})
            workspace=client.get("/api/ux/actions/email/MSG-EMAIL-OUTCOME/workspace")
            self.assertEqual(workspace.status_code,200,workspace.text)
            self.assertIn("estimate again",workspace.json()["action"]["body"])
            sent=client.post("/api/ux/actions/email/MSG-EMAIL-OUTCOME/outcome",json={"outcome":"quote_sent","note":"Resent estimate manually."})
            self.assertEqual(sent.status_code,200,sent.text)
            with transaction(db_path) as db:
                self.assertEqual(one(db,"SELECT stage FROM leads WHERE id='LEAD-EMAIL-OUTCOME'")["stage"],"Quote Sent")
                self.assertIsNotNone(one(db,"SELECT resolved_at FROM inbox_claims WHERE message_id='MSG-EMAIL-OUTCOME'")["resolved_at"])
                self.assertEqual(one(db,"SELECT outcome FROM action_outcomes WHERE action_id='MSG-EMAIL-OUTCOME'")["outcome"],"quote_sent")

    def test_gmail_readonly_diagnostic_reports_actual_account_and_required_labels(self):
        with tempfile.TemporaryDirectory() as td:
            db_path=Path(td)/"crownops.sqlite3"
            app=create_app(db_path)
            token="gmail-diagnostic-session";csrf="gmail-diagnostic-csrf"
            expiry=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(timespec="seconds")
            with transaction(db_path) as db:
                insert(db,"users",dict(id="USER-GMAIL-DIAG",email="owner@example.invalid",name="Owner",role="owner",
                    password_hash="unused-test-hash",active=1,created_at=now()))
                insert(db,"sessions",dict(token_hash=sha(token),user_id="USER-GMAIL-DIAG",csrf=csrf,expires_at=expiry))
            client=TestClient(app);client.cookies.set("crownops_session",token)
            responses=[
                {"emailAddress":"crown@example.invalid","messagesTotal":100,"threadsTotal":50},
                {"labels":[
                    {"name":"New Quote Request","id":"LQ"},
                    {"name":"Missed Calls","id":"LM"},
                    {"name":"Voice Mail","id":"LV"}]},
                {"resultSizeEstimate":7},{"resultSizeEstimate":3},{"resultSizeEstimate":2},
            ]
            with patch("app.providers.provider_token",return_value={"access_token":"test-token"}),                  patch("app.providers.gmail_sync._api",side_effect=responses):
                r=client.get("/api/gmail/test-readonly")
            self.assertEqual(r.status_code,200,r.text)
            body=r.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["email"],"crown@example.invalid")
            self.assertEqual(body["feeds"]["quote"]["recent_14d"],7)
            self.assertEqual(body["feeds"]["missed_call"]["recent_14d"],3)
            self.assertEqual(body["feeds"]["voicemail"]["recent_14d"],2)
            self.assertEqual(body["missing_labels"],[])

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
                put_setting(db, "quickbooks_mappings_sandbox", {"service_item_id": "saved-item"})
            self.assertEqual(qbo_mapping(db_path, "service_item_id"), "saved-item")

    def test_sandbox_mapping_never_leaks_into_production(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "crownops.sqlite3"
            initialize(db_path)
            with transaction(db_path) as db:
                put_setting(db, "quickbooks_mappings_sandbox", {"service_item_id": "sandbox-item"})
            self.assertEqual(qbo_mapping(db_path, "service_item_id"), "sandbox-item")
            os.environ["QBO_ENV"] = "production"
            os.environ.pop("QBO_SERVICE_ITEM_ID", None)
            self.assertEqual(qbo_mapping(db_path, "service_item_id"), "")

    def test_production_mapping_is_separate_and_can_be_prepared_while_writes_are_off(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "crownops.sqlite3"
            initialize(db_path)
            with transaction(db_path) as db:
                put_setting(db, "quickbooks_mappings_sandbox", {"service_item_id": "sandbox-item"})
                put_setting(db, "quickbooks_mappings_production", {"service_item_id": "production-item"})
            os.environ["QBO_ENV"] = "production"
            os.environ["CROWNOPS_ENV"] = "production"
            os.environ.pop("CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES", None)
            self.assertFalse(quickbooks_write_enabled())
            self.assertEqual(qbo_mapping(db_path, "service_item_id"), "production-item")


    def test_invoice_payload_uses_reviewed_item_tax_and_discount_refs(self):
        doc = {
            "id": "INV-TEST-1",
            "revision": 1,
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

    def test_qbo_customer_omits_blank_contact_fields(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "crownops.sqlite3"
            initialize(db_path)
            lead = {"id": "LEAD-1234567890123456", "client_name": "Sandbox Customer", "email": "", "phone": "514-555-0101"}
            with patch("app.providers.qbo_call", return_value={"Customer": {"Id": "77"}}) as call:
                external_id = qbo_customer(db_path, lead)
            self.assertEqual(external_id, "77")
            payload = call.call_args.args[3]
            self.assertNotIn("PrimaryEmailAddr", payload)
            self.assertEqual(payload["PrimaryPhone"]["FreeFormNumber"], "514-555-0101")

    def test_backup_restore_round_trip_preserves_data_and_uploads(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "source" / "crownops.sqlite3"
            uploads = root / "source" / "uploads"
            uploads.mkdir(parents=True)
            initialize(db_path)
            with transaction(db_path) as db:
                put_setting(db, "round_trip_probe", {"ok": True})
            (uploads / "proof.txt").write_text("backup-ok", encoding="utf-8")
            archive = root / "backup.zip"
            make_backup(db_path, archive, uploads)
            restored = root / "restored" / "crownops.sqlite3"
            restore_archive(archive, restored)
            with connect(restored) as db:
                self.assertEqual(settings(db, "round_trip_probe"), {"ok": True})
            self.assertEqual((restored.parent / "uploads" / "proof.txt").read_text(encoding="utf-8"), "backup-ok")

    def test_production_deployment_guard_requires_https_and_scanner_for_uploads(self):
        os.environ["CROWNOPS_ENV"] = "production"
        with self.assertRaises(RuntimeError):
            validate_deployment("http://example.invalid", False)
        os.environ["CROWNOPS_SECRET_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
        os.environ.pop("CROWNOPS_SCAN_COMMAND", None)
        os.environ["CROWNOPS_UPLOADS_ENABLED"] = "0"
        validate_deployment("https://example.invalid", True)
        os.environ["CROWNOPS_UPLOADS_ENABLED"] = "1"
        with self.assertRaises(RuntimeError) as ctx:
            validate_deployment("https://example.invalid", True)
        self.assertIn("malware scanning", str(ctx.exception))

    def _make_hos_job(self, db_path):
        initialize(db_path)
        start=(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat(timespec="seconds")
        end=(datetime.now(timezone.utc)+timedelta(hours=39)).isoformat(timespec="seconds")
        with transaction(db_path) as db:
            insert(db,"leads",dict(id="LEAD-HOS",client_name="Long Distance Client",created_at=now(),updated_at=now()))
            for eid,name in (("DRV-1","Driver One"),("DRV-2","Driver Two")):
                insert(db,"employees",dict(id=eid,name=name,role="Mover",active=1,driver_verified=1))
            for vid,name in (("TRUCK-1","Truck One"),("TRUCK-2","Truck Two")):
                insert(db,"vehicles",dict(id=vid,name=name,active=1))
                insert(db,"fleet_profiles",dict(vehicle_id=vid,gvwr_kg=7000,updated_at=now()))
            insert(db,"pricing_versions",dict(id="PRICE-HOS",label="test",rates_json="{}",settings_json="{}",created_at=now()))
            insert(db,"quotes",dict(id="QUOTE-HOS",lead_id="LEAD-HOS",revision=1,status="Accepted",
                inputs_json=dumps({"service_type":"Long Distance","transport_hours":12,"deposit_required":300}),
                result_json=dumps({"service_type":"Long Distance","service_high":5,"transport_hours":12,
                    "hos_plan":{"estimated_on_duty_hours":17,"minimum_schedule_days":2}}),
                pricing_id="PRICE-HOS",lead_snapshot_json="{}",lead_version=1,created_by="test",created_at=now()))
            insert(db,"jobs",dict(id="JOB-HOS",lead_id="LEAD-HOS",quote_id="QUOTE-HOS",title="Toronto",
                phase="Long Distance",start_at=start,end_at=end,status="Confirmed",created_by="test",created_at=now()))
            for eid in ("DRV-1","DRV-2"):insert(db,"assignments",dict(job_id="JOB-HOS",resource_type="employee",resource_id=eid))
            for vid in ("TRUCK-1","TRUCK-2"):insert(db,"assignments",dict(job_id="JOB-HOS",resource_type="vehicle",resource_id=vid))

    def _add_hos_verification(self,db,driver,truck,job_version=1):
        insert(db,"hos_dispatch_verifications",dict(id="HOS-"+driver+"-"+truck+"-"+str(job_version),job_id="JOB-HOS",
            job_version=job_version,driver_id=driver,vehicle_id=truck,cycle=1,cycle_on_duty_hours=30,
            on_duty_since_last_24h_off=30,consecutive_off_duty_before_shift=8,had_24h_off_in_prev_14d=1,
            dce_log_checked=1,daily_off_duty_plan_checked=1,elapsed_window_checked=1,projected_on_duty_hours=17,
            status="Verified",note="Current DCE reviewed for dispatch.",verified_by="dispatcher",verified_at=now()))

    def test_crown_tax_defaults_and_deferred_fleet_document_setup(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            os.environ["CROWNOPS_UPLOADS"]=str(root/"uploads")
            db_path=root/"crownops.sqlite3"
            app=create_app(db_path)
            token="fleet-owner-session";csrf="fleet-owner-csrf"
            expiry=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(timespec="seconds")
            with transaction(db_path) as db:
                insert(db,"users",dict(id="USER-FLEET",email="fleet-owner@example.invalid",name="Fleet Owner",role="owner",
                    password_hash="unused-test-hash",active=1,created_at=now()))
                insert(db,"sessions",dict(token_hash=sha(token),user_id="USER-FLEET",csrf=csrf,expires_at=expiry))
                insert(db,"vehicles",dict(id="TRUCK-DEFERRED",name="Deferred Fleet Truck",active=1))
                insert(db,"fleet_profiles",dict(vehicle_id="TRUCK-DEFERRED",inspection_interval_hours=24,
                    check_required=0,updated_at=now()))

            client=TestClient(app);client.cookies.set("crownops_session",token)
            headers={"x-csrf-token":csrf}

            settings_response=client.get("/api/platform/settings")
            self.assertEqual(settings_response.status_code,200,settings_response.text)
            billing=settings_response.json()["billing_policy"]
            self.assertFalse(bool(billing.get("approved")))
            self.assertEqual(billing["terms"]["issuer"]["name"],"9411-8213 Quebec Inc. / Déménagement Crown / Crown Movers")
            self.assertEqual(billing["terms"]["issuer"]["address"],"4030 Blvd. de la Côte-Vertu, Suite 111, Montréal, QC H4R 1V4")
            taxes=billing["terms"]["taxes"]
            self.assertEqual(taxes[0],{"name":"GST","rate_percent":5.0,"registration":"737446534RT001"})
            self.assertEqual(taxes[1],{"name":"QST","rate_percent":9.975,"registration":"1227197265TQ001"})

            readiness=client.get("/api/readiness")
            self.assertEqual(readiness.status_code,200,readiness.text)
            fleet_item=next(x for x in readiness.json()["launch"]["items"] if x["key"]=="fleet_gvwr")
            self.assertFalse(fleet_item["ready"])
            self.assertFalse(fleet_item["critical"])
            self.assertIn("TRUCK-DEFERRED",[x["id"] for x in readiness.json()["launch"]["missing_gvwr"]])

            pdf=b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
            uploaded=client.post("/api/fleet/TRUCK-DEFERRED/documents",headers=headers,
                files={"file":("registration.pdf",pdf,"application/pdf")},
                data={"category":"Registration"})
            self.assertEqual(uploaded.status_code,200,uploaded.text)
            doc=uploaded.json();self.assertEqual(doc["scan_status"],"Quarantined")

            fleet=client.get("/api/fleet")
            self.assertEqual(fleet.status_code,200,fleet.text)
            truck=next(x for x in fleet.json() if x["id"]=="TRUCK-DEFERRED")
            self.assertEqual(len(truck["documents"]),1)
            self.assertEqual(truck["documents"][0]["id"],doc["id"])

            blocked=client.get("/api/fleet-documents/"+doc["id"])
            self.assertEqual(blocked.status_code,409)
            reviewed=client.post("/api/fleet-documents/"+doc["id"]+"/review",headers=headers,
                json={"decision":"Clean","note":"Test-only safety review for fleet document."})
            self.assertEqual(reviewed.status_code,200,reviewed.text)
            downloaded=client.get("/api/fleet-documents/"+doc["id"])
            self.assertEqual(downloaded.status_code,200,downloaded.text)
            self.assertEqual(downloaded.content,pdf)

    def test_hos_cycle_capacity_blocks_insufficient_cycle_hours(self):
        self.assertEqual(hos_cycle_capacity_errors(1,50,50,17),[])
        self.assertTrue(any("Cycle 1" in x for x in hos_cycle_capacity_errors(1,60,60,17)))
        self.assertTrue(any("70 on-duty hours" in x for x in hos_cycle_capacity_errors(2,90,60,17)))
        self.assertTrue(any("Cycle 2" in x for x in hos_cycle_capacity_errors(2,110,20,17)))

    def test_long_distance_dispatch_requires_current_hos_record_per_heavy_truck(self):
        with tempfile.TemporaryDirectory() as td:
            db_path=Path(td)/"crownops.sqlite3"
            self._make_hos_job(db_path)
            with transaction(db_path) as db:
                status=hos_dispatch_status(db,"JOB-HOS")
                self.assertTrue(status["required"]);self.assertFalse(status["ready"])
                self._add_hos_verification(db,"DRV-1","TRUCK-1")
                self.assertFalse(hos_dispatch_status(db,"JOB-HOS")["ready"])
                self._add_hos_verification(db,"DRV-2","TRUCK-2")
                self.assertTrue(hos_dispatch_status(db,"JOB-HOS")["ready"])
                db.execute("UPDATE jobs SET version=version+1 WHERE id='JOB-HOS'")
                self.assertFalse(hos_dispatch_status(db,"JOB-HOS")["ready"])

    def test_missing_heavy_truck_gvwr_blocks_hos_readiness(self):
        with tempfile.TemporaryDirectory() as td:
            db_path=Path(td)/"crownops.sqlite3"
            self._make_hos_job(db_path)
            with transaction(db_path) as db:
                db.execute("UPDATE fleet_profiles SET gvwr_kg=NULL WHERE vehicle_id='TRUCK-2'")
                status=hos_dispatch_status(db,"JOB-HOS")
                self.assertFalse(status["ready"])
                self.assertTrue(any("GVWR" in x and "Truck Two" in x for x in status["blockers"]))

    def test_sales_clients_are_ranked_by_newest_form_call_or_voicemail_activity(self):
        with tempfile.TemporaryDirectory() as td:
            db_path=Path(td)/"crownops.sqlite3"
            app=create_app(db_path)
            token="sales-inbox-session";csrf="sales-inbox-csrf"
            expiry=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(timespec="seconds")
            with transaction(db_path) as db:
                insert(db,"users",dict(id="USER-INBOX",email="inbox@example.invalid",name="Inbox Owner",role="owner",
                    password_hash="unused-test-hash",active=1,created_at=now()))
                insert(db,"sessions",dict(token_hash=sha(token),user_id="USER-INBOX",csrf=csrf,expires_at=expiry))
                insert(db,"leads",dict(id="LEAD-FORM",client_name="Fresh Form",email="form@example.invalid",phone="5145551001",
                    move_date="2026-11-01",source="Website",lead_type="Form",stage="New Lead",archived_import=0,
                    notifications_suppressed=0,raw_json="{}",version=1,created_at="2026-10-05T12:00:00+00:00",updated_at="2026-10-05T12:00:00+00:00"))
                insert(db,"leads",dict(id="LEAD-VOICE",client_name="Older Client",email="voice@example.invalid",phone="5145551002",
                    move_date="2026-10-20",source="Phone",lead_type="Moving",stage="Contacted",archived_import=0,
                    notifications_suppressed=0,raw_json="{}",version=1,created_at="2026-09-20T12:00:00+00:00",updated_at="2026-09-20T12:00:00+00:00"))
                insert(db,"client_accounts",dict(id="CLIENT-FORM",identity_key="e:form@example.invalid",client_name="Fresh Form",
                    email="form@example.invalid",phone="5145551001",language="English",created_at="2026-10-05T12:00:00+00:00",updated_at="2026-10-05T12:00:00+00:00"))
                insert(db,"client_accounts",dict(id="CLIENT-VOICE",identity_key="e:voice@example.invalid",client_name="Older Client",
                    email="voice@example.invalid",phone="5145551002",language="English",created_at="2026-09-20T12:00:00+00:00",updated_at="2026-09-20T12:00:00+00:00"))
                insert(db,"client_account_leads",dict(client_id="CLIENT-FORM",lead_id="LEAD-FORM",created_at="2026-10-05T12:00:00+00:00"))
                insert(db,"client_account_leads",dict(client_id="CLIENT-VOICE",lead_id="LEAD-VOICE",created_at="2026-09-20T12:00:00+00:00"))
                insert(db,"calls",dict(id="CALL-VOICE",legacy_id="phone-event-1",lead_id="LEAD-VOICE",
                    payload_json=dumps({"source":"Gmail / UniFi","phone":"5145551002","caller":"Older Client",
                        "voicemail":True,"subject":"You Have a New Voicemail from Older Client",
                        "call_time":"10/06/2026 09:00:00"}),
                    created_at="2026-10-06T13:00:00+00:00"))

            client=TestClient(app);client.cookies.set("crownops_session",token)
            response=client.get("/api/crm/clients")
            self.assertEqual(response.status_code,200,response.text)
            clients=response.json()
            self.assertEqual(clients[0]["id"],"CLIENT-VOICE")
            self.assertEqual(clients[0]["latest_activity"]["label"],"Voicemail")
            self.assertEqual(clients[0]["latest_activity"]["source"],"Gmail / UniFi")
            self.assertIn("New Voicemail",clients[0]["latest_activity"]["summary"])
            self.assertEqual(clients[1]["id"],"CLIENT-FORM")
            self.assertEqual(clients[1]["latest_activity"]["label"],"Quote request")

    def test_existing_gmail_voicemail_review_item_promotes_into_sales_locally(self):
        with tempfile.TemporaryDirectory() as td:
            db_path=Path(td)/"crownops.sqlite3"
            initialize(db_path)
            from app.sales_crm_v6 import schema as crm_schema
            crm_schema(db_path)
            with transaction(db_path) as db:
                insert(db,"leads",dict(id="LEAD-GMAIL-VOICE",client_name="Existing Caller",email="",phone="5145552200",
                    move_date="",source="Manual",lead_type="Moving",stage="Contacted",archived_import=0,
                    notifications_suppressed=1,raw_json="{}",version=1,created_at="2026-09-01T12:00:00+00:00",updated_at="2026-09-01T12:00:00+00:00"))
                insert(db,"client_accounts",dict(id="CLIENT-GMAIL-VOICE",identity_key="p:5145552200",client_name="Existing Caller",
                    email="",phone="5145552200",language="English",created_at="2026-09-01T12:00:00+00:00",updated_at="2026-09-01T12:00:00+00:00"))
                insert(db,"client_account_leads",dict(client_id="CLIENT-GMAIL-VOICE",lead_id="LEAD-GMAIL-VOICE",created_at="2026-09-01T12:00:00+00:00"))
                insert(db,"integration_events",dict(id="GMAIL-VOICE-1",event_key="gmail:message-voice-1",
                    event_type="gmail_voicemail",object_id="message-voice-1",
                    payload_json=dumps({"gmail_id":"message-voice-1","subject":"You Have a New Voicemail from Existing Caller",
                        "snippet":"Please call back","parsed":{"kind":"Voicemail","caller":"Existing Caller","phone":"5145552200",
                        "call_time":"10/06/2026 09:15:00","attachments":[]}}),
                    status="Needs review",created_at="2026-10-06T13:15:00+00:00"))

                def should_not_create_lead(*args,**kwargs):
                    raise AssertionError("Existing caller should match by phone instead of creating a duplicate lead.")

                promoted=promote_pending_call_events(db,"USER-INBOX",should_not_create_lead)
                self.assertEqual(promoted,1)
                event=one(db,"SELECT * FROM integration_events WHERE id='GMAIL-VOICE-1'")
                self.assertEqual(event["status"],"Imported")
                payload=json.loads(event["payload_json"])
                self.assertEqual(payload["lead_id"],"LEAD-GMAIL-VOICE")
                self.assertTrue(payload["call_id"])
                call=one(db,"SELECT * FROM calls WHERE id=?",(payload["call_id"],))
                self.assertIsNotNone(call)
                details=json.loads(call["payload_json"])
                self.assertTrue(details["voicemail"])
                self.assertEqual(details["source"],"Gmail / UniFi")
                task=one(db,"SELECT * FROM tasks WHERE lead_id='LEAD-GMAIL-VOICE' AND title='Review voicemail' AND status='Open'")
                self.assertIsNotNone(task)

    def test_launch_simulation_long_distance_quote_to_dispatch_and_invoice_draft(self):
        with tempfile.TemporaryDirectory() as td:
            db_path=Path(td)/"crownops.sqlite3"
            app=create_app(db_path)
            staff_token="staff-session-token"
            staff_csrf="staff-csrf-token"
            portal_token="client-session-token"
            portal_csrf="client-csrf-token"
            expiry=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(timespec="seconds")
            # Simulate day-of dispatch so the HOS verification falls inside CrownOps' 24-hour freshness window.
            service_date=datetime.now(timezone.utc).date().isoformat()

            with transaction(db_path) as db:
                insert(db,"users",dict(id="USER-OWNER",email="owner@example.invalid",name="Owner",role="owner",
                    password_hash="unused-test-hash",active=1,created_at=now()))
                insert(db,"sessions",dict(token_hash=sha(staff_token),user_id="USER-OWNER",csrf=staff_csrf,expires_at=expiry))
                insert(db,"pricing_versions",dict(id="PRICE-LAUNCH",label="Launch test",rates_json="{}",settings_json="{}",approved=1,
                    approved_by="USER-OWNER",approved_at=now(),created_at=now()))
                insert(db,"daily_rates",dict(id="RATE-LAUNCH",service_date=service_date,movers=3,hourly_cents=17500,version=1,
                    approved_by="USER-OWNER",note="Launch simulation",created_at=now()))
                for eid,name,verified in (("DRV-LAUNCH","Driver Launch",1),("MOV-2","Mover Two",0),("MOV-3","Mover Three",0)):
                    insert(db,"employees",dict(id=eid,name=name,role="Mover",active=1,driver_verified=verified,hourly_cents=2500))
                insert(db,"vehicles",dict(id="TRUCK-LAUNCH",name="Launch Truck",active=1))
                insert(db,"fleet_profiles",dict(vehicle_id="TRUCK-LAUNCH",gvwr_kg=7000,inspection_interval_hours=24,
                    check_required=0,updated_at=now()))
                put_setting(db,"client_terms",{"approved":True,"version":"launch-test-v1",
                    "text":"Launch simulation booking terms. These terms are intentionally test-only and confirm the complete CrownOps acceptance workflow without contacting a real client."})
                put_setting(db,"billing_policy",{"approved":True,"terms":{
                    "issuer":{"name":"Crown Movers","address":"700 Rue Hodge, Saint-Laurent, QC","email":"info@crownmovers.ca","phone":"514-606-4030"},
                    "taxes":[
                        {"name":"GST","rate_percent":5,"registration":"TEST-GST"},
                        {"name":"QST","rate_percent":9.975,"registration":"TEST-QST"}],
                    "catalogue":[],"approval_reference":"Launch simulation billing policy"}})
                put_setting(db,"quickbooks_mappings_sandbox",{"service_item_id":"TEST-SERVICE","tax_code_id":"TEST-TAX"})

            staff=TestClient(app)
            staff.cookies.set("crownops_session",staff_token)
            sh={"x-csrf-token":staff_csrf}

            readiness=staff.get("/api/readiness")
            self.assertEqual(readiness.status_code,200,readiness.text)
            self.assertIn("launch",readiness.json())
            self.assertEqual(readiness.json()["launch"]["verified_driver_count"],1)
            self.assertEqual(readiness.json()["launch"]["missing_gvwr"],[])
            self.assertFalse(readiness.json()["launch"]["critical_ready"])  # test mode: Gmail/live actions intentionally off

            r=staff.post("/api/crm/clients",headers=sh,json={"client_name":"Launch Toronto Client",
                "email":"launch-client@example.invalid","phone":"514-555-0142","language":"English"})
            self.assertEqual(r.status_code,200,r.text);cid=r.json()["id"]

            r=staff.post(f"/api/crm/clients/{cid}/services",headers=sh,json={"service_type":"Long Distance",
                "move_date":service_date,"pickup":"700 Rue Hodge, Saint-Laurent, QC",
                "dropoff":"Toronto, ON","home_size":"2 bedroom","boxes":"40","inventory":"Launch simulation inventory"})
            self.assertEqual(r.status_code,200,r.text);lid=r.json()["lead_id"]

            quote_data={"lead_id":lid,"service_type":"Long Distance","language":"English","template_key":"long-en",
                "move_date":service_date,"start_window":"morning","movers":3,"trucks":1,"hourly_rate":175,
                "hours_low":4,"hours_high":5,"minimum_hours":3,"deposit_required":300,
                "transport_hours":12,"distance_km":1082,"distance_rate":0.94,"materials":[],
                "other_amount":0,"discount_amount":0}
            r=staff.post("/api/crm/quotes",headers=sh,json=quote_data)
            self.assertEqual(r.status_code,200,r.text);q=r.json();qid=q["id"]
            self.assertEqual(q["result"]["transport_cents"],241500)
            self.assertEqual(q["result"]["fuel_cents"],106793)
            self.assertEqual(q["result"]["hos_plan"]["minimum_schedule_days"],2)

            # Finalize prepares portal/email artifacts. QBO is requested deliberately while writes are disabled:
            # it must fail safely without poisoning a future retry operation.
            r=staff.post(f"/api/crm/quotes/{qid}/finalize",headers=sh,json={"send_now":False,"sync_quickbooks":True})
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()["quote_status"],"Approved")
            self.assertTrue(r.json()["quickbooks_error"])
            with connect(db_path) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM provider_operations WHERE object_id=?",(qid,)).fetchone()[0],0)
                insert(db,"portal_sessions",dict(token_hash=stamp_hash(portal_token),lead_id=lid,
                    email="launch-client@example.invalid",csrf=portal_csrf,expires_at=expiry,created_at=now()))

            portal=TestClient(app)
            portal.cookies.set("crownops_client",portal_token)
            r=portal.post(f"/api/portal/quotes/{qid}/accept",headers={"x-csrf-token":portal_csrf},
                json={"agree":True,"signed_name":"Launch Toronto Client","terms_version":"launch-test-v1"})
            self.assertEqual(r.status_code,200,r.text);jid=r.json()["tentative_job_id"]
            self.assertFalse(r.json()["booking_confirmed"])

            r=staff.post(f"/api/crm/quotes/{qid}/manual-deposit",headers=sh,json={"method":"Interac e-Transfer",
                "mode":"received","amount":300,"reference":"TEST-INTERAC-LAUNCH-001"})
            self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()["confirmed"])
            self.assertEqual(r.json()["job_id"],jid)

            jobs=staff.get("/api/jobs").json();job=next(x for x in jobs if x["id"]==jid)
            r=staff.put(f"/api/jobs/{jid}/plan",headers=sh,json={"version":job["version"],"title":"Toronto launch simulation",
                "start_at":job["start_at"],"end_at":job["end_at"],"quote_id":qid,
                "crew_ids":["DRV-LAUNCH","MOV-2","MOV-3"],"truck_ids":["TRUCK-LAUNCH"],
                "stops":["700 Rue Hodge, Saint-Laurent, QC","Toronto, ON"],"notes":"Launch simulation",
                "reason":"Assign launch crew and truck","capacity_reviewed":True})
            self.assertEqual(r.status_code,200,r.text)

            r=staff.get(f"/api/jobs/{jid}/hos-verification")
            self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()["ready"])
            r=staff.post(f"/api/jobs/{jid}/hos-verification",headers=sh,json={"driver_id":"DRV-LAUNCH",
                "vehicle_id":"TRUCK-LAUNCH","cycle":1,"cycle_on_duty_hours":40,
                "on_duty_since_last_24h_off":40,"consecutive_off_duty_before_shift":8,
                "had_24h_off_in_prev_14d":True,"dce_log_checked":True,
                "daily_off_duty_plan_checked":True,"elapsed_window_checked":True,
                "note":"Launch simulation DCE and dispatch review completed."})
            self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()["ready"])

            jobs=staff.get("/api/jobs").json();job=next(x for x in jobs if x["id"]==jid)
            r=staff.patch(f"/api/jobs/{jid}",headers=sh,json={"version":job["version"],"status":"In progress"})
            self.assertEqual(r.status_code,200,r.text)

            r=staff.post(f"/api/jobs/{jid}/invoice-from-quote",headers=sh,json={})
            self.assertEqual(r.status_code,200,r.text);iid=r.json()["id"]
            with connect(db_path) as db:
                inv=db.execute("SELECT status,terms_json FROM job_invoices WHERE id=?",(iid,)).fetchone()
                lead=db.execute("SELECT stage FROM leads WHERE id=?",(lid,)).fetchone()
                quote=db.execute("SELECT status FROM quotes WHERE id=?",(qid,)).fetchone()
                jobrow=db.execute("SELECT status FROM jobs WHERE id=?",(jid,)).fetchone()
                self.assertEqual(inv["status"],"Draft")
                self.assertEqual(lead["stage"],"Booked")
                self.assertEqual(quote["status"],"Accepted")
                self.assertEqual(jobrow["status"],"In progress")
                self.assertIn("Long Distance",db.execute("SELECT phase FROM jobs WHERE id=?",(jid,)).fetchone()["phase"])

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
