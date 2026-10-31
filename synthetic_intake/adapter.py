"""Frozen synthetic feed over a minimal, separately authorized HTTP surface.

Factory calls in qualification are ASGI/in-process only. This module never starts
a listener, creates a container or imports staff credentials.
"""
import base64
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch
from fastapi import FastAPI,HTTPException,Request
from starlette.concurrency import run_in_threadpool
from .isolation import OutboundGuard

CORE_COMMIT='758d74a3a4ae07ced4fc5b873f4345d63b97ee95'
CORE_BUILD='b377d4b1342981c5f5892950'
HERE=Path(__file__).resolve().parent
TABLES=('client_accounts','client_account_leads','leads','integration_events','messages','tasks','intake_drafts',
    'quotes','outbound_messages','jobs','assignments','payment_links','deposit_links','payments','job_invoices','audit')
ZERO=('quotes','outbound_messages','jobs','assignments','payment_links','deposit_links','payments','job_invoices')
READY_COUNTS={'client_accounts':1,'client_account_leads':4,'leads':4,'integration_events':4,'messages':4,'tasks':7,'intake_drafts':4}
ENV={'CROWNOPS_ENV':'test','CROWNOPS_ALLOW_EXTERNAL':'0','CROWNOPS_RUN_WORKER':'0','QBO_ENV':'sandbox'}
ALLOWED_ENV=set(ENV)|{'CROWNOPS_SYNTHETIC_ENDPOINT_START','CROWNOPS_SYNTHETIC_IMAGE_DIGEST'}


def verify_core(root):
    manifest=json.loads((HERE/'core_manifest.json').read_text())
    for name,digest in manifest.items():
        p=root/name
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Accepted core source mismatch: '+name)
    from app.app_release import release_snapshot
    if release_snapshot(root)[0]!=CORE_BUILD:raise RuntimeError('Accepted core build mismatch')


def adapter_digest():
    digest=hashlib.sha256()
    for p in sorted(HERE.iterdir()):
        if p.is_file() and p.suffix in ('.py','.json'):
            digest.update(p.name.encode()+b'\0'+p.read_bytes()+b'\0')
    return digest.hexdigest()


def validate_environment():
    if any(os.getenv(k)!=v for k,v in ENV.items()):raise RuntimeError('Exact synthetic-only environment required')
    for key in os.environ:
        if ((key.startswith(('CROWNOPS_','QBO_')) and key not in ALLOWED_ENV)
            or key.startswith(('RAILWAY_','GMAIL_','WIX_','SMTP_','SQUARE_','OPENAI_','ANTHROPIC_','AWS_','GOOGLE_'))
            or key in ('DATABASE_URL','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY')):
            raise RuntimeError('Inherited provider/platform configuration prohibited')


class SyntheticRun:
    def __init__(self,workspace,capability,clock=time.monotonic):
        validate_environment()
        if not isinstance(capability,str) or not 32<=len(capability)<=256:raise RuntimeError('Private test capability required')
        root=Path(workspace).absolute()
        if root.resolve()!=root or not (root.is_relative_to('/tmp') or root==Path('/run/crownops-intake-test')):
            raise RuntimeError('New disposable runtime path required')
        if not root.is_dir() or root.stat().st_uid!=os.getuid() or any(root.iterdir()):raise RuntimeError('Owned empty runtime directory required')
        root.chmod(0o700)
        verify_core(HERE.parent)
        self.root=root;self.path=root/'intake.sqlite3';self.clock=clock;self.started=clock()
        self._capability=capability;self.lock=threading.Lock();self.guard=OutboundGuard()
        self.state={'phase':'READY','requests_completed':0,'notifications_sent':0,'provider_writes':0}
        self.mock_calls=[];self.namespace=None
        self.fixtures=json.loads((HERE/'fixtures.json').read_text())
        self.expected=json.loads((HERE/'expected_fields.json').read_text())
        self.fixture_set_hash=hashlib.sha256((HERE/'fixtures.json').read_bytes()).hexdigest()
        self.body_hashes={f['id']:hashlib.sha256(f['body'].encode()).hexdigest() for f in self.fixtures}
        reviewed=json.loads((HERE/'fixture_hashes.json').read_text())
        if self.body_hashes!=reviewed:raise RuntimeError('Frozen fixture mismatch')
        self.guard.install()
        try:
            from app.main import create_app
            from app.store import connect
            self.connect=connect
            core=create_app(self.path)
            endpoint=next(r.endpoint for r in core.routes if r.path=='/api/gmail/sync-readonly')
            cells=dict(zip(endpoint.__code__.co_freevars,[c.cell_contents for c in endpoint.__closure__]))
            self.new_lead=cells['new_lead']
            # No core app is mounted and its lifespan/workers are never entered.
            if any(self.snapshot()[t] for t in TABLES):raise RuntimeError('Business database was not empty')
            with closing(connect(self.path)) as db:
                for table in ('users','sessions','pricing_versions','annual_rate_periods'):
                    if db.execute('SELECT count(*) FROM '+table).fetchone()[0]:raise RuntimeError('Credential/pricing seed prohibited')
            self.save('initialized-empty')
        except BaseException:
            self.guard.close();raise

    def authorize(self,request):
        value=request.headers.get('authorization','')
        if self.clock()-self.started>1800:raise HTTPException(401,'Synthetic capability expired')
        if len(value)>300 or not hmac.compare_digest(value,'Bearer '+self._capability):raise HTTPException(401,'Private synthetic capability required')
        if request.headers.get('origin') or request.headers.get('cookie'):raise HTTPException(403,'Browser/cookie requests prohibited')

    def save(self,action):
        stamp=datetime.now(timezone.utc).isoformat()
        data={**self.state,'action':action,'at':stamp}
        tmp=self.root/'operation.tmp'
        with tmp.open('w') as f:
            os.chmod(tmp,0o600);json.dump(data,f);f.flush();os.fsync(f.fileno())
        os.replace(tmp,self.root/'operation.json')
        directory=os.open(self.root,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
        with (self.root/'adapter-audit.jsonl').open('a') as f:
            os.chmod(f.name,0o600);f.write(json.dumps(data)+'\n');f.flush();os.fsync(f.fileno())

    def snapshot(self):
        with closing(self.connect(self.path)) as db:
            return {t:[dict(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in TABLES}

    def mock_api(self,token,path,params=None):
        from app.gmail_sync import FEEDS
        self.mock_calls.append(path)
        if path=='labels':return {'labels':[{'id':'quote','name':FEEDS['quote']}]}
        if path=='messages':return {'messages':[{'id':'OCT24-'+f['id']} for f in self.fixtures]}
        if path.startswith('messages/'):
            f=next((f for f in self.fixtures if 'OCT24-'+f['id']==path.split('/')[1]),None)
            if f:
                return {'id':'OCT24-'+f['id'],'internalDate':'1792814400000','payload':{'mimeType':'text/plain','headers':[{'name':'Subject','value':f['subject']}],
                    'body':{'data':base64.urlsafe_b64encode(f['body'].encode()).decode()}}}
        raise RuntimeError('Unapproved synthetic feed operation')

    def ingest(self):
        from app.gmail_sync import sync_gmail_readonly
        with patch('app.gmail_sync._api',side_effect=self.mock_api):
            return sync_gmail_readonly(self.path,'MOCK-ONLY','synthetic-endpoint-test',self.new_lead,feed_names=('quote',))

    def validate_result(self,records):
        if any(len(records[t])!=n for t,n in READY_COUNTS.items()):raise RuntimeError('Unexpected intake record counts')
        if any(records[t] for t in ZERO):raise RuntimeError('Consequential record created')
        for f in self.fixtures:
            key='gmail:OCT24-'+f['id']
            lead=next(x for x in records['leads'] if x['external_id']==key)
            event=next(x for x in records['integration_events'] if x['event_key']==key)
            payload=json.loads(event['payload_json'])
            if event['status']!='Imported' or payload['body']!=f['body']:raise RuntimeError('Original source not preserved')
            if any(lead[k]!=v or payload['parsed'][k]!=v for k,v in self.expected[f['id']].items()):raise RuntimeError('Parsed/stored fields differ')
            draft=next(x for x in records['intake_drafts'] if x['lead_id']==lead['id'])
            missing=json.loads(draft['missing_json'])
            if draft['quote_id'] or draft['status']!='Needs details' or 'approved pricing setup' not in missing:raise RuntimeError('Unapproved draft/pricing state')
        if self.guard.evidence()['total_blocked_attempts']:raise RuntimeError('Outbound attempt detected')

    def replay(self):
        if not self.lock.acquire(blocking=False):raise HTTPException(409,'Operation in progress; inspect status')
        try:
            if self.state['phase'] not in ('READY','FIRST_COMPLETE'):raise HTTPException(409,'Terminal or uncertain operation; no further writes permitted')
            if self.guard.evidence()['total_blocked_attempts']:
                self.state['phase']='AMBIGUOUS';self.save('outbound-attempt-locked')
                raise HTTPException(409,'Outbound attempt detected; no ingestion or retry permitted')
            before=self.snapshot();retry=self.state['phase']=='FIRST_COMPLETE'
            self.state['phase']='RETRY_RUNNING' if retry else 'FIRST_RUNNING';self.save('claimed')
            try:
                summary=self.ingest();after=self.snapshot();self.validate_result(after)
                if summary['errors'] or (retry and (summary['duplicates']!=4 or before!=after)) or (not retry and summary['new_leads']!=4):
                    raise RuntimeError('Intake response needs reconciliation')
                self.state.update(phase='COMPLETE' if retry else 'FIRST_COMPLETE',requests_completed=self.state['requests_completed']+1)
                self.save('retry-complete' if retry else 'first-complete')
                return {'phase':self.state['phase'],'summary':summary,'counts':{t:len(v) for t,v in after.items()}}
            except Exception:
                self.state['phase']='AMBIGUOUS';self.save('reconciliation-required')
                raise HTTPException(409,'Uncertain operation; no retry or reset. Inspect read-only evidence.') from None
        finally:self.lock.release()

    def evidence(self):
        records=self.snapshot()
        return {'state':self.state,'counts':{t:len(v) for t,v in records.items()},'records':records,
                'fixture_body_hashes':self.body_hashes,'mock_feed_calls':self.mock_calls,'outbound':self.guard.evidence(),
                'namespace':self.namespace or {'status':'NOT QUALIFIED — in-process mock tests only'},
                'workers_started':0,'core_routes_mounted':False,'core_lifespan_entered':False}

    def close(self):
        self.guard.close();self._capability=''


def create_adapter(workspace,capability,clock=time.monotonic):
    run=SyntheticRun(workspace,capability,clock)
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,redirect_slashes=False)
    app.state.synthetic=run

    @app.get('/health')
    def health():return {'ok':True,'synthetic_only':True}

    @app.get('/identity')
    def identity():
        return {'core_commit':CORE_COMMIT,'core_build':CORE_BUILD,'adapter_sha256':adapter_digest(),
                'fixture_set_sha256':run.fixture_set_hash,'environment':'crownops-intake-synthetic-01',
                'image_digest':os.getenv('CROWNOPS_SYNTHETIC_IMAGE_DIGEST','NOT BUILT / mock-only qualification'),
                'provider_writes':False,'acceptance':False,'booking':False,'payments':False}

    @app.get('/test-status')
    def status(request:Request):
        run.authorize(request)
        return {'state':run.state,'counts':{t:len(v) for t,v in run.snapshot().items()},'outbound':run.guard.evidence()}

    @app.get('/test-evidence')
    def evidence(request:Request):run.authorize(request);return run.evidence()

    @app.post('/synthetic/intake/replay')
    async def replay(request:Request):
        run.authorize(request)
        if request.headers.get('content-type','').split(';')[0]!='application/json':raise HTTPException(415,'JSON manifest required')
        body=b''
        async for chunk in request.stream():
            body+=chunk
            if len(body)>1024:raise HTTPException(413,'Manifest too large')
        def pairs(items):
            obj={}
            for k,v in items:
                if k in obj:raise ValueError('Duplicate key')
                obj[k]=v
            return obj
        try:manifest=json.loads(body,object_pairs_hook=pairs)
        except (ValueError,UnicodeError):raise HTTPException(422,'Invalid manifest') from None
        expected={'scenario':'oct24-rendered-v1','fixture_set_sha256':run.fixture_set_hash}
        if manifest!=expected:raise HTTPException(409,'Only the approved frozen manifest is permitted')
        return await run_in_threadpool(run.replay)

    return app
