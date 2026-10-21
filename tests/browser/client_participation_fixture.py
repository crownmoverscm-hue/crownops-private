"""Fictional participation installation, isolated from customers and providers."""
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
os.environ['CROWNOPS_PUBLIC_URL']='http://127.0.0.1:8095'
from test_client_participation import ClientParticipationTests
import uvicorn
fixture=ClientParticipationTests();fixture.setUp();fixture.draw()
uvicorn.run(fixture.app,host='127.0.0.1',port=8095,log_level='warning')
