"""Independent channel ownership, source deduplication and explicit call matching."""
import unittest
from app.store import transaction,insert,now,dumps,one,connect
from contextlib import closing
import test_conversations as conversation_fixture

class InboxTaskTests(unittest.TestCase):
    def setUp(self):
        self.f=conversation_fixture.ConversationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.sales=self.f.sales;self.manager=self.f.manager;self.other=self.f.other;self.path=self.f.path;self.lid=self.f.lid
    def queue(self,**kw):
        r=self.sales.get('/api/inbox-tasks',params=kw);self.assertEqual(r.status_code,200,r.text);return r.json()
    def call(self,cid='unmatched',lid=None,status='Missed'):
        with transaction(self.path) as db:insert(db,'calls',dict(id=cid,lead_id=lid,payload_json=dumps({'phone':'5145550198','disposition':status,'note':'Fictional call note'}),created_at=now()))
        return cid
    def get(self,tid,client=None):
        r=(client or self.sales).get('/api/inbox-tasks/'+tid);self.assertEqual(r.status_code,200,r.text);return r.json()
    def change(self,t,action,client=None,**kw):return (client or self.sales).post('/api/inbox-tasks/'+t['id'],json={'review_token':t['review_token'],'action':action,**kw})
    def test_channels_and_deduplication_keep_existing_call_task(self):
        cid=self.call(lid=self.lid)
        with transaction(self.path) as db:
            insert(db,'tasks',dict(id='original-call-task',lead_id=self.lid,title='Return missed call',due_date='2026-10-20',owner='',raw_json=dumps({'call_id':cid}),created_at=now()))
            insert(db,'messages',dict(id='newmail',lead_id=self.lid,kind='Incoming email',subject='Packing question',body='Can you pack?',author='test',created_at=now()))
        calls=self.queue(channel='calls');self.assertEqual([t['id'] for t in calls['items']],['original-call-task'])
        self.assertEqual(self.queue(channel='calls')['total'],calls['total'])
        emails=self.queue(channel='emails');self.assertEqual(emails['total'],1)
        self.assertEqual(self.queue(channel='forms')['total'],0) # fixture has no outstanding form
        self.assertEqual(calls['counts']['emails'],1)
    def test_claim_assign_complete_and_stale_updates_never_change_service_owner(self):
        self.call(lid=self.lid);t=self.queue(channel='calls')['items'][0]
        self.assertEqual(self.change(t,'claim',self.manager).status_code,200)
        self.assertEqual(self.change(t,'claim',self.other).status_code,409)
        self.assertEqual(self.f.owner(),'sales')
        t=self.get(t['id']);self.assertEqual(self.change(t,'assign',self.manager,user_id='sales2',note='Call after lunch').status_code,200)
        t=self.get(t['id']);self.assertEqual(self.change(t,'done',self.sales).status_code,403)
        self.assertEqual(self.change(t,'done',self.other,note='Client reached').status_code,200)
        self.assertEqual(self.queue(channel='calls')['total'],0)
        self.assertEqual(self.queue(channel='calls',status='completed')['total'],1)
        self.assertEqual(self.f.owner(),'sales')
        self.assertNotIn('Call after lunch',self.f.portal.get('/api/portal/conversation').text)
    def test_call_link_is_reviewed_audited_and_moves_only_its_task(self):
        cid=self.call();t=self.queue(channel='calls')['items'][0]
        self.assertEqual(t['contact_phone'],'5145550198');self.assertEqual(t['call_status'],'Missed')
        c=self.sales.get('/api/inbox-call/'+cid).json()
        url='/api/inbox-calls/'+cid+'/link';payload={'lead_id':self.lid,'review_token':c['review_token']}
        self.assertEqual(self.sales.post(url,json=payload).status_code,422)
        self.assertEqual(self.sales.post(url,json={**payload,'confirmed':True}).status_code,200)
        self.assertEqual(self.get(t['id'])['lead_id'],self.lid)
        self.assertEqual(self.sales.post(url,json={**payload,'confirmed':True}).status_code,409)
        self.assertIn('Fictional call note',str(self.f.detail()['events']))
        self.assertNotIn('Fictional call note',self.f.portal.get('/api/portal/conversation').text)
        with closing(connect(self.path)) as db:self.assertIsNotNone(one(db,"SELECT id FROM audit WHERE action='Call linked to client service'"))
    def test_answered_missed_voicemail_and_unknown_remain_distinct(self):
        for s in ('Answered','Missed','Voicemail','unrecognized'):self.call(s,status=s)
        for s in ('Answered','Missed','Voicemail','Recorded'):
            r=self.sales.get('/api/inbox-calls',params={'status':s});self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['total'],1)
        self.assertEqual(self.queue(channel='calls')['total'],3)

    def test_activity_order_uses_source_time_before_pagination_not_deadline_or_claim(self):
        with transaction(self.path) as db:
            for n in range(33):
                insert(db,'messages',dict(id=f'ordered-{n:02}',lead_id=self.lid,kind='Incoming email',subject=f'Email {n}',body='Fictional',author='test',created_at=f'2026-10-20T12:{n:02}:00+00:00'))
            # Imported after the emails, but the actual call occurred before them.
            insert(db,'calls',dict(id='earlier-call',lead_id=self.lid,payload_json=dumps({'disposition':'Answered','occurred_at':'2026-10-20T07:59:00-04:00'}),created_at='2026-10-21T12:00:00+00:00'))
        newest=self.queue(status='all');oldest=self.queue(status='all',order='oldest')
        self.assertEqual(newest['items'][0]['title'],'Reply to email: Email 32')
        self.assertEqual(oldest['items'][0]['call_status'],'Answered')
        all_ids=[t['id'] for t in newest['items']+self.queue(status='all',offset=30)['items']]
        self.assertEqual(len(all_ids),34);self.assertEqual(len(set(all_ids)),34)
        t=newest['items'][-1]
        self.assertEqual(self.change(t,'claim').status_code,200)
        self.assertEqual(self.change(self.get(t['id']),'schedule',due_date='2099-12-31').status_code,200)
        self.assertEqual([t['id'] for t in self.queue(status='all')['items']],all_ids[:30])
        self.assertEqual(self.sales.get('/api/inbox-tasks?order=invalid').status_code,422)
        log=self.sales.get('/api/inbox-calls?order=oldest').json()['items'][0]
        self.assertEqual(log['received_at'],'2026-10-20T11:59:00+00:00')

    def test_today_activity_filters_before_pagination_and_keeps_unmatched_form_details(self):
        with transaction(self.path) as db:
            for n in range(35):
                insert(db,'tasks',dict(id=f'internal-{n}',title='Check equipment',owner='sales2',due_date='2099-01-01',created_at='2099-01-01T12:00:00+00:00'))
            insert(db,'integration_events',dict(id='test-form',event_key='gmail:test-form',event_type='gmail_quote',object_id='test-form',payload_json=dumps({'received_at':'2026-10-22T22:18:00-04:00','parsed':{'client_name':'TEST MUSE Alex','email':'test@example.invalid','phone':'5145550100','move_date':'2026-11-05'}}),status='Needs review',created_at=now()))
            insert(db,'tasks',dict(id='unmatched-form',legacy_id='gmail-match:gmail:test-form',title='Match incoming quote form to the correct client service',created_at='2026-10-24T02:20:00+00:00'))
        r=self.queue(activity_only=True,status='all')
        self.assertEqual(r['total'],1)
        t=r['items'][0]
        self.assertEqual(t['id'],'unmatched-form');self.assertIsNone(t['lead_id'])
        self.assertEqual(t['client_name'],'TEST MUSE Alex');self.assertEqual(t['move_date'],'2026-11-05')
        self.assertEqual(t['received_at'],'2026-10-23T02:18:00+00:00');self.assertTrue(t['matching_required'])
        self.assertEqual(self.get(t['id'])['client_name'],t['client_name'])
        self.assertEqual(self.f.crew.get('/api/inbox-tasks?activity_only=true').status_code,403)

    def test_mine_includes_old_and_future_unfinished_work_with_independent_scope_counts(self):
        with transaction(self.path) as db:
            for tid,owner,due,status in [('old','sales','2000-01-01','Open'),('future','sales','2099-01-01','Open'),('done','sales','2000-01-01','Done'),('other','sales2','2000-01-01','Open'),('free','','2099-01-01','Open')]:
                insert(db,'tasks',dict(id=tid,title='Check equipment',owner=owner,due_date=due,status=status,created_at=now()))
        mine=self.queue(scope='mine',status='open')
        self.assertEqual({t['id'] for t in mine['items']},{'old','future'})
        self.assertEqual(mine['scope_counts'],{'team':4,'mine':2,'unclaimed':1})
        self.assertEqual(self.queue(scope='unclaimed',status='open')['total'],1)

    def test_answered_legacy_task_stays_visible_without_overdue_and_explicit_followup_survives(self):
        cid=self.call(lid=self.lid,status='Answered')
        with transaction(self.path) as db:
            insert(db,'tasks',dict(id='answered-legacy',lead_id=self.lid,title='Return / review client call',due_date='2000-01-01',owner='',raw_json=dumps({'call_id':cid}),created_at=now()))
        t=self.queue(status='all')['items'][0]
        self.assertEqual(t['call_status'],'Answered');self.assertEqual(t['bucket'],'completed')
        self.assertEqual(self.queue(status='overdue')['total'],0)
        self.assertEqual(self.change(t,'schedule',self.manager,due_date='2099-12-31',note='Promised a callback').status_code,200)
        t=self.queue()['items'][0];self.assertEqual(t['call_status'],'Answered');self.assertEqual(t['bucket'],'upcoming')
        with transaction(self.path) as db:db.execute("UPDATE tasks SET due_date='2000-01-01' WHERE id=?",(t['id'],))
        overdue=self.queue(status='overdue')['items'][0];self.assertEqual(overdue['id'],t['id'])
        self.assertEqual(self.change(overdue,'done',self.manager).status_code,200)
        self.assertEqual(self.queue()['total'],0)

    def test_answered_call_preserves_followup_scheduled_before_this_release(self):
        cid=self.call(lid=self.lid,status='Answered')
        from app.store import audit
        with transaction(self.path) as db:
            insert(db,'tasks',dict(id='scheduled-legacy',lead_id=self.lid,title='Call again',due_date='2000-01-01',owner='manager',raw_json=dumps({'call_id':cid,'inbox_managed':True}),created_at=now()))
            audit(db,'manager','Inbox follow-up schedule','tasks','scheduled-legacy',{'status':'Open'})
        t=self.queue(status='overdue')['items'][0];self.assertEqual(t['id'],'scheduled-legacy')
        self.assertEqual(t['call_status'],'Answered')

    def test_sales_can_claim_answered_activity_by_adding_a_callback(self):
        self.call(lid=self.lid,status='Answered')
        t=self.queue(status='all')['items'][0]
        self.assertEqual(self.change(t,'schedule',due_date='2099-12-31',note='Call after client checks access').status_code,200)
        self.assertEqual(self.change(t,'schedule',self.other,due_date='2099-12-31').status_code,409)
        active=self.queue()['items'][0]
        self.assertEqual(active['owner_id'],'sales');self.assertEqual(active['due_date'],'2099-12-31')
        self.assertEqual(self.f.owner(),'sales')

    def test_recorded_staff_call_is_activity_without_automatic_followup(self):
        self.f.claim(self.manager)
        self.assertEqual(self.f.post('entries',{'kind':'call','body':'Reached client','request_key':'staff-call-activity'},self.manager).status_code,200)
        t=self.queue(channel='calls',status='all')['items'][0]
        self.assertEqual(t['call_status'],'Recorded');self.assertEqual(t['bucket'],'completed')
        self.assertEqual(self.queue(channel='calls')['total'],0)
    def test_completed_and_archived_sources_do_not_reappear(self):
        cid=self.call(lid=self.lid)
        with transaction(self.path) as db:insert(db,'tasks',dict(id='old-call',lead_id=self.lid,title='Return missed call',status='Done',historical=1,raw_json=dumps({'call_id':cid}),created_at=now()))
        self.assertEqual(self.queue(channel='calls',status='all')['total'],0)
    def test_portal_messages_are_forms_and_new_messages_create_new_work(self):
        self.f.message();t=self.queue(channel='forms')['items'][0]
        self.assertEqual(self.change(t,'claim').status_code,200)
        self.assertEqual(self.change(self.get(t['id']),'done').status_code,200)
        self.assertEqual(self.queue(channel='forms')['total'],0)
        self.f.message('second-portal-message','Another question')
        self.assertEqual(self.queue(channel='forms')['total'],1)
    def test_gmail_quote_is_form_and_reuses_review_task(self):
        with transaction(self.path) as db:
            insert(db,'messages',dict(id='form-email',lead_id=self.lid,kind='Incoming email',subject='Quote form',body='Fictional quote form',author='test',created_at=now()))
            insert(db,'tasks',dict(id='form-task',legacy_id='quote-form:fake-event',lead_id=self.lid,title='Review new quote form details for this service',created_at=now()))
            insert(db,'integration_events',dict(id='fake-gmail',event_key='fake-event',event_type='gmail_quote',object_id=self.lid,payload_json=dumps({'message_id':'form-email','lead_id':self.lid}),status='Imported',created_at=now()))
        items=self.queue(channel='forms')['items'];self.assertEqual([t['id'] for t in items],['form-task'])
        self.assertEqual(self.queue(channel='emails')['total'],0)
        cid=self.f.detail()['client_id'];history=self.sales.get('/api/crm/clients/'+cid+'/history?kind=forms').json()
        self.assertIn('Fictional quote form',str(history['items']))
    def test_completing_callback_updates_client_visible_status(self):
        with transaction(self.path) as db:insert(db,'client_callbacks',dict(id='callback-one',lead_id=self.lid,request_key='callback-test-0001',phone='5145550198',created_at=now()))
        t=self.queue(channel='calls')['items'][0]
        self.assertEqual(self.change(t,'done',self.manager).status_code,200)
        events=self.f.portal.get('/api/portal/conversation').json()['items']
        callback=next(e for e in events if e['id']=='callback:callback-one')
        self.assertEqual(callback['status'],'Completed')
        self.assertEqual(self.change(self.get(t['id']),'reopen',self.manager).status_code,200)
        with closing(connect(self.path)) as db:self.assertEqual(one(db,"SELECT status FROM client_callbacks WHERE id='callback-one'")['status'],'Requested')

    def test_due_date_uses_montreal_and_service_assignment_cannot_overwrite_task_handler(self):
        self.call(lid=self.lid)
        with transaction(self.path) as db:db.execute("UPDATE calls SET created_at='2026-10-20T01:00:00+00:00' WHERE id='unmatched'")
        t=self.queue(channel='calls')['items'][0];self.assertEqual(t['due_date'],'2026-10-19')
        self.assertEqual(self.change(t,'claim',self.manager).status_code,200)
        from app.ux_features import _sync_client_owner
        with transaction(self.path) as db:_sync_client_owner(db,self.lid,'sales2','manager','Fictional service reassignment')
        self.assertEqual(self.get(t['id'])['owner'],'manager')

    def test_client_history_filters_and_crew_privacy(self):
        self.call(lid=self.lid);cid=self.f.detail()['client_id']
        r=self.sales.get('/api/crm/clients/'+cid+'/history?kind=calls');self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()['items']);self.assertTrue(all(x['kind'] in ('call','callback') for x in r.json()['items']))
        for url in ('/api/inbox-tasks','/api/inbox-calls','/api/inbox-call/unmatched'):
            self.assertEqual(self.f.crew.get(url).status_code,403)
    def test_task_handler_can_reply_without_taking_client_history_owner(self):
        self.f.claim(self.sales);self.call(lid=self.lid)
        t=self.queue(channel='calls')['items'][0];self.assertEqual(self.change(t,'claim',self.other).status_code,200)
        r=self.f.post('entries',{'kind':'call','body':'Reached the client','request_key':'different-task-handler'},self.other)
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.f.detail()['conversation']['handler_id'],'sales')
        self.assertEqual(self.f.owner(),'sales')
        self.assertEqual(self.f.post('status',{'status':'Resolved'},self.other).status_code,403)
        self.assertEqual(self.f.post('assign',{'handler_id':'sales2','note':'Should not take over'},self.other).status_code,403)

if __name__=='__main__':unittest.main()
