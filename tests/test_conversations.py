"""Shared history and ownership regression tests; all people and providers are fictional."""
import copy
import unittest
from contextlib import closing
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import sha
from app.store import connect,transaction,one,rows,insert,now
import test_client_journey as journey_fixture

class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.f=journey_fixture.ClientJourneyTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.app=self.f.app;self.path=self.f.path;self.sales=self.f.sales;self.lid=self.f.lid
        self.f.open_portal();self.portal=self.f.portal
        with transaction(self.path) as db:
            for role in ('manager','sales2'):
                insert(db,'users',dict(id=role,email=role+'@example.invalid',name=role,role='sales' if role=='sales2' else role,password_hash='unused',active=1,created_at=now()))
                insert(db,'sessions',dict(token_hash=sha(role+'-token'),user_id=role,csrf=role+'-csrf',expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
            db.execute("UPDATE leads SET assigned_to='sales' WHERE id=?",(self.lid,))
        self.manager=self.client('manager');self.other=self.client('sales2');self.crew=self.client('crew')
    def client(self,role):
        c=TestClient(self.app);c.cookies.set('crownops_session',role+'-token');c.headers['x-csrf-token']=role+'-csrf';self.addCleanup(c.close);return c
    def detail(self,client=None):
        r=(client or self.sales).get('/api/inbox/'+self.lid);self.assertEqual(r.status_code,200,r.text);return r.json()
    def post(self,action,data=None,client=None):
        c=client or self.sales
        return c.post('/api/inbox/'+self.lid+'/'+action,json={'review_token':self.detail(c)['conversation']['review_token'],**(data or {})})
    def claim(self,client=None):
        r=self.post('claim',client=client);self.assertEqual(r.status_code,200,r.text)
    def message(self,key='client-message-0001',body='Can we add packing?'):
        return self.portal.post('/api/portal/conversation',json={'request_key':key,'body':body})
    def owner(self):
        with closing(connect(self.path)) as db:return one(db,'SELECT assigned_to FROM leads WHERE id=?',(self.lid,))['assigned_to']
    def test_manager_handles_call_without_taking_sales_ownership(self):
        self.claim(self.manager)
        r=self.post('entries',{'kind':'call','body':'Client confirmed elevator access.','request_key':'manual-call-00001'},self.manager)
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.owner(),'sales')
        self.assertIn('elevator',str(self.detail()['events']))
        self.assertNotIn('elevator',self.portal.get('/api/portal/conversation').text)
    def test_claim_is_exclusive_and_stale_claim_rejected(self):
        token=self.detail()['conversation']['review_token'];self.claim()
        r=self.other.post('/api/inbox/'+self.lid+'/claim',json={'review_token':token});self.assertEqual(r.status_code,409)
        self.assertEqual(self.post('claim',client=self.other).status_code,409)
    def test_handoff_requires_note_and_preserves_owner(self):
        self.claim();self.assertEqual(self.post('assign',{'handler_id':'manager'}).status_code,422)
        r=self.post('assign',{'handler_id':'manager','note':'Please confirm elevator.'});self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.detail()['conversation']['handler_id'],'manager');self.assertEqual(self.owner(),'sales')
        self.assertNotIn('Please confirm elevator',self.portal.get('/api/portal/conversation').text)
        self.assertEqual(self.post('entries',{'kind':'note','body':'Should not save','request_key':'blocked-note-0001'}).status_code,403)
    def test_new_client_message_reopens_and_cancels_waiting_reminder(self):
        self.claim();self.assertEqual(self.post('status',{'status':'Waiting on client'}).status_code,422)
        future=(datetime.now(timezone.utc)+timedelta(days=1)).date().isoformat()
        for _ in range(2):self.assertEqual(self.post('status',{'status':'Waiting on client','remind_on':future}).status_code,200)
        with closing(connect(self.path)) as db:self.assertEqual(len(rows(db,'SELECT * FROM tasks WHERE legacy_id=?',('conversation-reminder:'+self.lid,))),1)
        self.assertEqual(self.message().status_code,200)
        c=self.detail()['conversation'];self.assertEqual(c['status'],'Needs reply');self.assertEqual(c['handler_id'],'sales');self.assertEqual(c['remind_on'],'')
        with closing(connect(self.path)) as db:self.assertEqual(one(db,'SELECT status FROM tasks WHERE legacy_id=?',('conversation-reminder:'+self.lid,))['status'],'Done')
    def test_client_message_retry_is_idempotent_and_content_conflict_rejected(self):
        a=self.message();b=self.message();self.assertEqual(a.status_code,200,a.text);self.assertEqual(a.json()['id'],b.json()['id'])
        self.assertEqual(self.message(body='Different message').status_code,409)
        self.assertEqual(sum(x['title']=='Client message' for x in self.detail()['events']),1)
    def test_internal_notes_and_handoff_stay_private_public_reply_and_opt_in_call_appear(self):
        self.claim()
        for kind,body,share in [('note','STAFF SECRET',False),('portal','We can help with packing.',False),('call','INTERNAL CALL',False),('call','Shared call recap',True)]:
            r=self.post('entries',{'kind':kind,'body':body,'share_with_client':share,'request_key':'entry-'+body.replace(' ','_').replace('.','')});self.assertEqual(r.status_code,200,r.text)
        text=self.portal.get('/api/portal/conversation').text
        self.assertNotIn('STAFF SECRET',text);self.assertNotIn('INTERNAL CALL',text);self.assertIn('Shared call recap',text);self.assertIn('We can help',text)
    def test_second_service_aggregates_for_staff_but_isolated_in_portal(self):
        data=copy.deepcopy(self.f.f.data);data['draft_key']='second-service';data['move_date']='2026-11-20'
        data['client_id']=self.detail()['client_id'];data.pop('new_client',None)
        r=self.f.f.save(data);self.assertEqual(r.status_code,200,r.text);second=r.json()['lead_id'];self.assertNotEqual(second,self.lid)
        from app.conversations import entry
        with transaction(self.path) as db:entry(db,second,'portal','SECOND SERVICE SECRET','Other service','manager','second-service-entry','client')
        cid=self.detail()['client_id'];history=self.sales.get('/api/crm/clients/'+cid+'/history');self.assertIn('SECOND SERVICE SECRET',history.text)
        self.assertNotIn('SECOND SERVICE SECRET',self.portal.get('/api/portal/conversation').text)
        self.assertNotIn('SECOND SERVICE SECRET',self.portal.get('/api/portal/conversation?lead_id='+second).text)
    def test_draft_is_not_sent_and_private_until_confirmed(self):
        self.claim();r=self.post('email',{'subject':'Packing options','body':'A fictional email.','request_key':'draft-email-00001'})
        self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['sent']);mid=r.json()['mail_id']
        self.assertNotIn('A fictional email.',self.portal.get('/api/portal/conversation').text)
        m=self.sales.get('/api/inbox/mail/'+mid).json()
        self.assertEqual(self.sales.post('/api/inbox/mail/'+mid+'/send',json={'confirmed':False,'review_token':m['review_token']}).status_code,422)
        with patch('app.providers.process_mail',return_value={'sent':False,'reason':'Fictional test, provider disabled'}) as provider:
            r=self.sales.post('/api/inbox/mail/'+mid+'/send',json={'confirmed':True,'review_token':m['review_token']});self.assertEqual(r.status_code,200,r.text);provider.assert_called_once()
        self.assertNotIn('A fictional email.',self.portal.get('/api/portal/conversation').text)
        with transaction(self.path) as db:db.execute("UPDATE outbound_messages SET status='Sent',sent_at=? WHERE id=?",(now(),mid))
        self.assertIn('A fictional email.',self.portal.get('/api/portal/conversation').text)
    def test_new_activity_invalidates_review_token_even_same_second(self):
        self.claim();self.message();token=self.detail()['conversation']['review_token'];self.message('client-message-0002','Another question')
        r=self.sales.post('/api/inbox/'+self.lid+'/status',json={'review_token':token,'status':'Resolved'});self.assertEqual(r.status_code,409)
    def test_upload_and_legacy_email_reopen_conversation(self):
        self.claim();self.post('status',{'status':'Resolved'})
        with transaction(self.path) as db:insert(db,'messages',dict(id='INCOMING-TEST',lead_id=self.lid,kind='Incoming email',subject='New question',body='Please call',author='client',created_at=now()))
        self.assertEqual(self.detail()['conversation']['status'],'Needs reply')
        self.post('status',{'status':'Resolved'})
        with transaction(self.path) as db:insert(db,'attachments',dict(id='PHOTO-TEST',lead_id=self.lid,category='Move photos',original_name='room.jpg',stored_name='fake.jpg',mime='image/jpeg',size=100,sha256='fake',author='client',created_at=now()))
        self.assertEqual(self.detail()['conversation']['status'],'Needs reply');self.assertIn('room.jpg',str(self.detail()['events']))
    def test_crew_cannot_read_or_mutate_office_inbox(self):
        for path in ['/api/inbox','/api/inbox/'+self.lid,'/api/inbox/activity','/api/crm/clients/'+self.detail()['client_id']+'/history']:
            self.assertEqual(self.crew.get(path).status_code,403,path)
        self.assertEqual(self.crew.post('/api/inbox/'+self.lid+'/claim',json={}).status_code,403)
    def test_outbound_credentials_and_other_recipient_never_republished(self):
        from app.platform_common import queue_message
        with transaction(self.path) as db:
            for i,key in enumerate(['client-access:test','ordinary:test','other-recipient:test']):
                m=queue_message(db,self.lid,'Private access','https://example.invalid/portal#access=SECRET'+str(i),'Reply','sales',key)
                db.execute("UPDATE outbound_messages SET status='Sent',sent_at=? WHERE id=?",(now(),m['id']))
                if i==2:db.execute("UPDATE outbound_messages SET recipient='other@example.invalid' WHERE id=?",(m['id'],))
        for endpoint in ['/api/portal/project','/api/portal/conversation']:
            r=self.portal.get(endpoint);self.assertEqual(r.status_code,200,r.text);self.assertNotIn('SECRET',r.text)
