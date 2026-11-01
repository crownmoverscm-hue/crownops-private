"""Disposable staff UI fixture. No real identities, credentials, providers or business actions."""
from pathlib import Path
import json,os,sys,tempfile,threading,sqlite3
from contextlib import closing
from datetime import datetime,timedelta,timezone
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
os.environ.update(CROWNOPS_ENV='test',CROWNOPS_ALLOW_EXTERNAL='0',CROWNOPS_RUN_WORKER='0',CROWNOPS_UPLOADS_ENABLED='0',CROWNOPS_PUBLIC_URL='http://127.0.0.1:8098',CROWNOPS_TEAM_CHAT_MODE='off')
attempts=[]
def guard(event,args):
 if event=='socket.connect':
  address=args[1]
  if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):
   attempts.append(event);raise RuntimeError('External transport denied')
 if event=='socket.getaddrinfo' and args[0] not in ('127.0.0.1','::1','localhost',None):
  attempts.append(event);raise RuntimeError('External DNS denied')
 if event in ('subprocess.Popen','os.system','os.posix_spawn'):
  attempts.append(event);raise RuntimeError('Fixture subprocess denied')
sys.addaudithook(guard)
from app.main import create_app,sha
from app.store import transaction,insert,now
import uvicorn
fixture=tempfile.TemporaryDirectory(prefix='crownops-themes-fictional-')
path=Path(fixture.name)/'fictional.sqlite3';app=create_app(path)
expiry=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
with transaction(path) as db:
 for uid,role,token in [('THEME-A','owner','TEST-theme-owner-token'),('THEME-B','sales','TEST-theme-sales-token')]:
  insert(db,'users',dict(id=uid,email=role+'@example.invalid',name='TEST '+role.title(),role=role,password_hash='unused-fictional-session-fixture',active=1,created_at=now()))
  insert(db,'sessions',dict(token_hash=sha(token),user_id=uid,csrf='TEST-'+uid,expires_at=expiry))
 insert(db,'pricing_versions',dict(id='THEME-PRICE',label='TEST UI fixture only',rates_json='[]',settings_json='{}',approved=1,created_at=now()))
 insert(db,'annual_rate_periods',dict(id='THEME-RATE',label='TEST UI dates only',start_date='2026-01-01',end_date='2027-12-31',rate_2_cents=13500,rate_3_cents=19500,rate_4_cents=26000,rate_5_cents=30000,rate_6_cents=35000,active=1,version=1,note='Fictional UI fixture, not company pricing',approved_by='THEME-A',created_at=now()))
 for n,name in enumerate(['TEST Alex Example','TEST Morgan Example','TEST Camille Exemple']):
  insert(db,'leads',dict(id='THEME-LEAD-'+str(n),client_name=name,email='client-'+str(n)+'@example.invalid',phone='514555010'+str(n),move_date='2026-11-15',pickup='TEST pickup',dropoff='TEST destination',stage='New Lead',source='Manual fictional UI',lead_type='Moving',created_at=now(),updated_at=now()))
  insert(db,'client_accounts',dict(id='THEME-CLIENT-'+str(n),identity_key='e:client-'+str(n)+'@example.invalid',client_name=name,email='client-'+str(n)+'@example.invalid',phone='514555010'+str(n),language='English',created_at=now(),updated_at=now()))
  insert(db,'client_account_leads',dict(client_id='THEME-CLIENT-'+str(n),lead_id='THEME-LEAD-'+str(n),created_at=now()))
@app.middleware('http')
async def read_only(request,call_next):
 from starlette.responses import JSONResponse
 if request.method not in ('GET','HEAD'):return JSONResponse({'detail':'UI fixture forbids mutations'},status_code=403)
 return await call_next(request)
@app.get('/theme-logo-comparison')
async def logo_comparison():
 from starlette.responses import HTMLResponse
 return HTMLResponse('<!doctype html><html lang="en" data-crownops-staff data-staff-theme="black"><head><link rel="stylesheet" href="/static/staff_themes.css"><style>body{font:16px system-ui;padding:32px}#logo-comparison{width:760px;background:#fff;padding:24px;box-sizing:border-box;border-radius:16px}h1{font-size:23px}.comparison-row{display:flex;gap:52px;align-items:start}.comparison-row>div{width:310px}.sidebar{position:static!important;padding:0!important;background:transparent!important;border:0!important}.scaled{zoom:3}.brand{padding:0!important}.raw{width:192px;height:192px;object-fit:contain}p{color:#526170;font-size:14px}</style></head><body><section id="logo-comparison"><h1>Official crown: unchanged artwork, restored breathing room</h1><div class="comparison-row"><div><h2>Official asset</h2><img class="raw" src="/static/icon.svg" alt="Original Crown Movers crown and underline"><p>Original SVG, unchanged.</p></div><div><h2>Staff navigation container</h2><div class="sidebar"><div class="brand"><div class="brand-icon scaled"><img src="/static/icon.svg" alt="Same original Crown Movers crown"></div></div></div><p>3× view of a 64px container with 8px padding.<br>Same image source, proportions and orange gradient.</p></div></div></section></body></html>')
def report():
 for line in sys.stdin:
  if line.strip()=='snapshot':
   with closing(sqlite3.connect(path)) as db:
    counts={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('quotes','jobs','payments','outbound_messages','provider_operations','deposit_links','job_invoices')}
   out=ROOT/'evidence/staff-themes';out.mkdir(parents=True,exist_ok=True)
   (out/'fixture-readback.json').write_text(json.dumps({'fictional_only':True,'prohibited_business_counts':counts,'external_attempts':attempts,'runtime':'read-only disposable UI fixture; preseeded fictional sessions, no real login qualification'},indent=2))
threading.Thread(target=report,daemon=True).start()
try:uvicorn.run(app,host='127.0.0.1',port=8098,log_level='warning')
finally:fixture.cleanup()
