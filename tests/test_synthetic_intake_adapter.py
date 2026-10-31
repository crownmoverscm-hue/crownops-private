"""New adapter qualification only: ASGI requests, empty disposable DB, no listener."""
from contextlib import closing
import hashlib
import json
import io
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from synthetic_intake.adapter import create_adapter,verify_core,CORE_BUILD,ENV
from synthetic_intake.isolation import OutboundBlocked,network_none_inventory

TOKEN='DISPOSABLE-MOCK-QUALIFICATION-ONLY-'+'x'*32


class AdapterTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,ENV,clear=True);env.start();self.addCleanup(env.stop)
        temp=tempfile.TemporaryDirectory(prefix='crownops-adapter-test-',dir='/tmp');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.clock_value=1000
        self.app=create_adapter(self.root,TOKEN,clock=lambda:self.clock_value)
        self.run=self.app.state.synthetic;self.addCleanup(self.run.close)
        self.client=TestClient(self.app);self.addCleanup(self.client.close)
        self.client.headers['authorization']='Bearer '+TOKEN
        self.body={'scenario':'oct24-rendered-v1','fixture_set_sha256':self.run.fixture_set_hash}

    def post(self,body=None):return self.client.post('/synthetic/intake/replay',json=body or self.body)

    def test_real_ingestion_one_batch_one_retry_empty_pricing_and_zero_downstream(self):
        self.assertTrue(all(not r for r in self.run.snapshot().values()))
        first=self.post();self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(first.json()['summary']['new_leads'],4)
        before=self.run.snapshot();retry=self.post();self.assertEqual(retry.status_code,200,retry.text)
        self.assertEqual(retry.json()['summary']['duplicates'],4);self.assertEqual(before,self.run.snapshot())
        evidence=self.client.get('/test-evidence').json()
        for k,n in {'client_accounts':1,'leads':4,'client_account_leads':4,'integration_events':4,'messages':4,'tasks':7,'intake_drafts':4}.items():self.assertEqual(evidence['counts'][k],n,k)
        for table in ('quotes','outbound_messages','jobs','assignments','payment_links','deposit_links','payments','job_invoices'):self.assertEqual(evidence['counts'][table],0,table)
        for draft in evidence['records']['intake_drafts']:
            self.assertIsNone(draft['quote_id']);self.assertIn('approved pricing setup',json.loads(draft['missing_json']))
        for f in self.run.fixtures:
            ev=next(x for x in evidence['records']['integration_events'] if x['event_key']=='gmail:OCT24-'+f['id'])
            self.assertEqual(json.loads(ev['payload_json'])['body'],f['body'])
            self.assertEqual(hashlib.sha256(f['body'].encode()).hexdigest(),evidence['fixture_body_hashes'][f['id']])
        self.assertEqual(evidence['outbound']['total_blocked_attempts'],0)
        self.assertEqual(len(evidence['mock_feed_calls']),8)
        self.assertFalse(evidence['core_routes_mounted']);self.assertFalse(evidence['core_lifespan_entered'])
        self.assertFalse(evidence['outbound']['os_isolation_qualified'])
        self.assertEqual(self.post().status_code,409)

    def test_only_five_minimal_routes_and_no_core_or_docs_surface(self):
        self.assertEqual({r.path for r in self.app.routes},{'/health','/identity','/test-status','/test-evidence','/synthetic/intake/replay'})
        paths=['/docs','/openapi.json','/api/login','/api/users','/api/gmail/sync-readonly','/api/webhooks/intake','/api/owner-backups','/api/portal/accept','/api/jobs','/api/payments','/api/wix-drafts/create','/api/qbo/send']
        for path in paths:
            self.assertEqual(self.client.post(path,json={}).status_code,404,path)
        self.assertTrue(all(not r for r in self.run.snapshot().values()))

    def test_missing_or_wrong_auth_cannot_read_private_evidence_or_write(self):
        for value in ('','Bearer wrong'):
            self.client.headers['authorization']=value
            for path in ('/test-status','/test-evidence'):self.assertEqual(self.client.get(path).status_code,401)
            self.assertEqual(self.post().status_code,401)
        self.assertEqual(self.run.state['phase'],'READY')

    def test_cookie_or_browser_origin_is_not_a_csrf_capability(self):
        for header,value in [('origin','https://example.invalid'),('cookie','crownops_session=not-a-test-capability')]:
            self.client.headers[header]=value;self.assertEqual(self.post().status_code,403);del self.client.headers[header]
        self.assertEqual(self.run.state['phase'],'READY')

    def test_manifest_hash_extra_fields_or_wrong_scenario_rejected_before_any_write(self):
        for body in ({**self.body,'fixture_set_sha256':'0'*64},{**self.body,'source_body':'forged'},
                     {**self.body,'scenario':'anything-else'},[],None):
            response=self.client.post('/synthetic/intake/replay',json=body)
            self.assertIn(response.status_code,(409,415,422))
        self.assertTrue(all(not r for r in self.run.snapshot().values()))

    def test_duplicate_json_keys_size_and_non_json_are_rejected(self):
        self.assertEqual(self.client.post('/synthetic/intake/replay',content='{"scenario":"x","scenario":"y"}',headers={'content-type':'application/json'}).status_code,422)
        self.assertEqual(self.client.post('/synthetic/intake/replay',content='x'*1025,headers={'content-type':'application/json'}).status_code,413)
        self.assertEqual(self.client.post('/synthetic/intake/replay',content='{}').status_code,415)
        self.assertEqual(self.run.state['phase'],'READY')

    def test_capability_expiry_has_no_mutation(self):
        self.clock_value+=1801;self.assertEqual(self.post().status_code,401)
        self.assertEqual(self.run.state['phase'],'READY')

    def test_no_provider_credentials_users_or_pricing_seeded(self):
        with closing(self.run.connect(self.run.path)) as db:
            for t in ('users','sessions','pricing_versions','annual_rate_periods'):self.assertEqual(db.execute('SELECT count(*) FROM '+t).fetchone()[0],0)
        self.assertEqual(self.client.get('/identity').json()['core_build'],CORE_BUILD)
        self.assertFalse(self.client.get('/identity').json()['acceptance'])

    def test_network_transports_are_denied_before_native_call(self):
        import requests,urllib.request,subprocess
        calls=[lambda:requests.get('https://never-contact.example.invalid'),
               lambda:urllib.request.urlopen('https://never-contact.example.invalid'),
               lambda:socket.getaddrinfo('never-contact.example.invalid',443),
               lambda:socket.create_connection(('192.0.2.1',443)),
               lambda:subprocess.Popen(['not-a-command'])]
        with socket.socket() as sock:
            calls.extend([lambda:sock.connect(('192.0.2.1',443)),lambda:sock.connect_ex(('192.0.2.1',443))])
            for call in calls:
                with self.assertRaises(OutboundBlocked):call()
        self.assertEqual(self.run.guard.evidence()['total_blocked_attempts'],7)

    def test_blocked_attempt_prevents_a_false_zero_request_pass_and_locks_retries(self):
        with self.assertRaises(OutboundBlocked):socket.getaddrinfo('never-contact.example.invalid',443)
        self.assertEqual(self.post().status_code,409)
        self.assertEqual(self.run.state['phase'],'AMBIGUOUS');before=self.run.snapshot()
        self.assertEqual(before['leads'],[])
        self.assertEqual(self.post().status_code,409);self.assertEqual(before,self.run.snapshot())

    def test_partial_result_is_terminal_ambiguous_not_blindly_retried(self):
        original=self.run.mock_api
        def partial(token,path,params=None):
            result=original(token,path,params)
            if path=='messages':result['messages']=result['messages'][:2]
            return result
        with patch.object(self.run,'mock_api',side_effect=partial):self.assertEqual(self.post().status_code,409)
        self.assertEqual(len(self.run.snapshot()['leads']),2);self.assertEqual(self.run.state['phase'],'AMBIGUOUS')
        before=self.run.snapshot();self.assertEqual(self.post().status_code,409);self.assertEqual(before,self.run.snapshot())
        self.assertEqual(json.loads((self.root/'operation.json').read_text())['phase'],'AMBIGUOUS')

    def test_in_progress_operation_has_no_second_claim(self):
        self.run.lock.acquire()
        try:self.assertEqual(self.post().status_code,409)
        finally:self.run.lock.release()
        self.assertEqual(self.run.state['phase'],'READY')

    def test_uncaught_process_interruption_leaves_running_journal_nonretryable(self):
        with patch.object(self.run,'ingest',side_effect=SystemExit('simulated interruption')):
            with self.assertRaises(SystemExit):self.run.replay()
        self.assertEqual(json.loads((self.root/'operation.json').read_text())['phase'],'FIRST_RUNNING')
        self.assertEqual(self.post().status_code,409)

    def test_capability_never_appears_in_logs_evidence_or_errors(self):
        self.post();self.post()
        outputs=(self.root/'operation.json').read_text()+(self.root/'adapter-audit.jsonl').read_text()+self.client.get('/test-evidence').text+self.client.get('/identity').text
        self.assertNotIn(TOKEN,outputs);self.assertNotIn('authorization',outputs.lower())

    def test_existing_run_cannot_be_reopened_or_recreated(self):
        self.post();before=self.run.snapshot()
        with self.assertRaises(RuntimeError):create_adapter(self.root,TOKEN)
        self.assertEqual(before,self.run.snapshot())


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        p=patch.dict(os.environ,ENV,clear=True);p.start();self.addCleanup(p.stop)
        d=tempfile.TemporaryDirectory(prefix='crownops-admission-',dir='/tmp');self.addCleanup(d.cleanup);self.root=Path(d.name)

    def test_rejects_production_worker_external_and_inherited_credentials_before_db(self):
        for key,value in [('CROWNOPS_ENV','production'),('CROWNOPS_RUN_WORKER','1'),('CROWNOPS_ALLOW_EXTERNAL','1'),('WIX_API_KEY','not-real'),('CROWNOPS_SECRET_KEY','not-real'),('RAILWAY_PROJECT_ID','not-real')]:
            with patch.dict(os.environ,{key:value}):
                with self.assertRaises(RuntimeError):create_adapter(self.root,TOKEN)
            self.assertEqual(list(self.root.iterdir()),[])

    def test_nonempty_root_and_symlink_rejected_without_overwrite(self):
        old=self.root/'old.sqlite3';old.write_text('untouched')
        with self.assertRaises(RuntimeError):create_adapter(self.root,TOKEN)
        self.assertEqual(old.read_text(),'untouched')
        link=self.root/'link';link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(RuntimeError):create_adapter(link,TOKEN)

    def test_weak_or_absent_capability_rejected(self):
        for token in ('',None,'short'):
            with self.assertRaises(RuntimeError):create_adapter(self.root,token)

    def test_source_hash_mismatch_rejected_without_modifying_real_core(self):
        fake=self.root/'fake-adapter';fake.mkdir();(fake/'core_manifest.json').write_text(json.dumps({'app/example.py':'0'*64}))
        (self.root/'app').mkdir();(self.root/'app/example.py').write_text('not accepted core')
        with patch('synthetic_intake.adapter.HERE',fake):
            with self.assertRaises(RuntimeError):verify_core(self.root)

    def test_cli_without_explicit_later_authorization_never_reaches_network_or_server(self):
        from contextlib import redirect_stderr
        from synthetic_intake.__main__ import main
        with patch('sys.argv',['synthetic_intake']),patch('synthetic_intake.__main__.network_none_inventory') as inventory,redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):main()
            inventory.assert_not_called()

    def test_namespace_inventory_is_read_only_and_rejects_non_loopback(self):
        (self.root/'dev').write_text(' lo: 0 0\n');(self.root/'route').write_text('Iface Destination\n');(self.root/'ipv6_route').write_text('')
        self.assertEqual(network_none_inventory(self.root)['devices'],['lo'])
        (self.root/'dev').write_text(' lo: 0 0\n eth0: 1 1\n')
        with self.assertRaises(RuntimeError):network_none_inventory(self.root)

    def test_namespace_inventory_rejects_external_route_even_with_loopback_only(self):
        (self.root/'dev').write_text(' lo: 0 0\n');(self.root/'route').write_text('Iface Destination\neth0 00000000\n');(self.root/'ipv6_route').write_text('')
        with self.assertRaises(RuntimeError):network_none_inventory(self.root)
