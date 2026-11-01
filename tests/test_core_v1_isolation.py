"""Focused fictional-data qualification for the locked Core v1 profile."""
import csv
import hashlib
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.store import connect, initialize, one
from app.main import create_app, create_user

ROOT = Path(__file__).resolve().parents[1]
ROUTE_MANIFEST = ROOT / "docs" / "core-v1-route-inventory.csv"
PASSWORD = "TEST-only-safe-password-138!"


def payload(key, name, email, subject, body, client_id=""):
    return {
        "request_key": key,
        "client_id": client_id,
        "client": {"client_name": name, "email": email, "phone": "514-555-0101"},
        "service": {"service_type": "Moving", "move_date": "2027-06-10", "pickup": "TEST-A",
                    "dropoff": "TEST-B", "notes": "TEST fictional service"},
        "source": {"kind": "Email", "subject": subject, "body": body},
    }


class CoreV1Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="crownops-core-v1-fictional-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fictional.sqlite3"
        self.env = patch.dict(os.environ, {
            "CROWNOPS_PROFILE": "core-v1",
            "CROWNOPS_ENV": "test",
            "CROWNOPS_ALLOW_EXTERNAL": "0",
            "CROWNOPS_RUN_WORKER": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        initialize(self.path)
        for email, name, role in (
            ("owner@example.invalid", "TEST Owner", "owner"),
            ("manager@example.invalid", "TEST Manager", "manager"),
            ("sales@example.invalid", "TEST Sales", "sales"),
            ("crew@example.invalid", "TEST Crew", "crew"),
        ):
            create_user(self.path, email, name, PASSWORD, role=role)
        self.app = create_app(self.path)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.csrf = self.login(self.client, "sales@example.invalid")

    def login(self, client, email):
        response = client.post("/api/login", json={"email": email, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrf"]

    def request(self, client, csrf, method, path, data=None):
        headers = {"X-CSRF-Token": csrf} if method not in ("GET", "HEAD", "OPTIONS") else {}
        return client.request(method, path, json=data, headers=headers)

    def create_pair(self):
        first = payload("CORE-MAIL-001", "TEST Fictional Client", "client@example.invalid",
                        "TEST first request", "TEST original email body one")
        r1 = self.client.post("/api/core-v1/inquiries", json=first, headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(r1.status_code, 200, r1.text)
        cid = r1.json()["client_id"]
        second = payload("CORE-MAIL-002", "TEST Fictional Client", "client@example.invalid",
                         "TEST second request", "TEST original email body two", cid)
        r2 = self.client.post("/api/core-v1/inquiries", json=second, headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(r2.status_code, 200, r2.text)
        return r1.json(), r2.json(), first, second


class CoreV1AcceptanceTests(CoreV1Fixture):
    def test_01_manual_email_inquiries_create_client_and_two_distinct_services(self):
        a, b, _, _ = self.create_pair()
        self.assertNotEqual(a["inquiry_id"], b["inquiry_id"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM client_accounts").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM client_account_leads").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM leads").fetchone()[0], 2)

    def test_02_original_submissions_are_preserved_and_hash_verified(self):
        a, b, p1, p2 = self.create_pair()
        with connect(self.path) as db:
            sources = db.execute("SELECT * FROM manual_source_submissions ORDER BY request_key").fetchall()
            messages = db.execute("SELECT * FROM messages WHERE kind='Incoming pasted email' ORDER BY created_at,id").fetchall()
        self.assertEqual(len(sources), 2)
        expected = [p1["source"]["body"], p2["source"]["body"]]
        self.assertEqual(sorted(x["original_body"] for x in sources), sorted(expected))
        for row in sources:
            self.assertEqual(row["source_sha256"], hashlib.sha256(row["original_body"].encode()).hexdigest())
        self.assertEqual(len(messages), 2)
        self.assertEqual(sorted(x["body"] for x in messages), sorted(expected))
        with connect(self.path) as db:
            with self.assertRaises(Exception):
                db.execute("UPDATE manual_source_submissions SET original_body='TEST changed' WHERE id=?", (sources[0]["id"],))
            with self.assertRaises(Exception):
                db.execute("DELETE FROM manual_source_submissions WHERE id=?", (sources[0]["id"],))

    def test_03_identical_request_retry_returns_existing_record(self):
        first = payload("CORE-RETRY-001", "TEST Retry Client", "retry@example.invalid",
                        "TEST retry subject", "TEST retry original")
        one_result = self.client.post("/api/core-v1/inquiries", json=first, headers={"X-CSRF-Token": self.csrf}).json()
        retry = self.client.post("/api/core-v1/inquiries", json=first, headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertTrue(retry.json()["duplicate"])
        self.assertEqual(retry.json()["inquiry_id"], one_result["inquiry_id"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM manual_source_submissions WHERE request_key='CORE-RETRY-001'").fetchone()[0], 1)

    def test_04_changed_payload_cannot_reuse_request_key(self):
        first = payload("CORE-RETRY-002", "TEST Retry Client", "retry2@example.invalid",
                        "TEST subject", "TEST original")
        self.assertEqual(self.client.post("/api/core-v1/inquiries", json=first, headers={"X-CSRF-Token": self.csrf}).status_code, 200)
        changed = dict(first)
        changed["source"] = dict(first["source"], body="TEST modified")
        response = self.client.post("/api/core-v1/inquiries", json=changed, headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(response.status_code, 409)

    def test_05_same_client_can_have_distinct_services_and_duplicate_match_is_manual(self):
        a, b, _, _ = self.create_pair()
        self.assertNotEqual(a["inquiry_id"], b["inquiry_id"])
        extra = payload("CORE-MAIL-MATCH", "TEST Fictional Client", "client@example.invalid",
                        "TEST separate service", "TEST separate original")
        found = self.client.post("/api/core-v1/inquiries", json=extra, headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(found.status_code, 200)
        self.assertTrue(found.json()["requires_client_selection"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM client_accounts").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM manual_source_submissions").fetchone()[0], 2)

    def test_06_internal_note_is_audited_and_does_not_change_source(self):
        a, _, p1, _ = self.create_pair()
        before = hashlib.sha256(p1["source"]["body"].encode()).hexdigest()
        response = self.client.post(f"/api/core-v1/inquiries/{a['inquiry_id']}/notes",
                                    json={"body":"TEST internal note"},
                                    headers={"X-CSRF-Token":self.csrf})
        self.assertEqual(response.status_code, 200, response.text)
        detail = self.client.get(f"/api/core-v1/inquiries/{a['inquiry_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("TEST internal note", [x["body"] for x in detail.json()["notes"]])
        self.assertEqual(detail.json()["source"]["source_sha256"], before)
        self.assertEqual(detail.json()["source"]["original_body"], p1["source"]["body"])

    def test_07_review_tasks_and_explicit_followup_are_audited(self):
        a,b,_,_=self.create_pair()
        follow = self.client.post(f"/api/core-v1/inquiries/{a['inquiry_id']}/tasks",
                                 json={"title":"TEST explicit follow-up","due_date":"2026-11-02"},
                                 headers={"X-CSRF-Token":self.csrf})
        self.assertEqual(follow.status_code,200,follow.text)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM tasks WHERE title='Review new inquiry'").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT count(*) FROM tasks WHERE title='TEST explicit follow-up'").fetchone()[0],1)
            self.assertGreaterEqual(db.execute("SELECT count(*) FROM audit").fetchone()[0],5)

    def test_08_status_and_service_fields_change_without_repricing_or_source_edit(self):
        a,_,p1,_=self.create_pair()
        oldhash=hashlib.sha256(p1["source"]["body"].encode()).hexdigest()
        r=self.client.patch(f"/api/core-v1/inquiries/{a['inquiry_id']}",
            json={"expected_version":1,"stage":"In Progress","pickup":"TEST updated pickup","move_date":"2027-07-01"},
            headers={"X-CSRF-Token":self.csrf})
        self.assertEqual(r.status_code,200,r.text)
        d=self.client.get(f"/api/core-v1/inquiries/{a['inquiry_id']}").json()
        self.assertEqual(d["stage"],"In Progress")
        self.assertEqual(d["pickup"],"TEST updated pickup")
        self.assertEqual(d["source"]["source_sha256"],oldhash)
        self.assertEqual(d["source"]["original_body"],p1["source"]["body"])

    def test_09_role_denials_preserve_existing_role_boundaries(self):
        crew=TestClient(self.app,raise_server_exceptions=False)
        self.addCleanup(crew.close)
        ccsrf=self.login(crew,"crew@example.invalid")
        r=crew.post("/api/core-v1/inquiries",json=payload("CORE-CREW-001","TEST Crew","crew@example.invalid","TEST","TEST"),
                    headers={"X-CSRF-Token":ccsrf})
        self.assertEqual(r.status_code,403)
        inquiry,_,_,_=self.create_pair()
        manager=TestClient(self.app,raise_server_exceptions=False)
        self.addCleanup(manager.close)
        mcsrf=self.login(manager,"manager@example.invalid")
        manager_id=manager.get("/api/me").json()["user"]["id"]
        denied=self.client.patch(f"/api/core-v1/inquiries/{inquiry['inquiry_id']}",
            json={"expected_version":1,"assigned_to":manager_id},headers={"X-CSRF-Token":self.csrf})
        self.assertEqual(denied.status_code,403,denied.text)
        allowed=manager.patch(f"/api/core-v1/inquiries/{inquiry['inquiry_id']}",
            json={"expected_version":1,"assigned_to":manager_id},headers={"X-CSRF-Token":mcsrf})
        self.assertEqual(allowed.status_code,200,allowed.text)

    def test_10_duplicate_concurrent_retry_is_serialized(self):
        data=payload("CORE-CONCURRENT-001","TEST Concurrent Client","parallel@example.invalid","TEST parallel","TEST parallel original")
        def send(_):
            c=TestClient(self.app,raise_server_exceptions=False)
            c.cookies.set("crownops_session",self.client.cookies.get("crownops_session"))
            r=c.post("/api/core-v1/inquiries",json=data,headers={"X-CSRF-Token":self.csrf})
            c.close()
            return r.status_code,r.json()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(send,range(4)))
        self.assertEqual([x[0] for x in results],[200]*4)
        self.assertEqual(len({x[1]["inquiry_id"] for x in results}),1)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM manual_source_submissions WHERE request_key='CORE-CONCURRENT-001'").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM client_accounts WHERE email='parallel@example.invalid'").fetchone()[0],1)

    def test_11_database_authorizer_blocks_business_tables_and_immutable_source(self):
        self.create_pair()
        with connect(self.path) as db:
            for sql in (
                "INSERT INTO quotes(id,lead_id,revision,status,inputs_json,result_json,pricing_id,lead_snapshot_json,lead_version,created_by,created_at) VALUES('TEST','x',1,'Draft','{}','{}','x','{}',1,'x','x')",
                "INSERT INTO jobs(id,lead_id,title,phase,created_at,updated_at) VALUES('TEST','x','TEST','Tentative','x','x')",
                "INSERT INTO payments(id,lead_id,amount_cents,method,purpose,reference,verified,recorded_by,created_at) VALUES('TEST','x',1,'TEST','TEST','TEST',0,'TEST','x')",
            ):
                with self.assertRaises(Exception): db.execute(sql)

    def test_12_fictional_readbacks_have_zero_business_and_provider_effects(self):
        self.create_pair()
        from app.core_v1 import TRANSPORT_EVIDENCE
        with connect(self.path) as db:
            expected={"clients":1,"services":2,"sources":2,"source_messages":2,"review_tasks":2,
                      "quotes":0,"jobs":0,"assignments":0,"payments":0,"invoices":0,"outbound":0}
            actual={
                "clients":db.execute("SELECT count(*) FROM client_accounts").fetchone()[0],
                "services":db.execute("SELECT count(*) FROM leads").fetchone()[0],
                "sources":db.execute("SELECT count(*) FROM manual_source_submissions").fetchone()[0],
                "source_messages":db.execute("SELECT count(*) FROM messages WHERE kind='Incoming pasted email'").fetchone()[0],
                "review_tasks":db.execute("SELECT count(*) FROM tasks WHERE title='Review new inquiry'").fetchone()[0],
                "quotes":db.execute("SELECT count(*) FROM quotes").fetchone()[0],
                "jobs":db.execute("SELECT count(*) FROM jobs").fetchone()[0],
                "assignments":db.execute("SELECT count(*) FROM assignments").fetchone()[0],
                "payments":db.execute("SELECT count(*) FROM payments").fetchone()[0],
                "invoices":db.execute("SELECT count(*) FROM job_invoices").fetchone()[0],
                "outbound":db.execute("SELECT count(*) FROM outbound_messages").fetchone()[0],
            }
        self.assertEqual(actual,expected)
        self.assertEqual(TRANSPORT_EVIDENCE,{"blocked_provider_calls":0,"socket_attempts":0,"subprocess_attempts":0})
        print("CORE_V1_READBACK "+json.dumps(actual,sort_keys=True))
        print("CORE_V1_TRANSPORT "+json.dumps(TRANSPORT_EVIDENCE,sort_keys=True))

    def test_13_stale_update_is_rejected_without_record_or_audit_mutation(self):
        a, _, _, _ = self.create_pair()
        url = f"/api/core-v1/inquiries/{a['inquiry_id']}"
        initial = self.client.get(url).json()
        version = initial["version"]
        for value in (None, True, "1", 0, -1):
            data = {"pickup": "TEST invalid version"}
            if value is not None:
                data["expected_version"] = value
            result = self.client.patch(url, json=data, headers={"X-CSRF-Token": self.csrf})
            self.assertEqual(result.status_code, 422, result.text)
        manager = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(manager.close)
        token = self.login(manager, "manager@example.invalid")
        winner = manager.patch(url, json={"expected_version": version, "pickup": "TEST manager winner"},
                               headers={"X-CSRF-Token": token})
        self.assertEqual(winner.status_code, 200, winner.text)
        before = self.client.get(url).json()
        with connect(self.path) as db:
            audits = db.execute("SELECT count(*) FROM audit").fetchone()[0]
        stale = self.client.patch(url, json={"expected_version": version, "pickup": "TEST stale overwrite"},
                                 headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["current_version"], version + 1)
        after = self.client.get(url).json()
        self.assertEqual(after, before)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit").fetchone()[0], audits)
        retry = self.client.patch(url, json={"expected_version": version + 1, "pickup": "TEST refreshed retry"},
                                 headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(self.client.get(url).json()["version"], version + 2)
        print("CORE_V1_STALE_UPDATE missing_invalid=422 winner=200 stale=409 stale_record_unchanged=true stale_audit_unchanged=true refreshed_retry=200")

    def test_14_concurrent_updates_allow_only_one_version_winner(self):
        a, _, _, _ = self.create_pair()
        url = f"/api/core-v1/inquiries/{a['inquiry_id']}"
        version = self.client.get(url).json()["version"]
        from threading import Barrier
        barrier = Barrier(2)
        def save(index):
            client = TestClient(self.app, raise_server_exceptions=False)
            try:
                client.cookies.set("crownops_session", self.client.cookies.get("crownops_session"))
                barrier.wait(timeout=10)
                return client.patch(url, json={"expected_version": version, "pickup": f"TEST parallel {index}"},
                                    headers={"X-CSRF-Token": self.csrf}).status_code
            finally:
                client.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, range(2)))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(self.client.get(url).json()["version"], version + 1)
        print("CORE_V1_CONCURRENT_UPDATE requests=2 statuses=200,409 version_increments=1")

    def test_15_audit_update_delete_denied_and_original_row_preserved(self):
        self.create_pair()
        import sqlite3
        from app.core_v1 import authorize_write
        from app.store import _core_v1_authorizer, transaction, audit
        for authorizer in (authorize_write, _core_v1_authorizer):
            for operation in (sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                self.assertEqual(authorizer(operation, "audit", None, None, None) if authorizer is _core_v1_authorizer
                                 else authorizer(operation, "audit"), sqlite3.SQLITE_DENY)
        with connect(self.path) as db:
            before = [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY id").fetchall()]
            self.assertTrue(before)
            for sql in ("UPDATE audit SET action='TEST tamper'", "DELETE FROM audit"):
                with self.assertRaises(sqlite3.DatabaseError):
                    db.execute(sql)
            self.assertEqual([dict(row) for row in db.execute("SELECT * FROM audit ORDER BY id").fetchall()], before)
        with transaction(self.path) as db:
            audit(db, "TEST-ACTOR", "TEST append proof", "leads", "TEST-ID", {})
        with connect(self.path) as db:
            after = [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY id").fetchall()]
        self.assertEqual(after[:-1], before)
        self.assertEqual(len(after), len(before) + 1)
        print("CORE_V1_AUDIT update=denied delete=denied rows_preserved=true insert=allowed")
    def test_16_client_and_task_stale_updates_are_atomic(self):
        a, _, _, _ = self.create_pair()
        client_url = f"/api/core-v1/clients/{a['client_id']}"
        task = self.client.get(f"/api/core-v1/inquiries/{a['inquiry_id']}/tasks").json()[0]
        cases = [(client_url, {"client_name":"TEST revised"}, {"client_name":"TEST stale"}),
                 (f"/api/core-v1/tasks/{task['id']}", {"status":"Done"}, {"status":"Open"})]
        for url, winner, stale in cases:
            with connect(self.path) as db:
                before = [dict(x) for x in db.execute('SELECT * FROM audit ORDER BY id')]
            missing = self.client.patch(url,json=winner,headers={"X-CSRF-Token":self.csrf})
            self.assertEqual(missing.status_code,422,missing.text)
            ok = self.client.patch(url,json={"expected_version":1,**winner},headers={"X-CSRF-Token":self.csrf})
            self.assertEqual(ok.status_code,200,ok.text)
            with connect(self.path) as db:
                state = {table:[dict(x) for x in db.execute('SELECT * FROM '+table)] for table in ('client_accounts','tasks','audit')}
            bad = self.client.patch(url,json={"expected_version":1,**stale},headers={"X-CSRF-Token":self.csrf})
            self.assertEqual(bad.status_code,409,bad.text)
            with connect(self.path) as db:
                self.assertEqual(state,{table:[dict(x) for x in db.execute('SELECT * FROM '+table)] for table in state})
            retry=self.client.patch(url,json={"expected_version":2,**stale},headers={"X-CSRF-Token":self.csrf})
            self.assertEqual(retry.status_code,200,retry.text)
        print('CORE_V1_MUTABLE_SAVE clients=version_checked tasks=version_checked stale_no_change=true refreshed_retry=success')

    def test_17_audit_triggers_and_replace_preserve_history(self):
        self.create_pair()
        import sqlite3
        with connect(self.path) as db:
            before=[dict(x) for x in db.execute('SELECT * FROM audit ORDER BY id')]
            triggers={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
            self.assertTrue({'core_audit_no_update','core_audit_no_delete'} <= triggers)
            self.assertEqual(db.execute('PRAGMA recursive_triggers').fetchone()[0],1)
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute('INSERT OR REPLACE INTO audit(id,actor,action,object_type,object_id,details_json,created_at) SELECT id,actor,action,object_type,object_id,details_json,created_at FROM audit LIMIT 1')
            self.assertEqual(before,[dict(x) for x in db.execute('SELECT * FROM audit ORDER BY id')])
        print('CORE_V1_AUDIT_DEFENSE triggers=present replace=denied rows_unchanged=true')

    def test_18_original_submission_whitespace_and_unicode_are_preserved(self):
        original='  TEST original / é\r\n  second line\n\n'
        data=payload('CORE-EXACT-001','TEST Exact','exact@example.invalid','  TEST subject  ',original)
        result=self.client.post('/api/core-v1/inquiries',json=data,headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(result.status_code,200,result.text)
        detail=self.client.get('/api/core-v1/inquiries/'+result.json()['inquiry_id']).json()
        self.assertEqual(detail['source']['original_body'],original)
        self.assertEqual(detail['source']['original_subject'],'  TEST subject  ')
        self.assertEqual(detail['source']['source_sha256'],hashlib.sha256(original.encode()).hexdigest())
        print('CORE_V1_ORIGINAL exact_unicode_crlf_whitespace=true')

    def test_19_selection_transfer_and_status_have_durable_audit_details(self):
        a,b,_,_=self.create_pair()
        with connect(self.path) as db:
            selected=db.execute("SELECT details_json FROM audit WHERE action='Email-origin inquiry created' AND object_id=?",(b['inquiry_id'],)).fetchone()[0]
        selection=json.loads(selected)['client_selection']
        self.assertEqual(selection['selected_client_id'],a['client_id'])
        self.assertEqual(selection['mode'],'selected')
        manager=TestClient(self.app,raise_server_exceptions=False);self.addCleanup(manager.close)
        token=self.login(manager,'manager@example.invalid'); manager_id=manager.get('/api/me').json()['user']['id']
        url='/api/core-v1/inquiries/'+a['inquiry_id']; old=manager.get(url).json()
        result=manager.patch(url,json={'expected_version':old['version'],'assigned_to':manager_id,'stage':'In Progress','reason':'TEST reviewed call'},headers={'X-CSRF-Token':token})
        self.assertEqual(result.status_code,200,result.text)
        event=manager.get(url).json()['audit'][-1];details=json.loads(event['details_json'])
        self.assertEqual(details['changes']['assigned_to'],{'old':old['assigned_to'],'new':manager_id})
        self.assertEqual(details['changes']['stage'],{'old':old['stage'],'new':'In Progress'})
        self.assertEqual(details['reason'],'TEST reviewed call')
        self.assertEqual(event['actor'],manager_id);self.assertTrue(event['created_at'])
        print('CORE_V1_AUDIT_CONTENT selection=durable transfer=old_new status=old_new actor_timestamp_reason=present')

    def test_20_auth_csrf_static_and_owner_tampering_are_denied(self):
        a,_,_,_=self.create_pair();url='/api/core-v1/inquiries/'+a['inquiry_id']
        for token in ('','TEST-invalid-token'):
            r=self.client.patch(url,json={'expected_version':1,'pickup':'TEST denied'},headers={'X-CSRF-Token':token})
            self.assertEqual(r.status_code,403,r.text)
        cross=self.client.patch(url,json={'expected_version':1,'pickup':'TEST denied'},headers={'X-CSRF-Token':self.csrf,'Origin':'https://untrusted.example.invalid'})
        self.assertEqual(cross.status_code,403,cross.text)
        anon=TestClient(self.app,raise_server_exceptions=False);self.addCleanup(anon.close)
        anon.cookies.set('crownops_session','TEST-malformed-session')
        self.assertEqual(anon.get(url).status_code,401)
        for resource in ('/static/app.js','/static/index.html','/app/schema.sql','/api/core-v1/inquiries/TEST/source'):
            self.assertEqual(self.client.get(resource).status_code,403)
        for owner in ('','TEST-nonexistent-owner'):
            r=self.client.patch(url,json={'expected_version':1,'assigned_to':owner},headers={'X-CSRF-Token':self.csrf})
            self.assertIn(r.status_code,(403,422))
        self.assertEqual(self.client.get(url).json()['version'],1)
        print('CORE_V1_AUTH malformed_session=401 csrf_missing_invalid_cross_origin=403 static=403 invalid_ownership=denied')

    def test_21_degraded_read_error_hides_internal_details(self):
        a,_,_,_=self.create_pair()
        import sqlite3
        with patch('app.core_v1.connect',side_effect=sqlite3.OperationalError('TEST private/path and credential secret')):
            result=self.client.get('/api/core-v1/inquiries/'+a['inquiry_id'])
        self.assertEqual(result.status_code,500)
        for private in ('private/path','credential','secret','Traceback'):
            self.assertNotIn(private,result.text)
        self.assertEqual(self.client.get('/api/core-v1/inquiries/'+a['inquiry_id']).status_code,200)
        print('CORE_V1_DEGRADED_READ response=generic_500 recovered_read=200')

    def test_22_complete_business_table_before_after_readbacks(self):
        business=('quotes','jobs','assignments','payments','job_invoices','outbound_messages','deposit_links','estimate_drafts')
        def snapshot():
            with connect(self.path) as db:
                tables={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                return {name:db.execute('SELECT count(*) FROM '+name).fetchone()[0] if name in tables else 'ABSENT' for name in business}
        before=snapshot(); a,b,_,_=self.create_pair()
        self.client.post('/api/core-v1/inquiries/'+a['inquiry_id']+'/notes',json={'body':'TEST note'},headers={'X-CSRF-Token':self.csrf})
        self.client.post('/api/core-v1/inquiries/'+a['inquiry_id']+'/tasks',json={'title':'TEST explicit follow-up'},headers={'X-CSRF-Token':self.csrf})
        after=snapshot();self.assertEqual(before,after)
        self.assertTrue(all(x in (0,'ABSENT') for x in after.values()))
        with connect(self.path) as db:
            allowed={name:db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('client_accounts','leads','manual_source_submissions','messages','tasks')}
        self.assertEqual(allowed,{'client_accounts':1,'leads':2,'manual_source_submissions':2,'messages':3,'tasks':3})
        print('CORE_V1_DATABASE_BEFORE_AFTER '+json.dumps({'business_before':before,'business_after':after,'allowed_after':allowed},sort_keys=True))


class CoreV1SecurityTests(CoreV1Fixture):
    def test_44_security_cases_and_every_baseline_route_are_denied(self):
        # This manifest-driven exhaustive exercise covers each baseline method/path,
        # including GET routes that contact providers, all webhooks, and all callbacks.
        with ROUTE_MANIFEST.open(newline="") as stream:
            manifest=list(csv.DictReader(stream))
        self.assertGreaterEqual(len(manifest),423)
        tested=0
        body={"request_key":"TEST-DENIED","client_name":"TEST Fictional","email":"blocked@example.invalid",
              "phone":"514-555-0199","body":"TEST complete fictional payload","confirm":True,"amount":1}
        for row in manifest:
            if row["classification"]=="ALLOWED":
                continue
            route=row["path"]
            route=__import__("re").sub(r"\{([^}:]+)(?::[^}]+)?\}",lambda m:"TEST-"+m.group(1).upper(),route)
            method=row["method"]
            response=self.request(self.client,self.csrf,method,route,body if method in {"POST","PUT","PATCH"} else None)
            self.assertEqual(response.status_code,403,
                f"{row['source_file']}:{row['line']} {method} {route} -> {response.status_code}: {response.text[:200]}")
            tested+=1
        self.assertGreaterEqual(tested,400)
        print("CORE_V1_DENIED_ROUTE_ROWS "+str(tested))

    def test_known_bypass_paths_a_to_e_are_backend_denied(self):
        attempts=[
            ("POST","/api/portal/quotes/TEST-Q/accept",{"confirm":True,"quote_id":"TEST-Q"}),
            ("POST","/api/quotes/TEST-Q/accept",{"accepted":True,"signature":"TEST"}),
            ("POST","/api/webhooks/square",{"type":"payment.updated","data":{"id":"TEST-PAY"}}),
            ("PUT","/api/jobs/TEST-JOB/plan",{"confirmed":False,"crew":["TEST"],"trucks":["TEST"]}),
            ("POST","/api/leads",{"client_name":"TEST","email":"safe@example.invalid","lead_type":"Form","stage":"New"}),
            ("POST","/api/crm/quotes",{"lead_id":"TEST","draft_key":"TEST","estimate":True}),
            ("POST","/api/crm/quotes/TEST-Q/manual-deposit",{"amount":100,"method":"TEST"}),
            ("POST","/api/payments/TEST-PAY/verify",{"verified":True}),
            ("POST","/api/jobs/TEST-JOB/confirm",{"confirmed":True}),
            ("POST","/api/jobs/TEST-JOB/booking-confirmation",{"confirm":True}),
            ("POST","/api/portal/quotes/TEST-Q/deposit-checkout",{"amount":100}),
            ("GET","/api/invoices/TEST-INV/quickbooks-payment-link",None),
            ("POST","/api/invoices/TEST-INV/issue",{"issue":True}),
            ("POST","/api/invoices/TEST-INV/allocate-payment",{"payment_id":"TEST"}),
            ("POST","/api/ai/analyze",{"text":"TEST"}),
            ("POST","/api/forgot-password",{"email":"owner@example.invalid"}),
            ("POST","/api/reset-password",{"token":"TEST","password":"TEST"}),
            ("POST","/api/gmail/sync-readonly",{"limit":1}),
            ("GET","/api/quickbooks/test-connection",None),
            ("POST","/api/outbound/TEST/approve",{"approved":True}),
            ("POST","/api/outbound/TEST/send",{"send":True}),
            ("POST","/api/webhooks/wix",{"event":"TEST"}),
            ("POST","/api/intake/sync",{"force":True}),
            ("POST","/api/integrations/refresh",{"provider":"TEST"}),
            ("POST","/api/schedule-imports/promote",{"batch":"TEST"}),
            ("POST","/api/jobs/TEST/invoice-from-quote",{"invoice":True}),
            ("POST","/api/invoices/TEST/collect",{"amount":100}),
            ("POST","/api/portal/invoices/TEST/payment-link",{"create":True}),
            ("POST","/api/users/TEST/access",{"recover":True,"master":True}),
            ("POST","/api/account-access/invitations/TEST/send-email",{"send":True}),
            ("POST","/api/document-approvals",{"document":"TEST"}),
            ("POST","/api/public/document-approval",{"token":"TEST","approve":True}),
            ("POST","/api/company/fleet/TEST/availability",{"active":True}),
            ("POST","/api/fleet/TEST/maintenance",{"dispatch":True}),
            ("POST","/api/crew-protocols",{"assign":"TEST"}),
            ("POST","/api/automation/run",{"run":True}),
            ("GET","/api/portal/project",None),
            ("GET","/api/quotes/TEST/quickbooks-delivery",None),
            ("POST","/api/quotes/TEST/quickbooks-delivery/send",{"confirm":True}),
            ("POST","/api/costs/quickbooks/fetch",{"start":"TEST"}),
            ("POST","/api/invoices/TEST/quickbooks-email/send",{"send":True}),
            ("POST","/api/participation/draws",{"winner":"TEST"}),
            ("POST","/api/jobs/TEST/participation/credit",{"amount":100}),
            ("POST","/api/company/reference-import/apply",{"apply":True}),
        ]
        self.assertEqual(len(attempts),44)
        for method,path,data in attempts:
            response=self.request(self.client,self.csrf,method,path,data)
            self.assertEqual(response.status_code,403,f"{method} {path}: {response.status_code} {response.text[:200]}")

    def test_sensitive_helpers_and_workers_fail_closed(self):
        from app.core_v1 import TRANSPORT_EVIDENCE
        from app.providers import provider_token
        from app.ai import invoke
        from app.deposits import create_deposit_checkout, record_deposit_callback
        from app.worker import tick
        from app.automatic_intake import run_intake, prepare_draft
        from app.client_communications import prepare_deposit_request
        from app.sales_crm_v6 import prepare_booking_confirmation, confirm_job
        for call in (
            lambda:provider_token(self.path,"quickbooks"),
            lambda:invoke("TEST","English",{}),
            lambda:create_deposit_checkout(self.path,"TEST","TEST","TEST"),
            lambda:record_deposit_callback(None,{}, "TEST"),
            lambda:tick(self.path),
            lambda:run_intake(self.path,lambda *a:None),
            lambda:prepare_draft(None,{}, "TEST"),
            lambda:prepare_deposit_request(None,"TEST","TEST"),
            lambda:prepare_booking_confirmation(None,{}, {}, {}, "TEST"),
            lambda:confirm_job(None,{}, {}, "TEST", "TEST"),
        ):
            with self.assertRaises(HTTPException) as caught: call()
            self.assertEqual(caught.exception.status_code,403)
        self.assertEqual(TRANSPORT_EVIDENCE,{"blocked_provider_calls":0,"socket_attempts":0,"subprocess_attempts":0})

    def test_all_legacy_recovery_paths_denied_including_master(self):
        # The owner fixture is the synthetic Master account in this disposable database.
        for method,path,data in (
            ("GET","/api/recovery-status",None),
            ("POST","/api/forgot-password",{"email":"owner@example.invalid"}),
            ("POST","/api/reset-password",{"token":"TEST","password":"TEST-password-123"}),
            ("POST","/api/users/TEST/access",{"action":"recover","master_initiated":True}),
        ):
            response=self.request(self.client,self.csrf,method,path,data)
            self.assertEqual(response.status_code,403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
