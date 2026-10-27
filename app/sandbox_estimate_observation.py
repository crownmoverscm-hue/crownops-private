"""Pinned read-only diagnostics and post-send history. No acceptance or retry grants."""
from contextlib import closing
from collections.abc import Mapping
from copy import deepcopy
import json
import os
import re
import requests
from fastapi import HTTPException
from .store import connect, transaction, one, rows, insert, now, uid
from .platform_common import encrypt
from .estimate_delivery import digest
from . import quote_staging as scope

LIFECYCLE = ('SyncToken', 'EmailStatus', 'TxnStatus', 'DeliveryInfo', 'BillEmail')
APPROVAL_ACTION = 'Approved ONE Estimate 185 sandbox email; no email sent; acceptance and booking remain disabled'
CLAIM_ACTION = 'Claimed one separately approved sandbox email; no acceptance authorized'


def baseline(remote):
    return {'fields': {k: deepcopy(remote[k]) for k in LIFECYCLE if k in remote},
            'updated': {k: deepcopy(v) for k, v in remote.get('MetaData', {}).items() if k == 'LastUpdatedTime'},
            'metadata_present': 'MetaData' in remote}


def protected_provider_hash(realm, company, customer, remote, before, recipient):
    """Restore only explicit lifecycle values; all unknown/business fields stay hashed."""
    current = deepcopy(remote)
    for key in LIFECYCLE:
        if key in before['fields']:
            current[key] = deepcopy(before['fields'][key])
        else:
            current.pop(key, None)
    if 'LastUpdatedTime' in before['updated']:
        current.setdefault('MetaData', {})['LastUpdatedTime'] = before['updated']['LastUpdatedTime']
    elif 'MetaData' in current:
        current['MetaData'].pop('LastUpdatedTime', None)
        if not current['MetaData'] and not before.get('metadata_present'):
            current.pop('MetaData')
    return digest({'realm': realm, 'company': company, 'customer': customer, 'estimate': current, 'recipient': recipient})


def history_and_integrity(db, local, reconciliation, approval, attempt, remote, company, customer, saved, snapshot_hash):
    from .sandbox_estimate_send import RECIPIENT
    from .sandbox_revision_reconciliation import KEY
    record = one(db, 'SELECT * FROM sandbox_revision_reconciliations WHERE id=?', (KEY,))
    decision = json.loads(record['decision_json']) if record else {}
    approval_events = rows(db, 'SELECT * FROM audit WHERE object_id=? AND action=? ORDER BY id', (scope.QUOTE, APPROVAL_ACTION))
    approval_event = next((e for e in approval_events if json.loads(e['details_json']) == approval and e['actor'] == approval.get('approved_by')), None)
    claims = rows(db, 'SELECT * FROM audit WHERE object_id=? AND action=? ORDER BY id', (scope.QUOTE, CLAIM_ACTION))
    claim = next((e for e in claims if e['actor'] == attempt.get('actor') and e['created_at'] == attempt.get('at')), None)
    bound = bool(attempt and approval_event and claim and approval.get('version') == 2 and approval.get('owner_approved') is True
                 and approval.get('quote_id') == attempt.get('quote_id') == scope.QUOTE
                 and approval.get('estimate_id') == attempt.get('estimate_id') == scope.ESTIMATE
                 and approval.get('realm_id') == scope.REALM
                 and approval.get('recipient') == attempt.get('recipient') == RECIPIENT
                 and approval.get('approved_by') == attempt.get('actor')
                 and approval.get('review_hash') == attempt.get('review_hash')
                 and approval.get('snapshot_hash') == snapshot_hash and approval.get('total_cents') == 55877
                 and approval.get('approved_at', '') <= attempt.get('at', '')
                 and (not record or (reconciliation.get('historical_intact') and decision.get('recorded_at', '') <= approval.get('approved_at', '')
                                    and decision.get('owner_id') == approval.get('approved_by'))))
    authorization = attempt.get('authorization_at_claim') or {}
    captured = bool(bound and authorization and authorization.get('approval_hash') == digest(approval)
                    and authorization.get('reconciliation_hash') == reconciliation.get('record_hash')
                    and json.loads(claim['details_json']).get('authorization_at_claim') == authorization)
    historical = {'approval_recorded': bool(approval_event), 'approval_audit_id': approval_event['id'] if approval_event else None,
                  'claim_audit_id': claim['id'] if claim else None, 'authorization_consistent': bound,
                  'evidence': 'Captured at claim' if captured else 'Reconstructed from existing audits; original exchange unavailable' if bound else 'Incomplete or inconsistent historical evidence',
                  'reconciliation_recorded': bool(record), 'reconciliation_intact': reconciliation.get('historical_intact', False),
                  'reconciliation_audit_id': record['audit_id'] if record else None}
    reasons = []
    if not bound:
        reasons.append('Original approval and send claim could not be consistently linked.')
    if record and (not reconciliation.get('historical_intact') or not reconciliation.get('local_matches') or not local['eligible_difference']):
        reasons.append('Protected local history or reconciliation evidence changed.')
    if not record and local['latest_id'] != scope.QUOTE:
        reasons.append('A newer quote revision exists without the preserved specific withdrawal.')
    if captured and authorization.get('context_hash') != local['context_hash']:
        reasons.append('Local quote/customer context changed after the send claim.')
    # Legacy records have no captured baseline: reconstruct from the immutable
    # original provider snapshot, then require equality with event27's FULL hash.
    before = authorization.get('provider_baseline') if captured else baseline(saved)
    expected = authorization.get('provider_hash') if captured else decision.get('provider_hash')
    if not expected:
        reasons.append('No original provider identity fingerprint is available.')
    elif protected_provider_hash(scope.REALM, company, customer, remote, before, RECIPIENT) != expected:
        reasons.append('Provider business or customer/company identity differs from the authorized snapshot.')
    if remote.get('BillEmail', {}).get('Address', '').lower() != RECIPIENT:
        reasons.append('Provider recipient does not match the authorized test recipient.')
    original_token = str(before['fields'].get('SyncToken', ''))
    current_token = str(remote.get('SyncToken', ''))
    if not (original_token.isdigit() and current_token.isdigit() and int(current_token) >= int(original_token)):
        reasons.append('Provider version is missing, invalid or older than the reviewed version.')
    if remote.get('TxnStatus') not in ('Pending', 'Accepted', 'Rejected', 'Closed'):
        reasons.append('Unexpected provider estimate status; inspect without applying acceptance.')
    if attempt.get('state') == 'Sent' and remote.get('EmailStatus') != 'EmailSent':
        reasons.append('Provider email status no longer agrees with the recorded send result.')
    return {'historical_authorization': historical,
            'current_integrity': {'valid': not reasons, 'blockers': reasons, 'scope': 'Observation only; no acceptance or send grant'},
            'delivery_label': 'Provider reports sent — inbox receipt unconfirmed.' if remote.get('EmailStatus') == 'EmailSent' else 'Send completion unresolved — inbox receipt unconfirmed.',
            'send_terminal': bool(attempt), 'customer_click_verified': False, 'inbox_delivery_verified': False}


def sanitized_response(response):
    """Only named diagnostic fields: never return headers/body wholesale."""
    status = response.status_code if isinstance(response.status_code, int) else None
    headers = response.headers if isinstance(response.headers, Mapping) else {}
    trace = headers.get('intuit_tid', '')
    trace = trace if isinstance(trace, str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,160}', trace) else None
    result = {'http_status': status, 'intuit_tid': trace,
              'response_metadata': {k: str(headers[k])[:160] for k in ('Date', 'Content-Type') if isinstance(headers.get(k), str)},
              'parse_status': 'Invalid JSON', 'sanitized_response': {}}
    try:
        body = response.json()
        if not isinstance(body, dict):
            return result
        result['parse_status'] = 'JSON object'
        estimate = body.get('Estimate')
        if isinstance(estimate, dict):
            fields = ('Id', 'EmailStatus', 'TxnStatus', 'BillEmail', 'BillEmailCc', 'BillEmailBcc', 'SyncToken',
                      'CustomerRef', 'CurrencyRef', 'TotalAmt', 'HomeTotalAmt', 'GlobalTaxCalculation')
            clean = {k: estimate[k] for k in fields if isinstance(estimate.get(k), (str, int, float, dict))}
            # Nested objects are explicitly projected too, not passed through.
            for key in ('BillEmail', 'BillEmailCc', 'BillEmailBcc'):
                if key in clean: clean[key] = {k: estimate[key][k] for k in ('Address',) if isinstance(estimate[key], dict) and k in estimate[key]}
            for key in ('CustomerRef', 'CurrencyRef'):
                if key in clean: clean[key] = {k: estimate[key][k] for k in ('value',) if isinstance(estimate[key], dict) and k in estimate[key]}
            clean['MetaData'] = {k: estimate.get('MetaData', {})[k] for k in ('LastUpdatedTime',) if k in estimate.get('MetaData', {})}
            clean['DeliveryInfo'] = {k: estimate.get('DeliveryInfo', {})[k] for k in ('DeliveryType', 'DeliveryTime', 'DeliveryErrorType') if k in estimate.get('DeliveryInfo', {})}
            tax = estimate.get('TxnTaxDetail', {})
            clean['TxnTaxDetail'] = {'TotalTax': tax.get('TotalTax'), 'TaxLine': [{'Amount': x.get('Amount')} for x in tax.get('TaxLine', [])]}
            clean['LineAmounts'] = [{'Amount': x.get('Amount'), 'DetailType': x.get('DetailType')} for x in estimate.get('Line', [])]
            result['sanitized_response']['Estimate'] = clean
        if isinstance(body.get('Fault'), dict):
            result['sanitized_response']['Fault'] = {'codes': [str(x.get('code', ''))[:32] for x in body['Fault'].get('Error', []) if isinstance(x, dict)]}
    except (ValueError, TypeError, KeyError, AttributeError):
        result['parse_status'] = 'Unusable JSON shape'
        result['sanitized_response'] = {}
    return result


def retain_exchange(path, actor, kind, evidence):
    # A new, encrypted, append-only diagnostics ledger; never edits authorization.
    with transaction(path) as db:
        insert(db, 'sandbox_provider_exchanges', {'id': uid('QBEX'), 'quote_id': scope.QUOTE, 'actor': actor,
               'kind': kind, 'created_at': now(), 'ciphertext': encrypt(evidence)})


def raw_readback(path, qid, actor):
    from .sandbox_estimate_send import require_master, _qbo_connection
    scope.require_isolated()
    if qid != scope.QUOTE:
        raise HTTPException(404, 'Only existing sandbox Estimate 185 can be inspected.')
    with closing(connect(path)) as db:
        require_master(db, actor)
        row = one(db, 'SELECT external_id,realm_id,environment FROM qbo_estimate_deliveries WHERE quote_id=?', (qid,))
        if not row or str(row['external_id']) != scope.ESTIMATE or row['realm_id'] != scope.REALM or row['environment'] != 'sandbox':
            raise HTTPException(409, 'Existing sandbox mapping is not intact. No provider request made.')
    token, realm, host = _qbo_connection(path)
    if realm != scope.REALM or host != 'https://sandbox-quickbooks.api.intuit.com':
        raise HTTPException(409, 'Wrong sandbox identity. No provider request made.')
    version = os.getenv('QBO_MINOR_VERSION', '75')
    evidence = {'operation': 'GET', 'realm_id': realm, 'estimate_id': scope.ESTIMATE, 'minorversion': version,
                'observed_at': now(), 'trace_scope': 'This new GET only; original SEND trace was not retained',
                'http_status': None, 'intuit_tid': None, 'provider_write': False}
    try:
        response = requests.get(host + '/v3/company/' + realm + '/estimate/185', params={'minorversion': version},
                                headers={'Authorization': 'Bearer ' + token['access_token'], 'Accept': 'application/json'}, timeout=30, allow_redirects=False)
        evidence.update(sanitized_response(response))
        evidence['estimate_identity_matches'] = str(evidence.get('sanitized_response', {}).get('Estimate', {}).get('Id')) == scope.ESTIMATE
    except requests.RequestException:
        evidence['parse_status'] = 'GET unavailable; no automatic retry'
    return evidence  # GET itself does not insert any diagnostic/audit rows.
