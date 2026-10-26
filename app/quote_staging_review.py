"""Owner-only, content-minimized review of pre-existing staging mailbox data."""
from contextlib import closing
import json
from fastapi import Depends
from .store import connect, rows
from .platform_common import require_role
from .quote_staging import require_isolated, isolated, QUOTE


def inventory(path):
    require_isolated()
    with closing(connect(path)) as db:
        events = rows(db, "SELECT payload_json FROM integration_events WHERE event_type LIKE 'gmail_%'")
        counts = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                  for table in ('leads', 'messages', 'quotes', 'jobs', 'payments')}
        gmail_leads = db.execute("SELECT COUNT(*) FROM leads WHERE source='Gmail'").fetchone()[0]
        unmarked_leads = db.execute("SELECT COUNT(*) FROM leads WHERE source='Gmail' AND (client_name NOT LIKE 'TEST%' OR (email NOT LIKE '%@example.com' AND email NOT LIKE '%@example.invalid' AND email NOT LIKE '%@example.test'))").fetchone()[0]
    uncertain = 0
    for event in events:
        try:
            parsed = json.loads(event['payload_json']).get('parsed') or {}
            email = str(parsed.get('email') or '').lower()
            name = str(parsed.get('client_name') or '')
            fictional = name.startswith('TEST') and email.endswith(('@example.com', '@example.invalid', '@example.test'))
        except (ValueError, TypeError, AttributeError):
            fictional = False
        if not fictional:
            uncertain += 1
    return {'policy': 'synthetic_only_no_mailbox', 'gmail_event_count': len(events),
            'gmail_lead_count': gmail_leads, 'gmail_leads_without_test_markers': unmarked_leads,
            'events_not_proven_fictional': uncertain, 'record_counts': counts,
            'real_customer_data': 'Potential real correspondence requires owner review; not proven synthetic.' if uncertain or gmail_leads else 'No Gmail-origin events/leads found; other records have not been classified.',
            'deleted_records': 0, 'notice': 'Counts only. No mailbox read, message bodies, names, emails or credentials returned. No records removed.'}


def startup_report(path):
    """A bounded, count-only deployment diagnostic; no mailbox/provider request."""
    if not isolated():
        return
    import logging
    from .platform_common import settings
    try:
        report = inventory(path)
        with closing(connect(path)) as db:
            quote = db.execute('SELECT id,lead_id,revision,status FROM quotes WHERE id=?', (QUOTE,)).fetchone()
            delivery = db.execute('SELECT external_id,environment,realm_id,state,payload_json,remote_json,source_hash,sent_at FROM qbo_estimate_deliveries WHERE quote_id=?', (QUOTE,)).fetchone()
            report['quote'] = dict(quote) if quote else None
            report['create_attempt'] = settings(db, 'sandbox_estimate_test_attempt_v1', {})
            # Whitelist identity/state fields; never log arbitrary stored payloads.
            report['create_attempt'] = {k: report['create_attempt'].get(k) for k in ('quote_id','state','external_id')}
            report['send_attempt_present'] = bool(settings(db, 'sandbox_estimate_185_send_attempt_v1'))
            if quote:
                report['quote_family'] = [dict(r) for r in db.execute('SELECT id,revision,status FROM quotes WHERE lead_id=? ORDER BY revision', (quote['lead_id'],))]
            if delivery:
                payload, remote = json.loads(delivery['payload_json']), json.loads(delivery['remote_json'])
                from .estimate_delivery import digest
                report['stored_estimate'] = {k: delivery[k] for k in ('external_id','environment','realm_id','state','sent_at')}
                report['stored_estimate'].update(customer_id=payload.get('CustomerRef', {}).get('value'), total=remote.get('TotalAmt'),
                    snapshot_hash=digest({'payload':payload,'taxes':remote.get('TxnTaxDetail'),'total':remote.get('TotalAmt'),'source':delivery['source_hash']}))
            report['provider_readback_verified'] = False
            import os
            from .sandbox_estimate_send import OFF
            report['broad_flags_enabled'] = {k: os.getenv(k, '0') == '1' for k in OFF}
            report['send_flag_enabled'] = os.getenv('CROWNOPS_QBO_TEST_SEND_ESTIMATE', '') == '185'
            report['webhook_capture_enabled'] = os.getenv('CROWNOPS_QBO_TEST_WEBHOOK_CAPTURE', '0') == '1'
        logging.getLogger('crownops').warning('QUOTE_STAGING_READONLY_REVIEW %s', json.dumps(report, sort_keys=True))
    except Exception:
        logging.getLogger('crownops').warning('QUOTE_STAGING_READONLY_REVIEW unavailable; authenticated reconciliation required. No records changed.')


def register(app, path, auth):
    @app.get('/api/quote-staging/data-review')
    def review(u=Depends(auth)):
        require_role(u, {'owner'})
        return inventory(path)
