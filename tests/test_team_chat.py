import unittest
from fastapi.testclient import TestClient
from app.store import connect,one
import test_template_quotes as fixture

class TeamChatTests(unittest.TestCase):
 def setUp(self):
  from unittest.mock import patch
  self.flags=patch.dict('os.environ',{'CROWNOPS_TEAM_CHAT_MODE':'all'});self.flags.start();self.addCleanup(self.flags.stop)
  self.f=fixture.TemplateQuoteTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.sales=self.f.client
  self.crew=TestClient(self.f.app);self.addCleanup(self.crew.close);self.crew.cookies.set('crownops_session','crew-token');self.crew.headers['x-csrf-token']='crew-csrf'
 def test_dm_unread_retry_and_no_inbox_task(self):
  t=self.sales.post('/api/team-chat/threads',json={'members':['crew']}).json()['id']
  self.assertEqual(self.sales.post('/api/team-chat/threads',json={'members':['crew']}).json()['id'],t)
  path='/api/team-chat/threads/'+t
  data={'body':'TEST private message','client_key':'test-message-1'}
  m=self.sales.post(path+'/messages',json=data);self.assertEqual(m.status_code,200,m.text)
  self.assertTrue(self.sales.post(path+'/messages',json=data).json()['duplicate'])
  self.assertEqual(self.crew.get('/api/team-chat/threads').json()[0]['unread'],1)
  messages=self.crew.get(path+'/messages').json();self.assertEqual(len(messages),1)
  self.crew.post(path+'/read',json={'seq':messages[0]['seq']})
  self.assertEqual(self.crew.get('/api/team-chat/threads').json()[0]['unread'],0)
  with connect(self.f.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
 def test_invalid_members_and_unauthenticated_access(self):
  self.assertEqual(self.sales.post('/api/team-chat/threads',json={'members':['missing']}).status_code,422)
  anon=TestClient(self.f.app);self.addCleanup(anon.close)
  self.assertEqual(anon.get('/api/team-chat/people').status_code,401)
  self.assertEqual(self.crew.get('/api/team-chat/threads/unknown/messages').status_code,404)
 def test_retry_key_cannot_be_reused_for_other_contents(self):
  t=self.sales.post('/api/team-chat/threads',json={'members':['crew']}).json()['id'];p='/api/team-chat/threads/'+t+'/messages'
  self.sales.post(p,json={'body':'one','client_key':'test-message-1'})
  self.assertEqual(self.sales.post(p,json={'body':'two','client_key':'test-message-1'}).status_code,409)
 def test_nonmember_owner_cannot_read_private_dm(self):
  from app.main import sha
  from app.store import transaction,insert,now
  from datetime import datetime,timezone,timedelta
  with transaction(self.f.path) as db:
   insert(db,'users',dict(id='private-owner',name='TEST owner',email='private-owner@example.invalid',role='owner',password_hash='unused',created_at=now()))
   insert(db,'sessions',dict(token_hash=sha('owner-token'),user_id='private-owner',csrf='owner-csrf',expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
  owner=TestClient(self.f.app);self.addCleanup(owner.close);owner.cookies.set('crownops_session','owner-token');owner.headers['x-csrf-token']='owner-csrf'
  t=self.sales.post('/api/team-chat/threads',json={'members':['crew']}).json()['id'];p='/api/team-chat/threads/'+t
  self.assertEqual(owner.get(p+'/messages').status_code,404)
  self.assertEqual(owner.post(p+'/messages',json={'body':'intrusion','client_key':'forbidden-key'}).status_code,404)
  self.assertEqual(owner.post(p+'/read',json={'seq':9999}).status_code,404)
