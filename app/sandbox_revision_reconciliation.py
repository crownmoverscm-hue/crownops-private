"""One reviewed, append-only r3 withdrawal for EXISTING sandbox Estimate 185.

No quote is edited or relinked. This is not a general stale-revision override.
GETs only propose/validate; the authenticated Master owner must explicitly POST.
"""
from contextlib import closing
from copy import deepcopy
import json
from fastapi import Depends, HTTPException
from . import quote_staging as scope
from .store import connect, transaction, one, rows, insert, audit, now
from .platform_common import settings, require_role, SALES
from .estimate_delivery import digest

R1 = 'QUOTE-8a7b31fbc00e4302'
R3 = 'QUOTE-af7136453bf64544'
KEY = 'sandbox-estimate-185-r3-withdrawal-v1'
ACTION = 'Sandbox r3 delivery reconciliation recorded'
NEWER = 'A newer revision exists; the synced revision cannot be used for a valid booking or send.'


def local_review(db):
    """Hash all persisted evidence, but allow ONLY the observed business delta."""
    q2 = one(db, 'SELECT * FROM quotes WHERE id=?', (scope.QUOTE,))
    if not q2:
        raise HTTPException(409, 'Existing r2 is missing; reconciliation cannot create it.')
    family = rows(db, 'SELECT * FROM quotes WHERE lead_id=? ORDER BY revision,id', (q2['lead_id'],))
    lead = one(db, 'SELECT * FROM leads WHERE id=?', (q2['lead_id'],))
    accounts = rows(db, 'SELECT a.* FROM client_accounts a JOIN client_account_leads l ON l.client_id=a.id WHERE l.lead_id=? ORDER BY a.id', (q2['lead_id'],))
    links = rows(db, "SELECT * FROM external_records WHERE provider='quickbooks' AND (object_id=? OR object_id IN (SELECT id FROM quotes WHERE lead_id=?)) ORDER BY kind,object_id,revision", (q2['lead_id'], q2['lead_id']))
    operations = rows(db, 'SELECT * FROM provider_operations WHERE object_id=? OR object_id IN (SELECT id FROM quotes WHERE lead_id=?) ORDER BY id', (q2['lead_id'], q2['lead_id']))
    delivery = one(db, 'SELECT * FROM qbo_estimate_deliveries WHERE quote_id=?', (scope.QUOTE,))
    evidence = {'family': family, 'lead': lead, 'accounts': accounts, 'links': links, 'operations': operations,
                'delivery_snapshot': delivery, 'create_attempt': settings(db, 'sandbox_estimate_test_attempt_v1', {})}
    blockers = []
    if any(x['status'] not in ('Succeeded', 'Success', 'Done', 'Complete', 'Completed', 'Synced') for x in operations):
        blockers.append('A related provider operation is unresolved; no reconciliation is allowed.')
    if [(q['id'], q['revision'], q['status']) for q in family] != [(R1, 1, 'Draft'), (scope.QUOTE, 2, 'Draft'), (R3, 3, 'Draft')]:
        blockers.append('Only the exact preserved r1/r2/r3 Draft history can be reconciled.')
    if not lead or lead['email'] != 'testonly.fictional@example.com' or len(accounts) != 1 or accounts[0]['email'] != 'testonly.fictional@example.com':
        blockers.append('The service and client must retain the original fictional billing identity.')
    expected = [('Customer', q2['lead_id'], '70', 0), ('Estimate', scope.QUOTE, '185', 2)]
    if [(x['kind'], x['object_id'], x['external_id'], x['revision']) for x in links] != expected:
        blockers.append('Existing Customer 70 / r2 Estimate 185 mappings must be the only related exports.')
    if not blockers:
        a, b = deepcopy(family[1]), deepcopy(family[2])
        for q in (a, b):
            for key in ('inputs_json', 'result_json', 'lead_snapshot_json'):
                q[key] = json.loads(q[key])
        from .sandbox_estimate_send import CUSTOMER_EMAIL, RECIPIENT
        if a['inputs_json'].get('billing', {}).get('email') != CUSTOMER_EMAIL or b['inputs_json'].get('billing', {}).get('email') != RECIPIENT:
            blockers.append('The exact original-to-test Bill-to email difference was not found.')
        else:
            b['inputs_json']['billing']['email'] = CUSTOMER_EMAIL
        # These are the ONLY non-business differences observed in the read-only comparison.
        for q in (a, b):
            for key in ('id', 'revision', 'created_at'):
                q.pop(key)
            for key in ('draft_key', 'draft_request_hash'):
                q['inputs_json'].pop(key, None)
            q['lead_snapshot_json'].pop('updated_at', None)
        if a != b:
            blockers.append('r2/r3 differ beyond Bill-to email and save metadata; price, scope, taxes, date and terms must be identical.')
    return {'context_hash': digest(evidence), 'eligible_difference': not blockers, 'blockers': blockers,
            'family_hashes': {q['id']: digest(q) for q in family}, 'service_id': q2['lead_id'],
            'latest_id': family[-1]['id'] if family else None}


def inspect(db, local, provider_hash):
    from .sandbox_estimate_send import require_master
    row = one(db, 'SELECT * FROM sandbox_revision_reconciliations WHERE id=?', (KEY,))
    result = {'recorded': bool(row), 'valid': False, 'historical_intact': False, 'record_hash': None}
    if not row:
        return result
    result.update(owner_id=row['owner_id'], recorded_at=row['created_at'], reason=row['reason'], audit_id=row['audit_id'])
    try:
        decision = json.loads(row['decision_json'])
        event = one(db, 'SELECT * FROM audit WHERE id=?', (row['audit_id'],))
        intact = (event and event['action'] == ACTION and event['object_id'] == R3 and event['actor'] == row['owner_id']
                  and json.loads(event['details_json']) == decision and decision['owner_id'] == row['owner_id']
                  and decision['recorded_at'] == row['created_at'] and decision['reason'] == row['reason']
                  and decision['context_hash'] == row['context_hash'] and decision['provider_hash'] == row['provider_hash']
                  and decision['id'] == KEY and decision['retained_quote_id'] == scope.QUOTE and decision['withdrawn_quote_id'] == R3)
        result.update(historical_intact=bool(intact), owner_name=decision.get('owner_name', row['owner_id']),
                      local_matches=row['context_hash'] == local['context_hash'], record_hash=digest(dict(row)))
        owner = require_master(db, row['owner_id'])
        valid = (intact and local['eligible_difference'] and row['context_hash'] == local['context_hash']
                 and row['provider_hash'] == provider_hash)
        result.update(valid=bool(valid), record_hash=digest(dict(row)), owner_name=owner['name'])
    except (HTTPException, ValueError, KeyError, TypeError):
        pass
    return result


def current_for_delivery(db, qid, provider_hash, expected_context):
    if qid != scope.QUOTE:
        return False
    local = local_review(db)
    if local['context_hash'] != expected_context:
        return False
    state = inspect(db, local, provider_hash)
    if state['recorded']:
        return state['valid']
    return local['latest_id'] == qid


def proposal(db, local, provider_hash, snapshot_hash):
    from .sandbox_estimate_send import CUSTOMER_EMAIL, RECIPIENT
    state = inspect(db, local, provider_hash)
    review = digest({'id': KEY, 'context_hash': local['context_hash'], 'provider_hash': provider_hash,
                     'snapshot_hash': snapshot_hash, 'retained': scope.QUOTE, 'withdrawn': R3,
                     'recipient': RECIPIENT, 'estimate': scope.ESTIMATE, 'realm': scope.REALM})
    return {**state, 'review_hash': review, 'eligible_difference': local['eligible_difference'],
            'difference_blockers': local['blockers'], 'context_hash': local['context_hash'],
            'family_hashes': local['family_hashes'], 'retained_quote_id': scope.QUOTE, 'withdrawn_quote_id': R3,
            'original_bill_email': CUSTOMER_EMAIL, 'r3_bill_email': RECIPIENT,
            'before': 'r3 is latest: r2 delivery is blocked.',
            'after': 'Only this reviewed r3 billing change is withdrawn from Estimate 185 delivery use; r2 remains linked. Email approval and SEND are separate.'}


def record(path, qid, data, actor):
    from .sandbox_estimate_send import preflight, require_master, SEND_SLOT, APPROVAL
    scope.require_isolated()
    if qid != scope.QUOTE:
        raise HTTPException(404, 'Reconciliation is limited to existing r2 and Estimate 185.')
    with closing(connect(path)) as db:
        require_master(db, actor)
    p = preflight(path, qid)
    proposal = p['reconciliation']
    if (data.get('confirm_preserve_history') is not True or data.get('retained_quote_id') != scope.QUOTE
            or data.get('withdrawn_quote_id') != R3 or data.get('estimate_id') != scope.ESTIMATE
            or data.get('recipient') != p['recipient'] or data.get('review_hash') != proposal['review_hash']):
        raise HTTPException(409, 'Review the exact r2/r3 withdrawal, Estimate 185 and test recipient first.')
    reason = data.get('reason')
    if not isinstance(reason, str) or not 20 <= len(reason.strip()) <= 1000:
        raise HTTPException(422, 'Record why the intentional r3 billing email change is being withdrawn from delivery use.')
    with transaction(path) as db:
        owner = require_master(db, actor)
        local = local_review(db)
        state = inspect(db, local, p['provider_hash'])
        if state['recorded']:
            if state['valid'] and local['context_hash'] == proposal['context_hash']:
                return {**state, 'duplicate': True, 'email_approved': False, 'email_sent': False}
            raise HTTPException(409, 'The recorded reconciliation is no longer valid. It cannot be replaced or reset.')
        from .sandbox_estimate_send import stored_review, source, OFF
        import os
        row = one(db, 'SELECT * FROM qbo_estimate_deliveries WHERE quote_id=?', (qid,))
        if (not p['reconciliation_can_record'] or local['context_hash'] != proposal['context_hash']
                or not local['eligible_difference'] or not row or stored_review(row) != p['stored_review']
                or source(db, qid)[3] != row['source_hash'] or settings(db, SEND_SLOT) or settings(db, APPROVAL)
                or any(os.getenv(k, '0') != '0' for k in OFF)):
            raise HTTPException(409, 'Reconciliation evidence changed or another action exists. No correction recorded.')
        decision = {'id': KEY, 'action': 'Withdraw r3 Bill-to email change from Estimate 185 delivery use only',
                    'owner_id': actor, 'owner_name': owner['name'], 'recorded_at': now(), 'reason': reason.strip(),
                    'realm_id': scope.REALM, 'estimate_id': scope.ESTIMATE, 'customer_id': '70', 'total_cents': 55877,
                    'retained_quote_id': qid, 'withdrawn_quote_id': R3, 'preserved_quote_ids': [R1, qid, R3],
                    'original_bill_email': proposal['original_bill_email'], 'withdrawn_bill_email': proposal['r3_bill_email'],
                    'test_recipient': p['recipient'], 'context_hash': local['context_hash'],
                    'family_hashes': local['family_hashes'], 'provider_hash': p['provider_hash'], 'snapshot_hash': p['snapshot_hash'],
                    'email_approved': False, 'email_sent': False, 'downstream_actions_enabled': False}
        audit(db, actor, ACTION, 'quotes', R3, decision)
        audit_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        insert(db, 'sandbox_revision_reconciliations', {'id': KEY, 'owner_id': actor, 'created_at': decision['recorded_at'],
               'reason': decision['reason'], 'context_hash': local['context_hash'], 'provider_hash': p['provider_hash'],
               'decision_json': json.dumps(decision, sort_keys=True), 'audit_id': audit_id})
    return {'recorded': True, 'duplicate': False, 'owner_id': actor, 'recorded_at': decision['recorded_at'],
            'audit_id': audit_id, 'email_approved': False, 'email_sent': False}


def register(app, path, auth):
    @app.get('/api/quotes/{qid}/sandbox-revision-reconciliation')
    def preview(qid: str, u=Depends(auth)):
        from .sandbox_estimate_send import preflight
        require_role(u, SALES)
        p = preflight(path, qid)
        return {**p['reconciliation'], 'can_record': p['reconciliation_can_record'],
                'blockers': p['reconciliation_blockers'], 'estimate_id': p['external_id'], 'total_cents': p['total_cents'],
                'recipient': p['recipient'], 'snapshot_hash': p['snapshot_hash']}

    @app.post('/api/quotes/{qid}/sandbox-revision-reconciliation')
    def apply(qid: str, data: dict, u=Depends(auth)):
        require_role(u, {'owner'})
        return record(path, qid, data, u['id'])
