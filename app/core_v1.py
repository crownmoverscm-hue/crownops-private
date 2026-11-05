"""Locked manual-inquiry surface for CrownOps Core v1.

All legacy business endpoints remain registered for compatibility, but the Core v1
profile middleware denies them before dispatch. This module contains the only
business mutation surface permitted in that profile.
"""
from contextlib import closing
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import re

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from .store import connect, transaction, one, rows, insert, audit, now, uid, dumps

PROFILE = "core-v1"
OFFICE = {"owner", "manager", "sales"}
ALLOWED_ROUTE_KEYS = {
    "GET /api/health", "POST /api/login", "GET /api/me", "POST /api/logout",
    "GET /", "GET /core-v1.js",
    "GET /api/core-v1/clients", "POST /api/core-v1/clients",
    "GET /api/core-v1/clients/{cid}", "PATCH /api/core-v1/clients/{cid}",
    "GET /api/core-v1/inquiries", "POST /api/core-v1/inquiries",
    "GET /api/core-v1/inquiries/{iid}", "PATCH /api/core-v1/inquiries/{iid}",
    "POST /api/core-v1/inquiries/{iid}/notes",
    "GET /api/core-v1/inquiries/{iid}/tasks",
    "POST /api/core-v1/inquiries/{iid}/tasks",
    "PATCH /api/core-v1/tasks/{tid}", "GET /api/core-v1/audit",
}
SAFE_TABLES = {
    "sessions", "client_accounts", "client_account_leads", "leads",
    "manual_source_submissions", "core_v1_client_requests", "manual_action_requests", "messages", "tasks", "audit", "sqlite_sequence",
}
_LOCKED_PATHS = set()


def enabled():
    return os.getenv("CROWNOPS_PROFILE", "").strip().lower() == PROFILE


def canonical(path):
    return str(Path(path).resolve())


def protect_storage(path):
    _LOCKED_PATHS.add(canonical(path))


def authorize_write(action, table, allowed=SAFE_TABLES, column=None):
    """SQLite authorizer callback: allow only manual CRM and session/audit writes."""
    if action in (getattr(__import__("sqlite3"), "SQLITE_INSERT"),
                  getattr(__import__("sqlite3"), "SQLITE_UPDATE"),
                  getattr(__import__("sqlite3"), "SQLITE_DELETE")):
        name = str(table or "").lower()
        if name == "auth_factors":
            return __import__("sqlite3").SQLITE_OK if action == __import__("sqlite3").SQLITE_UPDATE and column == "last_step" else __import__("sqlite3").SQLITE_DENY
        if name not in allowed:
            return __import__("sqlite3").SQLITE_DENY
        if name in {"manual_source_submissions", "manual_action_requests", "audit"} and action != __import__("sqlite3").SQLITE_INSERT:
            return __import__("sqlite3").SQLITE_DENY
    if action in (getattr(__import__("sqlite3"), "SQLITE_ATTACH"),
                  getattr(__import__("sqlite3"), "SQLITE_DETACH"),
                  getattr(__import__("sqlite3"), "SQLITE_ALTER_TABLE"),
                  getattr(__import__("sqlite3"), "SQLITE_DROP_TABLE"),
                  getattr(__import__("sqlite3"), "SQLITE_CREATE_TABLE"),
                  getattr(__import__("sqlite3"), "SQLITE_CREATE_TRIGGER"),
                  getattr(__import__("sqlite3"), "SQLITE_DROP_TRIGGER")):
        return __import__("sqlite3").SQLITE_DENY
    return __import__("sqlite3").SQLITE_OK



TRANSPORT_EVIDENCE = {"blocked_provider_calls": 0, "socket_attempts": 0, "subprocess_attempts": 0}
_TRANSPORT_PATCHED = False


def install_transport_guards():
    """Final process boundary for accidental provider/network/subprocess work in Core v1."""
    global _TRANSPORT_PATCHED
    if not enabled() or _TRANSPORT_PATCHED:
        return
    import socket
    import subprocess
    import requests

    original_request = requests.sessions.Session.request
    def blocked_request(self, method, url, *args, **kwargs):
        TRANSPORT_EVIDENCE["blocked_provider_calls"] += 1
        raise RuntimeError("External provider operations are disabled in Core v1.")
    requests.sessions.Session.request = blocked_request

    def blocked_socket(*args, **kwargs):
        TRANSPORT_EVIDENCE["socket_attempts"] += 1
        raise OSError("Network sockets are disabled in Core v1.")
    socket.socket.connect = blocked_socket
    socket.socket.connect_ex = blocked_socket
    socket.create_connection = blocked_socket

    def blocked_process(*args, **kwargs):
        TRANSPORT_EVIDENCE["subprocess_attempts"] += 1
        raise RuntimeError("Subprocess execution is disabled in Core v1.")
    subprocess.Popen = blocked_process
    subprocess.run = blocked_process
    _TRANSPORT_PATCHED = True

def deny_if_enabled(operation):
    if enabled():
        raise HTTPException(403, f"{operation} is disabled in the locked Core v1 profile.")


def roles(user):
    if not user or user.get("role") not in OFFICE:
        raise HTTPException(403, "Your role does not allow Core v1 inquiry work.")


def text(value, label, limit=4000, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise HTTPException(422, f"{label} must be text under {limit} characters.")
    value = value.strip()
    if required and not value:
        raise HTTPException(422, f"{label} is required.")
    return value


def original_text(value, label, limit):
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > limit:
        raise HTTPException(422, f"{label} must be text of at most {limit} characters.")
    return value


def check_version(data, current, label):
    expected = data.get("expected_version")
    if type(expected) is not int or expected < 1:
        raise HTTPException(422, "A positive integer expected_version is required.")
    if expected != current["version"]:
        raise HTTPException(409, {"message": f"{label} changed; refresh before saving.",
                                  "current_version": current["version"]})
    return expected


def email(value):
    value = text(value or "", "Email", 254).lower()
    if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise HTTPException(422, "Enter a valid email address.")
    return value


def structured_date(value, label="Date"):
    value = original_text(value, label, 40)
    if not value.strip():
        return ""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise HTTPException(422, f"{label} must be a real calendar date in YYYY-MM-DD format.")
    from datetime import date
    try:
        date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"{label} must be a real calendar date in YYYY-MM-DD format.")
    return value


def structured_phone(value):
    value = original_text(value, "Phone", 80)
    if not value.strip():
        return ""
    candidate = value.strip()
    if not re.fullmatch(r"\+?[0-9 () .-]+", candidate) or candidate.count("(") != candidate.count(")"):
        raise HTTPException(422, "Enter a valid Canadian phone or an international phone beginning with +.")
    balance = 0
    for char in candidate:
        if char == "(": balance += 1
        if char == ")": balance -= 1
        if balance < 0 or balance > 1:
            raise HTTPException(422, "Review the phone number formatting.")
    digits = re.sub(r"[^0-9]", "", candidate)
    if candidate.startswith("+"):
        valid = 7 <= len(digits) <= 15 and not digits.startswith("0")
        if digits.startswith("1"):
            valid = len(digits) == 11 and digits[1] in "23456789" and digits[4] in "23456789"
    else:
        national = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
        valid = len(national) == 10 and national[0] in "23456789" and national[3] in "23456789"
    if not valid:
        raise HTTPException(422, "Enter a valid Canadian phone or an international phone beginning with +.")
    return value  # Preserve the supplied format; never infer or silently rewrite a contact number.


def phone_key(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def client_matches(db, email_value, phone_value):
    found = {}
    if email_value:
        for row in rows(db, "SELECT id,client_name,email,phone FROM client_accounts WHERE lower(email)=?", (email_value.lower(),)):
            found[row["id"]] = row
    key = phone_key(phone_value)
    if key:
        for row in rows(db, "SELECT id,client_name,email,phone FROM client_accounts WHERE phone!=''"):
            if phone_key(row.get("phone")) == key:
                found[row["id"]] = row
    return list(found.values())


def ensure_schema(path):
    # Schema creation happens before the SQLite write authorizer is installed.
    with closing(connect(path)) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS client_accounts(
          id TEXT PRIMARY KEY, identity_key TEXT UNIQUE NOT NULL, client_name TEXT NOT NULL,
          email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
          language TEXT NOT NULL DEFAULT 'English', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS client_account_leads(
          client_id TEXT NOT NULL REFERENCES client_accounts(id),
          lead_id TEXT NOT NULL UNIQUE REFERENCES leads(id), created_at TEXT NOT NULL,
          PRIMARY KEY(client_id,lead_id));
        CREATE TABLE IF NOT EXISTS core_v1_client_requests(
          request_key TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL, client_id TEXT NOT NULL REFERENCES client_accounts(id),
          created_by TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS manual_action_requests(
          kind TEXT NOT NULL, actor TEXT NOT NULL, request_key TEXT NOT NULL,
          request_sha256 TEXT NOT NULL, result_id TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(kind,actor,request_key));
        CREATE TABLE IF NOT EXISTS manual_source_submissions(
          id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, lead_id TEXT NOT NULL UNIQUE REFERENCES leads(id),
          source_kind TEXT NOT NULL, source_reference TEXT NOT NULL DEFAULT '',
          original_subject TEXT NOT NULL DEFAULT '', original_body TEXT NOT NULL DEFAULT '',
          source_sha256 TEXT NOT NULL, request_sha256 TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS manual_source_no_update
          BEFORE UPDATE ON manual_source_submissions BEGIN SELECT RAISE(ABORT,'Original source submissions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS manual_source_no_delete
          BEFORE DELETE ON manual_source_submissions BEGIN SELECT RAISE(ABORT,'Original source submissions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS core_audit_no_update
          BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'Audit history is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS core_audit_no_delete
          BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'Audit history is append-only'); END;
        CREATE INDEX IF NOT EXISTS manual_source_lead ON manual_source_submissions(lead_id);
        """)
        # Disposable schema initialization only; the dedicated pilot factory uses prequalified storage and skips DDL.
        for table in ("client_accounts", "tasks"):
            if "version" not in {row[1] for row in db.execute("PRAGMA table_info(" + table + ")")}:
                db.execute("ALTER TABLE " + table + " ADD COLUMN version INTEGER NOT NULL DEFAULT 1")


def action_retry(db, data, kind, iid, payload, actor):
    key = text(data.get("request_key", ""), "Request key", 100)
    digest = hashlib.sha256(dumps({"inquiry":iid,"payload":payload}).encode()).hexdigest()
    if not key:
        return key, digest, None
    prior = one(db,"SELECT request_sha256,result_id FROM manual_action_requests WHERE kind=? AND actor=? AND request_key=?",(kind,actor,key))
    if prior and prior["request_sha256"] != digest:
        raise HTTPException(409,"Request key was already used for different content.")
    return key, digest, prior["result_id"] if prior else None


def record_action(db, key, digest, kind, actor, result_id):
    if key:
        insert(db,"manual_action_requests",dict(kind=kind,actor=actor,request_key=key,request_sha256=digest,result_id=result_id,created_at=now()))


def create_app_routes(app, path, auth, root, *, initialize_schema=True):
    if initialize_schema:
        ensure_schema(path)

    @app.get("/core-v1.js")
    def core_script():
        return FileResponse(Path(root) / "app" / "static" / "core_v1.js",
                            media_type="application/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/api/core-v1/clients")
    def list_clients(q: str = "", u=Depends(auth)):
        roles(u)
        query = "%" + text(q, "Search", 200).lower() + "%"
        with closing(connect(path)) as db:
            return rows(db, """SELECT id,client_name,email,phone,language,version,created_at,updated_at
                FROM client_accounts WHERE lower(client_name||' '||email||' '||phone) LIKE ?
                ORDER BY updated_at DESC LIMIT 200""", (query,))

    @app.post("/api/core-v1/clients")
    def create_client(data: dict, u=Depends(auth)):
        roles(u)
        request_key = text(data.get("request_key"), "Request key", 100, True)
        name = text(data.get("client_name"), "Client name", 150, True)
        mail = email(data.get("email", ""))
        phone = structured_phone(data.get("phone", ""))
        if not mail and not phone_key(phone):
            raise HTTPException(422, "Enter an email or phone number.")
        request_hash = hashlib.sha256(dumps({"client_name": name, "email": mail, "phone": phone}).encode()).hexdigest()
        with transaction(path) as db:
            old = one(db, "SELECT * FROM core_v1_client_requests WHERE request_key=?", (request_key,))
            if old:
                if old["request_sha256"] != request_hash:
                    raise HTTPException(409, "Request key was already used for different client details.")
                return {"id": old["client_id"], "duplicate": True}
            matches = client_matches(db, mail, phone)
            if matches:
                return {"duplicate": True, "candidates": matches}
            identity_key = "e:" + mail if mail else "p:" + phone_key(phone)
            cid = uid("CLIENT")
            insert(db, "client_accounts", dict(id=cid, identity_key=identity_key, client_name=name,
                email=mail, phone=phone, language="English", created_at=now(), updated_at=now()))
            insert(db, "core_v1_client_requests", dict(request_key=request_key, request_sha256=request_hash,
                client_id=cid, created_by=u["id"], created_at=now()))
            audit(db, u["id"], "Manual client created", "client_accounts", cid, {"source": "email-origin"})
            return {"id": cid, "duplicate": False}

    @app.get("/api/core-v1/clients/{cid}")
    def get_client(cid: str, u=Depends(auth)):
        roles(u)
        with closing(connect(path)) as db:
            client = one(db, "SELECT id,client_name,email,phone,language,version,created_at,updated_at FROM client_accounts WHERE id=?", (cid,))
            if not client:
                raise HTTPException(404, "Client not found.")
            client["services"] = rows(db, """SELECT l.id,l.lead_type,l.source,l.stage,l.move_date,l.pickup,l.dropoff,l.created_at
                FROM client_account_leads x JOIN leads l ON l.id=x.lead_id WHERE x.client_id=? ORDER BY l.created_at DESC""", (cid,))
            return client

    @app.patch("/api/core-v1/clients/{cid}")
    def update_client(cid: str, data: dict, u=Depends(auth)):
        roles(u)
        allowed = {"expected_version", "client_name", "email", "phone", "language"}
        if not data or set(data) - allowed:
            raise HTTPException(422, "Only client contact fields may be updated.")
        with transaction(path) as db:
            current = one(db, "SELECT * FROM client_accounts WHERE id=?", (cid,))
            if not current:
                raise HTTPException(404, "Client not found.")
            expected = check_version(data, current, "Client")
            if not (set(data) - {"expected_version"}):
                raise HTTPException(422, "At least one client field is required.")
            name = text(data.get("client_name", current["client_name"]), "Client name", 150, True)
            mail = email(data.get("email", current["email"]))
            phone = structured_phone(data.get("phone", current["phone"]))
            language = "French" if str(data.get("language", current["language"])).lower().startswith("fr") else "English"
            if not mail and not phone_key(phone):
                raise HTTPException(422, "Enter an email or phone number.")
            matches = [m for m in client_matches(db, mail, phone) if m["id"] != cid]
            if matches:
                raise HTTPException(409, "These contact details match another client; review it before changing the record.")
            identity_key = "e:" + mail if mail else "p:" + phone_key(phone)
            db.execute("""UPDATE client_accounts SET identity_key=?,client_name=?,email=?,phone=?,language=?,updated_at=?,version=version+1
                WHERE id=?""", (identity_key,name,mail,phone,language,now(),cid))
            audit(db, u["id"], "Client contact fields updated", "client_accounts", cid,
                  {"old_version":expected,"new_version":expected+1,"fields":sorted(set(data)-{"expected_version"})})
        return {"ok": True, "id": cid, "version": expected+1}

    @app.post("/api/core-v1/inquiries")
    def create_inquiry(data: dict, u=Depends(auth)):
        roles(u)
        request_key = text(data.get("request_key"), "Request key", 100, True)
        client_data = data.get("client") or {}
        service = data.get("service") or {}
        source = data.get("source") or {}
        if not isinstance(client_data, dict) or not isinstance(service, dict) or not isinstance(source, dict):
            raise HTTPException(422, "Client, service, and source must be objects.")
        name = text(client_data.get("client_name"), "Client name", 150, True)
        mail = email(client_data.get("email", ""))
        phone = structured_phone(client_data.get("phone", ""))
        client_id = text(data.get("client_id", ""), "Client ID", 100)
        kind = text(source.get("kind", "Email"), "Source type", 40, True)
        if kind.lower() not in ("email", "email-origin"):
            raise HTTPException(422, "Core v1 accepts manual email-origin inquiries only.")
        original_subject = original_text(source.get("subject", ""), "Original subject", 300)
        original_body = original_text(source.get("body", ""), "Original submission", 20000)
        source_reference = text(source.get("secure_reference", ""), "Secure source reference", 1000)
        if not original_body and not source_reference:
            raise HTTPException(422, "Preserve the original submission or provide its secure reference.")
        editable = {}
        for field, limit in (("move_date",40),("pickup",500),("dropoff",500),("home_size",100),("boxes",100),
                             ("pickup_access",500),("dropoff_access",500),("pickup_walk",100),("dropoff_walk",100),
                             ("inventory",4000),("heavy_items",2000),("notes",4000),("service_type",60)):
            editable[field] = structured_date(service.get(field, ""), "Requested date") if field == "move_date" else text(service.get(field, ""), field, limit)
        req = {"client_id":client_id,"client":client_data,"service":editable,
               "source":{"kind":kind,"subject":original_subject,"body":original_body,"secure_reference":source_reference}}
        request_sha = hashlib.sha256(dumps(req).encode()).hexdigest()
        source_sha = hashlib.sha256((original_body or source_reference).encode()).hexdigest()
        with transaction(path) as db:
            old = one(db, "SELECT s.*,l.client_name FROM manual_source_submissions s JOIN leads l ON l.id=s.lead_id WHERE s.request_key=?", (request_key,))
            if old:
                if old["request_sha256"] != request_sha:
                    raise HTTPException(409, "Request key was already used for a different inquiry.")
                return {"client_id":one(db,"SELECT client_id FROM client_account_leads WHERE lead_id=?",(old["lead_id"],))["client_id"],
                        "inquiry_id":old["lead_id"],"source_id":old["id"],"duplicate":True}
            if client_id:
                client = one(db, "SELECT * FROM client_accounts WHERE id=?", (client_id,))
                if not client:
                    raise HTTPException(404, "Selected client not found.")
            else:
                if not mail and not phone_key(phone):
                    raise HTTPException(422, "Select an existing client or provide an email or phone number.")
                matches = client_matches(db, mail, phone)
                if matches:
                    return {"duplicate":True,"candidates":matches,"requires_client_selection":True}
                identity_key = "e:" + mail if mail else "p:" + phone_key(phone)
                client_id = uid("CLIENT")
                insert(db,"client_accounts",dict(id=client_id,identity_key=identity_key,client_name=name,email=mail,
                    phone=phone,language="French" if str(client_data.get("language","English")).lower().startswith("fr") else "English",
                    created_at=now(),updated_at=now()))
                audit(db,u["id"],"Manual client created from inquiry","client_accounts",client_id,{"request_key":request_key})
            lead_id = uid("LEAD")
            client = one(db, "SELECT * FROM client_accounts WHERE id=?", (client_id,))
            # A selected legacy client must not bypass D1 via stale stored contact fields.
            canonical_mail = email(client["email"])
            canonical_phone = structured_phone(client["phone"])
            if not canonical_mail and not canonical_phone:
                raise HTTPException(422,"Correct the selected client contact details before creating an inquiry.")
            t = now()
            rec = dict(id=lead_id,external_id=None,client_name=client["client_name"],email=client["email"],phone=client["phone"],
                move_date=editable["move_date"],pickup=editable["pickup"],dropoff=editable["dropoff"],home_size=editable["home_size"],
                boxes=editable["boxes"],pickup_access=editable["pickup_access"],dropoff_access=editable["dropoff_access"],
                pickup_walk=editable["pickup_walk"],dropoff_walk=editable["dropoff_walk"],inventory=editable["inventory"],
                heavy_items=editable["heavy_items"],photo_link="",language=client["language"],source="Manual email",
                lead_type=editable["service_type"] or "Moving",stage="New Lead",assigned_to=u["id"],priority="Normal",
                notes=editable["notes"],archived_import=0,notifications_suppressed=1,
                raw_json=dumps({"core_v1":True,"request_key":request_key}),version=1,created_at=t,updated_at=t)
            insert(db,"leads",rec)
            insert(db,"client_account_leads",dict(client_id=client_id,lead_id=lead_id,created_at=t))
            insert(db,"manual_source_submissions",dict(id=uid("SOURCE"),request_key=request_key,lead_id=lead_id,
                source_kind="Email",source_reference=source_reference,original_subject=original_subject,
                original_body=original_body,source_sha256=source_sha,request_sha256=request_sha,
                created_by=u["id"],created_at=t))
            msg_id=uid("MSG")
            insert(db,"messages",dict(id=msg_id,lead_id=lead_id,kind="Incoming pasted email",
                subject=original_subject,body=original_body or ("Secure source reference: "+source_reference),
                language=client["language"],author=u["id"],created_at=t))
            task_id=uid("TASK")
            insert(db,"tasks",dict(id=task_id,lead_id=lead_id,title="Review new inquiry",due_date=t[:10],
                owner=u["id"],created_at=t))
            audit(db,u["id"],"Email-origin inquiry created","leads",lead_id,
                {"client_id":client_id,"source_id":one(db,"SELECT id FROM manual_source_submissions WHERE lead_id=?",(lead_id,))["id"],
                 "source_sha256":source_sha,"request_key":request_key,
                 "client_selection":{"mode":"selected" if client_id == data.get("client_id") else "new",
                 "selected_client_id":client_id,"matched_fields":[k for k,v in (("email",mail and mail == client["email"]),("phone",phone_key(phone) and phone_key(phone)==phone_key(client["phone"]))) if v]}})
            return {"client_id":client_id,"inquiry_id":lead_id,"source_id":one(db,"SELECT id FROM manual_source_submissions WHERE lead_id=?",(lead_id,))["id"],
                    "message_id":msg_id,"review_task_id":task_id,"duplicate":False}

    @app.get("/api/core-v1/inquiries")
    def list_inquiries(q: str = "", u=Depends(auth)):
        roles(u)
        query = "%" + text(q, "Search", 200).lower() + "%"
        with closing(connect(path)) as db:
            return rows(db, """SELECT l.id,l.client_name,l.email,l.phone,l.lead_type,l.stage,l.assigned_to,l.move_date,l.created_at,
                    x.client_id,s.id source_id,s.source_sha256
                FROM leads l JOIN client_account_leads x ON x.lead_id=l.id
                JOIN manual_source_submissions s ON s.lead_id=l.id
                WHERE lower(l.client_name||' '||l.email||' '||l.phone||' '||l.pickup||' '||l.dropoff) LIKE ?
                ORDER BY l.created_at DESC LIMIT 200""", (query,))

    @app.get("/api/core-v1/inquiries/{iid}")
    def inquiry(iid: str, u=Depends(auth)):
        roles(u)
        with closing(connect(path)) as db:
            l=one(db,"""SELECT l.*,x.client_id FROM leads l JOIN client_account_leads x ON x.lead_id=l.id WHERE l.id=?""",(iid,))
            if not l: raise HTTPException(404,"Inquiry not found.")
            l.pop("raw_json",None)
            l["source"]=one(db,"SELECT id,source_kind,source_reference,original_subject,original_body,source_sha256,created_at FROM manual_source_submissions WHERE lead_id=?",(iid,))
            l["notes"]=rows(db,"SELECT id,kind,subject,body,author,created_at FROM messages WHERE lead_id=? ORDER BY created_at",(iid,))
            l["tasks"]=rows(db,"SELECT id,title,due_date,owner,status,version,created_at FROM tasks WHERE lead_id=? AND historical=0 ORDER BY created_at",(iid,))
            l["audit"]=rows(db,"SELECT actor,action,object_type,object_id,details_json,created_at FROM audit WHERE object_id=? ORDER BY id",(iid,))
            return l

    @app.patch("/api/core-v1/inquiries/{iid}")
    def update_inquiry(iid: str, data: dict, u=Depends(auth)):
        roles(u)
        allowed={"reason","expected_version","stage","assigned_to","move_date","pickup","dropoff","home_size","boxes","pickup_access",
                 "dropoff_access","pickup_walk","dropoff_walk","inventory","heavy_items","notes"}
        if not data or set(data)-allowed:
            raise HTTPException(422,"Only inquiry, ownership, and service fields may be updated.")
        expected = data.get("expected_version")
        if type(expected) is not int or expected < 1:
            raise HTTPException(422,"A positive integer expected_version is required.")
        if not (set(data) - {"expected_version", "reason"}):
            raise HTTPException(422,"At least one inquiry field is required.")
        with transaction(path) as db:
            l=one(db,"SELECT * FROM leads WHERE id=? AND id IN (SELECT lead_id FROM manual_source_submissions)",(iid,))
            if not l: raise HTTPException(404,"Inquiry not found.")
            if expected != l["version"]:
                raise HTTPException(409,{"message":"Inquiry changed; refresh before saving.","current_version":l["version"]})
            values={}
            for key,value in data.items():
                if key in {"expected_version","reason"}:
                    continue
                if key=="stage":
                    value=text(value,"Status",40,True)
                    if value not in {"New Lead","In Progress","Waiting for client","Follow-up","Closed","Lost"}:
                        raise HTTPException(422,"Invalid Core v1 inquiry status.")
                elif key=="move_date":
                    value=structured_date(value,"Requested date")
                elif key=="assigned_to":
                    value=text(value,"Owner",100)
                    if not value:
                        raise HTTPException(422,"Choose an inquiry owner.")
                    if u["role"]=="sales" and value != u["id"]:
                        raise HTTPException(403,"Sales may assign only to themselves.")
                    if value and u["role"] in {"owner","manager"} and not one(db,"SELECT id FROM users WHERE id=? AND active=1 AND role IN ('owner','manager','sales')",(value,)):
                        raise HTTPException(422,"Choose an active staff member.")
                else:
                    value=text(value,key,4000)
                values[key]=value
            sql="UPDATE leads SET "+",".join(k+"=?" for k in values)+",version=version+1,updated_at=? WHERE id=?"
            db.execute(sql,[*values.values(),now(),iid])
            audit(db,u["id"],"Manual inquiry updated","leads",iid,
                  {"fields":sorted(values),"changes":{k:{"old":l[k],"new":v} for k,v in values.items()},
                   "old_version":expected,"new_version":expected+1,"reason":text(data.get("reason", ""),"Reason",1000)})
        return {"ok":True,"version":expected+1}

    @app.post("/api/core-v1/inquiries/{iid}/notes")
    def add_note(iid: str, data: dict, u=Depends(auth)):
        roles(u)
        body=text(data.get("body"),"Note",4000,True)
        with transaction(path) as db:
            l=one(db,"SELECT * FROM leads WHERE id=? AND id IN (SELECT lead_id FROM manual_source_submissions)",(iid,))
            if not l: raise HTTPException(404,"Inquiry not found.")
            key,digest,existing=action_retry(db,data,"note",iid,{"body":body},u["id"])
            if existing: return {"id":existing,"kind":"Note","duplicate":True}
            mid=uid("MSG")
            insert(db,"messages",dict(id=mid,lead_id=iid,kind="Note",subject="",body=body,language=l["language"],author=u["id"],created_at=now()))
            audit(db,u["id"],"Internal note added","messages",mid,{"lead_id":iid})
            record_action(db,key,digest,"note",u["id"],mid)
        return {"id":mid,"kind":"Note"}

    @app.get("/api/core-v1/inquiries/{iid}/tasks")
    def list_tasks(iid: str, u=Depends(auth)):
        roles(u)
        with closing(connect(path)) as db:
            if not one(db,"SELECT id FROM manual_source_submissions WHERE lead_id=?",(iid,)):
                raise HTTPException(404,"Inquiry not found.")
            return rows(db,"SELECT id,title,due_date,owner,status,version,created_at FROM tasks WHERE lead_id=? AND historical=0 ORDER BY due_date",(iid,))

    @app.post("/api/core-v1/inquiries/{iid}/tasks")
    def add_task(iid: str, data: dict, u=Depends(auth)):
        roles(u)
        title=text(data.get("title","Follow up"),"Task",300,True)
        due=structured_date(data.get("due_date",""),"Due date")
        owner=text(data.get("owner",u["id"]),"Owner",100)
        with transaction(path) as db:
            if not one(db,"SELECT id FROM manual_source_submissions WHERE lead_id=?",(iid,)):
                raise HTTPException(404,"Inquiry not found.")
            if not owner:
                raise HTTPException(422,"Choose a follow-up owner.")
            if u["role"]=="sales" and owner != u["id"]:
                raise HTTPException(403,"Sales may assign follow-up only to themselves.")
            if owner and u["role"] in {"owner","manager"} and not one(db,"SELECT id FROM users WHERE id=? AND active=1 AND role IN ('owner','manager','sales')",(owner,)):
                raise HTTPException(422,"Choose active staff for this follow-up.")
            key,digest,existing=action_retry(db,data,"task",iid,{"title":title,"due_date":due,"owner":owner},u["id"])
            if existing: return {"id":existing,"duplicate":True}
            tid=uid("TASK")
            insert(db,"tasks",dict(id=tid,lead_id=iid,title=title,due_date=due,owner=owner,created_at=now()))
            audit(db,u["id"],"Follow-up task added","tasks",tid,{"lead_id":iid})
            record_action(db,key,digest,"task",u["id"],tid)
        return {"id":tid}

    @app.patch("/api/core-v1/tasks/{tid}")
    def edit_task(tid: str, data: dict, u=Depends(auth)):
        roles(u)
        if not data or set(data)-{"expected_version","status","owner"}:
            raise HTTPException(422,"Only follow-up status and owner may be updated.")
        with transaction(path) as db:
            task=one(db,"SELECT * FROM tasks WHERE id=? AND lead_id IN (SELECT lead_id FROM manual_source_submissions)",(tid,))
            if not task: raise HTTPException(404,"Follow-up not found.")
            expected = check_version(data, task, "Follow-up")
            if not (set(data) - {"expected_version"}):
                raise HTTPException(422,"At least one follow-up field is required.")
            if "status" in data:
                status=text(data["status"],"Status",20,True)
                if status not in {"Open","Done"}: raise HTTPException(422,"Invalid follow-up status.")
                db.execute("UPDATE tasks SET status=? WHERE id=?",(status,tid))
            if "owner" in data:
                owner=text(data["owner"],"Owner",100)
                if not owner:
                    raise HTTPException(422,"Choose a follow-up owner.")
                if u["role"]=="sales" and owner != u["id"]:
                    raise HTTPException(403,"Sales may assign follow-up only to themselves.")
                if owner and u["role"] in {"owner","manager"} and not one(db,"SELECT id FROM users WHERE id=? AND active=1 AND role IN ('owner','manager','sales')",(owner,)):
                    raise HTTPException(422,"Choose active staff for this follow-up.")
                db.execute("UPDATE tasks SET owner=? WHERE id=?",(owner,tid))
            db.execute("UPDATE tasks SET version=version+1 WHERE id=?",(tid,))
            audit(db,u["id"],"Follow-up updated","tasks",tid,
                  {"changes":{k:{"old":task[k],"new":data[k]} for k in data if k!="expected_version"},
                   "old_version":expected,"new_version":expected+1})
        return {"ok":True,"version":expected+1}

    @app.get("/api/core-v1/audit")
    def audit_history(limit: int=100, u=Depends(auth)):
        roles(u)
        limit=min(max(int(limit),1),500)
        with closing(connect(path)) as db:
            return rows(db,"""SELECT id,actor,action,object_type,object_id,details_json,created_at
                FROM audit WHERE object_type IN ('leads','client_accounts','messages','tasks')
                ORDER BY id DESC LIMIT ?""",(limit,))
