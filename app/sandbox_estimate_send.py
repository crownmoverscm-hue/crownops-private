"""Readback and separately gated delivery of the EXISTING Canadian test estimate.

Never creates an entity. Explicit Master-owner approval does not send. Provider
uncertainty consumes the durable send slot, which has no reset/retry API.
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
from .account_access import master_owner_id

RECIPIENT = 'jonathanprobonas15@gmail.com'
CUSTOMER_EMAIL = 'testonly.fictional@example.com'
CUSTOMER_ID = '70'
SEND_SLOT = 'sandbox_estimate_185_send_attempt_v1'
APPROVAL = 'sandbox_estimate_185_send_approval_v1'
APPROVAL_VERSION = 2
OFF = ('CROWNOPS_ALLOW_EXTERNAL', 'CROWNOPS_ALLOW_QBO_SANDBOX_WRITES',
       'CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES', 'CROWNOPS_ALLOW_QBO_ESTIMATE_DELIVERY',
       'CROWNOPS_ALLOW_QBO_INVOICE_DELIVERY', 'CROWNOPS_ALLOW_QBO_RECEIPT_SYNC',
       'CROWNOPS_GMAIL_QBO_SANDBOX_WORKFLOW', 'CROWNOPS_RUN_WORKER')


def _read_json(value):
    try:
        return json.loads(value or '{}')
    except (ValueError, TypeError):
        raise HTTPException(409, 'Stored estimate evidence is unreadable. Reconcile; never recreate it.')


def require_master(db, actor):
    owner = one(db, "SELECT id,name FROM users WHERE id=? AND role='owner' AND active=1", (actor,))
    if not owner or master_owner_id(db) != actor:
        raise HTTPException(403, 'Only the active Master owner may approve or send this one sandbox email.')
    return owner


def stored_review(row):
    return digest({k: row[k] for k in ('quote_id','environment','realm_id','external_id','state',
                                     'payload_json','remote_json','source_hash','sent_at')})


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
        approving_owner = one(db, "SELECT id,name FROM users WHERE id=? AND role='owner' AND active=1", (approval.get('approved_by', ''),))
        if approving_owner and master_owner_id(db) != approving_owner['id']:
            approving_owner = None
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
    if customer_id != CUSTOMER_ID or not customer_map or customer_map['external_id'] != customer_id:
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
    cc = {k: remote[k] for k in ('BillEmailCc', 'BillEmailBcc') if remote.get(k)}
    company_email = company.get('CustomerCommunicationEmailAddr', {}).get('Address', '')
    from . import sandbox_revision_reconciliation as revision_review
    provider_hash = digest({'realm': realm, 'company': company, 'customer': customer,
                            'estimate': remote, 'recipient': RECIPIENT})
    with closing(connect(path)) as db:
        local = revision_review.local_review(db)
        reconciliation = revision_review.proposal(db, local, provider_hash, snapshot_hash)
    review = digest({'snapshot': snapshot_hash, 'provider': review_hash(remote), 'to': RECIPIENT, 'realm': realm,
                     'estimate': scope.ESTIMATE, 'cc_bcc': cc, 'company_contact_email': company_email,
                     'stored_review': stored_review(row), 'family_context': local['context_hash'],
                     'provider_identity': provider_hash, 'reconciliation': reconciliation['record_hash']})
    blockers = ['Email sending requires separate owner approval.']
    if source_error:
        blockers.append(source_error)
    if local['latest_id'] != qid and not reconciliation['valid']:
        blockers.append(revision_review.NEWER)
    if reconciliation['recorded'] and not reconciliation['valid']:
        blockers.append('The recorded r3 reconciliation no longer matches current evidence; delivery remains blocked.')
    from .quote_options import expired_quote
    if expired_quote(q):
        blockers.append('The synced estimate has expired; do not send or recreate it.')
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
    # The API contract does not provide a full rendered email/header preview.
    evidence = approval.get('delivery_evidence') or {}
    if cc:
        blockers.append('Estimate contains CC/BCC fields. Remove them in QuickBooks and review again; CrownOps will not change them.')
    approval_blockers = [x for x in blockers if x != 'Email sending requires separate owner approval.']
    reconciliation_blockers = local['blockers'] + [x for x in approval_blockers if x != revision_review.NEWER]
    if approval:
        reconciliation_blockers.append('An email approval record already exists; reconciliation cannot alter or grant email approval.')
    if reconciliation['recorded'] and not reconciliation['valid']:
        reconciliation_blockers.append('The recorded reconciliation is stale or invalid. It cannot be overwritten or reset.')
    owner_approved = (approval.get('version') == APPROVAL_VERSION and approval.get('owner_approved') is True and bool(approving_owner)
                      and approval.get('review_hash') == review and approval.get('recipient') == RECIPIENT
                      and str(approval.get('estimate_id')) == scope.ESTIMATE and bool(approval.get('approved_at'))
                      and evidence.get('realm_id') == realm and evidence.get('no_cc_bcc_confirmed') is True
                      and bool(evidence.get('evidence_reference')) and approval.get('ack_bill_email_change') is True
                      and approval.get('ack_unknown_delivery') is True and not cc)
    if owner_approved:
        blockers.remove('Email sending requires separate owner approval.')
    configured = os.getenv('CROWNOPS_QBO_TEST_SEND_ESTIMATE', '') == scope.ESTIMATE
    if not configured:
        blockers.append('The dedicated Estimate 185 SEND capability is disabled on this deployment.')
    from .sandbox_estimate_observation import baseline, history_and_integrity
    with closing(connect(path)) as db:
        postsend = history_and_integrity(db, local, reconciliation, approval, send_attempt, remote, company, customer, saved, snapshot_hash) if send_attempt else None
    return {'test_only': True, 'existing_estimate_only': True, 'state': send_attempt.get('state') or row['state'],
            'company_name': company['CompanyName'], 'country': 'CA', 'realm_id': realm, 'environment': 'sandbox',
            'quote_id': qid, 'revision': q['revision'], 'service_id': q['lead_id'], 'client_ids': [x['client_id'] for x in clients],
            'revisions': history, 'customer_id': customer_id, 'customer_name': customer.get('DisplayName', ''),
            'customer_email': customer['PrimaryEmailAddr']['Address'], 'bill_email': bill_email, 'external_id': scope.ESTIMATE,
            'subtotal_cents': 48599, 'gst_cents': 2430, 'qst_cents': 4848, 'total_cents': total,
            'snapshot_hash': snapshot_hash, 'snapshot_immutable': True, 'source_matches_snapshot': not source_error,
            'lines': [{'description': x.get('Description', ''), 'amount_cents': round(Decimal(str(x.get('Amount', 0)))*100)} for x in payload['Line']],
            'memo': payload.get('CustomerMemo', {}).get('value', ''), 'expiry': payload.get('ExpirationDate'),
            'recipient': RECIPIENT, 'send_to_warning': 'Intuit sendTo can update Estimate.BillEmail.Address; it does not change Customer.PrimaryEmailAddr.',
            'cc_bcc': {'requested': [], 'estimate_fields': cc, 'provider_defaults_verified_absent': False,
                       'owner_confirmed_absent': owner_approved},
            'sender': 'QuickBooks controls the sender. Exact From address/header is unverified before this test.',
            'reply_to': 'Actual Reply-To header is unverified before this test.',
            'company_contact_email': company_email or 'Not returned by CompanyInfo; this is not verified Reply-To.',
            'message_preview': None, 'message_preview_note': 'Saved estimate content only. API does not expose an exact rendered message/header preview here.',
            'operation': 'POST https://sandbox-quickbooks.api.intuit.com/v3/company/' + realm + '/estimate/185/send?sendTo=' + RECIPIENT,
            'provider_email_status': remote.get('EmailStatus'), 'provider_status': remote.get('TxnStatus'),
            'send_configured': configured, 'owner_approved': owner_approved, 'can_send': configured and not blockers,
            'approval_eligible': not approval_blockers and not owner_approved, 'approval_blockers': approval_blockers,
            'approval': {'approved_by': approval.get('approved_by'), 'approved_name': approving_owner['name'] if approving_owner else None,
                         'approved_at': approval.get('approved_at'), 'current': owner_approved,
                         'evidence_reference': evidence.get('evidence_reference', '')},
            'stored_review': stored_review(row),
            'family_context_hash': local['context_hash'], 'provider_hash': provider_hash,
            'reconciliation': reconciliation, 'reconciliation_blockers': reconciliation_blockers,
            'reconciliation_can_record': not reconciliation['recorded'] and not reconciliation_blockers,
            'send_attempt': send_attempt, 'postsend': postsend, 'provider_lifecycle_baseline': baseline(remote),
            'blockers': blockers, 'review_hash': review,
            'approval_hash': digest(approval),
            'gates': {'create_customer': False, 'create_estimate': False, 'acceptance': False, 'booking': False,
                      'invoice': False, 'deposit': False, 'payment': False, 'gmail_mailbox': False,
                      'broad_flags': {k: os.getenv(k, '0') == '1' for k in OFF}},
            'customer_click_verified': False, 'inbox_delivery_verified': False,
            'duplicate_protection': 'One durable send slot claimed before provider POST; Sending/Unknown/Sent cannot be reset or retried via API.'}


def approve(path, qid, data, actor):
    scope.require_isolated()
    with closing(connect(path)) as db:
        require_master(db, actor)
    p = preflight(path, qid)
    if (data.get('confirm') is not True or data.get('recipient') != RECIPIENT
            or data.get('estimate_id') != scope.ESTIMATE or data.get('review_hash') != p['review_hash']):
        raise HTTPException(409, 'Approval review changed or targets differ. Refresh the existing preflight.')
    reference = data.get('evidence_reference')
    if (any(data.get(k) is not True for k in ('ack_bill_email_change','ack_unknown_delivery','no_cc_bcc_confirmed'))
            or not isinstance(reference, str) or not 10 <= len(reference.strip()) <= 1000):
        raise HTTPException(422, 'Confirm the billing-email change, delivery unknowns and no CC/BCC or self-copy in the sandbox. Record where you checked those settings.')
    if p['approval_blockers']:
        raise HTTPException(409, 'Approval blocked: ' + ' '.join(p['approval_blockers']))
    with transaction(path) as db:
        owner = require_master(db, actor)
        row = one(db, 'SELECT * FROM qbo_estimate_deliveries WHERE quote_id=?', (qid,))
        from .sandbox_revision_reconciliation import current_for_delivery
        if (settings(db, SEND_SLOT) or not row or stored_review(row) != p['stored_review']
                or not current_for_delivery(db, qid, p['provider_hash'], p['family_context_hash'])
                or source(db, qid)[3] != row['source_hash']):
            raise HTTPException(409, 'Estimate changed or send already claimed. Reconcile without retrying.')
        existing = settings(db, APPROVAL, {}) or {}
        if existing.get('review_hash') == p['review_hash'] and p['owner_approved']:
            return {'approved': True, 'duplicate': True, 'approved_by': existing['approved_by'],
                    'approved_at': existing['approved_at'], 'email_sent': False}
        approval = {'version': APPROVAL_VERSION, 'owner_approved': True, 'approved_by': owner['id'], 'approved_at': now(),
                    'quote_id': qid, 'estimate_id': scope.ESTIMATE, 'realm_id': scope.REALM,
                    'recipient': RECIPIENT, 'bill_email': p['bill_email'], 'total_cents': p['total_cents'],
                    'snapshot_hash': p['snapshot_hash'], 'review_hash': p['review_hash'],
                    'ack_bill_email_change': True, 'ack_unknown_delivery': True,
                    'delivery_evidence': {'realm_id': scope.REALM, 'no_cc_bcc_confirmed': True,
                                          'evidence_reference': reference.strip()}}
        put_setting(db, APPROVAL, approval)
        audit(db, actor, 'Approved ONE Estimate 185 sandbox email; no email sent; acceptance and booking remain disabled',
              'quotes', qid, approval)
    return {'approved': True, 'approved_by': actor, 'approved_at': approval['approved_at'], 'email_sent': False}


def send(path, qid, data, actor):
    scope.require_isolated()
    with closing(connect(path)) as db:
        require_master(db, actor)
        if settings(db, SEND_SLOT):
            raise HTTPException(409, 'Send slot permanently consumed. Observe the existing attempt; never resend.')
    if os.getenv('CROWNOPS_QBO_TEST_SEND_ESTIMATE', '') != scope.ESTIMATE:
        raise HTTPException(403, 'SEND disabled. A separate owner-approved test release is required; no email requested.')
    p = preflight(path, qid)
    if not p['can_send'] or data.get('confirm') is not True or data.get('review_hash') != p['review_hash'] or data.get('recipient') != RECIPIENT:
        raise HTTPException(409, 'Send preflight is blocked or changed. No email requested.')
    with transaction(path) as db:
        if settings(db, SEND_SLOT):
            raise HTTPException(409, 'Send already claimed; reconcile instead of retrying.')
        approval = settings(db, APPROVAL, {}) or {}
        require_master(db, actor)
        approving_owner = approval.get('approved_by') == actor
        row = one(db, 'SELECT * FROM qbo_estimate_deliveries WHERE quote_id=?', (qid,))
        from .sandbox_revision_reconciliation import current_for_delivery
        if (not approving_owner or digest(approval) != p['approval_hash'] or approval.get('review_hash') != p['review_hash'] or not approval.get('owner_approved')
                or not row or stored_review(row) != p['stored_review'] or any(os.getenv(k, '0') != '0' for k in OFF)
                or row['state'] != 'Ready' or not current_for_delivery(db, qid, p['provider_hash'], p['family_context_hash'])
                or source(db, qid)[3] != row['source_hash']):
            raise HTTPException(409, 'Approval or source changed during review. No send claimed.')
        authorization = {'approval_hash': digest(approval), 'reconciliation_hash': p['reconciliation']['record_hash'],
                         'context_hash': p['family_context_hash'], 'provider_hash': p['provider_hash'],
                         'snapshot_hash': p['snapshot_hash'], 'provider_baseline': p['provider_lifecycle_baseline']}
        put_setting(db, SEND_SLOT, {'state': 'Sending', 'quote_id': qid, 'estimate_id': scope.ESTIMATE, 'recipient': RECIPIENT,
                                  'review_hash': p['review_hash'], 'actor': actor, 'at': now(), 'authorization_at_claim': authorization})
        audit(db, actor, 'Claimed one separately approved sandbox email; no acceptance authorized', 'quotes', qid,
              {'authorization_at_claim': authorization})
    try:
        from .sandbox_estimate_observation import sanitized_response, retain_exchange
        token, realm, host = _qbo_connection(path)
        scope.require_isolated()
        if realm != scope.REALM or host != 'https://sandbox-quickbooks.api.intuit.com':
            raise ValueError('Provider identity changed')
        version = os.getenv('QBO_MINOR_VERSION', '75')
        requested_at = now()
        retain_exchange(path, actor, 'SEND_REQUEST', {'operation': 'SEND', 'method': 'POST', 'resource': 'estimate/185/send',
                        'realm_id': realm, 'recipient': RECIPIENT, 'minorversion': version, 'requested_at': requested_at,
                        'body_bytes': 0, 'approval_hash': digest(approval), 'http_status': None, 'intuit_tid': None})
        response = requests.post(host + '/v3/company/' + realm + '/estimate/185/send',
                                 params={'sendTo': RECIPIENT, 'minorversion': version}, data=b'',
                                 headers={'Authorization': 'Bearer ' + token['access_token'], 'Accept': 'application/json',
                                          'Content-Type': 'application/octet-stream'}, timeout=30, allow_redirects=False)
        # Retain status/trace BEFORE raise_for_status or JSON parsing. Never retain
        # a raw exception, arbitrary headers or response body containing secrets.
        retain_exchange(path, actor, 'SEND', {'operation': 'SEND', 'method': 'POST', 'resource': 'estimate/185/send',
                        'realm_id': realm, 'recipient': RECIPIENT, 'minorversion': version, 'requested_at': requested_at,
                        'received_at': now(), **sanitized_response(response)})
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
        from .sandbox_estimate_observation import retain_exchange
        try:
            retain_exchange(path, actor, 'TRANSPORT_FAILURE', {'operation': 'SEND', 'outcome': 'Unknown',
                            'observed_at': now(), 'http_status': None, 'intuit_tid': None,
                            'note': 'Completion or evidence retention uncertain; inspect SEND evidence if present. Slot consumed; never retry.'})
        except Exception:
            pass  # The durable claim already exists even if diagnostic storage fails.
    with transaction(path) as db:
        attempt = settings(db, SEND_SLOT, {})
        put_setting(db, SEND_SLOT, {**attempt, 'state': state, 'checked_at': now()})
        audit(db, actor, 'Sandbox email result: ' + state + '; inbox receipt unverified', 'quotes', qid)
    if state == 'Unknown':
        raise HTTPException(502, 'Email completion is unknown. The send slot is consumed; reconcile without resending.')
    return {'state': state, 'inbox_delivery_verified': False, 'customer_click_verified': False, 'booking_created': False}


def register(app, path, auth):
    from .sandbox_revision_reconciliation import register as register_reconciliation
    register_reconciliation(app, path, auth)
    @app.get('/api/quotes/{qid}/sandbox-provider-readback')
    def provider_readback(qid: str, u=Depends(auth)):
        from .sandbox_estimate_observation import raw_readback
        return raw_readback(path, qid, u['id'])
    @app.get('/api/quotes/{qid}/sandbox-send-preflight')
    def preview(qid: str, u=Depends(auth)):
        require_role(u, SALES)
        return preflight(path, qid)

    @app.post('/api/quotes/{qid}/sandbox-send-approval')
    def approve_route(qid: str, data: dict, u=Depends(auth)):
        require_role(u, {'owner'})
        return approve(path, qid, data, u['id'])

    @app.post('/api/quotes/{qid}/sandbox-send')
    def send_route(qid: str, data: dict, u=Depends(auth)):
        require_role(u, {'owner'})
        return send(path, qid, data, u['id'])
