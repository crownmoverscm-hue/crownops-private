"""Review draft tests; synthetic inert wheel bytes, no package install/build."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import create_lifecycle_context as c


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='g5-context-fictional-')
        self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.out=self.root/'output.tar'
        for p in ('g5','build/wheelhouse'): (self.root/p).mkdir(parents=True,exist_ok=True)
        self.write('g5/original.py',b'original')
        self.write('g5/controller.py',b'fictional overlay')
        wheels={}
        for i in range(35):
            name=f'fictional-{i}.whl';data=f'INERT NOT A PACKAGE {i}'.encode()
            self.write('build/wheelhouse/'+name,data);wheels[name]=self.sha(data)
        pins={'source_sha256':{'g5/original.py':self.sha(b'original')},'wheel_sha256':wheels}
        raw=json.dumps(pins).encode();self.write('g5/CONTEXT-PINS.json',raw)
        self.m={'legacy_context_pins_sha256':self.sha(raw),'wheel_count':35,
                'parent_runtime_sha256':{},'lifecycle_sha256':{'g5/controller.py':self.sha(b'fictional overlay')},
                'canonical_dockerfile':'g5/Dockerfile.g5-browser-source'}
        patch=mock.patch.object(c,'approved_manifest',return_value=self.m);patch.start();self.addCleanup(patch.stop)
    def sha(self,b):return hashlib.sha256(b).hexdigest()
    def write(self,p,b):(self.root/p).write_bytes(b)
    def run_context(self):return c.prepare(self.root,self.out)
    def test_valid_offline_context(self):
        r=self.run_context();self.assertEqual(r['wheels_verified'],35);self.assertFalse(r['docker_executed'])
    def test_original_changed_refused(self):
        self.write('g5/original.py',b'changed')
        with self.assertRaises(ValueError):self.run_context()
    def test_overlay_changed_refused(self):
        self.write('g5/controller.py',b'changed')
        with self.assertRaises(ValueError):self.run_context()
    def test_legacy_pin_manifest_changed_refused(self):
        self.write('g5/CONTEXT-PINS.json',b'{}')
        with self.assertRaises(ValueError):self.run_context()
    def test_missing_wheel_refused(self):
        (self.root/'build/wheelhouse/fictional-0.whl').unlink()
        with self.assertRaises(ValueError):self.run_context()
    def test_extra_wheel_refused(self):
        self.write('build/wheelhouse/extra.whl',b'x')
        with self.assertRaises(ValueError):self.run_context()
    def test_wrong_wheel_hash_refused(self):
        self.write('build/wheelhouse/fictional-0.whl',b'changed')
        with self.assertRaises(ValueError):self.run_context()
    def test_wheel_symlink_refused(self):
        p=self.root/'build/wheelhouse/fictional-0.whl';p.unlink();p.symlink_to('../outside')
        with self.assertRaises(OSError):self.run_context()
    def test_symlink_source_refused(self):
        p=self.root/'g5/controller.py';p.unlink();p.symlink_to('original.py')
        with self.assertRaises(OSError):self.run_context()
    def test_path_traversal_refused(self):
        self.m['lifecycle_sha256']={'../outside':'f'*64}
        with self.assertRaises(ValueError):self.run_context()
    def test_existing_output_preserved(self):
        self.out.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):self.run_context()
        self.assertEqual(self.out.read_bytes(),b'keep')
    def test_unlisted_secret_excluded(self):
        self.write('g5/not-allowed.key',b'FICTIONAL SENTINEL')
        self.run_context();import tarfile
        with tarfile.open(self.out) as a:self.assertNotIn('g5/not-allowed.key',a.getnames())
    def test_hardlink_source_refused(self):
        os.link(self.root/'g5/controller.py',self.root/'g5/other')
        with self.assertRaises(ValueError):self.run_context()


if __name__=='__main__':unittest.main(verbosity=2)
