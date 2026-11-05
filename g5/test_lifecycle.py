"""Offline SOURCE tests. No image, real privilege drop, /data or Railway access.

Uses temporary fictional storage and benign local Python children only.
MFA/backup adapter execution and real capabilities remain container gates.
"""
import contextlib
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lifecycle_entry as entry
import lifecycle_supervisor as s
import lifecycle_ctl as ctl

NETWORK_ATTEMPTS = []
def offline_guard(event, args):
    if event in ('socket.connect','socket.getaddrinfo','socket.gethostbyname',
                 'socket.gethostbyaddr','os.system','os.posix_spawn'):
        NETWORK_ATTEMPTS.append(event)
        raise RuntimeError('network/shell attempt in source qualification')
sys.addaudithook(offline_guard)


class FakeActions:
    def __init__(self):
        self.calls = []
        self.fail = False
    def run(self, action, target, data):
        self.calls.append((action,target,data))
        if self.fail:
            raise RuntimeError('fictional failure (never emit payload)')
        if action == 'backup': return {'receipt_sha256':'a'*64}
        if action == 'restore-copy': return {'fingerprint':'b'*64}
        return {}


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='crownops-g5-source-fictional-')
        self.path = Path(self.tmp.name)
        self.path.chmod(0o700)
        self.d = s.PrivateDir(self.path, os.getuid())
        self.j = s.Journal(self.d)
        self.a = FakeActions()
        self.e = s.Engine(self.j, self.a)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.d.close)
        self.addCleanup(lambda:os.close(self.j.fd))

    def req(self, action, data=None):
        return {'format':1,'operation':os.urandom(16).hex(),'boot':self.e.boot,
            'version':self.e.version,'action':action,'approval':s.APPROVAL,
            'scope':'fictional-g5-only','data':data or {}}

    def apply(self, action, data=None):
        return self.e.apply(self.req(action,data))

    def ready(self): self.apply('verify-fixture')

    def test_initial_hold_no_automatic_web(self):
        self.assertEqual(self.e.state,'HOLD'); self.assertEqual(self.a.calls,[])

    def test_allowed_fixture_then_web_then_stop(self):
        self.apply('provision-fixture'); self.apply('start-web'); self.apply('stop-web')
        self.assertEqual(self.e.state,'READY')
        self.assertEqual([x[0] for x in self.a.calls],['provision-fixture','start-web','stop-web'])

    def test_backup_refused_while_web_running(self):
        self.ready(); self.apply('start-web')
        with self.assertRaises(ValueError): self.apply('backup')
        self.assertEqual(len(self.a.calls),2)

    def test_no_provision_repeated_after_ready(self):
        self.ready()
        with self.assertRaises(ValueError): self.apply('provision-fixture')

    def test_old_version_refused(self):
        old=self.req('verify-fixture'); self.ready()
        with self.assertRaises(ValueError): self.e.apply(old)

    def test_old_boot_refused(self):
        r=self.req('verify-fixture');r['boot']='f'*32
        with self.assertRaises(ValueError): self.e.apply(r)

    def test_identical_retry_no_second_mutation(self):
        r=self.req('verify-fixture');self.e.apply(r)
        result=self.e.apply(r)
        self.assertTrue(result['duplicate']);self.assertEqual(len(self.a.calls),1)

    def test_operation_id_content_conflict_refused(self):
        r=self.req('verify-fixture');self.e.apply(r);r['action']='shutdown'
        with self.assertRaises(ValueError):self.e.apply(r)

    def test_missing_approval_refused(self):
        r=self.req('verify-fixture');r.pop('approval')
        with self.assertRaises(ValueError):self.e.apply(r)

    def test_wrong_scope_refused(self):
        r=self.req('verify-fixture');r['scope']='production'
        with self.assertRaises(ValueError):self.e.apply(r)

    def test_arbitrary_command_refused(self):
        r=self.req('verify-fixture');r['action']='shell'
        with self.assertRaises(ValueError):self.e.apply(r)

    def test_arbitrary_path_refused(self):
        r=self.req('verify-fixture');r['data']={'path':'/business.db'}
        with self.assertRaises(ValueError):self.e.apply(r)

    def test_bad_digest_refused(self):
        self.ready()
        with self.assertRaises(ValueError):self.apply('restore-copy',{'receipt_sha256':'abc'})

    def test_restore_without_pinned_receipt_refused(self):
        self.ready()
        with self.assertRaises(ValueError):self.apply('restore-copy',{'receipt_sha256':'a'*64})

    def test_wrong_receipt_refused(self):
        self.ready();self.apply('backup')
        with self.assertRaises(ValueError):self.apply('restore-copy',{'receipt_sha256':'b'*64})

    def test_restore_target_selection_explicit(self):
        self.ready();self.apply('backup');self.apply('restore-copy',{'receipt_sha256':'a'*64})
        self.assertEqual(self.e.target,'pilot.sqlite3')
        self.apply('select-recovered-copy',{'fingerprint':'b'*64})
        self.assertEqual(self.e.target,'g5-recovered.sqlite3')

    def test_unverified_target_selection_refused(self):
        self.ready()
        with self.assertRaises(ValueError):self.apply('select-recovered-copy',{'fingerprint':'b'*64})

    def test_action_failure_is_terminal_and_intent_retained(self):
        self.a.fail=True
        with self.assertRaises(ValueError):self.ready()
        self.assertEqual(self.e.state,'REFUSED')
        self.assertEqual(self.j.records[-1]['kind'],'failed')
        with self.assertRaises(ValueError):self.apply('shutdown')

    def test_crash_after_intent_refuses_new_engine(self):
        self.j.append({'kind':'intent','operation':'c'*32,'state':'TRANSITION'})
        with self.assertRaises(ValueError):s.Engine(self.j,FakeActions())

    def test_crash_in_web_refuses_new_engine(self):
        self.ready();self.apply('start-web')
        with self.assertRaises(ValueError):s.Engine(self.j,FakeActions())

    def test_clean_restart_holds_and_retains_selected_copy(self):
        self.ready();self.apply('backup');self.apply('restore-copy',{'receipt_sha256':'a'*64})
        self.apply('select-recovered-copy',{'fingerprint':'b'*64});self.apply('shutdown')
        new=s.Engine(self.j,FakeActions())
        self.assertEqual(new.state,'HOLD');self.assertEqual(new.target,'g5-recovered.sqlite3')
        self.assertNotEqual(new.boot,self.e.boot)

    def test_restart_duplicate_never_reexecutes(self):
        r=self.req('verify-fixture');self.e.apply(r);self.apply('shutdown')
        a=FakeActions();new=s.Engine(self.j,a)
        self.assertTrue(new.apply(r)['duplicate']);self.assertEqual(a.calls,[])

    def test_deadline_not_extended_on_restart(self):
        deadline=self.e.deadline;self.apply('shutdown')
        new=s.Engine(self.j,FakeActions())
        self.assertEqual(new.deadline,deadline)

    def test_expired_deadline_refuses_new_mutation(self):
        self.e.deadline=int(time.time())-1
        with self.assertRaises(ValueError):self.ready()
        self.assertEqual(self.a.calls,[])

    def test_journal_partial_tail_refused(self):
        os.write(self.j.fd,b'{partial');os.fsync(self.j.fd)
        with self.assertRaises(ValueError):s.Journal(self.d)

    def test_journal_hash_corruption_refused(self):
        p=self.path/'journal.jsonl';raw=p.read_bytes();p.write_bytes(raw.replace(b'HOLD',b'FAIL'))
        with self.assertRaises(ValueError):s.Journal(self.d)

    def test_symlink_control_refused(self):
        self.d.create('safe.json',b'{}');(self.path/'link.json').symlink_to('safe.json')
        with self.assertRaises(OSError):self.d.read('link.json')

    def test_hardlinked_control_refused(self):
        self.d.create('safe.json',b'{}');os.link(self.path/'safe.json',self.path/'link.json')
        with self.assertRaises(ValueError):self.d.read('safe.json')

    def test_loose_control_refused(self):
        self.d.create('safe.json',b'{}');(self.path/'safe.json').chmod(0o644)
        with self.assertRaises(ValueError):self.d.read('safe.json')

    def test_control_wrong_owner_metadata_refused(self):
        with mock.patch.object(self.d,'uid',os.getuid()+1):
            with self.assertRaises(ValueError):self.d.read('journal.jsonl')

    def test_control_oversize_refused(self):
        self.d.create('large.json',b'x'*(s.MAX_CONTROL+1))
        with self.assertRaises(ValueError):self.d.read('large.json')

    def test_parent_permission_drift_refused(self):
        self.path.chmod(0o755)
        with self.assertRaises(ValueError):self.d.read('journal.jsonl')
        self.path.chmod(0o700)

    def test_partial_journal_append_is_detected(self):
        original=os.write
        def partial(fd, data):return original(fd,data[:max(1,len(data)//2)])
        with mock.patch('lifecycle_supervisor.os.write',side_effect=partial):
            with self.assertRaises(OSError):self.j.append({'kind':'test','state':'HOLD'})
        with self.assertRaises(ValueError):s.Journal(self.d)

    def test_serial_sequential_retries_count_one(self):
        r=self.req('verify-fixture')
        for _ in range(5):self.e.apply(r)
        self.assertEqual(len(self.a.calls),1)

    def test_completed_receipt_precedes_retry_admission(self):
        r=self.req('verify-fixture');self.e.apply(r)
        self.assertEqual(self.j.records[-1]['operation'],r['operation'])
        self.assertEqual(self.j.records[-1]['kind'],'complete')
        self.assertEqual(self.e.apply(r)['request_sha256'],hashlib.sha256(s.encode(r)).hexdigest())

    def test_fifo_control_refused_without_block(self):
        os.mkfifo(self.path/'fifo.json',0o600)
        with self.assertRaises(ValueError):self.d.read('fifo.json')

    def test_path_traversal_refused(self):
        with self.assertRaises(ValueError):self.d.create('../unsafe',b'x')

    def test_directory_symlink_walk_refused(self):
        (self.path/'link').symlink_to(self.path,target_is_directory=True)
        with self.assertRaises(OSError):s.PrivateDir(self.path/'link',os.getuid())

    def test_atomic_no_overwrite_preserves_original(self):
        self.d.create('same.json',b'original')
        with self.assertRaises(FileExistsError):self.d.create('same.json',b'replacement')
        self.assertEqual(self.d.read('same.json'),b'original')

    def test_duplicate_json_keys_refused(self):
        with self.assertRaises(ValueError):s.strict_json(b'{"action":1,"action":2}')

    def test_nonfinite_json_refused(self):
        with self.assertRaises(ValueError):s.strict_json(b'{"v":NaN}')

    def test_unknown_or_interrupted_state_file_refused(self):
        self.d.create('tmp-interrupted',b'x')
        with self.assertRaises(ValueError):s.validate_state_files(self.d,True)

    def test_stale_job_result_refused_at_restart(self):
        self.d.create('job-result.json',b'{}')
        with self.assertRaises(ValueError):s.validate_state_files(self.d,True)

    def test_private_request_publish_and_readback(self):
        name='status-'+self.e.boot+'-0.json';self.d.create(name,s.encode(self.e.status()))
        result=ctl.publish_request(self.d,'verify-fixture',s.APPROVAL)
        req=s.strict_json(self.d.read('request-'+result['operation']+'.json'))
        self.assertFalse(result['completed']);self.e.apply(req)
        self.assertEqual(self.e.state,'READY')

    def test_status_uses_journal_boot_not_file_mtime(self):
        self.d.create('status-'+self.e.boot+'-0.json',s.encode(self.e.status()))
        self.d.create('status-'+'f'*32+'-999.json',b'{"wrong":true}')
        self.assertEqual(s.latest_status(self.d)['boot'],self.e.boot)

    def test_fake_action_sensitive_result_refused(self):
        self.a.run=lambda *a:{'password':'never-output'}
        with self.assertRaises(ValueError):self.ready()
        self.assertNotIn('never-output',self.d.read('journal.jsonl',s.MAX_JOURNAL).decode())

    def test_parallel_publication_same_operation_one_winner(self):
        # Two genuine parallel local processes race the same no-overwrite target.
        code='import sys;sys.path.insert(0,sys.argv[1]);from lifecycle_supervisor import PrivateDir;import os;d=PrivateDir(sys.argv[2],os.getuid());d.create("race.json",b"fictional")'
        ps=[subprocess.Popen([sys.executable,'-c',code,str(HERE),str(self.path)],
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) for _ in range(2)]
        self.assertEqual(sorted(p.wait(timeout=10) for p in ps),[0,1])
        self.assertEqual(self.d.read('race.json'),b'fictional')

    def test_parallel_lifetime_lock_refused(self):
        fd=os.open(self.path,os.O_RDONLY|os.O_DIRECTORY)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            code='import os,sys,fcntl;f=os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY);fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
            p=subprocess.run([sys.executable,'-c',code,str(self.path)],capture_output=True,timeout=10)
            self.assertNotEqual(p.returncode,0)
        finally:os.close(fd)


class BoundaryTests(unittest.TestCase):
    def require_proc_namespace(self):
        actual=int(Path('/proc/self/stat').read_text().split(' ',1)[0])
        if actual != os.getpid():
            self.skipTest('Workspace /proc PID namespace differs; actual container proof required')

    def test_all_scope_guards_missing_and_incorrect(self):
        entry.validate_scope(dict(entry.SCOPE))
        for key in entry.SCOPE:
            for missing in (False,True):
                env=dict(entry.SCOPE)
                if missing:env.pop(key)
                else:env[key]='wrong'
                with self.subTest(key=key,missing=missing):
                    with self.assertRaises(ValueError):entry.validate_scope(env)

    def test_secret_environment_refused(self):
        with self.assertRaises(ValueError):entry.validate_scope(dict(entry.SCOPE,CROWNOPS_SECRET_KEY='fictional'))

    def test_loader_environment_refused(self):
        for name in ('LD_PRELOAD','LD_LIBRARY_PATH','PYTHONHOME','PYTHONPATH'):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):entry.validate_scope(dict(entry.SCOPE,**{name:'unsafe'}))

    def test_identity_matrix(self):
        good={'Uid':'10001 '*3+'10001','Gid':'10001 '*3+'10001','Groups':'',
              'CapInh':'0','CapPrm':'0','CapEff':'0','CapBnd':'0','CapAmb':'0','NoNewPrivs':'1'}
        entry.assert_unprivileged(good)
        for key in good:
            bad=dict(good);bad[key]='0' if key in ('Uid','Gid','NoNewPrivs') else '1'
            with self.subTest(key=key):
                with self.assertRaises(ValueError):entry.assert_unprivileged(bad)

    def test_privilege_drop_call_order_with_injected_syscalls(self):
        events=[]
        lib=mock.Mock()
        lib.prctl.side_effect=lambda *a:events.append(('prctl',)+a) or 0
        lib.capset.side_effect=lambda *a:events.append(('capset',)) or 0
        api=mock.Mock()
        for name in ('setgroups','setresgid','setresuid'):
            getattr(api,name).side_effect=lambda *args,n=name:events.append((n,)+args)
        entry.drop_privileges(lib,api)
        self.assertLess(next(i for i,e in enumerate(events) if e[0]=='setgroups'),
                        next(i for i,e in enumerate(events) if e[0]=='setresgid'))
        self.assertLess(next(i for i,e in enumerate(events) if e[0]=='setresgid'),
                        next(i for i,e in enumerate(events) if e[0]=='setresuid'))
        self.assertEqual(events[-1],('prctl',38,1,0,0,0))

    def test_failed_drop_syscall_refuses(self):
        lib=mock.Mock();lib.prctl.return_value=-1
        with self.assertRaises(PermissionError):entry.drop_privileges(lib,mock.Mock())

    def test_missing_mfa_entry_refuses_before_filesystem(self):
        env=dict(entry.SCOPE);env.pop('CROWNOPS_REQUIRE_MFA')
        with mock.patch.dict(os.environ,env,clear=True), mock.patch('lifecycle_entry.os.open') as opened, contextlib.redirect_stderr(__import__('io').StringIO()):
            self.assertEqual(entry.main(),1)
        opened.assert_not_called()

    def test_wrong_project_entry_refuses_before_filesystem(self):
        env=dict(entry.SCOPE);env['RAILWAY_PROJECT_ID']='wrong'
        with mock.patch.dict(os.environ,env,clear=True), mock.patch('lifecycle_entry.os.open') as opened, contextlib.redirect_stderr(__import__('io').StringIO()):
            self.assertEqual(entry.main(),1)
        opened.assert_not_called()

    def test_partial_bootstrap_layout_refused_no_prepare(self):
        with tempfile.TemporaryDirectory(prefix='g5-layout-fixture-') as tmp:
            fd=os.open(tmp,os.O_RDONLY|os.O_DIRECTORY)
            try:
                (Path(tmp)/'private').mkdir()
                # Host ownership is injected; no real chown or privileged helper.
                info=mock.Mock(st_uid=0,st_mode=0o40700)
                prepare=mock.Mock()
                with mock.patch('lifecycle_entry.os.fstat',return_value=info):
                    with self.assertRaises(ValueError):entry.prepare_layout(fd,prepare)
                prepare.assert_not_called()
            finally:os.close(fd)

    def test_scope_extra_file_refuses_without_chown(self):
        with tempfile.TemporaryDirectory(prefix='g5-layout-fixture-') as tmp:
            (Path(tmp)/'unexpected').write_text('fictional')
            fd=os.open(tmp,os.O_RDONLY|os.O_DIRECTORY)
            try:
                prepare=mock.Mock()
                with mock.patch('lifecycle_entry.os.fstat',return_value=mock.Mock(st_uid=0,st_mode=0o40700)):
                    with self.assertRaises(ValueError):entry.prepare_layout(fd,prepare)
                prepare.assert_not_called()
            finally:os.close(fd)

    def test_process_group_real_child_stop(self):
        self.require_proc_namespace()
        p=s.Processes('/nonexistent-fictional')
        # /proc global writer audit is separately tested with fixture snapshots;
        # this test proves actual child termination, not Railway visibility.
        p.audit_writers=lambda:None
        child=p.spawn([sys.executable,'-c','import time;time.sleep(30)'])
        p.stop(timeout=1)
        self.assertIsNotNone(child.returncode);self.assertIsNone(p.child)

    def test_real_child_ignoring_term_is_killed(self):
        self.require_proc_namespace()
        p=s.Processes('/nonexistent-fictional');p.audit_writers=lambda:None
        child=p.spawn([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print("ready",flush=True);time.sleep(30)'])
        time.sleep(.15)
        p.stop(timeout=.1)
        self.assertIsNotNone(child.returncode)

    def test_second_managed_child_refused(self):
        self.require_proc_namespace()
        p=s.Processes('/nonexistent-fictional');p.audit_writers=lambda:None
        p.spawn([sys.executable,'-c','import time;time.sleep(30)'])
        try:
            with self.assertRaises(ValueError):p.spawn([sys.executable,'-c','pass'])
        finally:p.stop(timeout=1)

    def test_proc_permission_failure_refuses_stop(self):
        p=s.Processes('/nonexistent-fictional')
        with mock.patch('lifecycle_supervisor.process_snapshot',side_effect=PermissionError):
            with self.assertRaises(PermissionError):p.stop()

    def test_sqlite_exclusive_probe_rejects_writer(self):
        with tempfile.TemporaryDirectory(prefix='g5-fictional-sqlite-') as tmp:
            path=Path(tmp)/'pilot.sqlite3';db=sqlite3.connect(path)
            path.chmod(0o600)
            db.execute('CREATE TABLE fictional(n)');db.commit();db.execute('BEGIN IMMEDIATE')
            p=s.Processes(tmp);p.audit_writers=lambda:None
            try:
                with self.assertRaises(sqlite3.OperationalError):p.exclusive('pilot.sqlite3')
            finally:db.rollback();db.close()

    def test_database_symlink_refused_before_sqlite_probe(self):
        with tempfile.TemporaryDirectory(prefix='g5-fictional-path-') as tmp:
            (Path(tmp)/'pilot.sqlite3').symlink_to('/not-an-approved-database')
            p=s.Processes(tmp);p.audit_writers=lambda:None
            with self.assertRaises(OSError):p.exclusive('pilot.sqlite3')

    def test_missing_database_probe_does_not_create_database(self):
        with tempfile.TemporaryDirectory(prefix='g5-fictional-path-') as tmp:
            p=s.Processes(tmp);p.audit_writers=lambda:None;p.exclusive('pilot.sqlite3')
            self.assertFalse((Path(tmp)/'pilot.sqlite3').exists())

    def test_no_application_imports_in_controller_modules(self):
        # Jobs defer app imports until a fixed separate offline child execution.
        import ast
        for name in ('lifecycle_entry.py','lifecycle_supervisor.py','lifecycle_ctl.py'):
            tree=ast.parse((HERE/name).read_text())
            for node in tree.body:
                if isinstance(node,(ast.Import,ast.ImportFrom)):
                    module=getattr(node,'module','') or ''
                    self.assertFalse(module.startswith('app'))

    def test_source_compile_and_proposed_config(self):
        for p in HERE.glob('lifecycle_*.py'):compile(p.read_text(),str(p),'exec')
        import tomllib
        config=tomllib.loads((HERE/'railway.lifecycle.toml').read_text())
        self.assertEqual(config['deploy']['restartPolicyType'],'NEVER')
        self.assertEqual(config['deploy']['numReplicas'],1)
        self.assertNotIn('healthcheckPath',config['deploy'])

    def test_zero_actual_source_test_network_attempts(self):
        self.assertEqual(NETWORK_ATTEMPTS,[])

    def test_wrong_proc_namespace_refuses(self):
        with tempfile.TemporaryDirectory(prefix='g5-fake-proc-') as tmp:
            (Path(tmp)/'self').mkdir()
            (Path(tmp)/'self'/'stat').write_text(str(os.getpid()+123)+' (fictional) R 0')
            with self.assertRaises(ValueError):s.process_snapshot(tmp)

    def test_stop_decision_with_mocked_empty_snapshot(self):
        p=s.Processes('/fictional');child=mock.Mock();child.pid=12345
        child.poll.return_value=None;p.child=child;p.audit_writers=mock.Mock()
        with mock.patch('lifecycle_supervisor.os.killpg') as kill, mock.patch('lifecycle_supervisor.process_snapshot',return_value={}):
            p.stop(timeout=.1)
        kill.assert_called_once_with(12345,signal.SIGTERM)
        child.wait.assert_called_once_with(timeout=.1)
        p.audit_writers.assert_called_once();self.assertIsNone(p.child)

    def test_stop_decision_refuses_remaining_group(self):
        p=s.Processes('/fictional');child=mock.Mock();child.pid=12345
        child.poll.return_value=0;p.child=child;p.audit_writers=mock.Mock()
        with mock.patch('lifecycle_supervisor.process_snapshot',return_value={3:{'ppid':1,'group':12345}}):
            with self.assertRaises(ValueError):p.stop()
        p.audit_writers.assert_not_called()

    def test_stop_decision_refuses_remaining_descendant(self):
        p=s.Processes('/fictional');child=mock.Mock();child.pid=12345
        child.poll.return_value=0;p.child=child;p.audit_writers=mock.Mock()
        with mock.patch('lifecycle_supervisor.process_snapshot',return_value={3:{'ppid':os.getpid(),'group':9}}):
            with self.assertRaises(ValueError):p.stop()
        p.audit_writers.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
