"""Default-off signed event capture and recoverable READ-ONLY reconciliation.

An Estimate update is a hint to read the provider, never customer-click evidence.
No event processor calls synchronize(), acceptance, booking or deposit helpers.
"""
from contextlib import closing
import base64
import hashlib
import hmac
import json
import os
from fastapi import Depends, HTTPException, Request
from .store import connect, transaction, now
from .platform_common import settings, put_setting, require_role
from . import quote_staging as scope

PREFIX = 'sandbox_estimate_185_event:'


def verified_events(raw, signature):
    scope.require_isolated()
    secret = os.getenv('QBO_SANDBOX_WEBHOOK_VERIFIER_TOKEN', '')
    if not secret:
        raise HTTPException(503, 'Sandbox webhook verifier is not configured.')
    expected = base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(expected, signature or ''):
        raise HTTPException(401, 'Invalid Intuit webhook signature.')
    try:
        payload = json.loads(raw)
        candidates = []
        if isinstance(payload, list):
            for event in payload:
                if str(event.get('intuitaccountid')) != scope.REALM:
                    raise HTTPException(403, 'Wrong sandbox realm.')
                if str(event.get('intuitentityid')) == scope.ESTIMATE and '.estimate.' in str(event.get('type', '')).lower():
                    candidates.append({'event_id': str(event['id']), 'type': str(event['type']), 'time': str(event['time']),
                                       'realm_id': scope.REALM, 'estimate_id': scope.ESTIMATE})
        else:
            for notification in payload['eventNotifications']:
                if str(notification['realmId']) != scope.REALM:
                    raise HTTPException(403, 'Wrong sandbox realm.')
                for entity in notification['dataChangeEvent']['entities']:
                    if entity['name'] == 'Estimate' and str(entity['id']) == scope.ESTIMATE:
                        candidates.append({'event_id': '', 'type': str(entity['operation']), 'time': str(entity['lastUpdated']),
                                           'realm_id': scope.REALM, 'estimate_id': scope.ESTIMATE})
        return candidates
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(422, 'Invalid Intuit event structure.')


def capture(path, raw, signature):
    scope.require_isolated()
    if os.getenv('CROWNOPS_QBO_TEST_WEBHOOK_CAPTURE', '0') != '1':
        raise HTTPException(403, 'Sandbox event capture is disabled; no acceptance processing is enabled.')
    events = verified_events(raw, signature)
    duplicates = 0
    with transaction(path) as db:
        for event in events:
            identity = {k: event[k] for k in ('event_id','realm_id','estimate_id')} if event['event_id'] else event
            key = PREFIX + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            if settings(db, key):
                duplicates += 1
                continue
            put_setting(db, key, {**event, 'state': 'Queued', 'received_at': now(), 'attempts': 0})
    return {'received': len(events), 'duplicates': duplicates, 'acceptance_enabled': False}


def reconcile(path):
    scope.require_isolated()
    from .sandbox_estimate_send import preflight
    with closing(connect(path)) as db:
        pending = [dict(r) for r in db.execute("SELECT key,value_json FROM settings WHERE key LIKE ?", (PREFIX + '%',))]
    summary = {'checked': 0, 'failed': 0, 'customer_click_verified': False, 'booking_created': False}
    for row in pending:
        event = json.loads(row['value_json'])
        if event['state'] == 'Readback recorded':
            continue
        try:
            p = preflight(path, scope.QUOTE)
            changes = {'state': 'Readback recorded', 'provider_status': p['provider_status'],
                       'snapshot_hash': p['snapshot_hash'], 'customer_click_verified': False,
                       'note': 'Provider status may be staff-recorded. No acceptance or booking authorized.'}
            summary['checked'] += 1
        except Exception:
            changes = {'state': 'Failed', 'error': 'Readback not verified; retry reconciliation after resolving the connection or snapshot mismatch.'}
            summary['failed'] += 1
        with transaction(path) as db:
            current = settings(db, row['key'], {})
            if current.get('state') == 'Readback recorded':
                continue
            put_setting(db, row['key'], {**current, **changes, 'attempts': current.get('attempts', 0) + 1, 'checked_at': now()})
    return summary


def register(app, path, auth):
    @app.post('/api/webhooks/quickbooks/quote-sandbox')
    async def webhook(request: Request):
        raw = await request.body()
        if len(raw) > 100000:
            raise HTTPException(413, 'Webhook payload too large.')
        return capture(path, raw, request.headers.get('intuit-signature'))

    @app.post('/api/quote-staging/events/reconcile')
    def readback(u=Depends(auth)):
        require_role(u, {'owner'})
        return reconcile(path)
