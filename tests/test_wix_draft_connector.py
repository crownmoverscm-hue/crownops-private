"""Actual CrownOps routes/auth, disposable DB, mocked Wix read transport only."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json,os,sqlite3,unittest
from unittest.mock import patch,MagicMock
from fastapi.testclient import TestClient
from app.main import sha
from app.store import connect,transaction,insert,now,one,rows,dumps
from app.platform_common import put_setting
from app import wix_draft_connector as w
import test_template_quotes as fixture

class WixDraftConnectorTests(unittest.TestCase):
 def setUp(self):
  self.f=fixture.TemplateQuoteTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
  self.path=self.f.path;self.client=self.f.client
  self.f.data.update(expiry_date='2026-11-14')
  self.quote=self.f.save().json();self.qid=self.quote['id']
  with transaction(self.path) as db:
   put_setting(db,'client_terms',{'approved':True,'version':'FIXTURE-v1','text':'FULL TEST TERMS — no service, payment or reservation.'})
   expires=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
   for rid,role,active in [('owner','owner',1),('otherowner','owner',1),('manager','manager',1),('inactive','owner',0)]:
    insert(db,'users',dict(id=rid,email=rid+'@example.invalid',name='TEST '+rid,role=role,password_hash='unused',active=active,created_at=now()))
    insert(db,'sessions',dict(token_hash=sha(rid+'-token'),user_id=rid,csrf=rid+'-csrf',expires_at=expires))
   put_setting(db,'master_owner',{'user_id':'owner'})
  self.login('owner')
 def login(self,role):
  self.client.cookies.set('crownops_session',role+'-token');self.client.headers['x-csrf-token']=role+'-csrf'
 def preflight(self):return self.client.get('/api/wix-drafts/preflight/'+self.qid).json()
 def review(self,p=None):
  p=p or self.preflight();return self.client.post('/api/wix-drafts/review/'+self.qid,json={'basis':'high','snapshot_hash':p['snapshot_hash'],'confirm':True,'reason':'Reviewed exact fictional snapshot; no provider write.'})
 def count(self,t):
  with closing(connect(self.path)) as db:return db.execute('SELECT count(*) FROM '+t).fetchone()[0]
 def test_new_fixture_uses_rate_list_without_rewriting_existing_fixture(self):
  with patch('requests.Session.post') as post:
   response=self.client.post('/api/wix-drafts/fixture',json={'confirm':True})
   self.assertEqual(response.status_code,200,response.text)
   with transaction(self.path) as db:
    record=one(db,"SELECT * FROM pricing_versions WHERE id='WIX-TEST-UNAPPROVED-PRICE'")
    self.assertEqual(json.loads(record['rates_json']),[])
    db.execute("UPDATE pricing_versions SET rates_json='{}' WHERE id=?",(record['id'],))
   again=self.client.post('/api/wix-drafts/fixture',json={'confirm':True})
   self.assertTrue(again.json()['duplicate'])
   with closing(connect(self.path)) as db:
    self.assertEqual(one(db,'SELECT rates_json FROM pricing_versions WHERE id=?',(record['id'],))['rates_json'],'{}')
   post.assert_not_called()
 def test_preflight_uses_actual_snapshot_and_never_calls_provider(self):
  before={t:self.count(t) for t in ('quotes','leads','jobs','outbound_messages','payments','audit','wix_draft_reviews')}
  with patch('requests.Session.post') as post:
   p=self.preflight();self.assertEqual(p['snapshot']['site'],w.SITE);self.assertEqual(p['snapshot']['totals']['subtotal_cents'],self.quote['result']['high_cents']);self.assertIn('FULL TEST TERMS',p['snapshot']['full_legal_terms']);self.assertFalse(p['writes_enabled']);post.assert_not_called()
  self.assertEqual(before,{t:self.count(t) for t in before})
 def test_owner_review_separate_from_create_and_idempotent(self):
  with patch('requests.Session.post') as post:
   first=self.review();second=self.review();self.assertEqual(first.status_code,200,first.text);self.assertTrue(second.json()['duplicate']);self.assertFalse(first.json()['create_authorized']);post.assert_not_called()
  self.assertEqual(self.count('wix_draft_reviews'),1);self.assertEqual(self.count('wix_draft_evidence'),1);self.assertEqual(self.count('jobs'),0)
 def test_role_matrix_for_read_and_review(self):
  p=self.preflight()
  for role in ('manager','sales','otherowner'):
   self.login(role);self.assertEqual(self.client.get('/api/wix-drafts/status').status_code,200);self.assertEqual(self.review(p).status_code,403)
  self.login('crew');self.assertEqual(self.client.get('/api/wix-drafts/status').status_code,403);self.assertEqual(self.review(p).status_code,403)
 def test_inactive_and_anonymous_denied(self):
  self.login('inactive');self.assertEqual(self.client.get('/api/wix-drafts/status').status_code,401)
  self.client.cookies.clear();self.assertEqual(self.client.get('/wix-draft-sync').status_code,401)
 def test_csrf_and_forged_owner_fields_denied(self):
  p=self.preflight();self.client.headers['x-csrf-token']='forged';self.assertEqual(self.review(p).status_code,403)
  self.login('owner');self.assertEqual(self.client.post('/api/wix-drafts/review/'+self.qid,json={'role':'owner','actor':'owner'}).status_code,422)
 def test_every_provider_write_hard_blocked_even_flags_enabled(self):
  with patch.dict(os.environ,{'CROWNOPS_WIX_CREATE_ENABLED':'1','CROWNOPS_ALLOW_EXTERNAL':'1'}),patch('requests.Session.post') as post:
   for action in ('contact-create','draft-create','send','accept','invoice','booking','payment'):
    self.assertEqual(self.client.post('/api/wix-drafts/actions/'+action,json={'confirm':True}).status_code,503)
   post.assert_not_called()
 def test_changed_price_or_terms_stales_review(self):
  p=self.preflight()
  with transaction(self.path) as db:put_setting(db,'client_terms',{'approved':True,'version':'v2','text':'Changed terms'})
  self.assertEqual(self.review(p).status_code,409)
 def test_any_new_revision_blocks_old(self):
  with transaction(self.path) as db:
   q=one(db,'SELECT * FROM quotes WHERE id=?',(self.qid,));q.update(id='TEST-REV2',revision=2);insert(db,'quotes',q)
  self.assertEqual(self.client.get('/api/wix-drafts/preflight/'+self.qid).status_code,409)
 def test_protected_quote_family_cannot_be_reviewed(self):
  with transaction(self.path) as db:
   q=one(db,'SELECT * FROM quotes WHERE id=?',(self.qid,));q.update(id='QUOTE-08dd48a364a34f66',revision=2);insert(db,'quotes',q)
  self.assertEqual(self.client.get('/api/wix-drafts/preflight/QUOTE-08dd48a364a34f66').status_code,403)
 def test_non_synthetic_quote_denied(self):
  with transaction(self.path) as db:db.execute('UPDATE leads SET client_name=? WHERE id=?',('Real looking name',self.quote['lead_id']))
  self.assertEqual(self.client.get('/api/wix-drafts/preflight/'+self.qid).status_code,409)
 def test_immutable_review_audit_and_mapping(self):
  rid=self.review().json()['review_id']
  with transaction(self.path) as db:
   a={'id':'owner'};first,claimed=w.reserve(db,self.qid,rid,a);self.assertTrue(claimed)
  with transaction(self.path) as db:second,claimed=w.reserve(db,self.qid,rid,{'id':'owner'});self.assertFalse(claimed);self.assertEqual(first,second)
  for sql in ('DELETE FROM wix_draft_reviews','UPDATE wix_draft_reviews SET reason="bad"','DELETE FROM wix_draft_evidence','UPDATE wix_draft_evidence SET event="bad"','DELETE FROM wix_draft_slots','UPDATE wix_draft_slots SET site="wrong"'):
   with self.assertRaises(sqlite3.IntegrityError):
    with transaction(self.path) as db:db.execute(sql)
 def test_review_reservation_transaction_rollback(self):
  rid=self.review().json()['review_id']
  with patch.object(w,'evidence',side_effect=OSError('storage')):
   with self.assertRaises(OSError):
    with transaction(self.path) as db:w.reserve(db,self.qid,rid,{'id':'owner'})
  self.assertEqual(self.count('wix_draft_slots'),0)
 def test_arbitrary_readback_forbidden(self):
  with patch('requests.Session.post') as post:
   r=self.client.post('/api/wix-drafts/readback/'+self.qid,json={'basis':'high','remote_id':'other'});self.assertEqual(r.status_code,409);post.assert_not_called()
 def test_read_adapter_default_off_wrong_host_and_secret(self):
  expected=self.preflight()['snapshot']
  with patch('requests.Session.post') as post:
   with self.assertRaises(Exception):w.WixReadAdapter().read('fixture-id',expected)
   with patch.dict(os.environ,{'CROWNOPS_WIX_READ_ENABLED':'1','CROWNOPS_WIX_READ_URL':'https://evil.invalid/_functions/crownopsQuoteRead','CROWNOPS_WIX_READ_SECRET':'x'*40}):
    with self.assertRaises(Exception):w.WixReadAdapter().read('fixture-id',expected)
   post.assert_not_called()
 def adapter(self,body,status=200):
  response=MagicMock();response.status_code=status;response.iter_content.return_value=[json.dumps(body).encode()]
  return response
 def test_signed_read_transport_and_schema_with_mock_http(self):
  expected=self.preflight()['snapshot'];body={'site':w.SITE,'id':'fixture-id','version':1,'status':'Draft','was_sent':False,'snapshot':expected}
  env={'CROWNOPS_WIX_READ_ENABLED':'1','CROWNOPS_WIX_READ_URL':'https://'+w.READ_HOST+w.READ_PATH,'CROWNOPS_WIX_READ_SECRET':'FIXTURE-ONLY-'*4}
  with patch.dict(os.environ,env),patch('requests.Session.post',return_value=self.adapter(body)) as post:
   result=w.WixReadAdapter().read('fixture-id',expected);self.assertTrue(result['observation_only']);self.assertEqual(post.call_count,1)
   kwargs=post.call_args.kwargs;self.assertFalse(kwargs['allow_redirects']);self.assertIn('X-Crown-Signature',kwargs['headers']);self.assertEqual(json.loads(kwargs['data'])['purpose'],'quote.read')
 def test_read_transport_redirect_error_and_material_changes_never_retry(self):
  expected=self.preflight()['snapshot'];body={'site':w.SITE,'id':'fixture-id','version':1,'status':'Draft','was_sent':False,'snapshot':expected}
  env={'CROWNOPS_WIX_READ_ENABLED':'1','CROWNOPS_WIX_READ_URL':'https://'+w.READ_HOST+w.READ_PATH,'CROWNOPS_WIX_READ_SECRET':'FIXTURE-ONLY-'*4}
  for status in (302,401,500):
   with patch.dict(os.environ,env),patch('requests.Session.post',return_value=self.adapter(body,status)) as post:
    with self.assertRaises(Exception):w.WixReadAdapter().read('fixture-id',expected)
    self.assertEqual(post.call_count,1)
  for key,value in [('site','other'),('version',False),('was_sent','false'),('snapshot',{})]:
   bad=deepcopy(body);bad[key]=value
   with patch.dict(os.environ,env),patch('requests.Session.post',return_value=self.adapter(bad)):
    with self.assertRaises(Exception):w.WixReadAdapter().read('fixture-id',expected)
 def test_production_mode_not_exposed(self):
  with patch.dict(os.environ,{'CROWNOPS_ENV':'production'}):self.assertEqual(self.client.get('/api/wix-drafts/status').status_code,404)
 def test_real_preflight_page_has_no_live_approval_buttons(self):
  response=self.client.get('/wix-draft-sync');self.assertEqual(response.status_code,200);self.assertIn('Create unsent Wix draft — blocked',response.text)
  self.assertIn('no provider write',response.text)

 def test_private_fixture_accounts_no_email_and_no_reset(self):
  with patch('requests.Session.post') as post:
   r=self.client.post('/api/wix-drafts/test-access',json={'confirm':True});self.assertEqual(r.status_code,200,r.text)
   self.assertEqual(len(r.json()['accounts']),5);self.assertTrue(all(x['password'] for x in r.json()['accounts']));post.assert_not_called()
   second=self.client.post('/api/wix-drafts/test-access',json={'confirm':True});self.assertTrue(all(x['password'] is None for x in second.json()['accounts']))
  with closing(connect(self.path)) as db:
   self.assertEqual(one(db,"SELECT user_id FROM (SELECT json_extract(value_json,'$.user_id') AS user_id FROM settings WHERE key='master_owner')")['user_id'],'owner')
  self.assertEqual(self.count('outbound_messages'),0)
  self.login('sales');self.assertEqual(self.client.post('/api/wix-drafts/test-access',json={'confirm':True}).status_code,403)
 def test_dedicated_fixture_preserves_global_terms_and_existing_quotes(self):
  before=self.count('quotes')
  with closing(connect(self.path)) as db:terms=one(db,"SELECT value_json FROM settings WHERE key='client_terms'")['value_json']
  with patch('requests.Session.post') as post:
   a=self.client.post('/api/wix-drafts/fixture',json={'confirm':True});self.assertEqual(a.status_code,200,a.text)
   b=self.client.post('/api/wix-drafts/fixture',json={'confirm':True});self.assertTrue(b.json()['duplicate'])
   p=self.client.get('/api/wix-drafts/preflight/'+w.FIXTURE_QUOTE);self.assertEqual(p.status_code,200,p.text);self.assertEqual(p.json()['snapshot']['totals']['total_cents'],20809);post.assert_not_called()
  self.assertEqual(self.count('quotes'),before+1)
  with closing(connect(self.path)) as db:self.assertEqual(one(db,"SELECT value_json FROM settings WHERE key='client_terms'")['value_json'],terms)

 def test_readiness_fixture_cannot_enter_existing_accounting_delivery(self):
  self.client.post('/api/wix-drafts/fixture',json={'confirm':True})
  from fastapi import HTTPException
  from app.estimate_delivery import source
  with closing(connect(self.path)) as db:
   with self.assertRaises(HTTPException):source(db,w.FIXTURE_QUOTE)
 def test_concurrent_claim_and_restart_keep_one_consumed_slot(self):
  from concurrent.futures import ThreadPoolExecutor
  rid=self.review().json()['review_id']
  def claim(_):
   with transaction(self.path) as db:return w.reserve(db,self.qid,rid,{'id':'owner'})[1]
  with ThreadPoolExecutor(max_workers=4) as pool:claimed=list(pool.map(claim,range(4)))
  self.assertEqual(sum(claimed),1)
  w.setup(self.path)
  self.assertFalse(claim(None));self.assertEqual(self.count('wix_draft_slots'),1)
