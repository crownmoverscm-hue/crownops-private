"""Local gate model/static contract; NOT actual Actions scheduling or image tests."""
from pathlib import Path
import unittest

SOURCE='91cea018abf6cf1622532f841a5ae813517dba33'
BRANCH='refs/heads/authorize/g5-91cea01-once-v1'
HERE=Path(__file__).parent
WORKFLOW=(HERE/'g5-one-use-91cea01-v1.yml').read_text()

def allowed(event='push',repository='crownmoverscm-hue/crownops-private',ref=BRANCH,number='1',attempt='1'):
    return (event=='push' and repository=='crownmoverscm-hue/crownops-private'
            and ref==BRANCH and number=='1' and attempt=='1')

class GateTests(unittest.TestCase):
    def test_only_first_attempt_of_first_run_eligible(self):
        self.assertTrue(allowed())
        for n,a in [('1','2'),('2','1'),('2','2'),('10','1'),('','1')]:self.assertFalse(allowed(number=n,attempt=a))
    def test_wrong_branch_or_repository_refused(self):
        self.assertFalse(allowed(ref='refs/heads/main'));self.assertFalse(allowed(repository='other/repo'))
    def test_dispatch_pr_and_other_events_refused(self):
        for e in ['workflow_dispatch','pull_request','workflow_call','schedule']:self.assertFalse(allowed(event=e))
    def test_source_checkout_is_exact_reviewed_sha(self):
        self.assertIn('ref: '+SOURCE,WORKFLOW)
        self.assertIn('test "$(git rev-parse HEAD)" = '+SOURCE,WORKFLOW)
    def test_platform_job_gate_matches_local_predicates(self):
        for s in ["github.event_name == 'push'","github.repository == 'crownmoverscm-hue/crownops-private'","github.ref == '"+BRANCH+"'","github.run_number == '1'","github.run_attempt == '1'"]:self.assertIn(s,WORKFLOW)
    def test_no_dispatch_and_readonly_permission(self):
        self.assertNotIn('workflow_dispatch:',WORKFLOW);self.assertNotIn('contents: write',WORKFLOW)
        self.assertIn('contents: read',WORKFLOW);self.assertIn('persist-credentials: false',WORKFLOW)
    def test_serialized_and_one_build_command(self):
        self.assertIn('cancel-in-progress: false',WORKFLOW)
        self.assertEqual(WORKFLOW.count('docker build '),1)
    def test_controller_and_source_provenance_separate(self):
        for s in ['git-sha.txt','controller-sha.txt','run-identity.txt']:self.assertIn(s,WORKFLOW)
    def test_workflow_is_inert_here(self):
        self.assertFalse((HERE.parents[1]/'.github/workflows/g5-one-use-91cea01-v1.yml').exists())
    def test_canceled_or_failed_first_attempt_cannot_gain_second_slot(self):
        for first_outcome in ['failure','cancelled','success']:
            self.assertFalse(allowed(number='1',attempt='2'),first_outcome)
            self.assertFalse(allowed(number='2',attempt='1'),first_outcome)

if __name__=='__main__':unittest.main(verbosity=2)
