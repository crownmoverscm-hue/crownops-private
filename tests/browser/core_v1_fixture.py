"""Disposable local-only browser fixture; no production/debug endpoint is added."""
import os
import sys
import json
from pathlib import Path
from contextlib import closing
from threading import Thread
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
os.environ.update(CROWNOPS_PROFILE='core-v1', CROWNOPS_ENV='test', CROWNOPS_ALLOW_EXTERNAL='0')
from test_core_v1_isolation import CoreV1Fixture
from app.core_v1 import TRANSPORT_EVIDENCE
from app.store import connect
import uvicorn
fixture = CoreV1Fixture()
fixture.setUp()
evidence = ROOT / 'evidence' / 'core-v1'
evidence.mkdir(parents=True, exist_ok=True)

def snapshot():
    with closing(connect(fixture.path)) as db:
        names = ('quotes','jobs','assignments','payments','job_invoices','outbound_messages','deposit_links','payment_links','intake_drafts')
        business = {n: db.execute('SELECT count(*) FROM '+n).fetchone()[0] for n in names}
        allowed = {n: db.execute('SELECT count(*) FROM '+n).fetchone()[0] for n in ('client_accounts','leads','manual_source_submissions','messages','tasks')}
    target=evidence/'browser-app-evidence.json'
    temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps({'transport':TRANSPORT_EVIDENCE,'business':business,'allowed':allowed},indent=2))
    os.replace(temporary,target)

server = uvicorn.Server(uvicorn.Config(fixture.app, host='127.0.0.1', port=8097, log_level='warning'))

def control():
    # Commands arrive only on this fictional fixture's inherited runner stdin.
    for line in sys.stdin:
        if line.strip() == 'snapshot':
            snapshot()
        elif line.strip() == 'stop':
            server.should_exit=True
            return

Thread(target=control,daemon=True).start()
try:
    server.run()
finally:
    snapshot()
    fixture.doCleanups()
