"""CRM regression tests use isolated records and never connect to provider accounts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from zoneinfo import ZoneInfo
import base64
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app, sha
from app.store import transaction, insert, one, rows, dumps, now
from app.gmail_sync import FEEDS, sync_gmail_readonly


class CrmPolishTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', 'QBO_ENV': 'sandbox'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'crm.sqlite3'
        self.app = create_app(self.path)
        self.day = datetime.now(ZoneInfo('America/Toronto')).date()
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with transaction(self.path) as db:
            for name, role in [('manager', 'owner'), ('agent', 'sales'), ('other', 'sales')]:
                insert(db, 'users', dict(id=name, email=name+'@example.invalid', name=name.title(), role=role,
                    password_hash='unused', active=1, created_at=now()))
                insert(db, 'sessions', dict(token_hash=sha(name+'-session'), user_id=name, csrf=name+'-csrf', expires_at=expires))
        self.clients = {}
        for name in ('manager', 'agent', 'other'):
            client = TestClient(self.app)
            client.cookies.set('crownops_session', name+'-session')
            client.headers['x-csrf-token'] = name+'-csrf'
            self.clients[name] = client
            self.addCleanup(client.close)

    def lead(self, lid='lead', owner=''):
        with transaction(self.path) as db:
            insert(db, 'leads', dict(id=lid, client_name='Test '+lid, phone='5145557788', stage='New Lead',
                assigned_to=owner, created_at=now(), updated_at=now()))
        return lid

    def task(self, tid='task', lid='lead', historical=0, raw=None, title='Call client', owner=''):
        with transaction(self.path) as db:
            insert(db, 'tasks', dict(id=tid, lead_id=lid, title=title, status='Open', owner=owner,
                historical=historical, due_date=self.day.isoformat(), raw_json=dumps(raw or {}), created_at=now()))
        return tid

    def email(self, mid='email', lid='lead'):
        with transaction(self.path) as db:
            insert(db, 'messages', dict(id=mid, lead_id=lid, kind='Incoming email', subject='Test question',
                body='Disposable client email', author='test', created_at=now()))
        return mid

    def callback(self, name='old', owner='', age=7):
        self.lead(name, owner)
        stamp = datetime.combine(self.day-timedelta(days=age), datetime.min.time(), tzinfo=timezone.utc).replace(hour=16).isoformat()
        raw = {'call_id': 'call-'+name, 'gmail_id': 'gmail-'+name}
        with transaction(self.path) as db:
            insert(db, 'calls', dict(id=raw['call_id'], lead_id=name, created_at=stamp,
                payload_json=dumps({'source': 'Gmail / UniFi', 'gmail_id': raw['gmail_id'], 'phone': '5145557788', 'occurred_at': stamp})))
        self.task('task-'+name, name, raw=raw, title='Return missed call', owner=owner)
        return 'task-'+name

    def preview(self):
        r = self.clients['manager'].get('/api/ux/callback-backlog?before='+self.day.isoformat())
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def archive(self, preview, selected):
        return self.clients['manager'].post('/api/ux/callback-backlog/archive', json={
            'before': preview['before'], 'review_token': preview['review_token'], 'task_ids': selected})

    def test_repeated_task_outcome_creates_one_owned_followup_and_one_audit(self):
        self.lead(); self.task()
        payload = {'outcome': 'followup', 'followup_date': (self.day+timedelta(days=2)).isoformat(),
            'followup_title': 'Call after review', 'note': 'Client requested Friday.'}
        client = self.clients['agent']
        first = client.post('/api/ux/actions/task/task/outcome', json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        retry = client.post('/api/ux/actions/task/task/outcome', json=payload)
        self.assertEqual(retry.status_code, 409, retry.text)
        with transaction(self.path) as db:
            self.assertEqual(one(db, "SELECT count(*) n FROM tasks WHERE status!='Done'")['n'], 1)
            self.assertEqual(one(db, 'SELECT assigned_to FROM leads WHERE id=?', ('lead',))['assigned_to'], 'agent')
            follow = one(db, 'SELECT * FROM tasks WHERE id=?', (first.json()['followup_task_id'],))
            self.assertEqual((follow['owner'], follow['due_date']), ('agent', payload['followup_date']))
            self.assertEqual(one(db, 'SELECT count(*) n FROM action_outcomes')['n'], 1)
            self.assertEqual(one(db, "SELECT count(*) n FROM audit WHERE action='Action outcome recorded'")['n'], 1)
        history = client.get('/api/ux/actions/task/task/workspace').json()
        self.assertEqual(history['outcomes'], [])
        self.assertTrue(any(x['kind']=='outcome' and 'Client requested Friday.' in x['detail'] for x in history['history']))

    def test_waiting_quote_sent_done_and_lost_preserve_the_right_work(self):
        self.lead(owner='agent'); self.task(); self.task('other-task'); self.task('history', historical=1)
        client = self.clients['agent']
        waiting = client.post('/api/ux/actions/task/task/outcome', json={'outcome':'waiting'})
        self.assertEqual(waiting.status_code, 200, waiting.text)
        with transaction(self.path) as db:
            self.assertEqual(one(db, 'SELECT stage FROM leads')['stage'], 'Waiting on client')
            self.assertEqual(one(db, "SELECT status FROM tasks WHERE id='other-task'")['status'], 'Open')
        self.email()
        quoted = client.post('/api/ux/actions/email/email/outcome', json={'outcome':'quote_sent'})
        self.assertEqual(quoted.status_code, 200, quoted.text)
        with transaction(self.path) as db:
            self.assertEqual(one(db, 'SELECT stage FROM leads')['stage'], 'Quote Sent')
            self.assertTrue(one(db, "SELECT resolved_at FROM inbox_claims WHERE message_id='email'")['resolved_at'])
        self.assertEqual(client.post('/api/ux/actions/email/email/outcome', json={'outcome':'lost'}).status_code, 409)
        self.task('done-task')
        done = client.post('/api/ux/actions/task/done-task/outcome', json={'outcome':'done'})
        self.assertEqual(done.status_code, 200, done.text)
        self.email('unclaimed-email')
        lost = client.post('/api/ux/actions/task/other-task/outcome', json={'outcome':'lost'})
        self.assertEqual(lost.status_code, 200, lost.text)
        with transaction(self.path) as db:
            self.assertEqual(one(db, 'SELECT stage FROM leads')['stage'], 'Lost')
            self.assertEqual(one(db, "SELECT count(*) n FROM tasks WHERE historical=0 AND status!='Done'")['n'], 0)
            self.assertEqual(one(db, "SELECT status FROM tasks WHERE id='history'")['status'], 'Open')
            self.assertTrue(one(db, "SELECT resolved_at FROM inbox_claims WHERE message_id='unclaimed-email'")['resolved_at'])
            self.assertEqual(one(db, 'SELECT count(*) n FROM messages')['n'], 2)

    def test_action_permissions_and_invalid_dates_leave_records_untouched(self):
        self.lead(owner='other'); self.task(owner='other')
        client = self.clients['agent']
        self.assertEqual(client.post('/api/ux/actions/task/task/outcome', json={'outcome':'done'}).status_code, 403)
        self.assertEqual(client.post('/api/tasks/task/complete', json={}).status_code, 403)
        for day in ('', '2026-02-30', (self.day-timedelta(days=1)).isoformat()):
            r = self.clients['other'].post('/api/ux/actions/task/task/outcome', json={'outcome':'followup','followup_date':day})
            self.assertEqual(r.status_code, 422, r.text)
        with transaction(self.path) as db:
            self.assertEqual(one(db, 'SELECT count(*) n FROM tasks')['n'], 1)
            self.assertEqual(one(db, 'SELECT count(*) n FROM action_outcomes')['n'], 0)
            self.assertEqual(one(db, 'SELECT status,owner FROM tasks'), {'status':'Open','owner':'other'})

    def test_backlog_preview_protects_claimed_recent_and_worked_clients(self):
        old = self.callback()
        self.callback('claimed', owner='agent')
        self.callback('today', age=0)
        self.callback('manual'); self.task('manual-work', 'manual')
        self.callback('email'); self.email('received', 'email')
        self.callback('contacted')
        with transaction(self.path) as db:
            db.execute("UPDATE leads SET stage='Contacted' WHERE id='contacted'")
        self.callback('recent')
        with transaction(self.path) as db:
            insert(db, 'calls', dict(id='recent-call', lead_id='recent', payload_json=dumps({'occurred_at':now()}), created_at=now()))
        self.callback('quote')
        with transaction(self.path) as db:
            insert(db, 'pricing_versions', dict(id='pricing', label='Test', rates_json='{}', settings_json='{}', created_at=now()))
            insert(db, 'quotes', dict(id='quote', lead_id='quote', revision=1, pricing_id='pricing', inputs_json='{}', result_json='{}',
                lead_snapshot_json='{}', lead_version=1, created_by='agent', created_at=now()))
        self.callback('job')
        with transaction(self.path) as db:
            insert(db, 'jobs', dict(id='job', lead_id='job', title='Test move', phase='Moving', start_at=now(), end_at=now(),
                created_by='agent', created_at=now()))
        self.callback('outcome')
        with transaction(self.path) as db:
            insert(db, 'action_outcomes', dict(id='record', action_type='task', action_id='old-task', lead_id='outcome',
                outcome='done', actor='agent', created_at=now()))
        self.callback('uncertain')
        with transaction(self.path) as db:
            db.execute("UPDATE calls SET created_at='2026-01-01 12:00:00',payload_json='{}' WHERE id='call-uncertain'")
        self.assertEqual([x['id'] for x in self.preview()['candidates']], [old])

    def test_reviewed_archive_is_reversible_and_retains_source_history(self):
        old = self.callback(); kept = self.callback('keep')
        preview = self.preview()
        archived = self.archive(preview, [old])
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertEqual(archived.json()['archived'], 1)
        client = self.clients['manager']
        active = {x['id'] for x in client.get('/api/tasks?scope=active').json()}
        historical = {x['id'] for x in client.get('/api/tasks?scope=imported').json()}
        self.assertNotIn(old, active); self.assertIn(kept, active); self.assertIn(old, historical)
        detail = client.get('/api/ux/actions/task/'+old+'/workspace').json()
        self.assertEqual(detail['outcomes'], [])
        self.assertTrue(detail['action']['historical'])
        blocked = client.post('/api/ux/actions/task/'+old+'/outcome', json={'outcome':'done'})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(client.post('/api/tasks/'+old+'/complete', json={}).status_code, 409)
        with transaction(self.path) as db:
            self.assertEqual(one(db, 'SELECT count(*) n FROM calls')['n'], 2)
            self.assertEqual(one(db, 'SELECT count(*) n FROM leads')['n'], 2)
            self.assertEqual(one(db, 'SELECT status,owner,due_date FROM tasks WHERE id=?', (old,)),
                {'status':'Open','owner':'','due_date':self.day.isoformat()})
        restored = client.post('/api/ux/callback-backlog/'+old+'/restore', json={})
        self.assertEqual(restored.status_code, 200, restored.text)
        with transaction(self.path) as db:
            task = one(db, 'SELECT * FROM tasks WHERE id=?', (old,))
            self.assertEqual(task['historical'], 0)
            self.assertEqual(json.loads(task['raw_json'])['call_id'], 'call-old')
            self.assertEqual(one(db, "SELECT count(*) n FROM audit WHERE action IN ('Reviewed callback archived','Archived callback restored')")['n'], 2)

    def test_backlog_stale_preview_and_unlisted_selection_cannot_archive(self):
        old = self.callback()
        preview = self.preview()
        self.assertEqual(self.archive(preview, [old, 'unknown']).status_code, 409)
        self.assertEqual(self.archive(preview, []).status_code, 422)
        with transaction(self.path) as db:
            db.execute("UPDATE leads SET assigned_to='agent',version=version+1 WHERE id='old'")
        self.assertEqual(self.archive(preview, [old]).status_code, 409)
        with transaction(self.path) as db:
            self.assertEqual(one(db, 'SELECT historical FROM tasks')['historical'], 0)

    def test_backlog_is_manager_only_and_rejects_future_cutoff(self):
        old = self.callback()
        client = self.clients['agent']
        self.assertEqual(client.get('/api/ux/callback-backlog').status_code, 403)
        self.assertEqual(client.post('/api/ux/callback-backlog/archive', json={}).status_code, 403)
        self.assertEqual(client.post('/api/ux/callback-backlog/'+old+'/restore', json={}).status_code, 403)
        tomorrow = (self.day+timedelta(days=1)).isoformat()
        self.assertEqual(self.clients['manager'].get('/api/ux/callback-backlog?before='+tomorrow).status_code, 422)

    def test_restore_inherits_current_owner_and_preserves_a_newer_active_callback(self):
        old = self.callback()
        self.assertEqual(self.archive(self.preview(), [old]).status_code, 200)
        with transaction(self.path) as db:
            db.execute("UPDATE leads SET assigned_to='agent' WHERE id='old'")
        self.task('newer', 'old', title='Review voicemail', owner='agent')
        route = '/api/ux/callback-backlog/'+old+'/restore'
        self.assertEqual(self.clients['manager'].post(route, json={}).status_code, 409)
        with transaction(self.path) as db:
            db.execute("UPDATE tasks SET status='Done' WHERE id='newer'")
        self.assertEqual(self.clients['manager'].post(route, json={}).status_code, 200)
        with transaction(self.path) as db:
            self.assertEqual(one(db, 'SELECT historical,owner FROM tasks WHERE id=?', (old,)), {'historical':0,'owner':'agent'})

    def test_overlapping_and_repeated_gmail_syncs_import_one_callback_with_owner(self):
        self.lead(owner='agent')
        barrier = Barrier(2)
        body = base64.urlsafe_b64encode(b'Missed call 09/30/2026 23:16:05').decode().rstrip('=')
        message = {'id':'source', 'internalDate':'1790802965000', 'payload':{
            'headers':[{'name':'Subject','value':'You Have a Missed Call from Crown Movers: TEST CLIENT 5145557788'}],
            'mimeType':'text/plain','body':{'data':body}}}

        def api(token, path, params=None):
            if path=='labels':
                return {'labels':[{'id':'calls', 'name':FEEDS['missed_call']}]}
            if path=='messages':
                return {'messages':[{'id':'source'}]}
            if path=='messages/source':
                barrier.wait(timeout=5)
                return message
            raise AssertionError('Unexpected provider read: '+path)

        def no_new_lead(*args, **kwargs):
            raise AssertionError('Known caller should use the existing lead.')

        def sync():
            return sync_gmail_readonly(self.path, 'unused-test-token', 'agent', no_new_lead, fluid=True)

        with patch('app.gmail_sync._api', side_effect=api):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _:sync(), range(2)))
            retry = sync()  # Already-imported source is skipped before fetching the message.
        self.assertEqual(sum(r['new_events'] for r in results), 1)
        self.assertEqual(sum(r['duplicates'] for r in results), 1)
        self.assertEqual(retry['new_events'], 0)
        self.assertEqual(retry['duplicates'], 1)
        with transaction(self.path) as db:
            self.assertEqual(one(db, 'SELECT count(*) n FROM calls')['n'], 1)
            self.assertEqual(one(db, 'SELECT count(*) n FROM tasks')['n'], 1)
            callback = one(db, 'SELECT * FROM tasks')
            self.assertEqual(callback['owner'], 'agent')
            self.assertEqual(callback['due_date'], '2026-09-30')  # The UTC call happened Oct 1.
            self.assertEqual(one(db, 'SELECT count(*) n FROM integration_events')['n'], 1)


if __name__ == '__main__':
    unittest.main()
