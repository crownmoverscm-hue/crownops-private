"""FICTIONAL G5 ONLY: checked private file -> process memory -> locked factory.

No app imports, credentials in argv, shell substitutions, provider calls or
fallback. Source-only candidate; real Railway behavior remains unqualified.
"""
import base64
import os
import stat
import sys

KEY_PATH = '/data/private/fixture-app.key'
EXPECTED_UID = 10001
SCOPE = {
    'RAILWAY_PROJECT_ID': '57252bd0-e32f-45aa-9e5b-05bf4a1f8a85',
    'RAILWAY_ENVIRONMENT_ID': '3fbd111f-5a26-4e1c-80ce-af91fa6247ab',
    'RAILWAY_SERVICE_ID': '5ca1db2a-c51e-4476-b7f7-ba4a984075fc',
    'RAILWAY_VOLUME_MOUNT_PATH': '/data',
    'CROWNOPS_G5_FIXTURE_ACK': 'fictional-g5-only-no-real-accounts',
    'CROWNOPS_PROFILE': 'core-v1', 'CROWNOPS_ENV': 'pilot',
    'CROWNOPS_ALLOW_EXTERNAL': '0', 'CROWNOPS_RUN_WORKER': '0',
    'CROWNOPS_REQUIRE_MFA': '1', 'CROWNOPS_PILOT_ACK': 'manual-inquiry-only',
    'CROWNOPS_SECURE_COOKIES': '1',
}


def read_private_key(path=KEY_PATH, owner_uid=EXPECTED_UID):
    """Open every component without following links; validate the opened leaf."""
    if not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('Absolute private key path required')
    parts = path.split('/')[1:]
    if len(parts) < 2 or any(p in ('', '.', '..') for p in parts):
        raise ValueError('Private path refused')
    dflags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open('/', dflags)
    try:
        for part in parts[:-1]:
            following = os.open(part, dflags, dir_fd=parent)
            os.close(parent)
            parent = following
        directory = os.fstat(parent)
        if directory.st_uid != owner_uid or stat.S_IMODE(directory.st_mode) != 0o700:
            raise ValueError('Private parent refused')
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != owner_uid
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1
                    or before.st_size != 44):
                raise ValueError('Private key file refused')
            data = os.read(fd, 45)
            after = os.fstat(fd)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size,
                                       after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('Private file changed during read')
        finally:
            os.close(fd)
    finally:
        os.close(parent)
    if len(data) != 44:
        raise ValueError('Invalid private key length')
    decoded = base64.b64decode(data, altchars=b'-_', validate=True)
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded) != data:
        raise ValueError('Invalid private key format')
    return data.decode('ascii')


def launch():
    if any(x != EXPECTED_UID for x in (os.getuid(), os.geteuid(), os.getgid(), os.getegid())):
        raise PermissionError('Nonroot UID/GID10001 required')
    for name, wanted in SCOPE.items():
        if os.environ.get(name) != wanted:
            raise ValueError('Fictional startup scope refused')
    if os.environ.get('CROWNOPS_SECRET_KEY'):
        raise ValueError('Pre-existing application secret refused')
    if os.environ.get('CROWNOPS_DB') not in (
            '/data/private/pilot.sqlite3', '/data/private/g5-recovered.sqlite3'):
        raise ValueError('Fictional database path refused')
    port = os.environ.get('PORT', '8080')
    if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError('Invalid port')
    os.umask(0o077)
    # Core factory still verifies HTTPS origin, DB/schema/ownership and all D3 guards.
    os.environ['CROWNOPS_SECRET_KEY'] = read_private_key()
    args = [sys.executable, '-m', 'uvicorn', 'app.core_pilot:create_pilot_app',
            '--factory', '--host', '0.0.0.0', '--port', port,
            '--workers', '1', '--no-access-log']
    os.execv(sys.executable, args)


def main():
    try:
        launch()
    except Exception:
        # Do not print exception repr/path values, secret data, environment or traceback.
        print('REFUSED: fictional G5 web startup did not complete.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
