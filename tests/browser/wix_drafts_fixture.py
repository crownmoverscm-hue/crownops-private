"""Fresh fictional records and real local auth; no provider connections."""
import os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
os.environ['CROWNOPS_PUBLIC_URL']='http://127.0.0.1:8095'
from test_wix_draft_connector import WixDraftConnectorTests
import uvicorn
fixture=WixDraftConnectorTests('test_preflight_uses_actual_snapshot_and_never_calls_provider')
fixture.setUp()
uvicorn.run(fixture.f.app,host='127.0.0.1',port=8095,log_level='warning')
