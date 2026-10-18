"""Malformed external references cannot block unrelated real inquiry intake."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import base64
import json
import unittest
from fastapi import HTTPException
from app.main import sha
from app.gmail_sync import FEEDS, sync_gmail_readonly
from app.store import connect, transaction, insert, one, rows, uid, now
import test_template_quotes as fixture


class IntakeResilienceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.TemplateQuoteTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.path, self.client = self.f.path, self.f.client
        with transaction(self.path) as db:
            insert(db, 'users', dict(id='owner', email='owner@example.invalid', name='Owner', role='owner', password_hash='unused', active=1, created_at=now()))
            insert(db, 'sessions', dict(token_hash=sha('owner-token'), user_id='owner', csrf='owner-csrf', expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
        self.client.cookies.set('crownops_session', 'owner-token')
        self.client.headers['x-csrf-token'] = 'owner-csrf'
        self.messages = {}

    def message(self, key, photo='', details='Boxes and furniture'):
        phone = '514555'+str(1000+len(self.messages))
        body = '\n'.join(['CROWN_QUOTE_BUILDER_INPUT', 'Client Name: Client '+key, 'Email: '+key+'@example.invalid', 'Phone: '+phone, 'Moving Date: 2026-11-15', 'Pickup Address: 100 Test Street', 'Destination Address: 200 Test Street', 'Additional Details: '+details, 'Photo / Video Link: '+photo, 'END_CROWN_QUOTE_BUILDER_INPUT'])
        self.messages[key] = {'id':key, 'internalDate':'1792203300000', 'payload':{'headers':[{'name':'Subject','value':'New Crown Movers Quote Request - Client '+key+' - 2026-11-15'}], 'mimeType':'text/plain', 'body':{'data':base64.urlsafe_b64encode(body.encode()).decode()}}}
        return body

    def gmail(self, token, path, params=None):
        if path == 'labels':
            return {'labels':[{'id':key,'name':name} for key,name in FEEDS.items()]}
        if path == 'messages':
            return {'messages':[{'id':key} for key in self.messages]} if params['labelIds']=='quote' else {'messages':[]}
        if path.startswith('messages/'):
            return self.messages[path.split('/')[1]]
        raise AssertionError('Unexpected Gmail operation: '+path)

    def sync(self):
        with patch('app.providers.provider_token', return_value={'access_token':'mock'}), patch('app.gmail_sync._api', side_effect=self.gmail):
            response = self.client.post('/api/gmail/sync-readonly', json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def records(self, table):
        with closing(connect(self.path)) as db:
            return rows(db, 'SELECT * FROM '+table+' ORDER BY id')

    def test_invalid_optional_photo_reference_keeps_the_inquiry_and_next_form(self):
        original = self.message('invalid', 'Please ask me for the photos')
        self.message('valid', 'https://drive.google.com/drive/folders/fictional-folder')
        result = self.sync()
        self.assertEqual(result['new_leads'], 2)
        lead = next(x for x in self.records('leads') if x['email']=='invalid@example.invalid')
        self.assertEqual(lead['photo_link'], '')
        self.assertTrue(any('photo' in t['title'].lower() and t['lead_id']==lead['id'] for t in self.records('tasks')))
        event = next(x for x in self.records('integration_events') if x['event_key']=='gmail:invalid')
        payload = json.loads(event['payload_json'])
        self.assertEqual(payload['parsed']['photo_link'], 'Please ask me for the photos')
        self.assertEqual(payload['body'], original)
        self.assertTrue(payload['photo_link_review'])
        self.assertEqual(next(x for x in self.records('leads') if x['email']=='valid@example.invalid')['photo_link'], 'https://drive.google.com/drive/folders/fictional-folder')

    def test_absent_photo_placeholders_do_not_create_false_review_tasks(self):
        for i,value in enumerate(('', '-', 'N/A', 'None')):
            self.message('placeholder'+str(i), value)
        self.assertEqual(self.sync()['new_leads'], 4)
        self.assertFalse(any('photo' in t['title'].lower() for t in self.records('tasks')))

    def test_other_invalid_details_are_queued_without_stopping_the_next_inquiry(self):
        original = self.message('oversized', details='x'*7000)
        self.message('usable')
        result = self.sync()
        self.assertEqual(result['new_leads'], 1)
        self.assertEqual(result['needs_review'], 1)
        self.assertEqual(len(self.records('leads')), 1)
        event = next(x for x in self.records('integration_events') if x['event_key']=='gmail:oversized')
        self.assertEqual(event['status'], 'Needs review')
        payload = json.loads(event['payload_json'])
        self.assertEqual(payload['body'], original)
        self.assertTrue(payload['validation_review'])

    def test_retry_keeps_review_tasks_and_client_records_idempotent(self):
        self.message('invalid', 'Ask me later')
        self.message('oversized', details='x'*7000)
        self.sync()
        before = tuple(len(self.records(t)) for t in ('leads','tasks','messages','integration_events'))
        self.assertEqual(self.sync()['duplicates'], 2)
        self.assertEqual(before, tuple(len(self.records(t)) for t in ('leads','tasks','messages','integration_events')))
        self.assertEqual(self.records('outbound_messages'), [])
        self.assertEqual(self.records('quotes'), [])

    def test_manual_photo_entry_still_requires_a_valid_complete_url(self):
        response = self.client.post('/api/leads', json={'client_name':'Manual test', 'photo_link':'Ask me later'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.records('leads'), [])

    def test_validation_after_a_partial_insert_rolls_back_that_message_only(self):
        self.message('broken')
        self.message('usable')
        def create(db,data,actor,external_id=None,suppressed=1):
            record=dict(id=uid('LEAD'), client_name=data['client_name'], external_id=external_id, created_at=now(), updated_at=now())
            insert(db,'leads',record)
            if 'broken' in data['client_name']:
                insert(db,'tasks',dict(id=uid('TASK'),lead_id=record['id'],title='Partial task',created_at=now()))
                raise HTTPException(422,'Review the incoming client details.')
            return record
        with patch('app.gmail_sync._api', side_effect=self.gmail):
            result=sync_gmail_readonly(self.path,'mock','owner',create,fluid=True,feed_names=('quote',))
        self.assertEqual(result['new_leads'],1)
        self.assertEqual(len(self.records('leads')),1)
        self.assertFalse(any(x['title']=='Partial task' for x in self.records('tasks')))
        self.assertEqual(len(self.records('integration_events')),2)
