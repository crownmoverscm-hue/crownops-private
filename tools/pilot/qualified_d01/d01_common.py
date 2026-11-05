"""Shared helpers for D-01 offline provisioning and revocation tools.

No secret is ever printed by these helpers. Paths are checked for symlinks and
private permissions before use. The logical fingerprint is a hash of the
database's ordered SQL dump, stable across an exact backup/restore round-trip.
"""
import hashlib
import os
import sqlite3
import stat
from pathlib import Path


def fail(msg):
    raise SystemExit(f"REFUSED: {msg}")


def check_no_symlink(path, label):
    p = Path(path)
    for part in [p, *p.parents]:
        if part.is_symlink():
            fail(f"{label} path contains a symlink: {part}")
        if str(part) in (".", ""):
            break


def check_private_file(path, label):
    st = os.stat(path)
    if stat.S_IMODE(st.st_mode) != 0o600 or st.st_uid != os.getuid():
        fail(f"{label} must be mode 0600 owned by the current user: {path}")


def check_private_dir(path, label):
    st = os.stat(path)
    if stat.S_IMODE(st.st_mode) != 0o700 or st.st_uid != os.getuid():
        fail(f"{label} must be mode 0700 owned by the current user: {path}")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def logical_fingerprint(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dump = "\n".join(con.iterdump())
    finally:
        con.close()
    return hashlib.sha256(dump.encode()).hexdigest()


def table_counts(db_path, tables):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out = {}
        for t in tables:
            row = con.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
            if not row or row[0] == 0:
                out[t] = None
            else:
                out[t] = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        return out
    finally:
        con.close()


def integrity_and_fk(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        integ = con.execute("PRAGMA integrity_check").fetchall()
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        return (len(integ) == 1 and integ[0][0] == "ok"), len(fk)
    finally:
        con.close()
