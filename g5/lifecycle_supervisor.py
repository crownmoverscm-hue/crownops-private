"""Source-only G5 lifecycle overlay; no application imports in the supervisor.

Private files authorize a trusted Console operator, not a hostile same-UID user.
Locks coordinate this controller; they do not prevent raw SQLite/file access.
All actual root/container/Railway behavior still needs separate qualification.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import stat
import subprocess
import sys
import time
import uuid

MAX_CONTROL = 8192
MAX_JOURNAL = 8 * 1024 * 1024
APPROVAL = 'approve-fictional-g5-transition'
ID = re.compile(r'[a-f0-9]{32}\Z')
HASH = re.compile(r'[a-f0-9]{64}\Z')
TARGETS = ('pilot.sqlite3', 'g5-recovered.sqlite3')


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def strict_json(data):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ValueError('duplicate JSON key')
            out[k] = v
        return out
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('constant')))


class PrivateDir:
    """Descriptor-anchored files; no symlink walk or caller-controlled basename."""
    def __init__(self, path, uid=10001):
        p = Path(path)
        if not p.is_absolute() or '..' in p.parts:
            raise ValueError('absolute private path')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in p.parts[1:]:
                new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
                os.close(fd)
                fd = new
            s = os.fstat(fd)
            if (s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)) != (uid, uid, 0o700):
                raise ValueError('private directory mode/owner')
            self.fd, self.uid, self.path = fd, uid, p
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def name(name):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', name):
            raise ValueError('basename')
        return name

    def check(self, fd, limit):
        parent = os.fstat(self.fd)
        if (parent.st_uid, parent.st_gid, stat.S_IMODE(parent.st_mode)) != (self.uid,self.uid,0o700):
            raise ValueError('private parent drift')
        s = os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != self.uid
                or s.st_gid != self.uid or stat.S_IMODE(s.st_mode) != 0o600
                or s.st_size > limit):
            raise ValueError('private file')
        return s

    def read(self, name, limit=MAX_CONTROL):
        fd = os.open(self.name(name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=self.fd)
        try:
            before = self.check(fd, limit)
            chunks, size = [], 0
            while True:
                data = os.read(fd, min(65536, limit + 1 - size))
                if not data:
                    break
                chunks.append(data)
                size += len(data)
                if size > limit:
                    raise ValueError('size')
            after = self.check(fd, limit)
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            if any(getattr(before, f) != getattr(after, f) for f in fields):
                raise ValueError('changed during read')
            return b''.join(chunks)
        finally:
            os.close(fd)

    def create(self, name, data):
        name = self.name(name)
        if len(data) > MAX_JOURNAL:
            raise ValueError('size')
        # Write + fsync staging, then no-overwrite link + directory fsync.
        tmp = 'tmp-' + uuid.uuid4().hex
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            self.check(fd, MAX_JOURNAL)
            with os.fdopen(fd, 'wb', closefd=False) as f:
                f.write(data); f.flush(); os.fsync(fd)
            os.link(tmp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd,
                    follow_symlinks=False)
            os.unlink(tmp, dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            os.close(fd)
            try:
                os.unlink(tmp, dir_fd=self.fd)
                os.fsync(self.fd)
            except FileNotFoundError:
                pass

    def child(self, name):
        name = self.name(name)
        try:
            os.mkdir(name, 0o700, dir_fd=self.fd)
            os.fsync(self.fd)
        except FileExistsError:
            pass
        return PrivateDir(self.path / name, self.uid)

    def close(self):
        os.close(self.fd)


class Journal:
    """Fsync each intent/result. Truncation/tamper => refusal, never auto-repair.

    Hashes detect accidents; an unrestricted same-UID/file attacker can rewrite it.
    """
    def __init__(self, directory):
        self.d = directory
        self.records = []
        self.prev = '0' * 64
        try:
            raw = directory.read('journal.jsonl', MAX_JOURNAL)
        except FileNotFoundError:
            directory.create('journal.jsonl', b'')
            raw = b''
        if raw and not raw.endswith(b'\n'):
            raise ValueError('interrupted journal')
        for line in raw.splitlines():
            row = strict_json(line)
            if set(row) != {'n', 'prev', 'event', 'sha256'}:
                raise ValueError('journal schema')
            digest = row.pop('sha256')
            if (row['n'] != len(self.records) + 1 or row['prev'] != self.prev
                    or hashlib.sha256(encode(row)).hexdigest() != digest):
                raise ValueError('journal integrity')
            self.records.append(row['event'])
            self.prev = digest
        self.fd = os.open('journal.jsonl', os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW,
                          dir_fd=directory.fd)
        directory.check(self.fd, MAX_JOURNAL)

    def append(self, event):
        if os.fstat(self.fd).st_size > MAX_JOURNAL - 32768:
            raise ValueError('journal limit')
        row = {'n':len(self.records) + 1, 'prev':self.prev, 'event':event}
        digest = hashlib.sha256(encode(row)).hexdigest()
        raw = encode(dict(row, sha256=digest)) + b'\n'
        # A partial write is deliberately detectable after restart.
        if os.write(self.fd, raw) != len(raw):
            raise OSError('partial journal write')
        os.fsync(self.fd)
        self.records.append(event)
        self.prev = digest


def validate_request(r):
    if not isinstance(r, dict) or set(r) != {
        'format','operation','boot','version','action','approval','scope','data'}:
        raise ValueError('control schema')
    if (r['format'] != 1 or type(r['version']) is not int or r['version'] < 0
            or not isinstance(r['operation'], str) or not ID.fullmatch(r['operation'])
            or not isinstance(r['boot'], str) or not ID.fullmatch(r['boot'])
            or r['approval'] != APPROVAL or r['scope'] != 'fictional-g5-only'
            or not isinstance(r['data'], dict)):
        raise ValueError('control authorization')
    options = {
        'provision-fixture': set(), 'verify-fixture':set(), 'start-web':set(),
        'stop-web':set(), 'backup':set(), 'shutdown':set(),
        'verify-retrieved':{'receipt_sha256'}, 'restore-copy':{'receipt_sha256'},
        'select-recovered-copy':{'fingerprint'},
    }
    if r['action'] not in options or set(r['data']) != options[r['action']]:
        raise ValueError('action/arguments')
    if any(not isinstance(x, str) or not HASH.fullmatch(x) for x in r['data'].values()):
        raise ValueError('digest')
    return r


class Engine:
    def __init__(self, journal, actions):
        self.journal, self.actions = journal, actions
        self.boot = uuid.uuid4().hex
        self.state, self.version = 'HOLD', 0
        self.target, self.receipt, self.recovered = TARGETS[0], None, None
        self.completed = {}
        pending = set()
        previous = journal.records
        self.deadline = int(time.time()) + 24*3600
        for e in previous:
            if e.get('kind') == 'boot':
                deadline = e.get('deadline_utc')
                if type(deadline) is not int or deadline > self.deadline:
                    raise ValueError('deadline evidence')
                self.deadline = deadline
            if e.get('kind') == 'intent':
                pending.add(e['operation'])
            elif e.get('kind') == 'complete':
                pending.discard(e['operation'])
                self.completed[e['operation']] = e
                self.target = e.get('target', self.target)
                self.receipt = e.get('receipt_sha256', self.receipt)
                self.recovered = e.get('recovered_fingerprint', self.recovered)
        # Crash in WEB or unresolved intent must never become a clean hold.
        if pending or (previous and previous[-1].get('state') not in ('HOLD','READY','STOPPED')):
            raise ValueError('unsafe interrupted lifecycle; offline review required')
        if time.time() >= self.deadline:
            raise ValueError('qualification deadline expired')
        self.journal.append({'kind':'boot', 'boot':self.boot, 'state':'HOLD',
                             'deadline_utc':self.deadline})

    def status(self):
        return {'format':1, 'boot':self.boot, 'version':self.version,
                'state':self.state, 'target':self.target,
                'deadline_utc':self.deadline,
                'receipt_sha256':self.receipt, 'recovered_fingerprint':self.recovered}

    def apply(self, request):
        r = validate_request(request)
        digest = hashlib.sha256(encode(r)).hexdigest()
        if r['operation'] in self.completed:
            prior = self.completed[r['operation']]
            if prior['request_sha256'] != digest:
                raise ValueError('operation conflict')
            return dict(prior, duplicate=True)
        if (r['boot'] != self.boot or r['version'] != self.version
                or self.state in ('REFUSED','STOPPED')):
            raise ValueError('stale/state')
        action = r['action']
        if time.time() >= self.deadline and action != 'shutdown':
            raise ValueError('qualification deadline expired')
        allowed = {
            'HOLD': {'provision-fixture','verify-fixture','shutdown'},
            'READY': {'start-web','backup','verify-retrieved','restore-copy',
                      'select-recovered-copy','shutdown'},
            'WEB': {'stop-web','shutdown'},
        }
        if action not in allowed.get(self.state, set()):
            raise ValueError('phase denied')
        if action in ('verify-retrieved','restore-copy') and r['data']['receipt_sha256'] != self.receipt:
            raise ValueError('trusted receipt mismatch')
        if action == 'select-recovered-copy' and r['data']['fingerprint'] != self.recovered:
            raise ValueError('unverified recovered target')
        transient = 'DRAIN' if action in ('stop-web','shutdown') else 'TRANSITION'
        self.journal.append({'kind':'intent', 'operation':r['operation'],
                             'request_sha256':digest, 'action':action, 'state':transient})
        self.state = transient
        try:
            result = self.actions.run(action, self.target, r['data'])
            if not isinstance(result, dict) or set(result) - {'receipt_sha256','fingerprint'}:
                raise ValueError('unsanitized action result')
            for value in result.values():
                if not isinstance(value, str) or not HASH.fullmatch(value):
                    raise ValueError('invalid action evidence')
            if action == 'backup':
                self.receipt = result['receipt_sha256']
            if action == 'restore-copy':
                self.recovered = result['fingerprint']
            if action == 'select-recovered-copy':
                self.target = TARGETS[1]
            self.state = 'WEB' if action == 'start-web' else 'READY'
            if action == 'shutdown':
                self.state = 'STOPPED'
            self.version += 1
            event = {'kind':'complete','operation':r['operation'],
                     'request_sha256':digest,'action':action, **self.status(), **result}
            self.journal.append(event)
            self.completed[r['operation']] = event
            return event
        except BaseException:
            self.state = 'REFUSED'
            self.journal.append({'kind':'failed', 'operation':r['operation'],
                                 'state':'REFUSED', 'reason':'transition incomplete'})
            raise ValueError('transition incomplete; no automatic retry') from None


def process_snapshot(proc='/proc'):
    """Fail if any visible process disappears during its stat read: conservative."""
    if int((Path(proc)/'self'/'stat').read_text().split(' ',1)[0]) != os.getpid():
        raise ValueError('proc namespace does not match process namespace')
    out = {}
    for name in os.listdir(proc):
        if not name.isdecimal():
            continue
        raw = (Path(proc) / name / 'stat').read_text()
        tail = raw[raw.rfind(')') + 2:].split()
        out[int(name)] = {'ppid':int(tail[1]), 'group':int(tail[2]),
                          'start':int(tail[19]), 'state':tail[0]}
    return out


class Processes:
    """Only fixed Python child commands; no shell, network utility or arbitrary SQL."""
    def __init__(self, private, proc='/proc'):
        self.private, self.proc = Path(private), proc
        self.child = None

    def audit_writers(self):
        # Same-UID trusted Console may inspect/control but must not open DB files.
        # /proc cannot prove inaccessible processes safe; any denial aborts.
        wanted = {str(self.private / x) for n in TARGETS for x in (n,n+'-wal',n+'-shm',n+'-journal')}
        for pid in process_snapshot(self.proc):
            if pid == os.getpid():
                continue
            folder = Path(self.proc) / str(pid) / 'fd'
            for fd in folder.iterdir():
                dest = os.readlink(fd)
                if dest in wanted or dest.removesuffix(' (deleted)') in wanted:
                    raise ValueError('unexpected open database descriptor')

    def spawn(self, args):
        if self.child is not None:
            raise ValueError('child already running')
        env = {k:v for k,v in os.environ.items() if k not in (
            'PYTHONPATH','PYTHONHOME','LD_PRELOAD','LD_LIBRARY_PATH',
            'CROWNOPS_SECRET_KEY','CROWNOPS_G5_LOCK_FD')}
        self.child = subprocess.Popen(args, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=True, env=env)
        return self.child

    def stop(self, timeout=5):
        p = self.child
        if p is None:
            self.audit_writers()
            return
        # Children are launched in a distinct session/group, never a PID from disk.
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait(timeout=timeout)
        else:
            p.wait()
        snapshot = process_snapshot(self.proc)
        if any(v['group'] == p.pid for v in snapshot.values()):
            raise ValueError('process group remains')
        # A detached child reparented to PID1 (this supervisor in container) is
        # unknown, not proof of stopped work. Reject all remaining descendants.
        descendants = {pid for pid,v in snapshot.items() if v['ppid'] == os.getpid()}
        if descendants:
            raise ValueError('unexpected descendant')
        self.child = None
        self.audit_writers()

    def exclusive(self, target):
        self.audit_writers()
        path = self.private / target
        if os.path.lexists(path):
            d = PrivateDir(self.private, os.getuid())
            try:
                d.read(target, 64*1024*1024)
                for suffix in ('-wal','-shm','-journal'):
                    if os.path.lexists(str(path)+suffix):
                        d.read(target+suffix, 64*1024*1024)
            finally:
                d.close()
            # Short readiness probe, NOT a lock against arbitrary future writers.
            with contextlib.closing(sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, timeout=0)) as db:
                db.execute('BEGIN EXCLUSIVE'); db.rollback()


def no_transport():
    def guard(event, args):
        if event in ('socket.connect','socket.getaddrinfo','socket.gethostbyname',
                     'socket.gethostbyaddr','os.system','subprocess.Popen','os.posix_spawn'):
            raise RuntimeError('offline maintenance transport refused')
    sys.addaudithook(guard)


def job(action, target):
    """Fixed offline adapter; use unchanged maintenance functions, no real identity."""
    sys.path.insert(0, '/app')
    sys.path.insert(0, '/app/tools/pilot/maintenance')
    sys.path.insert(0, '/opt/g5')
    from lifecycle_entry import assert_unprivileged, validate_scope
    assert_unprivileged(); validate_scope(os.environ)
    os.umask(0o077)
    no_transport()
    import base64
    import secrets
    from cryptography.fernet import Fernet
    import core_maintenance as m
    from start_fictional_web import read_private_key
    d = PrivateDir('/data/private')
    state = d.child('lifecycle')
    out, incoming = d.child('export'), d.child('import')
    source = d.path / target
    if action == 'provision-fixture':
        if set(os.listdir(d.fd)) != {'lifecycle','export','import'}:
            raise ValueError('fresh data layout required')
        key = Fernet.generate_key()
        d.create('fixture-app.key', key)
        from app.security import totp
        step = int(time.time() // 30)
        accounts = []
        for role in ('owner','sales'):
            secret = base64.b32encode(secrets.token_bytes(20)).decode()
            accounts.append({'role':role,'email':role+'@example.invalid',
                             'name':'FICTIONAL '+role,'password':secrets.token_urlsafe(24),
                             'totp_secret':secret,'code':totp(secret, step)})
        m.provision(source, '/app', accounts, key)
        d.create('fixture-identities.json', encode(accounts))
        m.verify_ready(source, key, {'owner':'owner@example.invalid','sales':'sales@example.invalid'})
        result = {}
    else:
        appkey = read_private_key().encode()
        if action == 'verify-fixture':
            m.verify_ready(source, appkey, {'owner':'owner@example.invalid','sales':'sales@example.invalid'})
            result = {}
        elif action == 'backup':
            # Fixed one archive per qualification. Existing output is never replaced.
            key = Fernet.generate_key()
            d.create('fixture-backup.key', key)
            receipt = m.backup(source, out.path/'archive.enc', key,
                               '/data/uploads', True, app_key=appkey)
            raw = encode(receipt)
            out.create('receipt.json', raw)
            state.create('trusted-receipt.json', raw)
            result = {'receipt_sha256':hashlib.sha256(raw).hexdigest()}
        elif action in ('verify-retrieved','restore-copy'):
            key = m.key_file(d.path/'fixture-backup.key')
            # Both downloaded ciphertext and receipt must be reuploaded, and the
            # controller separately pins the original receipt digest.
            incoming.read('archive.enc', 128*1024*1024)
            incoming.read('receipt.json', MAX_JOURNAL)
            raw = state.read('trusted-receipt.json', MAX_JOURNAL)
            expected = strict_json(state.read('job-input.json'))
            if hashlib.sha256(raw).hexdigest() != expected['receipt_sha256']:
                raise ValueError('independently pinned receipt')
            record = strict_json(raw)
            m.verify_readback(incoming.path/'archive.enc', key, appkey,
                              incoming.path/'receipt.json', state.path/'trusted-receipt.json')
            if action == 'restore-copy':
                result_restore = m.restore(incoming.path/'archive.enc',
                    d.path/TARGETS[1], key, appkey, record)
                result = {'fingerprint':result_restore['post_revocation']['logical_sha256']}
                state.create('recovery-evidence.json', encode(result_restore))
            else:
                result = {}
        elif action == 'select-recovered-copy':
            m.verify_ready(d.path/TARGETS[1], appkey,
                           {'owner':'owner@example.invalid','sales':'sales@example.invalid'})
            saved = strict_json(state.read('recovery-evidence.json', MAX_JOURNAL))
            expected = strict_json(state.read('job-input.json'))
            if saved['post_revocation']['logical_sha256'] != expected['fingerprint']:
                raise ValueError('pinned recovery fingerprint')
            if m.summary(d.path/TARGETS[1])['logical_sha256'] != saved['post_revocation']['logical_sha256']:
                raise ValueError('recovered copy changed')
            result = {}
        else:
            raise ValueError('job denied')
    state.create('job-result.json', encode(result))


class Actions:
    def __init__(self, directory):
        self.d = directory
        self.p = Processes('/data/private')
        self.cancelled = False

    def run(self, action, target, data):
        if action in ('stop-web','shutdown'):
            self.p.stop()
            return {}
        self.p.exclusive(target)
        # Never overwrite child result. Any stale result after interruption is a
        # reconciliation hold, not a reason to delete and retry blindly.
        if action == 'start-web':
            os.environ['CROWNOPS_DB'] = '/data/private/' + target
            self.p.spawn([sys.executable, '-I', '/opt/g5/start_fictional_web.py'])
            # WEB means a supervised process was spawned, not HTTP/MFA readiness.
            # Readiness must be established in container/platform review.
            time.sleep(0.1)
            if self.p.child.poll() is not None:
                raise ValueError('web exited')
            return {}
        self.d.create('job-input.json', encode(data))
        p = self.p.spawn([sys.executable, '-I', '/opt/g5/lifecycle_supervisor.py',
                          '--job', action, target])
        deadline = time.monotonic() + 120
        while p.poll() is None:
            if self.cancelled or time.monotonic() >= deadline:
                self.p.stop()
                raise ValueError('maintenance interrupted/deadline')
            time.sleep(.05)
        code = p.wait()
        self.p.stop()
        if code:
            raise ValueError('maintenance failed')
        raw = self.d.read('job-result.json')
        result = strict_json(raw)
        # Retain each child result durably under a unique immutable receipt name.
        self.d.create('job-result-' + uuid.uuid4().hex + '.json', raw)
        os.unlink('job-result.json', dir_fd=self.d.fd)
        os.unlink('job-input.json', dir_fd=self.d.fd)
        os.fsync(self.d.fd)
        return result


def latest_status(directory):
    """Read-only CLI view, grounded in the last boot record, not filenames/mtime."""
    raw = directory.read('journal.jsonl', MAX_JOURNAL)
    if not raw or not raw.endswith(b'\n'):
        raise ValueError('journal not ready')
    boot = None
    prev = '0' * 64
    for n, line in enumerate(raw.splitlines(), 1):
        row = strict_json(line)
        digest = row.pop('sha256')
        if row.get('n') != n or row.get('prev') != prev or hashlib.sha256(encode(row)).hexdigest() != digest:
            raise ValueError('journal')
        prev = digest
        if row['event'].get('kind') == 'boot':
            boot = row['event']['boot']
    if not isinstance(boot, str) or not ID.fullmatch(boot):
        raise ValueError('boot')
    names = [n for n in os.listdir(directory.fd)
             if re.fullmatch('status-' + boot + r'-[0-9]+\.json', n)]
    if not names:
        raise ValueError('status not ready')
    name = max(names, key=lambda n:int(n.rsplit('-',1)[1][:-5]))
    return strict_json(directory.read(name))


def validate_state_files(directory, startup=False):
    fixed = {'journal.jsonl','trusted-receipt.json','recovery-evidence.json'}
    patterns = (r'(request|receipt|done)-[a-f0-9]{32}\.json',
                r'job-result-[a-f0-9]{32}\.json',
                r'status-[a-f0-9]{32}-[0-9]+\.json')
    for name in os.listdir(directory.fd):
        if name in ('job-input.json','job-result.json') and not startup:
            continue
        if name not in fixed and not any(re.fullmatch(p,name) for p in patterns):
            raise ValueError('unexpected/interrupted state file')
        # Validate metadata and reject symlinks/hardlinks even for receipts.
        directory.read(name, MAX_JOURNAL)


def main():
    sys.path.insert(0, '/opt/g5')
    from lifecycle_entry import assert_unprivileged, validate_scope
    try:
        assert_unprivileged(); validate_scope(os.environ)
        if len(sys.argv) == 4 and sys.argv[1] == '--job':
            if sys.argv[3] not in TARGETS:
                raise ValueError('target')
            job(sys.argv[2], sys.argv[3]); return 0
        if len(sys.argv) != 1 or os.getpid() != 1:
            raise ValueError('supervisor PID1 required')
        lock = int(os.environ.pop('CROWNOPS_G5_LOCK_FD'))
        if not stat.S_ISDIR(os.fstat(lock).st_mode) or not os.path.samefile('/data', '/proc/self/fd/'+str(lock)):
            raise ValueError('mount lock descriptor')
        # Parent retained the exclusive mount lock across exec; acquire again on
        # same open description and retain it for the entire lifecycle.
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.set_inheritable(lock, False)
        os.umask(0o077)
        private = PrivateDir('/data/private')
        state = private.child('lifecycle')
        validate_state_files(state, startup=True)
        actions = Actions(state)
        journal = Journal(state)
        engine = Engine(journal, actions)
        stopping = False
        def stop_signal(signum, frame):
            nonlocal stopping
            stopping = True
            actions.cancelled = True
        signal.signal(signal.SIGTERM, stop_signal)
        signal.signal(signal.SIGINT, stop_signal)
        start = time.monotonic()
        while not stopping and engine.state not in ('STOPPED','REFUSED'):
            validate_state_files(state)
            # Running-web child death is never treated as stopped/ready.
            if engine.state == 'WEB' and actions.p.child.poll() is not None:
                raise ValueError('unexpected web exit')
            if time.monotonic() - start >= 24*3600 or time.time() >= engine.deadline:
                stopping = True
                break
            raw = encode(engine.status())
            # Unique immutable status receipts; CLI reads the newest validated
            # sequence. No mutable state file is used as transition authority.
            name = 'status-' + engine.boot + '-' + str(engine.version) + '.json'
            try:
                state.create(name, raw)
            except FileExistsError:
                if state.read(name) != raw:
                    raise ValueError('status conflict')
            requests = sorted(n for n in os.listdir(state.fd) if re.fullmatch(r'request-[a-f0-9]{32}\.json',n))
            for name in requests:
                r = strict_json(state.read(name))
                if name != 'request-'+r.get('operation','')+'.json':
                    raise ValueError('operation filename mismatch')
                result = engine.apply(r)
                receipt = 'receipt-' + r['operation'] + '.json'
                try:
                    state.create(receipt, encode(result))
                except FileExistsError:
                    expected = dict(result)
                    expected.pop('duplicate', None)
                    if state.read(receipt) != encode(expected):
                        raise ValueError('existing transition receipt differs')
                # Keep request evidence: rename by no-overwrite link publication.
                archived = 'done-' + r['operation'] + '.json'
                if not os.path.exists(state.path/archived):
                    state.create(archived, state.read(name))
                os.unlink(name, dir_fd=state.fd); os.fsync(state.fd)
            time.sleep(0.1)
        actions.p.stop()
        journal.append({'kind':'exit','state':'STOPPED','boot':engine.boot})
        return 0
    except BaseException:
        # A terminal refusal leaves unresolved intents intact. Never log values.
        if 'actions' in locals():
            try:
                actions.p.stop()
            except BaseException:
                pass
        print('REFUSED: G5 lifecycle stopped; inspect private transition evidence.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
