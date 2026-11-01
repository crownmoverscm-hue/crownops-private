"""Disposable local-only browser fixture; run only with approved dependencies installed."""
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
os.environ.update(CROWNOPS_PROFILE='core-v1', CROWNOPS_ENV='test', CROWNOPS_ALLOW_EXTERNAL='0')
from test_core_v1_isolation import CoreV1Fixture
import uvicorn
fixture = CoreV1Fixture()
fixture.setUp()
try:
    uvicorn.run(fixture.app, host='127.0.0.1', port=8097, log_level='warning')
finally:
    import json
    from contextlib import closing
    from app.core_v1 import TRANSPORT_EVIDENCE
    from app.store import connect
    evidence = ROOT / 'evidence' / 'core-v1'
    evidence.mkdir(parents=True, exist_ok=True)
    with closing(connect(fixture.path)) as db:
        names = ('quotes','jobs','assignments','payments','job_invoices','outbound_messages','deposit_links','payment_links','intake_drafts')
        business = {n: db.execute('SELECT count(*) FROM '+n).fetchone()[0] for n in names}
        allowed = {n: db.execute('SELECT count(*) FROM '+n).fetchone()[0] for n in ('client_accounts','leads','manual_source_submissions','messages','tasks')}
    (evidence/'browser-app-evidence.json').write_text(json.dumps({'transport':TRANSPORT_EVIDENCE,'business':business,'allowed':allowed},indent=2))
    fixture.doCleanups()
