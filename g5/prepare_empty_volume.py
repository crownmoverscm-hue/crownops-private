"""Bounded empty-volume helper for owner-authorized FICTIONAL container tests.

No Railway privileged execution is authorized. No database/app/secret/web is
created. A separately approved future maintenance deployment could execute this
once as root, then must be removed before UID-10001 web startup.
"""
import json
import os
import stat
import uuid

TARGET_UID = 10001
TARGET_GID = 10001


def validate_context(env):
    required = {
        'CROWNOPS_BOOTSTRAP_ACK': 'fresh-empty-core-v1-volume-only',
        'CROWNOPS_PROFILE': 'core-v1', 'CROWNOPS_ENV': 'pilot',
        'CROWNOPS_ALLOW_EXTERNAL': '0', 'CROWNOPS_RUN_WORKER': '0',
        'CROWNOPS_REQUIRE_MFA': '1', 'RAILWAY_VOLUME_MOUNT_PATH': '/data',
    }
    for name, value in required.items():
        if env.get(name) != value:
            raise ValueError('Bootstrap context refused: ' + name)
    for kind in ('PROJECT', 'ENVIRONMENT', 'SERVICE'):
        wanted = env.get('CROWNOPS_BOOTSTRAP_' + kind + '_ID', '')
        actual = env.get('RAILWAY_' + kind + '_ID', '')
        try:
            uuid.UUID(wanted)
        except ValueError:
            raise ValueError('Approved isolated scope ID required') from None
        if wanted != actual:
            raise ValueError('Bootstrap scope mismatch: ' + kind)
    if os.getuid() != 0 or os.geteuid() != 0:
        raise PermissionError('Approved maintenance UID 0 required; never web UID')


def prepare(env=None):
    validate_context(os.environ if env is None else env)
    os.umask(0o077)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root = os.open('/data', flags)
    opened = []
    try:
        st = os.fstat(root)
        if st.st_uid != 0 or stat.S_IMODE(st.st_mode) & 0o022:
            raise ValueError('Mount parent ownership/mode refused')
        if set(os.listdir(root)) - {'private', 'uploads'}:
            raise ValueError('Fresh pilot volume required; unexpected entries')
        # Validate every existing directory before any creation/chown. Never
        # recurse, overwrite a DB, follow symlinks or repair a populated volume.
        existing = {}
        for name in ('private', 'uploads'):
            if name in os.listdir(root):
                fd = os.open(name, flags, dir_fd=root)
                opened.append(fd)
                info = os.fstat(fd)
                if info.st_uid not in (0, TARGET_UID) or os.listdir(fd):
                    raise ValueError('Empty trusted private directory required')
                existing[name] = fd
        for name in ('private', 'uploads'):
            fd = existing.get(name)
            if fd is None:
                os.mkdir(name, 0o700, dir_fd=root)
                fd = os.open(name, flags, dir_fd=root)
                opened.append(fd)
            os.fchown(fd, TARGET_UID, TARGET_GID)
            os.fchmod(fd, 0o700)
            os.fsync(fd)
        os.fsync(root)
    finally:
        for fd in opened:
            os.close(fd)
        os.close(root)
    return {'prepared_directories': 2, 'uid': TARGET_UID, 'gid': TARGET_GID,
            'directory_mode': '0700', 'database_created': False,
            'web_started': False, 'secrets_accessed': False}


if __name__ == '__main__':
    try:
        print(json.dumps(prepare(), sort_keys=True))
    except Exception:
        # Preserve partial preparation for manual inspection. Do not blindly
        # delete/chown recursively or fall back to running the application.
        raise SystemExit('REFUSED: bootstrap incomplete; no application started')
