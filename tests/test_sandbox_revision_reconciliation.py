"""Disposable copies and mocked QuickBooks only; never touches the real r3."""
from contextlib import closing
from copy import deepcopy
import json
import os
import sqlite3
import unittest
from unittest.mock import patch
from app.store import connect, transaction, one, rows, insert, audit, dumps, now
from app.platform_common import settings
from app import sandbox_revision_reconciliation as recon
from app import sandbox_estimate_send as delivery
from app import quote_staging as scope
import test_quote_staging_release as staging_fixture


class RevisionReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.f = staging_fixture.QuoteStagingReleaseTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.path, self.client = self.f.path, self.f.client
        self.base = self.f.base + '/sandbox-revision-reconciliation'
        with transaction(self.path) as db:
            q = one(db, 'SELECT * FROM quotes WHERE id=?', (scope.QUOTE,))
            q.update(id=recon.R3, revision=3, created_at=now())
            inp = json.loads(q['inputs_json']); inp['billing']['email'] = delivery.RECIPIENT
            inp.update(draft_key='TEST r3 save', draft_request_hash='TEST r3 request')
            q['inputs_json'] = dumps(inp)
            insert(db, 'quotes', q)
            audit(db, q['created_by'], 'CRM quote revision created', 'quotes', recon.R3, {'revision': 3})
        self.customer = {'Id': '70', 'DisplayName': 'TEST Customer', 'PrimaryEmailAddr': {'Address': delivery.CUSTOMER_EMAIL}}
        original_read = self.f.read
        self.read = patch('app.sandbox_estimate_send.qbo_read', side_effect=lambda path, resource: {'Customer': deepcopy(self.customer)} if resource == 'customer/70' else original_read(path, resource))
        self.read.start(); self.addCleanup(self.read.stop)

    def preview(self):
        result = self.client.get(self.base); self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def data(self):
        p = self.preview()
        return {'confirm_preserve_history': True, 'retained_quote_id': scope.QUOTE, 'withdrawn_quote_id': recon.R3,
                'estimate_id': '185', 'recipient': delivery.RECIPIENT, 'review_hash': p['review_hash'],
                'reason': 'TEST owner intentionally selected a test inbox in r3; withdraw billing change and keep the separate delivery recipient.'}

    def apply(self, data=None):
        r = self.client.post(self.base, json=data or self.data()); self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def preserved(self):
        with closing(connect(self.path)) as db:
            return {t: rows(db, 'SELECT * FROM '+t+' ORDER BY rowid') for t in ('quotes','leads','client_accounts','external_records','qbo_estimate_deliveries')}

    def test_readonly_preview_explains_exact_change_and_grant_separation(self):
        before = self.preserved(); p = self.preview()
        self.assertTrue(p['can_record']); self.assertFalse(p['recorded'])
        self.assertEqual((p['retained_quote_id'],p['withdrawn_quote_id'],p['total_cents']), (scope.QUOTE,recon.R3,55877))
        self.assertEqual(p['original_bill_email'], delivery.CUSTOMER_EMAIL)
        self.assertEqual(p['r3_bill_email'], delivery.RECIPIENT)
        self.assertEqual(set(p['family_hashes']), {recon.R1,scope.QUOTE,recon.R3})
        self.assertFalse(self.f.review()['approval_eligible']); self.assertFalse(self.f.review()['can_send'])
        self.assertEqual(self.preserved(), before)
        self.assertEqual(self.f.f.count('sandbox_revision_reconciliations'), 0)

    def test_explicit_record_is_idempotent_immutable_and_preserves_all_history(self):
        before = self.preserved()
        with closing(connect(self.path)) as db:
            audits_before = rows(db, 'SELECT * FROM audit ORDER BY id')
        with patch('requests.post') as post:
            data = self.data(); recorded = self.apply(data); duplicate = self.apply(data)
            self.assertFalse(recorded['email_approved']); self.assertFalse(recorded['email_sent'])
            self.assertTrue(duplicate['duplicate']); post.assert_not_called()
        self.assertEqual(self.preserved(), before)
        with closing(connect(self.path)) as db:
            ledger = one(db, 'SELECT * FROM sandbox_revision_reconciliations')
            decision = json.loads(ledger['decision_json'])
            self.assertEqual(decision['preserved_quote_ids'], [recon.R1,scope.QUOTE,recon.R3])
            self.assertEqual((decision['owner_id'],decision['estimate_id'],decision['customer_id']), ('owner','185','70'))
            self.assertEqual(decision['reason'],data['reason'])
            self.assertTrue(decision['recorded_at']); self.assertTrue(decision['snapshot_hash'])
            after = rows(db, 'SELECT * FROM audit ORDER BY id')
            self.assertEqual(after[:-1], audits_before); self.assertEqual(after[-1]['id'],recorded['audit_id'])
            self.assertEqual(json.loads(after[-1]['details_json']), decision)
            self.assertFalse(settings(db, delivery.APPROVAL)); self.assertFalse(settings(db,delivery.SEND_SLOT))
        os.environ['CROWNOPS_QBO_TEST_SEND_ESTIMATE']='185'
        p=self.f.review(); self.assertTrue(p['reconciliation']['valid']); self.assertTrue(p['approval_eligible'])
        self.assertFalse(p['owner_approved']); self.assertFalse(p['can_send'])
        for table in ('jobs','outbound_messages','payments'):
            self.assertEqual(self.f.f.count(table),0)

    def test_material_business_differences_cannot_use_email_only_exception(self):
        with closing(connect(self.path)) as db:
            original = one(db, 'SELECT * FROM quotes WHERE id=?',(recon.R3,))
        changes=[('inputs_json','hourly_rate',200),('inputs_json','move_date','2026-11-16'),
                 ('inputs_json','payment_terms','Changed terms'),('inputs_json','client_note','Changed scope'),
                 ('inputs_json','hours_low',5),('result_json','low_cents',99999),
                 ('result_json','gst_cents',1),('lead_snapshot_json','inventory','Different inventory'),
                 ('lead_snapshot_json','pickup','Different address')]
        for column,key,value in changes:
            with self.subTest(field=key):
                payload=json.loads(original[column]); payload[key]=value
                with transaction(self.path) as db:
                    db.execute('UPDATE quotes SET '+column+'=? WHERE id=?',(dumps(payload),recon.R3))
                p=self.preview(); self.assertFalse(p['can_record']); self.assertFalse(p['eligible_difference'])
                self.assertEqual(self.client.post(self.base,json=self.data()).status_code,409)
                with transaction(self.path) as db:
                    db.execute('UPDATE quotes SET '+column+'=? WHERE id=?',(original[column],recon.R3))
        self.assertEqual(self.f.f.count('sandbox_revision_reconciliations'),0)

    def test_master_owner_csrf_and_exact_review_required(self):
        data=self.data()
        for key,value in [('retained_quote_id',recon.R3),('withdrawn_quote_id',scope.QUOTE),('estimate_id','186'),
                          ('recipient','someone@example.invalid'),('review_hash','forged'),('confirm_preserve_history',False)]:
            self.assertEqual(self.client.post(self.base,json={**data,key:value}).status_code,409)
        self.assertEqual(self.client.post(self.base,json={**data,'reason':''}).status_code,422)
        self.client.headers['x-csrf-token']='forged'
        self.assertEqual(self.client.post(self.base,json=data).status_code,403)
        self.client.headers['x-csrf-token']='owner-csrf'
        from app.main import sha
        for role in ('manager','other-owner'):
            with transaction(self.path) as db:
                insert(db,'users',dict(id=role,email=role+'@example.invalid',name=role,role='owner' if role=='other-owner' else role,password_hash='unused',active=1,created_at=now()))
                insert(db,'sessions',dict(token_hash=sha(role+'-token'),user_id=role,csrf=role+'-csrf',expires_at='2099-01-01T00:00:00+00:00'))
        from app.platform_common import put_setting
        with transaction(self.path) as db:put_setting(db,'master_owner',{'user_id':'owner'})
        for role in ('sales','crew','manager','other-owner'):
            self.client.cookies.set('crownops_session',role+'-token');self.client.headers['x-csrf-token']=role+'-csrf'
            self.assertEqual(self.client.post(self.base,json=data).status_code,403)
        self.assertEqual(self.f.f.count('sandbox_revision_reconciliations'),0)

    def test_new_revision_or_wrong_history_never_reconciles(self):
        data=self.data()
        with transaction(self.path) as db:
            q=one(db,'SELECT * FROM quotes WHERE id=?',(recon.R3,));q.update(id='TEST-r4',revision=4);insert(db,'quotes',q)
        self.assertFalse(self.preview()['can_record'])
        self.assertEqual(self.client.post(self.base,json=data).status_code,409)
        self.assertEqual(self.client.post('/api/quotes/'+recon.R3+'/sandbox-revision-reconciliation',json=data).status_code,404)
        self.assertFalse(self.f.review()['approval_eligible'])

    def test_stale_review_and_provider_state_block_recording(self):
        data=self.data(); self.f.remote['SyncToken']='1'
        self.assertEqual(self.client.post(self.base,json=data).status_code,409)
        self.assertEqual(self.f.f.count('sandbox_revision_reconciliations'),0)
        self.f.remote['TotalAmt']=999.99
        self.assertEqual(self.client.get(self.base).status_code,409)

    def test_every_relevant_change_invalidates_existing_resolution(self):
        self.apply()
        self.customer['DisplayName']='Changed customer'
        self.assertFalse(self.f.review()['reconciliation']['valid'])
        self.assertFalse(self.f.review()['approval_eligible'])
        self.assertEqual(self.client.post(self.base,json=self.data()).status_code,409)
        self.customer['DisplayName']='TEST Customer'
        self.f.remote['SyncToken']='1'
        self.assertFalse(self.f.review()['reconciliation']['valid'])
        self.f.remote['SyncToken']='0'
        with transaction(self.path) as db:
            db.execute("UPDATE client_accounts SET phone='5555550199'")
        self.assertFalse(self.f.review()['reconciliation']['valid'])
        self.assertFalse(self.f.review()['approval_eligible'])

    def test_r4_after_reconciliation_and_material_change_during_approval_fail_closed(self):
        self.apply(); os.environ['CROWNOPS_QBO_TEST_SEND_ESTIMATE']='185'
        data=self.f.approval_data(); original=delivery.preflight
        def raced(*args):
            p=original(*args)
            with transaction(self.path) as db:
                q=one(db,'SELECT * FROM quotes WHERE id=?',(recon.R3,)); q.update(id='TEST-r4',revision=4);insert(db,'quotes',q)
            return p
        with patch('app.sandbox_estimate_send.preflight',side_effect=raced):
            self.assertEqual(self.client.post(self.f.base+'/sandbox-send-approval',json=data).status_code,409)
        self.assertFalse(self.f.review()['approval_eligible'])
        with closing(connect(self.path)) as db:self.assertFalse(settings(db,delivery.APPROVAL))

    def test_separate_email_approval_and_uncertain_single_send_remain_protected(self):
        self.apply(); data=self.f.approve_simulated_send()
        with patch('app.sandbox_estimate_send.requests.post',side_effect=TimeoutError('SIMULATED')) as post:
            self.assertEqual(self.client.post(self.f.base+'/sandbox-send',json=data).status_code,502)
            self.assertEqual(self.client.post(self.f.base+'/sandbox-send',json=data).status_code,409)
            self.assertEqual(post.call_count,1)
        for route in ('/accept','/quickbooks-delivery/sync','/booking-workflow/sync-acceptance'):
            self.assertEqual(self.client.post(self.f.base+route,json={}).status_code,409)
        for table in ('jobs','outbound_messages','payments'):
            self.assertEqual(self.f.f.count(table),0)

    def test_append_only_ledger_and_audit_cannot_be_overwritten_or_deleted(self):
        result=self.apply()
        for sql,args in [("UPDATE sandbox_revision_reconciliations SET reason='forged'",()),
                         ('DELETE FROM sandbox_revision_reconciliations',()),
                         ("UPDATE audit SET details_json='{}' WHERE id=?",(result['audit_id'],)),
                         ('DELETE FROM audit WHERE id=?',(result['audit_id'],))]:
            with self.assertRaises(sqlite3.IntegrityError):
                with transaction(self.path) as db:db.execute(sql,args)
        self.assertTrue(self.preview()['valid'])

    def test_concurrent_reconciliation_records_one_decision_and_one_audit(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        data=self.data(); original=delivery.preflight; barrier=Barrier(2)
        def together(*args):
            p=original(*args); barrier.wait(timeout=10); return p
        with patch('app.sandbox_estimate_send.preflight',side_effect=together):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:recon.record(self.path,scope.QUOTE,data,'owner'),range(2)))
        self.assertEqual(sum(bool(x['duplicate']) for x in results),1)
        self.assertEqual(self.f.f.count('sandbox_revision_reconciliations'),1)
        with closing(connect(self.path)) as db:
            self.assertEqual(len(rows(db,'SELECT id FROM audit WHERE action=?',(recon.ACTION,))),1)

    def test_material_change_to_both_revisions_cannot_match_old_provider_snapshot(self):
        with transaction(self.path) as db:
            for qid in (scope.QUOTE,recon.R3):
                q=one(db,'SELECT * FROM quotes WHERE id=?',(qid,)); inp=json.loads(q['inputs_json'])
                inp['hourly_rate']=999; db.execute('UPDATE quotes SET inputs_json=? WHERE id=?',(dumps(inp),qid))
        p=self.preview(); self.assertTrue(p['eligible_difference']); self.assertFalse(p['can_record'])
        self.assertEqual(self.client.post(self.base,json=self.data()).status_code,409)

    def test_state_change_between_preflight_and_record_or_send_never_writes(self):
        data=self.data(); original=delivery.preflight
        def changed_before_record(*args):
            p=original(*args)
            with transaction(self.path) as db:db.execute("UPDATE client_accounts SET phone='5555550999'")
            return p
        with patch('app.sandbox_estimate_send.preflight',side_effect=changed_before_record):
            self.assertEqual(self.client.post(self.base,json=data).status_code,409)
        self.assertEqual(self.f.f.count('sandbox_revision_reconciliations'),0)
        # A new explicit preview can reconcile this contact state; it is then bound.
        self.apply(); send_data=self.f.approve_simulated_send()
        def changed_before_send(*args):
            p=original(*args)
            with transaction(self.path) as db:db.execute("UPDATE client_accounts SET phone='5555550888'")
            return p
        with patch('app.sandbox_estimate_send.preflight',side_effect=changed_before_send),patch('app.sandbox_estimate_send.requests.post') as post:
            self.assertEqual(self.client.post(self.f.base+'/sandbox-send',json=send_data).status_code,409)
            post.assert_not_called()
        with closing(connect(self.path)) as db:self.assertFalse(settings(db,delivery.SEND_SLOT))

    def test_reconciled_concurrent_send_still_claims_only_once(self):
        self.apply()
        self.f.test_concurrent_sends_claim_only_one_durable_slot()

    def test_missing_r3_cannot_fall_back_to_unreconciled_latest_revision_rule(self):
        self.apply()
        # Simulate database damage ONLY in the disposable fixture; never a supported operation.
        with transaction(self.path) as db:db.execute('DELETE FROM quotes WHERE id=?',(recon.R3,))
        p=self.f.review()
        self.assertFalse(p['reconciliation']['valid']); self.assertFalse(p['approval_eligible']); self.assertFalse(p['can_send'])
