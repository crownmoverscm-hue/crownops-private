"""Only in-process qualification: no uvicorn, Docker, image build or listener."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from .adapter import create_adapter,ENV,CORE_COMMIT,CORE_BUILD,adapter_digest


def qualify(output):
    output=Path(output)
    if not output.is_dir() or output.resolve()!=output.absolute() or not output.is_relative_to('/tmp') or any(output.iterdir()):
        raise RuntimeError('New empty output directory under /tmp required')
    output.chmod(0o700)
    if any(os.getenv(k)!=v for k,v in ENV.items()):raise RuntimeError('Use the clean-environment qualification wrapper')
    with (output/'adapter-tests.log').open('w') as stream:
        suite=unittest.defaultTestLoader.loadTestsFromName('test_synthetic_intake_adapter')
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    if not result.wasSuccessful():raise RuntimeError('Adapter qualification failed; inspect local log')
    with tempfile.TemporaryDirectory(prefix='crownops-adapter-qualification-',dir='/tmp') as folder:
        app=create_adapter(folder,'DISPOSABLE-QUALIFICATION-CAPABILITY-'+'x'*32)
        run=app.state.synthetic
        try:
            with TestClient(app) as client:
                client.headers['authorization']='Bearer DISPOSABLE-QUALIFICATION-CAPABILITY-'+'x'*32
                manifest={'scenario':'oct24-rendered-v1','fixture_set_sha256':run.fixture_set_hash}
                first=client.post('/synthetic/intake/replay',json=manifest)
                if first.status_code!=200:raise RuntimeError('First in-process request failed')
                before=run.snapshot()
                retry=client.post('/synthetic/intake/replay',json=manifest)
                if retry.status_code!=200 or before!=run.snapshot():raise RuntimeError('Retry verification failed')
                identity=client.get('/identity').json();evidence=client.get('/test-evidence').json()
                comparisons=[]
                for f in run.fixtures:
                    key='gmail:OCT24-'+f['id']
                    event=next(x for x in evidence['records']['integration_events'] if x['event_key']==key)
                    parsed=json.loads(event['payload_json'])['parsed']
                    lead=next(x for x in evidence['records']['leads'] if x['external_id']==key)
                    comparisons.append({'fixture':f['id'],'body_sha256':hashlib.sha256(f['body'].encode()).hexdigest(),
                        'fields':{field:{'expected':v,'parsed':parsed[field],'stored':lead[field],'pass':v==parsed[field]==lead[field]}
                            for field,v in run.expected[f['id']].items()}})
                evidence.update({'identity':identity,'qualification':'ASGI/in-process mocks only; no deployed or listening endpoint',
                    'first_response':first.json(),'retry_response':retry.json(),'field_comparisons':comparisons,
                    'tests_run':result.testsRun,'core_commit':CORE_COMMIT,'core_build':CORE_BUILD,'adapter_sha256':adapter_digest()})
                (output/'adapter-results.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
                (output/'adapter-audit.jsonl').write_text((run.root/'adapter-audit.jsonl').read_text())
        finally:run.close()
    print('PASS:',result.testsRun,'new adapter tests; one client, four services, four blocked drafts, no downstream records')
    print('Core:',CORE_COMMIT,'build',CORE_BUILD)
    print('Adapter:',adapter_digest());print('Evidence:',output)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    qualify(parser.parse_args().output)
