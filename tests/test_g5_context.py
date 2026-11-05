"""Packaging-only tests with tiny fictional fixtures; no Docker or downloads."""
import importlib.util,json,tempfile,unittest,tarfile,hashlib
from pathlib import Path

spec=importlib.util.spec_from_file_location('context',Path(__file__).parents[1]/'g5/create_build_context.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class ContextTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory(prefix='g5-context-fictional-');self.addCleanup(self.t.cleanup)
        self.r=Path(self.t.name);(self.r/'app').mkdir();(self.r/'build/wheelhouse').mkdir(parents=True)
        (self.r/'app/source.py').write_text('TEST source\n');(self.r/'build/wheelhouse/approved.whl').write_bytes(b'TEST fictional wheel')
        (self.r/'.dockerignore').write_text('**\n!app/**\n')
        self.pins={'source_sha256':{'app/source.py':m.digest(b'TEST source\n')},'wheel_sha256':{'approved.whl':m.digest(b'TEST fictional wheel')}}
        self.out=self.r/'result.tar'

    def test_exact_context_excludes_git_evidence_private_and_untracked_files(self):
        for name in ['app/untracked-secret.txt','app/test.sqlite3','.env','image.tar','evidence.json']:(self.r/name).write_text('TEST not real secret')
        result=m.prepare(self.r,self.out,self.pins)
        with tarfile.open(self.out) as t:self.assertEqual(set(t.getnames()),{'.dockerignore','app/source.py','build/wheelhouse/approved.whl'})
        self.assertFalse(result['docker_executed'])

    def test_source_tamper_refuses_before_publication(self):
        (self.r/'app/source.py').write_text('TEST altered')
        with self.assertRaisesRegex(ValueError,'source hash'):m.prepare(self.r,self.out,self.pins)
        self.assertFalse(self.out.exists())

    def test_unapproved_wheel_refuses(self):
        (self.r/'build/wheelhouse/extra.whl').write_bytes(b'TEST')
        with self.assertRaisesRegex(ValueError,'Unexpected'):m.prepare(self.r,self.out,self.pins)
        self.assertFalse(self.out.exists())

    def test_wheel_tamper_refuses(self):
        (self.r/'build/wheelhouse/approved.whl').write_bytes(b'TEST changed')
        with self.assertRaisesRegex(ValueError,'Wheel hash'):m.prepare(self.r,self.out,self.pins)

    def test_symlink_source_refused(self):
        p=self.r/'app/source.py';p.unlink();p.symlink_to(self.r/'.dockerignore')
        with self.assertRaisesRegex(ValueError,'Unsafe source'):m.prepare(self.r,self.out,self.pins)

    def test_existing_output_untouched(self):
        self.out.write_bytes(b'TEST existing')
        with self.assertRaises(FileExistsError):m.prepare(self.r,self.out,self.pins)
        self.assertEqual(self.out.read_bytes(),b'TEST existing')

    def test_symlink_wheel_parent_refused(self):
        (self.r/'build').rename(self.r/'moved-build');(self.r/'build').symlink_to(self.r/'moved-build',target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'nonsymlink wheelhouse'):m.prepare(self.r,self.out,self.pins)
        self.assertFalse(self.out.exists())

    def test_output_bytes_deterministic_for_identical_inputs(self):
        first=m.prepare(self.r,self.out,self.pins)
        second=m.prepare(self.r,self.r/'second.tar',self.pins)
        self.assertEqual(first['tar_sha256'],second['tar_sha256'])

if __name__=='__main__':unittest.main(verbosity=2)
