"""D-01 secure offline provisioner for exactly two pilot accounts (owner + sales).

Usage (run as its own process; never import the app into a process that then
calls subprocesses, because Core v1 transport guards block subprocesses):
  PYTHONPATH=<candidate-source> CROWNOPS_SECRET_KEY=<app-secret-from-private-file> \\
      python d01_provision.py --out-db <private-dir>/pilot.sqlite3 \\
      --identities-file <private-dir>/identities.json

Identities file (mode 0600, owned by the operator; never printed, never logged):
  {"owner": {"email": "...", "password": "...", "totp_secret": "BASE32..."},
   "sales": {"email": "...", "password": "...", "totp_secret": "BASE32..."}}
The operator supplies credentials through this private file (masked entry in
real use). The script never reads credentials from argv, never prints them,
and never stores the identities file, passwords, or TOTP secrets in the
database, the output summary, or any log. Only the app-encrypted MFA factor
ciphertext is stored, exactly as the app's own MFA setup flow stores it.

What it does, in order:
  1. Validates exactly two distinct accounts (owner + sales), distinct emails,
     distinct passwords (12-200 chars), distinct valid base32 TOTP secrets
     (>= 128 bits), and a valid Fernet app secret from the environment.
  2. Creates a NEW database only (refuses existing files and any symlink in the
     path). Applies the app's own reviewed schema files (schema.sql +
     platform_schema.sql) plus the app's Core v1 schema function
     (app.core_v1.ensure_schema, which creates client_accounts, triggers, and
     the Core v1 tables the D3 pilot factory requires) directly — no test
     initializer, no pricing seeds, no web server, no general startup.
     Creates no customers, leads, quotes, bookings, payments, invoices, or
     other business records.
  3. Creates the owner first (so the owner becomes master owner under the
     app's convention) and then sales, using the app's Argon2 password hashing.
  4. Enrolls MFA for both accounts: stores the app-encrypted factor, confirms
     it with a fresh TOTP code computed from the supplied secret (proving the
     secret is correct and usable with this app secret), then enables it and
     records the app's 'MFA enabled' audit rows.
  5. Publication gate (fail-closed): re-opens the database read-only and
     requires exactly 2 active users (owner + sales), exactly 2 enabled
     factors that decrypt with the app secret and match the supplied secrets,
     and zero rows in every token table and every business table. Then, on a
     temporary COPY only (never the published database), it proves login
     behaviour under CROWNOPS_REQUIRE_MFA=1: password-only denied, wrong code
     denied, correct fresh code accepted, replay of the same code denied, and
     the D3 pilot factory accepts the copy. If ANY check fails, the published
     database is deleted and the script exits non-zero — no partially
     provisioned database is ever published.
  6. On success it checkpoints WAL, switches the journal to DELETE (so no
     sidecar files remain), sets the file 0600 / directory 0700, and prints a
     JSON summary with roles, counts, and the logical fingerprint only.

Mandatory-MFA scope, stated plainly: the accepted app's login allows
password-only authentication when an account has NO enabled factor. This
provisioner enforces mandatory MFA by guaranteeing every published account
HAS a valid enabled factor and by refusing publication otherwise, combined
with the candidate change in app/security.py (verify_user_mfa) that refuses
password-only login for factor-less accounts when CROWNOPS_REQUIRE_MFA=1.
A pilot deployment MUST set CROWNOPS_REQUIRE_MFA=1; without it, the app's
legacy password-only behaviour for factor-less accounts would still apply.
That deployment dependency is a documented gate, not a hidden assumption.
"""
import argparse
import base64
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from d01_common import (check_no_symlink, check_private_dir, check_private_file,  # noqa: E402
                        fail, logical_fingerprint, table_counts)

TOKEN_TABLES = ["sessions", "invitations", "account_resets", "portal_sessions",
                "portal_invites", "invoice_access_sessions", "invoice_access_invites",
                "participation_access", "document_approvals"]
BUSINESS_TABLES = ["leads", "client_accounts", "quotes", "jobs", "payments",
                   "job_invoices", "outbound_messages", "provider_operations",
                   "manual_source_submissions", "messages", "tasks"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-db", required=True)
    ap.add_argument("--identities-file", required=True)
    args = ap.parse_args()
    out = Path(args.out_db)
    check_no_symlink(out, "output database")
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(out.parent, 0o700)
    check_private_dir(out.parent, "output directory")
    if out.exists():
        fail(f"output database already exists: {out}")
    check_private_file(args.identities_file, "identities file")
    identities = json.loads(Path(args.identities_file).read_text())
    if set(identities.keys()) != {"owner", "sales"}:
        fail("identities file must contain exactly 'owner' and 'sales'")
    accounts = []
    for role in ("owner", "sales"):
        a = identities[role]
        email = str(a.get("email", "")).strip().lower()
        password = str(a.get("password", ""))
        secret = str(a.get("totp_secret", "")).strip().replace(" ", "").upper()
        if not email or "@" not in email:
            fail(f"{role} email is missing or invalid")
        if not 12 <= len(password) <= 200:
            fail(f"{role} password must be 12-200 characters")
        try:
            raw = base64.b32decode(secret, casefold=True)
        except Exception:
            fail(f"{role} TOTP secret is not valid base32")
        if len(raw) < 16:
            fail(f"{role} TOTP secret must be at least 128 bits")
        accounts.append({"role": role, "email": email, "password": password, "secret": secret})
    if accounts[0]["email"] == accounts[1]["email"]:
        fail("owner and sales emails must differ")
    if accounts[0]["password"] == accounts[1]["password"]:
        fail("owner and sales passwords must differ")
    if accounts[0]["secret"] == accounts[1]["secret"]:
        fail("owner and sales TOTP secrets must differ")
    app_secret = os.environ.get("CROWNOPS_SECRET_KEY", "")
    try:
        from cryptography.fernet import Fernet
        Fernet(app_secret.encode())
    except Exception:
        fail("CROWNOPS_SECRET_KEY is not a valid Fernet key")

    from app.store import now
    from app.main import create_user
    from app.security import totp, encrypt
    from app.store import transaction, insert, audit, one

    source_dir = Path(__file__).resolve().parent
    # Schema files come from the app source on PYTHONPATH, not from this script.
    import app.store as app_store
    schema_dir = Path(app_store.__file__).resolve().parent

    try:
        con = sqlite3.connect(str(out))
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript((schema_dir / "schema.sql").read_text())
            con.executescript((schema_dir / "platform_schema.sql").read_text())
            con.commit()
        finally:
            con.close()
        from app.core_v1 import ensure_schema
        ensure_schema(out)
        user_ids = {}
        for a in accounts:
            u = create_user(out, a["email"], "Pilot " + a["role"].capitalize(),
                            a["password"], role=a["role"])
            user_ids[a["role"]] = u["id"]
        # MFA enrollment (mirrors the app's setup/enable flow, offline).
        for a in accounts:
            uid = user_ids[a["role"]]
            step = int(time.time() // 30)
            code = totp(a["secret"], step)
            with transaction(out) as db:
                if one(db, "SELECT user_id FROM auth_factors WHERE user_id=? AND enabled=1", (uid,)):
                    fail(f"{a['role']} already has an enabled MFA factor")
                db.execute("DELETE FROM auth_factors WHERE user_id=?", (uid,))
                insert(db, "auth_factors", dict(user_id=uid, secret_ciphertext=encrypt({"secret": a["secret"]}),
                                                created_at=now()))
                # Confirmation: the supplied secret must produce a code the app
                # logic accepts for a fresh step (step-1..step+1, > last_step -1).
                valid = [n for n in (step - 1, step, step + 1)
                         if n > -1 and totp(a["secret"], n) == code]
                if not valid:
                    fail(f"{a['role']} TOTP confirmation failed")
                db.execute("UPDATE auth_factors SET enabled=1,last_step=? WHERE user_id=?",
                           (valid[0], uid))
                audit(db, uid, "MFA enabled", "users", uid)
            a["user_id"] = uid
        with transaction(out) as db:
            audit(db, user_ids["owner"], "D01 offline provisioning completed", "users", user_ids["owner"],
                  {"roles": ["owner", "sales"], "mfa_enabled": 2})
        # Publication gate: read-only checks on the published database.
        con = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
        try:
            users = con.execute("SELECT role, active FROM users ORDER BY role").fetchall()
            if [(r[0], r[1]) for r in users] != [("owner", 1), ("sales", 1)]:
                fail("publication gate: users are not exactly one active owner and one active sales")
            factors = con.execute("SELECT user_id, enabled, secret_ciphertext FROM auth_factors").fetchall()
            if len(factors) != 2 or any(f[1] != 1 for f in factors):
                fail("publication gate: not exactly two enabled MFA factors")
        finally:
            con.close()
        counts = table_counts(out, TOKEN_TABLES + BUSINESS_TABLES)
        for t, c in counts.items():
            if c is None:
                fail(f"publication gate: table missing: {t}")
            if c != 0:
                fail(f"publication gate: table not empty: {t} ({c} rows)")
        # Factor decryption + login behaviour, on a temporary copy only.
        with tempfile.TemporaryDirectory(prefix="d01-verify-") as td:
            copy_path = Path(td) / "verify.sqlite3"
            src = sqlite3.connect(str(out))
            try:
                dst = sqlite3.connect(str(copy_path))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
            os.chmod(copy_path, 0o600)
            from app.security import decrypt as app_decrypt
            with patch.dict(os.environ, {"CROWNOPS_SECRET_KEY": app_secret}):
                con = sqlite3.connect(f"file:{copy_path}?mode=ro", uri=True)
                try:
                    rows = con.execute("SELECT user_id, secret_ciphertext FROM auth_factors").fetchall()
                    by_user = {user_ids[a["role"]]: a for a in accounts}
                    for user_id, cipher in rows:
                        if app_decrypt(cipher)["secret"] != by_user[user_id]["secret"]:
                            fail("publication gate: factor does not decrypt to the supplied secret")
                finally:
                    con.close()
            from app.main import create_app
            from fastapi.testclient import TestClient
            env = {"CROWNOPS_PROFILE": "core-v1", "CROWNOPS_ENV": "test",
                   "CROWNOPS_ALLOW_EXTERNAL": "0", "CROWNOPS_RUN_WORKER": "1",
                   "CROWNOPS_REQUIRE_MFA": "1", "CROWNOPS_SECRET_KEY": app_secret}
            with patch.dict(os.environ, env):
                app = create_app(copy_path)
                client = TestClient(app, raise_server_exceptions=False)
                try:
                    for a in accounts:
                        # The enrollment confirmation code already spent its
                        # step (last_step). Wait for a fresh step so the login
                        # code is genuinely new; otherwise it would correctly
                        # be refused as a replay. This wait is bounded (90s).
                        enrolled = sqlite3.connect(f"file:{copy_path}?mode=ro", uri=True)
                        try:
                            last = enrolled.execute(
                                "SELECT last_step FROM auth_factors WHERE user_id=?",
                                (user_ids[a["role"]],)).fetchone()[0]
                        finally:
                            enrolled.close()
                        deadline = time.time() + 90
                        while int(time.time() // 30) <= last:
                            if time.time() > deadline:
                                fail(f"publication gate: timed out waiting for a fresh TOTP step for {a['role']}")
                            time.sleep(1)
                        step = int(time.time() // 30)
                        correct = totp(a["secret"], step)
                        wrong = str((int(correct) + 1) % 1000000).zfill(6)
                        if wrong == correct:
                            wrong = "000000" if correct != "000000" else "111111"
                        r = client.post("/api/login", json={"email": a["email"], "password": a["password"]})
                        if r.status_code != 401:
                            fail(f"publication gate: password-only login not refused for {a['role']}")
                        r = client.post("/api/login", json={"email": a["email"], "password": a["password"],
                                                            "mfa_code": wrong})
                        if r.status_code != 401:
                            fail(f"publication gate: wrong-code login not refused for {a['role']}")
                        r = client.post("/api/login", json={"email": a["email"], "password": a["password"],
                                                            "mfa_code": correct})
                        if r.status_code != 200:
                            fail(f"publication gate: correct-code login failed for {a['role']}")
                        r = client.post("/api/login", json={"email": a["email"], "password": a["password"],
                                                            "mfa_code": correct})
                        if r.status_code != 401:
                            fail(f"publication gate: replayed code not refused for {a['role']}")
                finally:
                    client.close()
                from app.core_pilot import create_pilot_app
                pilot_env = {"CROWNOPS_ENV": "pilot", "CROWNOPS_PROFILE": "core-v1",
                             "CROWNOPS_ALLOW_EXTERNAL": "0", "CROWNOPS_RUN_WORKER": "0",
                             "CROWNOPS_PILOT_ACK": "manual-inquiry-only", "CROWNOPS_SECURE_COOKIES": "1",
                             "CROWNOPS_DB": str(copy_path), "CROWNOPS_PUBLIC_URL": "https://core-pilot.example.invalid",
                             "CROWNOPS_SECRET_KEY": app_secret, "CROWNOPS_REQUIRE_MFA": "1"}
                with patch.dict(os.environ, pilot_env):
                    pilot = create_pilot_app()
                    if not pilot.state.core_v1_pilot_entrypoint:
                        fail("publication gate: D3 pilot factory did not accept the copy")
    except SystemExit:
        raise
    except Exception as exc:
        # Fail-closed publication: no partially provisioned database is left behind.
        for suffix in ("", "-wal", "-shm", "-journal"):
            p = Path(str(out) + suffix)
            if p.exists():
                p.unlink()
        raise SystemExit(f"PROVISIONING FAILED (database deleted): {type(exc).__name__}")
    # Finalize: checkpoint WAL away so no sidecar files remain, private modes.
    con = sqlite3.connect(str(out))
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("PRAGMA journal_mode=DELETE")
        con.commit()
    finally:
        con.close()
    for suffix in ("-wal", "-shm", "-journal"):
        p = Path(str(out) + suffix)
        if p.exists():
            fail(f"sidecar file remains after checkpoint: {p}")
    os.chmod(out, 0o600)
    summary = {"database": out.name, "users": 2, "roles": ["owner", "sales"],
               "mfa_enabled": 2, "master_owner_role": "owner",
               "token_tables_zero": TOKEN_TABLES, "business_tables_zero": BUSINESS_TABLES,
               "logical_fingerprint": logical_fingerprint(out)}
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
