"""Reproduce legacy malformed rates in a disposable, fictional installation only."""
import os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
os.environ['CROWNOPS_PUBLIC_URL']='http://127.0.0.1:8095'
from test_wix_draft_connector import WixDraftConnectorTests
from app.store import transaction,insert
import uvicorn
fixture=WixDraftConnectorTests('test_preflight_uses_actual_snapshot_and_never_calls_provider')
fixture.setUp()
with transaction(fixture.path) as db:
 insert(db,'pricing_versions',dict(id='LEGACY-MALFORMED-WIX-TEST',label='UNAPPROVED fictional legacy rates',rates_json='{}',settings_json='{}',approved=0,created_at='2099-01-01T00:00:00+00:00'))
uvicorn.run(fixture.f.app,host='127.0.0.1',port=8095,log_level='warning')
