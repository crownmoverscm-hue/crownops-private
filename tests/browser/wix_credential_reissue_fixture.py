"""Disposable credential tests only; verified manifest exists only in temporary test directory."""
import os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
os.environ['CROWNOPS_PUBLIC_URL']='http://127.0.0.1:8095'
from test_wix_test_credential_reissue import SyntheticCredentialReissueTests
import uvicorn
fixture=SyntheticCredentialReissueTests('test_preflight_selects_only_five_safe_metadata_rows_and_never_mutates')
fixture.setUp()
uvicorn.run(fixture.f.f.app,host='127.0.0.1',port=8095,log_level='warning')
