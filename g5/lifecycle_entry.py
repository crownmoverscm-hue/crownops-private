"""G5-only entrypoint. NOT authorized for a container/platform run by this commit.

No application import or data/key operation before irreversible privilege drop.
Runtime Console UID remains a separate platform acceptance gate.
"""
import ctypes
import fcntl
import os
import resource
import stat
import sys

UID = 10001
ACK = 'reviewed-single-lifecycle-fictional-only'
SCOPE = {
    'RAILWAY_PROJECT_ID': '57252bd0-e32f-45aa-9e5b-05bf4a1f8a85',
    'RAILWAY_ENVIRONMENT_ID': '3fbd111f-5a26-4e1c-80ce-af91fa6247ab',
    'RAILWAY_SERVICE_ID': '5ca1db2a-c51e-4476-b7f7-ba4a984075fc',
    'RAILWAY_VOLUME_MOUNT_PATH': '/data',
    'CROWNOPS_G5_LIFECYCLE_ACK': ACK,
    'CROWNOPS_G5_FIXTURE_ACK': 'fictional-g5-only-no-real-accounts',
    'CROWNOPS_PROFILE': 'core-v1', 'CROWNOPS_ENV': 'pilot',
    'CROWNOPS_ALLOW_EXTERNAL': '0', 'CROWNOPS_RUN_WORKER': '0',
    'CROWNOPS_REQUIRE_MFA': '1', 'CROWNOPS_PILOT_ACK': 'manual-inquiry-only',
    'CROWNOPS_SECURE_COOKIES': '1',
}


def validate_scope(env):
    if any(env.get(k) != v for k, v in SCOPE.items()):
        raise ValueError('scope')
    if env.get('CROWNOPS_SECRET_KEY'):
        raise ValueError('secret variable')
    if any(env.get(k) for k in ('LD_PRELOAD','LD_LIBRARY_PATH','PYTHONHOME','PYTHONPATH')):
        raise ValueError('loader environment')


def private_directory(path, uid=UID):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    s = os.fstat(fd)
    if s.st_uid != uid or s.st_gid != uid or stat.S_IMODE(s.st_mode) != 0o700:
        os.close(fd)
        raise ValueError('directory')
    return fd


def identity():
    values = {}
    with open('/proc/self/status', encoding='ascii') as stream:
        for line in stream:
            key, _, value = line.partition(':')
            if key in ('Uid', 'Gid', 'Groups', 'CapInh', 'CapPrm', 'CapEff',
                       'CapBnd', 'CapAmb', 'NoNewPrivs'):
                values[key] = value.strip()
    return values


def assert_unprivileged(values=None):
    v = identity() if values is None else values
    if v.get('Uid', '').split() != [str(UID)] * 4:
        raise ValueError('uid')
    if v.get('Gid', '').split() != [str(UID)] * 4:
        raise ValueError('gid')
    if v.get('Groups', '').split() not in ([], [str(UID)]):
        raise ValueError('groups')
    for name in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
        if name not in v or int(v[name], 16) != 0:
            raise ValueError('capabilities')
    if v.get('NoNewPrivs') != '1':
        raise ValueError('no new privileges')
    return v


def drop_privileges(libc=None, os_api=os):
    """Linux only. Test using injected APIs; never run as host root for QA."""
    if libc is None:
        libc = ctypes.CDLL(None, use_errno=True)
    def prctl(*args):
        if libc.prctl(*args) != 0:
            raise PermissionError('prctl')
    # Clear ambient caps, disable keepcaps, drop every supported bounding cap.
    prctl(47, 4, 0, 0, 0)  # PR_CAP_AMBIENT / CLEAR_ALL
    prctl(8, 0, 0, 0, 0)   # PR_SET_KEEPCAPS
    with open('/proc/sys/kernel/cap_last_cap', encoding='ascii') as f:
        last = int(f.read())
    if not 0 <= last <= 63:
        raise ValueError('capability range')
    for cap in range(last + 1):
        prctl(24, cap, 0, 0, 0)  # PR_CAPBSET_DROP
    os_api.setgroups([])
    os_api.setresgid(UID, UID, UID)
    os_api.setresuid(UID, UID, UID)
    # Explicitly clear any inherited/permitted/effective capability set.
    class Header(ctypes.Structure):
        _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32),
                    ('inheritable', ctypes.c_uint32)]
    if libc.capset(ctypes.byref(Header(0x20080522, 0)), (Data * 2)()) != 0:
        raise PermissionError('capset')
    prctl(38, 1, 0, 0, 0)  # PR_SET_NO_NEW_PRIVS


def prepare_layout(root_fd, prepare):
    """Root metadata only; never read data/key files in a populated directory."""
    st = os.fstat(root_fd)
    if st.st_uid != 0 or stat.S_IMODE(st.st_mode) & 0o022:
        raise ValueError('mount')
    names = set(os.listdir(root_fd))
    if names - {'private', 'uploads'}:
        raise ValueError('mount contents')
    if not names:
        prepare()
    elif names != {'private', 'uploads'}:
        raise ValueError('partial bootstrap')
    else:
        # Prepared layouts are never chowned/repaired. Empty existing directories
        # can also be resumed without mutating ownership.
        for name in sorted(names):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=root_fd)
            try:
                s = os.fstat(fd)
                if (s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)) != (UID, UID, 0o700):
                    raise ValueError('prepared metadata')
            finally:
                os.close(fd)


def main():
    try:
        validate_scope(os.environ)
        os.umask(0o077)
        # Never retain an inherited arbitrary FD from a privileged launcher.
        ceiling = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        if ceiling == resource.RLIM_INFINITY:
            raise ValueError('unbounded inherited descriptors')
        os.closerange(3, ceiling)
        root = os.open('/data', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fcntl.flock(root, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.geteuid() == 0:
            sys.path.insert(0, '/opt/g5')
            from prepare_empty_volume import prepare
            prepare_layout(root, prepare)
            drop_privileges()
        assert_unprivileged()
        # Held mount lock is the only inherited privileged-opened descriptor.
        os.set_inheritable(root, True)
        # Do not pass a caller-controlled import path or loader into subprocesses.
        for name in ('PYTHONPATH', 'PYTHONHOME', 'LD_PRELOAD', 'LD_LIBRARY_PATH',
                     'CROWNOPS_BOOTSTRAP_ACK', 'RAILWAY_RUN_UID'):
            os.environ.pop(name, None)
        os.environ['CROWNOPS_G5_LOCK_FD'] = str(root)
        os.execv(sys.executable, [sys.executable, '-I',
                 '/opt/g5/lifecycle_supervisor.py'])
    except Exception:
        print('REFUSED: G5 lifecycle entry failed; no web fallback.', file=sys.stderr)
        return 1
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
