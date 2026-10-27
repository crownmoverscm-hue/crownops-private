"""Synthetic local records and simulated provider reads/writes only."""
from contextlib import closing
from copy import deepcopy
import base64
import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from app.store import connect, transaction, one, rows, insert, now, dumps
from app.platform_common import settings, put_setting
from app.main import sha
from app import quote_staging as scope
from app import sandbox_estimate_send as delivery
from app import sandbox_estimate_events as events
import test_template_quotes as fixture


class QuoteStagingReleaseTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.TemplateQuoteTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.path, self.client = self.f.path, self.f.client
        self.env = {'CROWNOPS_ENV': 'staging', 'QBO_ENV': 'sandbox', 'QBO_SANDBOX_REALM_ID': scope.REALM,
                    'CROWNOPS_PUBLIC_URL': 'https://' + scope.HOST, 'CROWNOPS_DB': scope.DATABASE,
                    'RAILWAY_SERVICE_ID': scope.SERVICE, 'RAILWAY_ENVIRONMENT_ID': scope.ENVIRONMENT,
                    'CROWNOPS_QUOTE_TEST_FIXTURE': '2026-11-15', 'CROWNOPS_QBO_TEST_CREATE_QUOTE': scope.QUOTE,
                    'CROWNOPS_QBO_TEST_SEND_ESTIMATE': '', 'CROWNOPS_QBO_TEST_WEBHOOK_CAPTURE': '0',
                    **{k: '0' for k in delivery.OFF}}
        env = patch.dict(os.environ, self.env); env.start(); self.addCleanup(env.stop)
        self.f.data.update(hourly_rate=100, transport_mode='local_amount', fixed_transport=150, local_fuel_rate=35.99, expiry_date='2026-11-14')
        self.f.data['new_client']['email'] = delivery.CUSTOMER_EMAIL
        q1 = self.f.save().json()
        data = deepcopy(self.f.data); data.pop('new_client'); data.pop('service_details')
        data.update(lead_id=q1['lead_id'], lead_version=q1['lead_version'], source_quote_id=q1['id'], draft_key='r2', client_note='TEST revision 2')
        result = self.f.save(data); self.assertEqual(result.status_code, 200, result.text)
        q2 = result.json()
        with transaction(self.path) as db:
            db.execute('UPDATE quotes SET id=? WHERE id=?', ('QUOTE-8a7b31fbc00e4302', q1['id']))
            db.execute('UPDATE quotes SET id=? WHERE id=?', (scope.QUOTE, q2['id']))
            insert(db, 'users', dict(id='owner', email='owner@example.invalid', name='TEST Owner', role='owner', password_hash='unused', active=1, created_at=now()))
            insert(db, 'sessions', dict(token_hash=sha('owner-token'), user_id='owner', csrf='owner-csrf', expires_at='2099-01-01T00:00:00+00:00'))
            from app.estimate_delivery import delivery_source, estimate_payload
            q, self.lead, terms, source_hash = delivery_source(db, scope.QUOTE)
            self.payload = estimate_payload(q, self.lead, terms, 'low', '1', '2', '')
            self.payload['CustomerRef'] = {'value': '70'}
            self.remote = deepcopy(self.payload)
            self.remote.update(Id='185', SyncToken='0', TotalAmt=558.77, TxnTaxDetail={'TotalTax': 72.78,
                'TaxLine': [{'Amount': 24.30}, {'Amount': 48.48}]})
            insert(db, 'qbo_estimate_deliveries', dict(quote_id=scope.QUOTE, environment='sandbox', realm_id=scope.REALM,
                   external_id='185', basis='low', state='Ready', source_hash=source_hash, payload_json=dumps(self.payload),
                   remote_json=dumps(self.remote), recipient=delivery.CUSTOMER_EMAIL, created_by='owner', created_at=now()))
            for kind, oid, eid, revision in [('Customer', self.lead['id'], '70', 0), ('Estimate', scope.QUOTE, '185', 2)]:
                insert(db, 'external_records', dict(provider='quickbooks', kind=kind, object_id=oid, external_id=eid, revision=revision, updated_at=now()))
            put_setting(db, 'sandbox_estimate_test_attempt_v1', {'quote_id': scope.QUOTE, 'state': 'Ready', 'external_id': '185'})
        self.client.cookies.set('crownops_session', 'owner-token'); self.client.headers['x-csrf-token'] = 'owner-csrf'
        self.connection = patch('app.sandbox_estimate_send._qbo_connection', return_value=({'access_token': 'fake'}, scope.REALM, 'https://sandbox-quickbooks.api.intuit.com'))
        self.reads = patch('app.sandbox_estimate_send.qbo_read', side_effect=self.read)
        for mock in (self.connection, self.reads):
            mock.start(); self.addCleanup(mock.stop)
        self.base = '/api/quotes/' + scope.QUOTE

    def read(self, path, resource):
        if resource.startswith('companyinfo/'):
            return {'CompanyInfo': {'CompanyName': 'Sandbox Company CA 5634', 'Country': 'CA'}}
        if resource == 'customer/70':
            return {'Customer': {'Id': '70', 'DisplayName': 'TEST Customer', 'PrimaryEmailAddr': {'Address': delivery.CUSTOMER_EMAIL}}}
        if resource == 'estimate/185':
            return {'Estimate': deepcopy(self.remote)}
        raise AssertionError(resource)

    def review(self):
        result = self.client.get(self.base + '/sandbox-send-preflight')
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def approval_data(self):
        p = self.review()
        return {'confirm': True, 'review_hash': p['review_hash'], 'recipient': delivery.RECIPIENT,
                'estimate_id': '185', 'no_cc_bcc_confirmed': True, 'ack_bill_email_change': True,
                'ack_unknown_delivery': True, 'evidence_reference': 'SIMULATED sandbox email settings review'}

    def approve_simulated_send(self):
        data = self.approval_data()
        result = self.client.post(self.base + '/sandbox-send-approval', json=data)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse(result.json()['email_sent'])
        os.environ['CROWNOPS_QBO_TEST_SEND_ESTIMATE'] = '185'
        p = self.review(); self.assertTrue(p['can_send'], p['blockers'])
        return {'confirm': True, 'review_hash': p['review_hash'], 'recipient': delivery.RECIPIENT}

    def test_owner_approval_is_separate_durable_idempotent_and_snapshot_bound(self):
        data = self.approval_data()
        with closing(connect(self.path)) as db:
            original = one(db, 'SELECT payload_json,remote_json,source_hash FROM qbo_estimate_deliveries')
        with patch('app.sandbox_estimate_send.requests.post') as post:
            result = self.client.post(self.base + '/sandbox-send-approval', json=data)
            self.assertEqual(result.status_code, 200, result.text)
            again = self.client.post(self.base + '/sandbox-send-approval', json=data)
            self.assertTrue(again.json()['duplicate'])
            self.assertEqual(result.json()['approved_at'], again.json()['approved_at'])
            post.assert_not_called()
        p = self.review(); self.assertTrue(p['owner_approved']); self.assertFalse(p['can_send'])
        self.assertFalse(p['cc_bcc']['provider_defaults_verified_absent'])
        self.assertTrue(p['cc_bcc']['owner_confirmed_absent'])
        self.assertIn('unverified', p['sender']); self.assertIn('unverified', p['reply_to'])
        with closing(connect(self.path)) as db:
            a = settings(db, delivery.APPROVAL)
            self.assertEqual((a['approved_by'], a['estimate_id'], a['recipient'], a['total_cents']),
                             ('owner', '185', delivery.RECIPIENT, 55877))
            self.assertEqual(one(db, 'SELECT payload_json,remote_json,source_hash FROM qbo_estimate_deliveries'), original)
            self.assertFalse(settings(db, delivery.SEND_SLOT))
        os.environ['CROWNOPS_QBO_TEST_SEND_ESTIMATE'] = '185'
        self.assertTrue(self.review()['can_send'])
        self.remote['SyncToken'] = '1'
        self.assertFalse(self.review()['owner_approved'])
        self.assertFalse(self.review()['can_send'])
        for table in ('jobs','payments','outbound_messages'):
            self.assertEqual(self.f.count(table), 0)

    def test_approval_requires_master_csrf_confirmations_and_exact_review(self):
        data = self.approval_data()
        for key, wrong in [('recipient','other@example.invalid'), ('estimate_id','186'), ('review_hash','forged')]:
            self.assertEqual(self.client.post(self.base+'/sandbox-send-approval',json={**data,key:wrong}).status_code,409)
        for key in ('confirm','no_cc_bcc_confirmed','ack_bill_email_change','ack_unknown_delivery'):
            self.assertIn(self.client.post(self.base+'/sandbox-send-approval',json={**data,key:False}).status_code,(409,422))
        self.assertEqual(self.client.post(self.base+'/sandbox-send-approval',json={**data,'evidence_reference':''}).status_code,422)
        self.client.headers['x-csrf-token']='forged'
        self.assertEqual(self.client.post(self.base+'/sandbox-send-approval',json=data).status_code,403)
        self.client.headers['x-csrf-token']='owner-csrf'
        with transaction(self.path) as db:
            insert(db,'users',dict(id='other-owner',email='other@example.invalid',name='Other owner',role='owner',password_hash='unused',active=1,created_at=now()))
            insert(db,'sessions',dict(token_hash=sha('other-token'),user_id='other-owner',csrf='other-csrf',expires_at='2099-01-01T00:00:00+00:00'))
            put_setting(db,'master_owner',{'user_id':'owner'})
        for token, csrf in [('sales-token','sales-csrf'),('crew-token','crew-csrf'),('other-token','other-csrf')]:
            self.client.cookies.set('crownops_session',token); self.client.headers['x-csrf-token']=csrf
            self.assertEqual(self.client.post(self.base+'/sandbox-send-approval',json=data).status_code,403)
            self.assertEqual(self.client.post(self.base+'/sandbox-send',json=data).status_code,403)
        with closing(connect(self.path)) as db:
            self.assertFalse(settings(db,delivery.APPROVAL)); self.assertFalse(settings(db,delivery.SEND_SLOT))

    def test_cc_bcc_stale_revision_and_consumed_slot_prevent_approval(self):
        data = self.approval_data()
        self.remote['BillEmailCc']={'Address':'unexpected@example.invalid'}
        p=self.review(); self.assertFalse(p['approval_eligible'])
        self.assertEqual(self.client.post(self.base+'/sandbox-send-approval',json={**data,'review_hash':p['review_hash']}).status_code,409)
        self.remote.pop('BillEmailCc')
        with transaction(self.path) as db:
            put_setting(db,delivery.SEND_SLOT,{'state':'Unknown'})
        self.assertEqual(self.client.post(self.base+'/sandbox-send-approval',json=data).status_code,409)
        self.assertFalse(self.review()['approval_eligible'])

    def test_readonly_existing_snapshot_linkage_and_gates(self):
        before = self.f.count('quotes')
        p = self.review()
        self.assertEqual((p['customer_id'], p['external_id'], p['revision']), ('70', '185', 2))
        self.assertEqual(p['service_id'], self.lead['id'])
        self.assertEqual([x['revision'] for x in p['revisions']], [1, 2])
        self.assertEqual([p[k] for k in ('subtotal_cents', 'gst_cents', 'qst_cents', 'total_cents')], [48599, 2430, 4848, 55877])
        self.assertFalse(p['can_send']); self.assertIsNone(p['message_preview'])
        self.assertEqual(p['customer_email'], delivery.CUSTOMER_EMAIL)
        self.assertEqual(p['recipient'], delivery.RECIPIENT)
        for key in ('acceptance', 'booking', 'invoice', 'deposit', 'payment', 'gmail_mailbox'):
            self.assertFalse(p['gates'][key])
        self.assertEqual(self.client.get(self.base + '/quickbooks-delivery').json()['snapshot_hash'], p['snapshot_hash'])
        self.assertEqual(self.f.count('quotes'), before)
        self.assertEqual(self.f.count('jobs'), 0)

    def test_default_off_and_enabled_flag_without_owner_approval_cannot_send(self):
        with patch('app.sandbox_estimate_send.requests.post') as post:
            self.assertEqual(self.client.post(self.base + '/sandbox-send', json={'confirm': True}).status_code, 403)
            os.environ['CROWNOPS_QBO_TEST_SEND_ESTIMATE'] = '185'
            p = self.review()
            self.assertEqual(self.client.post(self.base + '/sandbox-send', json={'confirm': True, 'review_hash': p['review_hash'], 'recipient': delivery.RECIPIENT}).status_code, 409)
            post.assert_not_called()

    def test_persisted_customer_and_financial_mismatch_block(self):
        self.remote['TotalAmt'] = 559.77
        self.assertEqual(self.client.get(self.base + '/sandbox-send-preflight').status_code, 409)
        self.remote['TotalAmt'] = 558.77
        with transaction(self.path) as db:
            db.execute("UPDATE external_records SET external_id='11' WHERE kind='Customer'")
        self.assertEqual(self.client.get(self.base + '/sandbox-send-preflight').status_code, 409)

    def test_newer_draft_blocks_send_without_erasing_existing_snapshot(self):
        with transaction(self.path) as db:
            q = one(db, 'SELECT * FROM quotes WHERE id=?', (scope.QUOTE,))
            q.update(id='TEST-NEWER', revision=3); insert(db, 'quotes', q)
        p = self.review(); self.assertFalse(p['can_send'])
        self.assertTrue(any('newer revision' in x for x in p['blockers']))
        self.assertEqual(p['total_cents'], 55877)

    def test_timeout_consumes_slot_even_with_valid_simulated_approval(self):
        data = self.approve_simulated_send()
        with patch('app.sandbox_estimate_send.requests.post', side_effect=TimeoutError('SIMULATED')) as post:
            self.assertEqual(self.client.post(self.base + '/sandbox-send', json=data).status_code, 502)
            self.assertEqual(self.client.post(self.base + '/sandbox-send', json=data).status_code, 409)
            self.assertEqual(post.call_count, 1)
        with closing(connect(self.path)) as db:
            self.assertEqual(settings(db, delivery.SEND_SLOT)['state'], 'Unknown')
        self.assertEqual(self.f.count('jobs'), 0)
        self.assertEqual(self.f.count('outbound_messages'), 0)

    def test_concurrent_sends_claim_only_one_durable_slot(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from unittest.mock import Mock
        data = self.approve_simulated_send()
        barrier = Barrier(2)
        real_preflight = delivery.preflight
        def simultaneous_review(*args):
            result = real_preflight(*args)
            barrier.wait(timeout=10)
            return result
        def post(*args, **kwargs):
            self.remote.update(EmailStatus='EmailSent', BillEmail={'Address': delivery.RECIPIENT})
            response = Mock(); response.json.return_value = {'Estimate': deepcopy(self.remote)}
            return response
        def call():
            try:
                return delivery.send(self.path, scope.QUOTE, data, 'owner')['state']
            except HTTPException as error:
                return error.status_code
        with patch('app.sandbox_estimate_send.preflight', side_effect=simultaneous_review), patch('app.sandbox_estimate_send.requests.post', side_effect=post) as posted:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: call(), range(2)))
            self.assertCountEqual(results, ['Sent', 409])
            self.assertEqual(posted.call_count, 1)
        self.assertEqual(self.client.post(self.base+'/sandbox-send-approval', json=self.approval_data()).status_code,409)

    def test_expiry_and_retired_approving_owner_block_send(self):
        self.approve_simulated_send()
        with patch('app.quote_options.expired_quote', return_value=True):
            p = self.review(); self.assertFalse(p['can_send'])
            self.assertTrue(any('expired' in x for x in p['blockers']))
        with transaction(self.path) as db:
            db.execute("UPDATE users SET active=0 WHERE id='owner'")
        p = delivery.preflight(self.path, scope.QUOTE)
        self.assertFalse(p['owner_approved']); self.assertFalse(p['can_send'])

    def test_simulated_send_keeps_original_snapshot_and_never_books(self):
        data = self.approve_simulated_send()
        with closing(connect(self.path)) as db:
            original = one(db, 'SELECT payload_json,remote_json FROM qbo_estimate_deliveries')['remote_json']
        def post(*args, **kwargs):
            self.assertIn('/estimate/185/send', args[0]); self.assertEqual(kwargs['params']['sendTo'], delivery.RECIPIENT)
            with closing(connect(self.path)) as db:
                self.assertEqual(settings(db, delivery.SEND_SLOT)['state'], 'Sending')
            self.remote.update(EmailStatus='EmailSent', BillEmail={'Address': delivery.RECIPIENT})
            from unittest.mock import Mock
            result = Mock(); result.json.return_value = {'Estimate': deepcopy(self.remote)}; return result
        with patch('app.sandbox_estimate_send.requests.post', side_effect=post) as posted:
            self.assertEqual(self.client.post(self.base + '/sandbox-send', json=data).status_code, 200)
            self.assertEqual(self.client.post(self.base + '/sandbox-send', json=data).status_code, 409)
            self.assertEqual(posted.call_count, 1)
        self.assertFalse(self.review()['can_send'])
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db, 'SELECT remote_json FROM qbo_estimate_deliveries')['remote_json'], original)
        for table in ('jobs', 'outbound_messages', 'payments'):
            self.assertEqual(self.f.count(table), 0)

    def test_manual_accepted_status_cannot_book_or_prepare_deposit(self):
        self.remote['TxnStatus'] = 'Accepted'
        p = self.review(); self.assertFalse(p['customer_click_verified']); self.assertFalse(p['can_send'])
        for endpoint in ('/quickbooks-delivery/sync', '/accept', '/booking-workflow/sync-acceptance'):
            self.assertEqual(self.client.post(self.base + endpoint, json={}).status_code, 409)
        self.assertEqual(self.client.post('/api/jobs', json={}).status_code, 409)
        self.assertEqual(self.client.post('/api/payments', json={}).status_code, 409)
        self.assertEqual(self.f.count('jobs'), 0); self.assertEqual(self.f.count('outbound_messages'), 0)

    def test_mailbox_routes_and_direct_helpers_block_before_network_or_writes(self):
        from app.gmail_sync import _api, sync_gmail_readonly
        from app.automatic_intake import run_intake
        from app.providers import provider_token
        with patch('requests.get') as get, patch('requests.post') as post:
            for endpoint, method in [('/api/gmail/test-readonly', 'get'), ('/api/gmail/sync-readonly', 'post'),
                                     ('/api/gmail/sync-v8-test', 'post'), ('/api/intake/sync', 'post')]:
                self.assertEqual(getattr(self.client, method)(endpoint).status_code, 403)
            for call in (lambda: _api('fake', 'messages'), lambda: sync_gmail_readonly(self.path, 'fake', 'owner', None, fluid=True),
                         lambda: run_intake(self.path, None, force=True), lambda: provider_token(self.path, 'gmail', False)):
                with self.assertRaises(HTTPException): call()
            get.assert_not_called(); post.assert_not_called()
        self.assertEqual(self.f.count('integration_events'), 0)
        with closing(connect(self.path)) as db:
            self.assertIsNone(settings(db, 'intake_sync_state'))

    def test_staging_identifiers_fail_closed_but_production_and_staff_ai_not_targeted(self):
        for key in ('RAILWAY_SERVICE_ID', 'RAILWAY_PUBLIC_DOMAIN', 'CROWNOPS_PUBLIC_URL', 'CROWNOPS_DB'):
            value = {'RAILWAY_SERVICE_ID': scope.SERVICE, 'RAILWAY_PUBLIC_DOMAIN': scope.HOST,
                     'CROWNOPS_PUBLIC_URL': 'https://' + scope.HOST, 'CROWNOPS_DB': scope.DATABASE}[key]
            with patch.dict(os.environ, {key: value}, clear=True):
                self.assertTrue(scope.isolated())
                with self.assertRaises(HTTPException): scope.deny_mailbox()
        for sid in ('production-service', '7f305475-540a-49ff-a380-18592136605b'):
            with patch.dict(os.environ, {'CROWNOPS_ENV': 'production', 'RAILWAY_SERVICE_ID': sid}, clear=True):
                self.assertFalse(scope.isolated()); scope.deny_mailbox(); scope.deny_side_effects()

    def test_retired_create_does_not_recreate_if_attempt_missing(self):
        with transaction(self.path) as db:
            db.execute("DELETE FROM settings WHERE key='sandbox_estimate_test_attempt_v1'")
        with patch('app.sandbox_estimate_test._post') as post:
            self.assertEqual(self.client.post(self.base + '/quickbooks-delivery/prepare', json={'confirm': True}).status_code, 409)
            post.assert_not_called()

    def test_data_review_preserves_and_flags_unproven_correspondence_without_exposing_it(self):
        with transaction(self.path) as db:
            insert(db, 'integration_events', dict(id='TEST-EVENT', event_key='gmail:TEST', event_type='gmail_quote', object_id='TEST',
                   payload_json=dumps({'body': 'Private test body', 'parsed': {'email': 'somebody@invalid.test'}}), status='Needs review', created_at=now()))
        result = self.client.get('/api/quote-staging/data-review')
        self.assertEqual(result.status_code, 200); self.assertEqual(result.json()['events_not_proven_fictional'], 1)
        self.assertNotIn('Private test body', result.text); self.assertNotIn('somebody', result.text)
        self.assertEqual(self.f.count('integration_events'), 1)

    def test_signed_event_deduplication_and_failed_readback_recovery_never_accept(self):
        raw = dumps([{'id': 'evt1', 'type': 'qbo.estimate.updated.v1', 'time': '2026-10-26T12:00:00Z',
                     'intuitaccountid': scope.REALM, 'intuitentityid': '185'}]).encode()
        secret = 'SIMULATED verifier'
        signature = base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()
        with patch.dict(os.environ, {'QBO_SANDBOX_WEBHOOK_VERIFIER_TOKEN': secret, 'CROWNOPS_QBO_TEST_WEBHOOK_CAPTURE': '1'}):
            with self.assertRaises(HTTPException): events.capture(self.path, raw, 'wrong')
            self.assertEqual(events.capture(self.path, raw, signature)['duplicates'], 0)
            self.assertEqual(events.capture(self.path, raw, signature)['duplicates'], 1)
            with patch('app.sandbox_estimate_send.qbo_read', side_effect=TimeoutError('SIMULATED')):
                self.assertEqual(events.reconcile(self.path)['failed'], 1)
            self.remote['TxnStatus'] = 'Accepted'
            self.assertEqual(events.reconcile(self.path)['checked'], 1)
            self.assertEqual(events.reconcile(self.path)['checked'], 0)
        for table in ('jobs', 'outbound_messages', 'payments'):
            self.assertEqual(self.f.count(table), 0)
        with closing(connect(self.path)) as db:
            self.assertEqual(one(db, 'SELECT status FROM quotes WHERE id=?', (scope.QUOTE,))['status'], 'Draft')

    def test_preflight_roles_and_wrong_target_blocked(self):
        self.client.cookies.set('crownops_session', 'crew-token')
        self.assertEqual(self.client.get(self.base + '/sandbox-send-preflight').status_code, 403)
        self.client.cookies.set('crownops_session', 'sales-token')
        self.assertEqual(self.client.get(self.base + '/sandbox-send-preflight').status_code, 200)
        self.client.headers['x-csrf-token'] = 'sales-csrf'
        self.assertEqual(self.client.post(self.base + '/sandbox-send', json={}).status_code, 403)
        with patch.dict(os.environ, {'QBO_ENV': 'production'}):
            self.assertEqual(self.client.get(self.base + '/sandbox-send-preflight').status_code, 503)
