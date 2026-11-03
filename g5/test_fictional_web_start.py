"""Fictional private-file/exec tests; no app, network, subprocess or live key."""
import base64
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('fixture_start', Path(__file__).with_name('start_fictional_web.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
# Fixed public test vector, not a generated operational key or real credential.
TEST_VECTOR = base64.urlsafe_b64encode(bytes(32))

class PrivateKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='g5-key-file-fictional-')
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name) / 'private'
        self.parent.mkdir(mode=0o700)
        self.key = self.parent / 'fixture-app.key'
        self.key.write_bytes(TEST_VECTOR); self.key.chmod(0o600)

    def read(self, **kw):
        return m.read_private_key(str(self.key), owner_uid=kw.get('uid', os.getuid()))

    def test_valid_private_file(self):
        self.assertEqual(self.read(), TEST_VECTOR.decode())
    def test_missing_file(self):
        self.key.unlink()
        with self.assertRaises(OSError): self.read()
    def test_loose_key_file(self):
        self.key.chmod(0o644)
        with self.assertRaises(ValueError): self.read()
    def test_loose_parent(self):
        self.parent.chmod(0o755)
        with self.assertRaises(ValueError): self.read()
    def test_wrong_owner(self):
        with self.assertRaises(ValueError): self.read(uid=os.getuid()+1)
    def test_wrong_leaf_owner_with_correct_parent(self):
        real_fstat=m.os.fstat
        def altered(fd):
            info=real_fstat(fd)
            if m.stat.S_ISREG(info.st_mode):
                return SimpleNamespace(st_mode=info.st_mode,st_uid=os.getuid()+1)
            return info
        with patch.object(m.os,'fstat',side_effect=altered):
            with self.assertRaises(ValueError):self.read()
    def test_changed_file_during_read_refused(self):
        real_read=m.os.read
        def changed(fd,n):
            data=real_read(fd,n)
            info=self.key.stat()
            os.utime(self.key,ns=(info.st_atime_ns,info.st_mtime_ns+1000000000))
            return data
        with patch.object(m.os,'read',side_effect=changed):
            with self.assertRaises(ValueError):self.read()
    def test_symlink_leaf(self):
        other=self.parent/'other';self.key.rename(other);self.key.symlink_to(other)
        with self.assertRaises(OSError): self.read()
    def test_symlink_parent(self):
        actual=self.parent.with_name('actual');self.parent.rename(actual);self.parent.symlink_to(actual,target_is_directory=True)
        with self.assertRaises(OSError): self.read()
    def test_hard_link(self):
        os.link(self.key,self.parent/'second')
        with self.assertRaises(ValueError): self.read()
    def test_nonregular_fifo(self):
        self.key.unlink();os.mkfifo(self.key,0o600)
        with self.assertRaises(ValueError): self.read()
    def test_blank_file(self):
        self.key.write_bytes(b'')
        with self.assertRaises(ValueError): self.read()
    def test_invalid_base64(self):
        self.key.write_bytes(b'!'*44)
        with self.assertRaises(ValueError): self.read()
    def test_oversized_key(self):
        self.key.write_bytes(TEST_VECTOR+b'\n')
        with self.assertRaises(ValueError): self.read()
    def test_relative_and_traversal_refused(self):
        for path in ('relative.key','/data/../fixture-app.key'):
            with self.assertRaises(ValueError): m.read_private_key(path)

class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.env={**m.SCOPE,'CROWNOPS_DB':'/data/private/pilot.sqlite3','CROWNOPS_PUBLIC_URL':'https://example.invalid'}
    @contextlib.contextmanager
    def prepared(self, **overrides):
        with contextlib.ExitStack() as s:
            s.enter_context(patch.dict(os.environ,{**self.env,**overrides},clear=True))
            for f in ('getuid','geteuid','getgid','getegid'):s.enter_context(patch.object(m.os,f,return_value=10001))
            s.enter_context(patch.object(m.os,'umask'))
            reader=s.enter_context(patch.object(m,'read_private_key',return_value=TEST_VECTOR.decode()))
            execute=s.enter_context(patch.object(m.os,'execv'))
            yield reader,execute
    def test_valid_exec_is_locked_without_secret_in_argv_or_output(self):
        with self.prepared() as (reader,execute):
            out=io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(out):m.launch()
            reader.assert_called_once();execute.assert_called_once()
            args=execute.call_args.args[1]
            self.assertIn('app.core_pilot:create_pilot_app',args)
            self.assertIn('--factory',args);self.assertIn('--no-access-log',args)
            self.assertNotIn(TEST_VECTOR.decode(),str(args));self.assertEqual(out.getvalue(),'')
    def test_missing_mfa_never_reads_secret_or_executes(self):
        with self.prepared(CROWNOPS_REQUIRE_MFA='') as (reader,execute):
            with self.assertRaises(ValueError):m.launch()
            reader.assert_not_called();execute.assert_not_called()
    def test_wrong_scope_never_executes(self):
        with self.prepared(RAILWAY_SERVICE_ID='wrong') as (_,execute):
            with self.assertRaises(ValueError):m.launch()
            execute.assert_not_called()
    def test_root_never_reads_key(self):
        with self.prepared() as (reader,execute),patch.object(m.os,'geteuid',return_value=0):
            with self.assertRaises(PermissionError):m.launch()
            reader.assert_not_called();execute.assert_not_called()
    def test_existing_environment_key_refused(self):
        with self.prepared(CROWNOPS_SECRET_KEY='TEST inert conflict') as (reader,execute):
            with self.assertRaises(ValueError):m.launch()
            reader.assert_not_called();execute.assert_not_called()
    def test_bad_key_stops_exec(self):
        with self.prepared() as (reader,execute):
            reader.side_effect=ValueError('TEST refused')
            with self.assertRaises(ValueError):m.launch()
            execute.assert_not_called()
    def test_entrypoint_refusal_never_prints_error_or_secret(self):
        with patch.object(m,'launch',side_effect=ValueError(TEST_VECTOR.decode())):
            out=io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(out):
                self.assertEqual(m.main(),1)
            self.assertNotIn(TEST_VECTOR.decode(),out.getvalue())
            self.assertEqual(out.getvalue(),'REFUSED: fictional G5 web startup did not complete.\n')
    def test_bad_port_stops_exec(self):
        with self.prepared(PORT='8080; command') as (reader,execute):
            with self.assertRaises(ValueError):m.launch()
            reader.assert_not_called();execute.assert_not_called()
    def test_wrong_database_stops_exec(self):
        with self.prepared(CROWNOPS_DB='/data/private/other.sqlite3') as (_,execute):
            with self.assertRaises(ValueError):m.launch()
            execute.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
