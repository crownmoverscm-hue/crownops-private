"""Staging-only Wix draft readiness. ALL provider writes are unconditionally disabled.
Local review records are not Wix creation approval or customer acceptance.
"""
from contextlib import closing
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlsplit
import hashlib,hmac,json,os,re,secrets,time
import requests
from fastapi import Depends,HTTPException
from fastapi.responses import FileResponse,JSONResponse
from .store import connect,transaction,one,rows,audit,now,dumps,insert
from .account_access import master_owner_id
from .estimate_delivery import digest,source
from .quebec_taxes import taxable_move

SITE='8911c013-d113-41ff-bca5-8484b699083b'
PROTECTED_LEAD='LEAD-30d14a9f79a346f7'
PROTECTED_QUOTES={'QUOTE-8a7b31fbc00e4302','QUOTE-08dd48a364a34f66','QUOTE-af7136453bf64544'}
READ_HOST='www.xn--crown-dmnagement-hqbb.com'
READ_PATH='/_functions/crownopsQuoteRead'  # Proposed custom bridge; not deployed/qualified.
READERS={'owner','manager','sales'}
FIXTURE_QUOTE='WIX-TEST-READINESS-01'

def gate():
    if os.getenv('CROWNOPS_ENV') not in ('staging','test'):raise HTTPException(404,'Not available.')

def permission(db,u,owner=False):
    actual=one(db,'SELECT * FROM users WHERE id=? AND active=1',(u['id'],))
    if not actual:raise HTTPException(401,'Please sign in.')
    if actual['role'] not in READERS or (owner and (actual['role']!='owner' or actual['id']!=master_owner_id(db))):
        raise HTTPException(403,'Active Master owner required.' if owner else 'Office access required.')
    return actual

def setup(path):
    with transaction(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS wix_draft_reviews(id TEXT PRIMARY KEY,quote_id TEXT NOT NULL,snapshot_hash TEXT NOT NULL,snapshot_json TEXT NOT NULL,owner_id TEXT NOT NULL,created_at TEXT NOT NULL,reason TEXT NOT NULL,UNIQUE(quote_id,snapshot_hash));
        CREATE TABLE IF NOT EXISTS wix_draft_slots(site TEXT NOT NULL,quote_id TEXT NOT NULL,review_id TEXT NOT NULL,snapshot_hash TEXT NOT NULL,state TEXT NOT NULL,remote_id TEXT,remote_version INTEGER,PRIMARY KEY(site,quote_id),UNIQUE(site,remote_id));
        CREATE TABLE IF NOT EXISTS wix_draft_evidence(id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,actor TEXT NOT NULL,event TEXT NOT NULL,details_json TEXT NOT NULL,previous_hash TEXT NOT NULL,hash TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS wix_reviews_no_update BEFORE UPDATE ON wix_draft_reviews BEGIN SELECT RAISE(ABORT,'Immutable review'); END;
        CREATE TRIGGER IF NOT EXISTS wix_reviews_no_delete BEFORE DELETE ON wix_draft_reviews BEGIN SELECT RAISE(ABORT,'Immutable review'); END;
        CREATE TRIGGER IF NOT EXISTS wix_evidence_no_update BEFORE UPDATE ON wix_draft_evidence BEGIN SELECT RAISE(ABORT,'Append-only evidence'); END;
        CREATE TRIGGER IF NOT EXISTS wix_evidence_no_delete BEFORE DELETE ON wix_draft_evidence BEGIN SELECT RAISE(ABORT,'Append-only evidence'); END;
        CREATE TRIGGER IF NOT EXISTS wix_slots_no_delete BEFORE DELETE ON wix_draft_slots BEGIN SELECT RAISE(ABORT,'Consumed slot'); END;
        CREATE TRIGGER IF NOT EXISTS wix_slots_binding BEFORE UPDATE ON wix_draft_slots WHEN NEW.site!=OLD.site OR NEW.quote_id!=OLD.quote_id OR NEW.review_id!=OLD.review_id OR NEW.snapshot_hash!=OLD.snapshot_hash OR (OLD.remote_id IS NOT NULL AND (NEW.remote_id IS NOT OLD.remote_id OR NEW.remote_version IS NOT OLD.remote_version)) BEGIN SELECT RAISE(ABORT,'Immutable binding'); END;
        ''')

def evidence(db,actor,event,details):
    prior=one(db,'SELECT hash FROM wix_draft_evidence ORDER BY id DESC LIMIT 1')
    prev=prior['hash'] if prior else '';at=now();value=dumps(details)
    db.execute('INSERT INTO wix_draft_evidence(at,actor,event,details_json,previous_hash,hash) VALUES(?,?,?,?,?,?)',(at,actor,event,value,prev,digest([at,actor,event,details,prev])))

def snapshot(db,qid,basis):
    if basis not in ('low','high'):raise HTTPException(422,'Choose low or high explicitly.')
    raw=one(db,'SELECT * FROM quotes WHERE id=?',(qid,))
    if not raw:raise HTTPException(404,'Quote not found.')
    if qid in PROTECTED_QUOTES or raw['lead_id']==PROTECTED_LEAD:raise HTTPException(403,'Estimate 185 and its revision family are excluded.')
    if qid==FIXTURE_QUOTE:
        q=raw;lead=one(db,'SELECT * FROM leads WHERE id=?',(q['lead_id'],))
        inp_fixture=json.loads(q['inputs_json'])
        if q['lead_id']!='WIX-TEST-READINESS-LEAD' or lead['email']!='wix-readiness@example.invalid' or not json.loads(q['result_json']).get('test_only'):raise HTTPException(409,'Fixture identity differs.')
        terms=inp_fixture.get('wix_fixture_terms') or {}
        if not terms.get('approved') or terms.get('version')!='WIX-FICTIONAL-v1' or not terms.get('text'):raise HTTPException(409,'Fixture terms missing.')
    else:
        q,lead,terms,_=source(db,qid)
    if not str(lead['client_name']).upper().startswith('TEST'):raise HTTPException(409,'Use a fictional TEST-prefixed client only.')
    if one(db,'SELECT id FROM quotes WHERE lead_id=? AND revision>?',(q['lead_id'],q['revision'])):raise HTTPException(409,'A newer quote revision exists.')
    inp=json.loads(q['inputs_json']);result=json.loads(q['result_json'])
    if result.get('currency')!='CAD' or not result.get('line_items'):raise HTTPException(409,'Current CAD itemized quote required.')
    expiry=inp.get('expiry_date','')
    from datetime import date
    try:
        if date.fromisoformat(expiry).isoformat()!=expiry:raise ValueError()
    except (ValueError,TypeError):raise HTTPException(409,'An exact quote expiry date is required.')
    lines=[];notes=[]
    for i,item in enumerate(result['line_items']):
        line=dict(item)
        if i==0 and result.get('hourly_rate_cents'):
            line.update(qty=result['service_'+basis],total_cents=result['service_'+basis+'_cents'])
        if type(line.get('unit_cents')) is not int or type(line.get('total_cents')) is not int or line['total_cents']<0:raise HTTPException(409,'Discount/negative lines require separate qualification.')
        qty=Decimal(str(line['qty']))
        if not qty.is_finite() or qty<=0 or int((qty*line['unit_cents']).quantize(Decimal('1'),rounding=ROUND_HALF_UP))!=line['total_cents']:raise HTTPException(409,'Unit/quantity/line amount mismatch.')
        if not line['total_cents']:
            notes.append(str(line['name'])+' '+str(line.get('note','')));continue
        lines.append({'id':str(i+1),'name':str(line['name']),'description':str(line.get('note','')),'quantity':str(qty),'unit_cents':line['unit_cents'],'total_cents':line['total_cents']})
    net=sum(l['total_cents'] for l in lines)
    if net!=result.get(basis+'_cents'):raise HTTPException(409,'Quote lines and selected total differ.')
    template=inp.get('template_snapshot') or {}
    full_terms='\n\n'.join(str(x) for x in (template.get('liability'),template.get('fragile'),inp.get('payment_terms'),terms['text']) if x)
    body={'site':SITE,'quote_id':qid,'revision':q['revision'],'basis':basis,'client_id':(one(db,'SELECT client_id FROM client_account_leads WHERE lead_id=?',(q['lead_id'],)) or {}).get('client_id'),
          'customer':{'name':lead['client_name'],'email':(inp.get('billing') or {}).get('email') or lead['email'],'billing':inp.get('billing') or {}},
          'currency':'CAD','lines':lines,'totals':taxable_move(net),'expiry':expiry,'service_date':inp.get('move_date') or lead['move_date'],
          'notes':'\n'.join([inp.get('client_note') or '',*notes]),'full_legal_terms':full_terms,'terms_version':terms['version'],
          'source_hash':digest({'quote':q,'lead':lead,'terms':terms})}
    return body

class WixReadAdapter:
    """Real requests transport for proposed signed GET-only bridge purpose.
    No create/send/accept method. Does not contact Wix unless explicitly configured.
    URL contract is our proposed custom endpoint, not a claimed deployed Wix API.
    """
    def read(self,remote_id,expected):
        gate()
        if os.getenv('CROWNOPS_WIX_READ_ENABLED')!='1':raise HTTPException(503,'Wix read bridge is not enabled or deployed.')
        endpoint=os.getenv('CROWNOPS_WIX_READ_URL','');url=urlsplit(endpoint)
        if url.scheme!='https' or url.hostname!=READ_HOST or url.path!=READ_PATH or url.query or url.fragment or url.username or url.password or url.port not in (None,443):raise HTTPException(503,'Exact Wix read bridge host/path required.')
        secret=os.getenv('CROWNOPS_WIX_READ_SECRET','')
        if len(secret)<32:raise HTTPException(503,'Read credential not configured.')
        if not re.fullmatch(r'[A-Za-z0-9-]{1,100}',str(remote_id)):raise HTTPException(422,'Invalid Wix quote ID.')
        payload=dumps({'site':SITE,'purpose':'quote.read','quote_id':str(remote_id),'snapshot_hash':digest(expected)}).encode()
        ts=str(int(time.time()));nonce=secrets.token_urlsafe(24)
        signed='\n'.join(['POST',READ_PATH,ts,nonce,hashlib.sha256(payload).hexdigest()]).encode()
        with requests.Session() as session:
            session.trust_env=False
            response=session.post(endpoint,data=payload,headers={'Content-Type':'application/json','X-Crown-Timestamp':ts,'X-Crown-Nonce':nonce,'X-Crown-Signature':hmac.new(secret.encode(),signed,hashlib.sha256).hexdigest()},timeout=(5,15),allow_redirects=False,stream=True)
            if response.status_code!=200:raise HTTPException(502,'Wix read failed; no write or retry performed.')
            content=b''
            for chunk in response.iter_content(8192):
                content+=chunk
                if len(content)>200000:raise HTTPException(502,'Oversized Wix read response.')
            try:data=json.loads(content)
            except ValueError:raise HTTPException(502,'Invalid Wix read JSON.')
        if not isinstance(data,dict) or data.get('site')!=SITE or str(data.get('id'))!=str(remote_id) or type(data.get('version')) is not int or data['version']<1 or not isinstance(data.get('status'),str) or type(data.get('was_sent')) is not bool:raise HTTPException(502,'Wix read identity/status schema mismatch.')
        if data.get('snapshot')!=expected:raise HTTPException(409,'Wix readback business snapshot differs.')
        return {'site':SITE,'id':data['id'],'version':data['version'],'status':data['status'],'was_sent':data['was_sent'],'readback_hash':digest(data),'provider_http_status':200,'observation_only':True}

def register(app,path,auth):
    if os.getenv('CROWNOPS_ENV') not in ('test','staging'):return
    setup(path)
    def actor(u,owner=False):
        gate()
        with closing(connect(path)) as db:return permission(db,u,owner)
    @app.get('/wix-draft-sync')
    def page(u=Depends(auth)):
        actor(u);return FileResponse(Path(__file__).parent/'static'/'wix_draft_sync.html',headers={'Cache-Control':'no-store'})
    @app.get('/api/wix-drafts/status')
    def status(u=Depends(auth)):
        a=actor(u)
        with closing(connect(path)) as db:
            reviews=rows(db,'SELECT id,quote_id,snapshot_hash,owner_id,created_at FROM wix_draft_reviews ORDER BY created_at DESC LIMIT 30')
            recent=rows(db,"SELECT q.id,q.revision,l.client_name FROM quotes q JOIN leads l ON l.id=q.lead_id WHERE l.client_name LIKE 'TEST%' AND l.id!=? AND NOT EXISTS(SELECT 1 FROM quotes n WHERE n.lead_id=q.lead_id AND n.revision>q.revision) ORDER BY q.created_at DESC LIMIT 30",(PROTECTED_LEAD,))
            master=a['role']=='owner' and a['id']==master_owner_id(db)
        return {'site':SITE,'mode':'Staging local readiness only','can_review':master,'can_create_contact':False,'can_create_quote':False,'can_send':False,'can_accept':False,'downstream_enabled':False,'reminder_safety':'UNKNOWN','tax_configuration':'Not read from Wix; expected GST 5% / QST 9.975% only','bridge_configured':bool(os.getenv('CROWNOPS_WIX_READ_URL')),'bridge_runtime_qualified':False,'read_enabled':os.getenv('CROWNOPS_WIX_READ_ENABLED')=='1','reviews':reviews,'quotes':recent}
    @app.get('/api/wix-drafts/preflight/{qid}')
    def preflight(qid:str,basis:str='high',u=Depends(auth)):
        actor(u)
        with closing(connect(path)) as db:
            s=snapshot(db,qid,basis);h=digest(s)
            old=one(db,'SELECT id,owner_id,created_at FROM wix_draft_reviews WHERE quote_id=? AND snapshot_hash=?',(qid,h))
        return {'snapshot':s,'snapshot_hash':h,'review':old,'writes_enabled':False,'contact_match':'UNVERIFIED','tax_mapping':'UNVERIFIED','blockers':['Wix reminder safety UNKNOWN','Native bridge not deployed/qualified','Wix contact/tax mapping not verified','All provider writes disabled in code']}
    @app.post('/api/wix-drafts/review/{qid}')
    def review(qid:str,data:dict,u=Depends(auth)):
        actor(u,True)
        if set(data)-{'basis','snapshot_hash','confirm','reason'}:raise HTTPException(422,'Unknown review fields.')
        reason=data.get('reason','')
        if data.get('confirm') is not True or not isinstance(reason,str) or not 10<=len(reason.strip())<=1000:raise HTTPException(422,'Explicit local-only review and reason required.')
        with transaction(path) as db:
            permission(db,u,True);s=snapshot(db,qid,data.get('basis'));h=digest(s)
            if h!=data.get('snapshot_hash'):raise HTTPException(409,'Snapshot changed; reopen preflight.')
            rid='WIX-REVIEW-'+h
            old=one(db,'SELECT * FROM wix_draft_reviews WHERE id=?',(rid,))
            if not old:
                db.execute('INSERT INTO wix_draft_reviews VALUES(?,?,?,?,?,?,?)',(rid,qid,h,dumps(s),u['id'],now(),reason.strip()))
                evidence(db,u['id'],'Local snapshot review; provider writes NOT authorized',{'review_id':rid,'quote_id':qid,'snapshot_hash':h})
                audit(db,u['id'],'Wix local readiness review; nothing created or sent','wix_draft_reviews',rid,{'snapshot_hash':h})
        return {'review_id':rid,'duplicate':bool(old),'provider_writes':0,'email_approved':False,'create_authorized':False}
    @app.post('/api/wix-drafts/readback/{qid}')
    def readback(qid:str,data:dict,u=Depends(auth)):
        actor(u,True)
        with closing(connect(path)) as db:
            s=snapshot(db,qid,data.get('basis'))
            op=one(db,'SELECT * FROM wix_draft_slots WHERE site=? AND quote_id=?',(SITE,qid))
            if not op or not op['remote_id']:raise HTTPException(409,'No mapped Wix quote; arbitrary provider reads are forbidden.')
            if op['snapshot_hash']!=digest(s):raise HTTPException(409,'Mapped snapshot changed.')
        result=WixReadAdapter().read(op['remote_id'],s)
        with transaction(path) as db:evidence(db,u['id'],'Provider observation only',result)
        return result
    @app.post('/api/wix-drafts/fixture')
    def prepare_fixture(data:dict,u=Depends(auth)):
        actor(u,True)
        from .quote_staging import require_isolated
        if os.getenv('CROWNOPS_ENV')!='test':require_isolated()
        if data!={'confirm':True}:raise HTTPException(422,'Confirm fictional local records only.')
        with transaction(path) as db:
            permission(db,u,True)
            old=one(db,'SELECT id FROM quotes WHERE id=?',(FIXTURE_QUOTE,))
            if old:return {'quote_id':FIXTURE_QUOTE,'duplicate':True,'provider_writes':0}
            t=now();lid='WIX-TEST-READINESS-LEAD';pid='WIX-TEST-UNAPPROVED-PRICE'
            insert(db,'pricing_versions',dict(id=pid,label='UNAPPROVED fictional Wix readiness only',rates_json='{}',settings_json='{}',approved=0,created_at=t))
            lead=dict(id=lid,client_name='TEST Wix readiness — fictional only',email='wix-readiness@example.invalid',phone='',move_date='2026-12-31',created_at=t,updated_at=t)
            insert(db,'leads',lead)
            inp={'service_type':'Moving','move_date':'2026-12-31','expiry_date':'2027-01-31','quote_title':'TEST ONLY — Wix local readiness','billing':{'name':lead['client_name'],'email':lead['email']},'template_snapshot':{},'payment_terms':'No payment, booking or service requested.','wix_fixture_terms':{'approved':True,'version':'WIX-FICTIONAL-v1','text':'Fictional local test only. Not an offer of services. No contact, quote, email, acceptance, invoice, payment or booking is authorized at any provider.'}}
            result={'currency':'CAD','test_only':True,'wix_readiness_only':True,'low_cents':18099,'high_cents':18099,'line_items':[{'name':name,'qty':qty,'unit_cents':unit,'total_cents':total} for name,qty,unit,total in [('Fictional labour',2,5000,10000),('Fictional transportation',1,3500,3500),('Fictional fuel',1,3599,3599),('Fictional materials',2,500,1000)]]}
            insert(db,'quotes',dict(id=FIXTURE_QUOTE,lead_id=lid,revision=1,status='Draft',inputs_json=dumps(inp),result_json=dumps(result),pricing_id=pid,lead_snapshot_json=dumps(lead),lead_version=1,created_by=u['id'],created_at=t))
            evidence(db,u['id'],'Fictional local readiness fixture prepared; no provider action',{'quote_id':FIXTURE_QUOTE})
        return {'quote_id':FIXTURE_QUOTE,'duplicate':False,'provider_writes':0}

    @app.post('/api/wix-drafts/test-access')
    def test_access(data:dict,u=Depends(auth)):
        actor(u,True)
        from .quote_staging import require_isolated
        if os.getenv('CROWNOPS_ENV')!='test':require_isolated()
        if data!={'confirm':True}:raise HTTPException(422,'Confirm fictional staff test access only.')
        from argon2 import PasswordHasher
        prepared=[]
        with transaction(path) as db:
            permission(db,u,True)
            for label,role,active in [('manager','manager',1),('sales','sales',1),('crew','crew',1),('inactive','sales',0),('nonmaster-owner','owner',1)]:
                email='wix-pilot-'+label+'@example.invalid'
                old=one(db,'SELECT id FROM users WHERE email=?',(email,))
                if old:
                    prepared.append({'role':label,'email':email,'existing':True,'password':None});continue
                password=secrets.token_urlsafe(24)
                insert(db,'users',dict(id='WIX-PILOT-'+label,email=email,name='TEST Wix pilot '+label,role=role,password_hash=PasswordHasher().hash(password),active=active,created_at=now()))
                prepared.append({'role':label,'email':email,'existing':False,'password':password})
            evidence(db,u['id'],'Fictional staff pilot accounts prepared; no invitation sent',{'roles':[x['role'] for x in prepared]})
        return JSONResponse({'accounts':prepared,'delivery':'Shown once in this authenticated owner response only. Save privately; never paste into chat. Existing passwords are not returned or reset.','messages_sent':0},headers={'Cache-Control':'no-store','Pragma':'no-cache'})

    # Explicit hard blocks, even if an environment flag or browser payload asks otherwise.
    @app.post('/api/wix-drafts/actions/{action}')
    def blocked(action:str,data:dict,u=Depends(auth)):
        actor(u,True);raise HTTPException(503,'Wix contact/create/send/accept/invoice/booking writes are disabled in this release. No provider request made.')

# Future write adapter must use these reservations. No HTTP route invokes a create.
def reserve(db,qid,review_id,actor):
    permission(db,actor,True)
    review=one(db,'SELECT * FROM wix_draft_reviews WHERE id=? AND quote_id=?',(review_id,qid))
    if not review:raise HTTPException(409,'Review missing.')
    expected=json.loads(review['snapshot_json']);current=snapshot(db,qid,expected['basis'])
    if digest(current)!=review['snapshot_hash']:raise HTTPException(409,'Review stale.')
    old=one(db,'SELECT * FROM wix_draft_slots WHERE site=? AND quote_id=?',(SITE,qid))
    if old:
        if old['snapshot_hash']!=review['snapshot_hash']:raise HTTPException(409,'Consumed slot differs; reconcile existing operation.')
        return old,False
    db.execute('INSERT INTO wix_draft_slots VALUES(?,?,?,?,?,?,?)',(SITE,qid,review_id,review['snapshot_hash'],'Reserved',None,None))
    evidence(db,actor['id'],'Draft operation reserved; provider NOT called',{'quote_id':qid,'snapshot_hash':review['snapshot_hash']})
    return one(db,'SELECT * FROM wix_draft_slots WHERE site=? AND quote_id=?',(SITE,qid)),True
