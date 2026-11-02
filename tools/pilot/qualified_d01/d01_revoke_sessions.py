"""D-01 restored-session/token revocation for a Core v1 database copy.

Usage:
  python d01_revoke_sessions.py --source-db <restored.sqlite3> --dest-db <revoked.sqlite3>

Safety rules (all enforced, all failures delete the destination if created):
  - The source is never modified. The destination must not already exist, must
    sit in a 0700 directory with no symlinks in its path, and is written 0600.
  - The destination is a consistent copy of the source (SQLite backup API),
    then a single offline maintenance transaction revokes bearer capabilities:
      deleted: sessions, invitations, account_resets, portal_sessions,
        portal_invites, invoice_access_sessions, invoice_access_invites,
        participation_access.
      expired (rows preserved, status changed): document_approvals rows that
        are still Prepared with no approval/decline recorded. Responded rows
        are untouched.
  - Preserved exactly: users, password hashes, MFA factors INCLUDING
    last_step counters (never reset to -1 — resetting would reopen TOTP
    replay), immutable originals, business records, and the audit history
    itself (plus one new recovery audit row, see below).
  - One sanitized audit row is appended (actor 'offline-recovery'): action
    'D-01 restored sessions and tokens revoked', with per-table deleted
    counts only — no token values, no secrets, no personal data beyond roles.
  - After revocation the script verifies: integrity_check ok,
    foreign_key_check empty, token tables empty, users/factors/originals/
    business counts unchanged (except document_approvals status changes and
    audit +1), and reports the pre- and post-revocation logical fingerprints.
    The fingerprints WILL differ (sessions deleted + audit appended); the
    report explains exactly which counts changed, so a fingerprint change is
    never mistaken for corruption.
  - This script does not log anyone in, does not re-enroll MFA, and does not
    publish the revoked copy to any service. Cutover to a revoked copy needs
    separate explicit approval, and old sessions must be re-tested as refused
    on the revoked copy before any use (see the qualification harness).
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from d01_common import (check_no_symlink, check_private_dir, check_private_file,  # noqa: E402
                        fail, integrity_and_fk, logical_fingerprint, table_counts)

DELETED_TABLES = ["sessions", "invitations", "account_resets", "portal_sessions",
                  "portal_invites", "invoice_access_sessions", "invoice_access_invites",
                  "participation_access"]
PRESERVED_TABLES = ["users", "auth_factors", "manual_source_submissions", "leads",
                    "client_accounts", "quotes", "jobs", "payments", "job_invoices"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-db", required=True)
    ap.add_argument("--dest-db", required=True)
    args = ap.parse_args()
    source = Path(args.source_db)
    dest = Path(args.dest_db)
    check_no_symlink(source, "source database")
    check_no_symlink(dest, "destination database")
    check_private_file(source, "source database")
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(dest.parent, 0o700)
    check_private_dir(dest.parent, "destination directory")
    if dest.exists():
        fail(f"destination database already exists: {dest}")
    if source.resolve() == dest.resolve():
        fail("source and destination must differ")
    try:
        src = sqlite3.connect(str(source))
        try:
            dst = sqlite3.connect(str(dest))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        os.chmod(dest, 0o600)
        pre_counts = table_counts(dest, DELETED_TABLES + PRESERVED_TABLES + ["audit", "document_approvals"])
        pre_fp = logical_fingerprint(dest)
        pre_integ, pre_fk = integrity_and_fk(dest)
        if not pre_integ:
            fail("source copy failed integrity_check before revocation")
        con = sqlite3.connect(str(dest))
        try:
            con.execute("BEGIN IMMEDIATE")
            deleted = {}
            for t in DELETED_TABLES:
                cur = con.execute(f'DELETE FROM "{t}"')
                deleted[t] = cur.rowcount
            cur = con.execute(
                "UPDATE document_approvals SET status='Expired' "
                "WHERE status='Prepared' AND approved_at IS NULL AND declined_at IS NULL")
            expired = cur.rowcount
            details = {"deleted": deleted, "document_approvals_expired": expired}
            con.execute(
                "INSERT INTO audit (actor, action, object_type, object_id, details_json, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("offline-recovery", "D01 restored sessions and tokens revoked",
                 "sessions", "all", json.dumps(details, sort_keys=True),
                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        post_counts = table_counts(dest, DELETED_TABLES + PRESERVED_TABLES + ["audit", "document_approvals"])
        post_fp = logical_fingerprint(dest)
        post_integ, post_fk = integrity_and_fk(dest)
        if not post_integ:
            fail("revoked copy failed integrity_check")
        if post_fk != 0:
            fail(f"revoked copy has foreign-key violations: {post_fk}")
        for t in DELETED_TABLES:
            if post_counts[t] != 0:
                fail(f"token table not empty after revocation: {t} ({post_counts[t]} rows)")
        for t in PRESERVED_TABLES:
            if post_counts[t] != pre_counts[t]:
                fail(f"preserved table changed during revocation: {t} ({pre_counts[t]} -> {post_counts[t]})")
        if post_counts["audit"] != pre_counts["audit"] + 1:
            fail("audit row count did not increase by exactly one")
        if post_counts["manual_source_submissions"] != pre_counts["manual_source_submissions"]:
            fail("original submissions changed during revocation")
    except SystemExit:
        raise
    except Exception as exc:
        if dest.exists():
            dest.unlink()
        raise SystemExit(f"REVOCATION FAILED (destination deleted): {type(exc).__name__}")
    summary = {"source": source.name, "destination": dest.name,
               "pre_fingerprint": pre_fp, "post_fingerprint": post_fp,
               "deleted": deleted, "document_approvals_expired": expired,
               "pre_counts": pre_counts, "post_counts": post_counts,
               "integrity": "ok", "foreign_key_violations": post_fk,
               "audit_delta": 1}
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
