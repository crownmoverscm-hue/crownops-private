"""Readback and separately gated delivery of the EXISTING Canadian test estimate.

Never creates an entity. No route grants approval. Provider uncertainty consumes
the durable send slot, which has no reset/retry API. All defaults are disabled.
"""
from contextlib import closing
from decimal import Decimal
import json
import os
import requests
from fastapi import Depends, HTTPException
from .store import connect, transaction, one, rows, dumps, now, audit
from .platform_common import settings, put_setting, require_role, SALES
from . import quote_staging as scope
from .providers import _qbo_connection, qbo_read
from .estimate_delivery import digest, validate_remote, source, review_hash

RECIPIENT = 'jonathanprobonas15@gmail.com'
CUSTOMER_EMAIL = 'testonly.fictional@example.com'
SEND_SLOT = 'sandbox_estimate_185_send_attempt_v1'
APPROVAL = 'sandbox_estimate_185_send_approval_v1'
OFF = ('CROWNOPS_ALLOW_EXTERNAL', 'CROWNOPS_ALLOW_QBO_SANDBOX_WRITES',
       'CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES', 'CROWNOPS_ALLOW_QBO_ESTIMATE_DELIVERY',
       'CROWNOPS_ALLOW_QBO_INVOICE_DELIVERY', 'CROWNOPS_ALLOW_QBO_RECEIPT_SYNC',
       'CROWNOPS_GMAIL_QBO_SANDBOX_WORKFLOW', 'CROWNOPS_RUN_WORKER')


def _read_json(value):
    try:
        return json.loads(value or '{}')
    except (ValueError, TypeError):
        raise HTTPException(409, 'Stored estimate evidence is unreadable. Reconcile; never recreate it.')


def preflight(path, qid):
    scope.require_isolated()
    if qid != scope.QUOTE:
        raise HTTPException(404, 'Only the existing Estimate 185 has a send preflight.')
    with closing(connect(path)) as db:
        q = one(db, 'SELECT * FROM quotes WHERE id=?', (qid,))
        row = one(db, 'SELECT * FROM qbo_estimate_deliveries WHERE quote_id=?', (qid,))
        attempt = settings(db, 'sandbox_estimate_test_attempt_v1', {}) or {}
        send_attempt = settings(db, SEND_SLOT, {}) or {}
        approval = settings(db, APPROVAL, {}) or {}
        if not q or not row or str(row['external_id']) != scope.ESTIMATE:
            raise HTTPException(409, 'Existing quote / Estimate 185 mapping is missing. Reconcile; do not CREATE.')
        lead = one(db, 'SELECT * FROM leads WHERE id=?', (q['lead_id'],))
        history = rows(db, 'SELECT id,lead_id,revision,status FROM quotes WHERE lead_id=? ORDER BY revision', (q['lead_id'],))
        clients = rows(db, 'SELECT client_id FROM client_account_leads WHERE lead_id=?', (q['lead_id'],))
        customer_map = one(db, "SELECT external_id FROM external_records WHERE provider='quickbooks' AND kind='Customer' AND object_id=? AND revision=0", (q['lead_id'],))
        estimate_map = one(db, "SELECT external_id,revision FROM external_records WHERE provider='quickbooks' AND kind='Estimate' AND object_id=?", (qid,))
        unresolved = rows(db, "SELECT id,status FROM provider_operations WHERE object_id IN (SELECT id FROM quotes WHERE lead_id=?) AND status NOT IN ('Succeeded','Success','Done','Complete','Completed','Synced')", (q['lead_id'],))
        try:
            _, _, _, current_source = source(db, qid)
            source_error = '' if current_source == row['source_hash'] else 'Quote scope differs from the synced snapshot.'
        except HTTPException as error:
            source_error = str(error.detail)
    if (not lead or q['revision'] != 2 or row['environment'] != 'sandbox' or row['realm_id'] != scope.REALM
            or attempt.get('quote_id') != qid or str(attempt.get('external_id')) != scope.ESTIMATE
            or not estimate_map or str(estimate_map['external_id']) != scope.ESTIMATE or estimate_map['revision'] != 2):
        raise HTTPException(409, 'Stored sandbox / quote revision / attempt linkage differs. Reconcile without recreating.')
    payload, saved = _read_json(row['payload_json']), _read_json(row['remote_json'])
    customer_id = str(payload.get('CustomerRef', {}).get('value') or '')
    if not customer_id.isdigit() or not customer_map or customer_map['external_id'] != customer_id:
        raise HTTPException(409, 'Persisted QuickBooks Customer ID linkage is missing or inconsistent.')
    _, realm, host = _qbo_connection(path)
    if realm != scope.REALM or host != 'https://sandbox-quickbooks.api.intuit.com':
        raise HTTPException(409, 'Wrong QuickBooks host or realm. No send permitted.')
    company = qbo_read(path, 'companyinfo/' + realm).get('CompanyInfo', {})
    if company.get('Country') != 'CA' or company.get('CompanyName') != 'Sandbox Company CA 5634':
        raise HTTPException(409, 'The exact Canadian sandbox identity could not be read back.')
    customer = qbo_read(path, 'customer/' + customer_id).get('Customer', {})
    if str(customer.get('Id')) != customer_id or customer.get('PrimaryEmailAddr', {}).get('Address', '').lower() != CUSTOMER_EMAIL:
        raise HTTPException(409, 'The existing fictional customer identity changed. Reconcile first.')
    remote = qbo_read(path, 'estimate/' + scope.ESTIMATE).get('Estimate', {})
    # Compare against the immutable original, not a recomputed quote range.
    total, tax = validate_remote(row, saved, require_quebec=True)
    checked_row = dict(row)
    bill_email = remote.get('BillEmail', {}).get('Address', '')
    if send_attempt and bill_email.lower() == RECIPIENT:
        checked_row['recipient'] = RECIPIENT  # The explicitly disclosed sendTo side effect only.
    validate_remote(checked_row, remote, require_quebec=True)
    if total != 55877 or tax != 7278 or [round(Decimal(str(x['Amount']))*100) for x in payload.get('Line', []) if x.get('DetailType') == 'SalesItemLineDetail'] != [30000, 15000, 3599]:
        raise HTTPException(409, 'Estimate 185 must preserve the exact CAD 558.77 financial snapshot.')
    if (remote.get('TotalAmt') != saved.get('TotalAmt') or remote.get('TxnTaxDetail') != saved.get('TxnTaxDetail')):
        raise HTTPException(409, 'Remote financial/tax snapshot changed. Stop and reconcile.')
    snapshot_hash = digest({'payload': payload, 'taxes': saved.get('TxnTaxDetail'), 'total': saved.get('TotalAmt'), 'source': row['source_hash']})
    review = digest({'snapshot': snapshot_hash, 'provider': review_hash(remote), 'to': RECIPIENT, 'realm': realm,
                     'estimate': scope.ESTIMATE, 'delivery_evidence': approval.get('delivery_evidence') or {}})
    blockers = ['Email sending requires separate owner approval.']
    if source_error:
        blockers.append(source_error)
    if history[-1]['id'] != qid:
        blockers.append('A newer revision exists; the synced revision cannot be used for a valid booking or send.')
    if not any(x['id'] == 'QUOTE-8a7b31fbc00e4302' and x['revision'] == 1 for x in history):
        blockers.append('Reported r1/r2 logical service parent is not verified.')
    if unresolved or attempt.get('state') != 'Ready' or row['state'] != 'Ready':
        blockers.append('Existing provider operation is unresolved or no longer Ready.')
    if send_attempt:
        blockers.append('A send attempt already exists (' + str(send_attempt.get('state')) + '); never retry automatically.')
    if row['sent_at'] or remote.get('EmailStatus') not in (None, 'NotSet') or remote.get('TxnStatus') != 'Pending':
        blockers.append('Estimate is already sent or its status changed.')
    if any(os.getenv(k, '0') != '0' for k in OFF):
        blockers.append('Broad external/write/delivery/worker gates must all remain OFF.')
    cc = {k: remote[k] for k in ('BillEmailCc', 'BillEmailBcc') if remote.get(k)}
    # The API contract does not provide a full rendered email/header preview.
    evidence = approval.get('delivery_evidence') or {}
    evidence_ok = (evidence.get('realm_id') == realm and evidence.get('no_cc_bcc_verified') is True
                   and bool(evidence.get('sender')) and bool(evidence.get('reply_to'))
                   and bool(evidence.get('evidence_reference')) and not cc)
    if not evidence_ok:
        blockers.append('Sender, Reply-To and absence of provider-default CC/BCC require independent sandbox evidence.')
    owner_approved = (approval.get('owner_approved') is True and bool(approval.get('approved_by'))
                      and approval.get('review_hash') == review and approval.get('recipient') == RECIPIENT
                      and str(approval.get('estimate_id')) == scope.ESTIMATE)
    if owner_approved:
        blockers.remove('Email sending requires separate owner approval.')
    configured = os.getenv('CROWNOPS_QBO_TEST_SEND_ESTIMATE', '') == scope.ESTIMATE
    return {'test_only': True, 'existing_estimate_only': True, 'state': row['state'],
            'company_name': company['CompanyName'], 'country': 'CA', 'realm_id': realm, 'environment': 'sandbox',
            'quote_id': qid, 'revision': q['revision'], 'service_id': q['lead_id'], 'client_ids': [x['client_id'] for x in clients],
            'revisions': history, 'customer_id': customer_id, 'customer_name': customer.get('DisplayName', ''),
            'customer_email': customer['PrimaryEmailAddr']['Address'], 'bill_email': bill_email, 'external_id': scope.ESTIMATE,
            'subtotal_cents': 48599, 'gst_cents': 2430, 'qst_cents': 4848, 'total_cents': total,
            'snapshot_hash': snapshot_hash, 'snapshot_immutable': True, 'source_matches_snapshot': not source_error,
            'lines': [{'description': x.get('Description', ''), 'amount_cents': round(Decimal(str(x.get('Amount', 0)))*100)} for x in payload['Line']],
            'memo': payload.get('CustomerMemo', {}).get('value', ''), 'expiry': payload.get('ExpirationDate'),
            'recipient': RECIPIENT, 'send_to_warning': 'Intuit sendTo can update Estimate.BillEmail.Address; it does not change Customer.PrimaryEmailAddr.',
            'cc_bcc': {'requested': [], 'estimate_fields': cc, 'provider_defaults_verified_absent': evidence_ok},
            'sender': evidence.get('sender') if evidence_ok else 'Unknown — QuickBooks controls the sender; not CrownOps Gmail.',
            'reply_to': evidence.get('reply_to') if evidence_ok else 'Unknown — verify sandbox company customer-facing email and actual headers.',
            'message_preview': None, 'message_preview_note': 'Saved estimate content only. API does not expose an exact rendered message/header preview here.',
            'operation': 'POST https://sandbox-quickbooks.api.intuit.com/v3/company/' + realm + '/estimate/185/send?sendTo=' + RECIPIENT,
            'provider_email_status': remote.get('EmailStatus'), 'provider_status': remote.get('TxnStatus'),
            'send_configured': configured, 'owner_approved': owner_approved, 'can_send': configured and not blockers,
            'send_attempt': send_attempt, 'blockers': blockers, 'review_hash': review,
            'approval_hash': digest(approval),
            'gates': {'create_customer': False, 'create_estimate': False, 'acceptance': False, 'booking': False,
                      'invoice': False, 'deposit': False, 'payment': False, 'gmail_mailbox': False,
                      'broad_flags': {k: os.getenv(k, '0') == '1' for k in OFF}},
            'customer_click_verified': False, 'inbox_delivery_verified': False,
            'duplicate_protection': 'One durable send slot claimed before provider POST; Sending/Unknown/Sent cannot be reset or retried via API.'}


def send(path, qid, data, actor):
    scope.require_isolated()
    if os.getenv('CROWNOPS_QBO_TEST_SEND_ESTIMATE', '') != scope.ESTIMATE:
        raise HTTPException(403, 'SEND disabled. A separate owner-approved test release is required; no email requested.')
    p = preflight(path, qid)
    if not p['can_send'] or data.get('confirm') is not True or data.get('review_hash') != p['review_hash'] or data.get('recipient') != RECIPIENT:
        raise HTTPException(409, 'Send preflight is blocked or changed. No email requested.')
    with transaction(path) as db:
        if settings(db, SEND_SLOT):
            raise HTTPException(409, 'Send already claimed; reconcile instead of retrying.')
        approval = settings(db, APPROVAL, {}) or {}
        row = one(db, 'SELECT * FROM qbo_estimate_deliveries WHERE quote_id=?', (qid,))
        latest = one(db, 'SELECT id FROM quotes WHERE lead_id=(SELECT lead_id FROM quotes WHERE id=?) ORDER BY revision DESC LIMIT 1', (qid,))
        if (digest(approval) != p['approval_hash'] or approval.get('review_hash') != p['review_hash'] or not approval.get('owner_approved')
                or not row or row['state'] != 'Ready' or not latest or latest['id'] != qid or source(db, qid)[3] != row['source_hash']):
            raise HTTPException(409, 'Approval or source changed during review. No send claimed.')
        put_setting(db, SEND_SLOT, {'state': 'Sending', 'quote_id': qid, 'estimate_id': scope.ESTIMATE, 'recipient': RECIPIENT,
                                  'review_hash': p['review_hash'], 'actor': actor, 'at': now()})
        audit(db, actor, 'Claimed one separately approved sandbox email; no acceptance authorized', 'quotes', qid)
    try:
        token, realm, host = _qbo_connection(path)
        scope.require_isolated()
        if realm != scope.REALM or host != 'https://sandbox-quickbooks.api.intuit.com':
            raise ValueError('Provider identity changed')
        response = requests.post(host + '/v3/company/' + realm + '/estimate/185/send',
                                 params={'sendTo': RECIPIENT, 'minorversion': os.getenv('QBO_MINOR_VERSION', '75')}, data=b'',
                                 headers={'Authorization': 'Bearer ' + token['access_token'], 'Accept': 'application/json',
                                          'Content-Type': 'application/octet-stream'}, timeout=30)
        response.raise_for_status()
        remote = response.json()['Estimate']
        if str(remote.get('Id')) != scope.ESTIMATE or remote.get('EmailStatus') != 'EmailSent':
            raise ValueError('Unconfirmed send')
        # Readback is mandatory; keep the original saved payload/remote snapshot untouched.
        remote = qbo_read(path, 'estimate/185')['Estimate']
        checked = dict(row)
        checked['recipient'] = RECIPIENT
        validate_remote(checked, remote, require_quebec=True)
        if remote.get('TxnStatus') != 'Pending' or remote.get('EmailStatus') != 'EmailSent':
            raise ValueError('Post-send readback not verified')
        state = 'Sent'
    except Exception:
        state = 'Unknown'
    with transaction(path) as db:
        attempt = settings(db, SEND_SLOT, {})
        put_setting(db, SEND_SLOT, {**attempt, 'state': state, 'checked_at': now()})
        audit(db, actor, 'Sandbox email result: ' + state + '; inbox receipt unverified', 'quotes', qid)
    if state == 'Unknown':
        raise HTTPException(502, 'Email completion is unknown. The send slot is consumed; reconcile without resending.')
    return {'state': state, 'inbox_delivery_verified': False, 'customer_click_verified': False, 'booking_created': False}


def register(app, path, auth):
    @app.get('/api/quotes/{qid}/sandbox-send-preflight')
    def preview(qid: str, u=Depends(auth)):
        require_role(u, SALES)
        return preflight(path, qid)

    @app.post('/api/quotes/{qid}/sandbox-send')
    def send_route(qid: str, data: dict, u=Depends(auth)):
        require_role(u, {'owner'})
        return send(path, qid, data, u['id'])
