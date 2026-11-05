"""Drive one fictional company installation from setup to an issued invoice (Priority 4).

Run in its own process:  python tests/installation_workflow.py <db_path> <config.json>
Prints a JSON summary. Everything goes through the HTTP API except synthetic seed rows
(users/sessions, a rate calendar row, approved client terms and billing terms), which an
owner would normally enter through the settings screens. No provider is contacted.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.update({'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', 'QBO_ENV': 'sandbox'})


def main(db_path, cfg):
    from fastapi.testclient import TestClient
    from app.company_profile import store_profile
    from app.main import create_app, sha
    from app.platform_common import put_setting
    from app.store import connect, initialize, insert, now, transaction
    from app.business_zone import zone_name
    from contextlib import closing

    def ok(r):
        assert r.status_code == 200, (r.request.method, str(r.request.url), r.status_code, r.text[:300])
        return r.json()

    initialize(db_path)
    with transaction(db_path) as db:
        put_setting(db, 'company_identity_mode', 'unconfigured')
        store_profile(db, {**cfg['profile'], 'version': 0}, 'setup')
    app = create_app(db_path)
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    token = cfg['token']
    with transaction(db_path) as db:
        insert(db, 'users', dict(id='owner', email=cfg['owner_email'], name='Fictional Owner', role='owner', password_hash='x', active=1, created_at=now()))
        insert(db, 'sessions', dict(token_hash=sha(token), user_id='owner', csrf=token + '-csrf', expires_at=expires))
        insert(db, 'pricing_versions', dict(id='PV', label='Company', rates_json='{}', settings_json='{}', approved=1, created_at=now()))
        insert(db, 'annual_rate_periods', dict(id='RATE', label='Company rates', start_date='2026-01-01', end_date='2099-12-31', active=1, version=1, note='',
                                               approved_by='owner', created_at=now(), **{f'rate_{n}_cents': c for n, c in cfg['rates'].items()}))
        put_setting(db, 'client_terms', {'approved': True, 'version': 'terms-1', 'text': 'Fictional booking terms.'})
        put_setting(db, 'billing_policy', {'approved': True, 'terms': {'issuer': {'name': cfg['profile']['name'], 'address': cfg['address'],
                    'email': cfg['profile']['email'], 'phone': cfg['profile']['phone']}, 'taxes': cfg['taxes'], 'catalogue': [],
                    'hourly_rate': 1, 'minimum_hours': 0, 'transport_amount': 0, 'fuel_amount': 0, 'discount_amount': 0, 'approval_reference': 'Approved'}})
    staff = TestClient(app); staff.cookies.set('crownops_session', token); staff.headers['x-csrf-token'] = token + '-csrf'
    ok(staff.put('/api/company/pricing-policy', json={**cfg['pricing'], 'approved': True, 'version': 0}))
    status = ok(staff.get('/api/company/setup-status'))
    quote = ok(staff.post('/api/crm/quotes', json=dict(
        service_type='Moving', language=cfg['language'], template_key='moving-fr' if cfg['language'] == 'French' else 'moving-en', move_date='2026-11-20', movers=2, trucks=1, hours_low=3, hours_high=4,
        minimum_hours=cfg['pricing']['default_minimum_hours'], transport_hours=1, start_window='morning', quote_title='Fictional local move',
        draft_key='draft-' + token, new_client=dict(client_name='Fictional Client ' + token, email=token + '@example.invalid', phone='5555550100'),
        service_details=dict(pickup='1 Fictional St', dropoff='2 Fictional Ave', inventory='Boxes'))))
    qid = quote['id']
    final = ok(staff.post('/api/crm/quotes/' + qid + '/finalize', json={'send_now': False, 'sync_quickbooks': False}))
    portal = TestClient(app)
    exchange = ok(portal.post('/api/portal/exchange', json={'token': urlsplit(final['portal_url']).fragment.removeprefix('access=')}))
    portal.headers['x-csrf-token'] = exchange['csrf']
    accepted = ok(portal.post('/api/portal/quotes/' + qid + '/accept', json={'agree': True, 'signed_name': 'Fictional Client', 'terms_version': 'terms-1'}))
    deposit = ok(staff.post('/api/crm/quotes/' + qid + '/manual-deposit', json={'method': 'Cash', 'mode': 'received',
                 'amount': float(cfg['pricing']['default_deposit']), 'reference': 'Fictional cash deposit'}))
    jid = deposit['job_id']

    def job():
        with closing(connect(db_path)) as db:
            return dict(db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone())
    with transaction(db_path) as db:  # synthetic crew and truck (dispatch planning UI is covered by other suites)
        for eid in ('E1', 'E2'):
            insert(db, 'employees', dict(id=eid, name='Fictional mover ' + eid, role='Mover', active=1, driver_verified=1))
            insert(db, 'assignments', dict(job_id=jid, resource_type='employee', resource_id=eid))
        insert(db, 'vehicles', dict(id='V1', name='Fictional truck', active=1))
        insert(db, 'assignments', dict(job_id=jid, resource_type='vehicle', resource_id='V1'))
    for status_name in ('In progress', 'Completed'):
        ok(staff.patch('/api/jobs/' + jid, json={'version': job()['version'], 'status': status_name}))
    iid = ok(staff.post('/api/jobs/' + jid + '/invoice-from-quote'))['id']
    inv = ok(staff.get('/api/invoices/' + iid))
    saved = ok(staff.patch('/api/invoices/' + iid, json={'version': inv['version'], 'service_hours': 3.5, 'quantities': {}, 'notes': '', 'reason': 'Fictional actuals checked'}))
    issued = ok(staff.post('/api/invoices/' + iid + '/issue', json={'version': saved['version'], 'confirmed': True}))
    with closing(connect(db_path)) as db:
        messages = [dict(r) for r in db.execute('SELECT subject, body FROM outbound_messages')]
        start = job()['start_at']
    from app.client_communications import booking_details
    with closing(connect(db_path)) as db:
        q = dict(db.execute('SELECT * FROM quotes WHERE id=?', (qid,)).fetchone())
    details = booking_details(job(), q)
    final_invoice = ok(staff.get('/api/invoices/' + iid))
    doc = final_invoice
    return {'company': cfg['profile']['name'], 'zone': zone_name(), 'setup_ready_before_billing': status['ready'],
            'quote_low_cents': quote['result']['low_cents'], 'pricing_policy_version': quote['result'].get('pricing_policy_version'),
            'fuel_cents': quote['result']['fuel_cents'], 'accepted_booking_confirmed': accepted.get('booking_confirmed'),
            'job_start_utc': start, 'local_arrival': details['arrival_window'], 'local_date': details['date'],
            'invoice_id': iid, 'invoice_status': doc.get('status'), 'invoice_total_cents': (doc.get('calculation') or doc.get('calc') or {}).get('total_cents'),
            'invoice_issuer': ((doc.get('terms') or {}).get('issuer') or {}).get('name'), 'invoice_keys': sorted(doc)[:40],
            'message_subjects': [m['subject'] for m in messages], 'message_text': ' '.join(m['subject'] + ' ' + m['body'] for m in messages),
            'portal_page': portal.get('/portal').text[:4000], 'session_token': token}


if __name__ == '__main__':
    print(json.dumps(main(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()))))
