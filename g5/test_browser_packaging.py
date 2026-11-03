"""Static boundaries only; does not claim Docker or network qualification."""
from pathlib import Path
import hashlib,json,re,unittest
HERE=Path(__file__).parent
DOCKER=(HERE/'Dockerfile.g5-browser-source').read_text()
BASE='docker.io/library/python@sha256:70bda1e795ded2a684ef9815955e05829b14ee5bb5c3eb2a940648e88f10fce9'
class PackagingTests(unittest.TestCase):
    def test_only_exact_base_in_both_stages(self):
        self.assertEqual([s.split(' AS ')[0] for s in DOCKER.splitlines() if s.startswith('FROM ')],['FROM '+BASE]*2)
        self.assertNotIn('ARG PYTHON_BASE',DOCKER)
    def test_binary_hash_required_no_deps_acquisition(self):
        self.assertEqual(DOCKER.count('python -m pip download'),1)
        for s in ['--only-binary=:all: --no-deps --require-hashes','requirements-python313.lock']:self.assertIn(s,DOCKER)
    def test_install_offline_and_checked(self):
        for s in ['--no-index --find-links=/wheels --no-deps --require-hashes','python -m pip check','PIP_DISABLE_PIP_VERSION_CHECK=1']:self.assertIn(s,DOCKER)
    def test_single_fixed_nonroot_web_launcher(self):
        self.assertIn('USER 10001:10001',DOCKER)
        self.assertEqual([s for s in DOCKER.splitlines() if s.startswith('CMD ')],['CMD ["python", "/opt/g5/start_fictional_web.py"]'])
        self.assertNotIn('cat /data',DOCKER);self.assertNotIn('app.main',DOCKER)
    def test_no_extra_package_source_or_whole_checkout_copy(self):
        for s in ['apt-get','npm ','curl ','wget ','COPY . .','ADD https:']:self.assertNotIn(s,DOCKER)
    def test_manifest_expected_counts(self):
        pins=json.loads((HERE/'CONTEXT-PINS.json').read_text())
        self.assertEqual(len(pins['source_sha256']),184);self.assertEqual(len(pins['wheel_sha256']),35)
    def test_explicit_dockerignore_exists(self):
        text=(HERE.parent/'.dockerignore').read_text()
        for s in ['**/*.key','**/*.sqlite3','**/.env','**/uploads/','**/.venv/','**/evidence/']:self.assertIn(s,text)
    def test_no_secret_value_in_dockerfile(self):
        self.assertNotIn('CROWNOPS_SECRET_KEY=',DOCKER)
    def test_build_activation_must_match_reviewed_draft(self):
        draft=HERE/'review-ci/g5-browser-build.yml'
        active=HERE.parent/'.github/workflows/g5-browser-build.yml'
        self.assertTrue(draft.is_file())
        if active.exists():self.assertEqual(active.read_bytes(),draft.read_bytes())
    def test_source_workflow_has_no_image_acquisition(self):
        text=(HERE.parent/'.github/workflows/g5-no-cli-source-checks.yml').read_text()
        for s in ['docker pull','docker build','pip install','railway up']:self.assertNotIn(s,text)
        self.assertIn('55b431a3085acac42846179566b8d2e7daf07a8f',text)
if __name__=='__main__':unittest.main(verbosity=2)
