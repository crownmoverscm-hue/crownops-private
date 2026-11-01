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
    fixture.doCleanups()
