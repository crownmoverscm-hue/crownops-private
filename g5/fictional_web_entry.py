"""Disposable G5 fictional-only startup bridge; never real staff/customer usage.
Reads existing 0600 fixture key without printing it or including it in argv.
"""
import base64
import os
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

G5_SCOPE = {
    "RAILWAY_PROJECT_ID": "57252bd0-e32f-45aa-9e5b-05bf4a1f8a85",
    "RAILWAY_ENVIRONMENT_ID": "3fbd111f-5a26-4e1c-80ce-af91fa6247ab",
    "RAILWAY_SERVICE_ID": "5ca1db2a-c51e-4476-b7f7-ba4a984075fc",
    "CROWNOPS_G5_FICTIONAL_ACK": "fictional-accounts-only",
    "CROWNOPS_PROFILE": "core-v1",
    "CROWNOPS_ENV": "pilot",
    "CROWNOPS_ALLOW_EXTERNAL": "0",
    "CROWNOPS_RUN_WORKER": "0",
    "CROWNOPS_REQUIRE_MFA": "1",
    "CROWNOPS_PILOT_ACK": "manual-inquiry-only",
    "CROWNOPS_SECURE_COOKIES": "1",
    "CROWNOPS_UPLOADS_ENABLED": "0",
    "RAILWAY_VOLUME_MOUNT_PATH": "/data",
}
KEY_FILE = "/data/private/fixture-app.key"


def validate_setup(env, *, uid, gid):
    if uid != 10001 or gid != 10001:
        raise PermissionError("G5 web entry requires non-root UID/GID 10001")
    for name, expected in G5_SCOPE.items():
        if env.get(name) != expected:
            raise ValueError("G5 fictional scope/configuration refused: " + name)
    origin = env.get("CROWNOPS_PUBLIC_URL", "")
    url = urlsplit(origin)
    if (url.scheme != "https" or not url.hostname or url.username is not None
            or url.password is not None or url.path or url.query or url.fragment
            or origin != f"https://{url.netloc}"):
        raise ValueError("G5 HTTPS public origin required")
    if env.get("CROWNOPS_DB") not in (
            "/data/private/pilot.sqlite3", "/data/private/g5-recovered.sqlite3"):
        raise ValueError("G5 fictional DB path refused")
    if "CROWNOPS_SECRET_KEY" in env:
        raise ValueError("G5 key must originate in private fixture file")


def read_private_key(file_path=KEY_FILE):
    p = Path(file_path)
    info = os.lstat(p.parent)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 10001
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("G5 private directory refused")
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 10001
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError("G5 fixture key ownership/mode refused")
        data = os.read(fd, 128)
    finally:
        os.close(fd)
    if len(data) != 44:
        raise ValueError("G5 fixture key format refused")
    try:
        if len(base64.urlsafe_b64decode(data)) != 32:
            raise ValueError("G5 fixture key format refused")
        return data.decode("ascii")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("G5 fixture key format refused") from exc


def main():
    os.umask(0o077)
    validate_setup(os.environ, uid=os.getuid(), gid=os.getgid())
    key = read_private_key()
    env = dict(os.environ)
    env["CROWNOPS_SECRET_KEY"] = key
    argv = [sys.executable, "-m", "uvicorn", "app.core_pilot:create_pilot_app",
            "--factory", "--host", "0.0.0.0", "--port",
            env.get("PORT", "8080"), "--workers", "1", "--no-access-log"]
    os.execve(sys.executable, argv, env)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, PermissionError) as exc:
        raise SystemExit("REFUSED: G5 fictional-only web entry not permitted") from exc
