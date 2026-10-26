"""One owner-reviewed, unsent Canadian sandbox estimate. Not business policy.

No generic accounting gate is enabled. The durable attempt is consumed before
the first provider mutation; an uncertain result never permits a second POST.
"""
from contextlib import closing
from decimal import Decimal
import hashlib
import json
import os
import requests
from fastapi import HTTPException
from .store import connect, transaction, one, rows, insert, dumps, now, audit
from .platform_common import settings, put_setting
from .providers import _qbo_connection, qbo_read, qbo_query

SLOT = 'sandbox_estimate_test_attempt_v1'
TERMS = {'approved': True, 'version': 'TEST-ONLY-2026-10-26', 'text':
    'TEST ONLY — SANDBOX ESTIMATE\n\n'
    'This estimate is created exclusively for software integration testing. The customer, moving date, service details and prices are fictional.\n\n'
    'This document is not a binding commercial offer, moving contract or reservation. It does not authorize actual moving services, crew assignment, deposits, invoices or payments.\n\n'
    'No real customer communication or booking may be created from this test estimate.'}


def selected(qid):
    return bool(os.getenv('CROWNOPS_QBO_TEST_CREATE_QUOTE')) and qid == os.getenv('CROWNOPS_QBO_TEST_CREATE_QUOTE')


def deny_actions(qid):
    from .quote_staging import deny_side_effects
    deny_side_effects()
    if selected(qid):
        raise HTTPException(409, 'TEST ONLY create-only permission: sending, acceptance and booking are disabled.')


def guard(qid):
    from .quote_test_fixture import rate, DAY
    if not selected(qid) or rate(DAY) is None:
        raise HTTPException(503, 'No isolated one-estimate permission exists for this quote.')
    required = {'RAILWAY_SERVICE_ID': '5de22c3a-ea7e-4fa6-bc96-2f9472431c7c',
                'RAILWAY_ENVIRONMENT_ID': '53900324-c678-40b0-91f0-b6560316a5d2',
                'CROWNOPS_DB': '/data/crownops-quote-acceptance.sqlite3'}
    if any(os.getenv(k) != v for k, v in required.items()):
        raise HTTPException(503, 'The one-estimate permission requires the isolated service, environment and database.')
    off = ('CROWNOPS_ALLOW_EXTERNAL', 'CROWNOPS_ALLOW_QBO_SANDBOX_WRITES',
           'CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES', 'CROWNOPS_ALLOW_QBO_ESTIMATE_DELIVERY',
           'CROWNOPS_ALLOW_QBO_INVOICE_DELIVERY', 'CROWNOPS_ALLOW_QBO_RECEIPT_SYNC',
           'CROWNOPS_GMAIL_QBO_SANDBOX_WORKFLOW', 'CROWNOPS_RUN_WORKER')
    if any(os.getenv(k, '0') != '0' for k in off):
        raise HTTPException(503, 'Create-only testing requires all broad writes, delivery, acceptance workers and external actions OFF.')


def terms_for(q):
    guard(q['id'])
    inp, result = json.loads(q['inputs_json']), json.loads(q['result_json'])
    if (not result.get('test_only') or not str(inp.get('quote_title', '')).startswith('TEST ONLY')
            or inp.get('move_date') != '2026-11-15' or result.get('hourly_rate_cents') != 10000):
        raise HTTPException(409, 'This permission requires the labelled November 15 fictional $100/hour quote.')
    return dict(TERMS)


def preflight(path, qid):
    guard(qid)
    from .estimate_delivery import delivery_source, estimate_payload, digest
    with closing(connect(path)) as db:
        q, lead, terms, fingerprint = delivery_source(db, qid)
        if not lead['client_name'].startswith('TEST'):
            raise HTTPException(409, 'Only a fictional TEST client is permitted.')
        if one(db, 'SELECT id FROM quotes WHERE lead_id=? AND revision>?', (lead['id'], q['revision'])):
            raise HTTPException(409, 'A newer quote revision exists. Review it; no second estimate is permitted.')
        history = rows(db, 'SELECT id,lead_id,revision,status FROM quotes WHERE lead_id=? ORDER BY revision', (lead['id'],))
        if qid == 'QUOTE-08dd48a364a34f66' and not any(x['id']=='QUOTE-8a7b31fbc00e4302' and x['revision']==1 for x in history):
            raise HTTPException(409, 'The reported r1 and r2 do not share a verified service parent. Investigate before any write.')
        mappings = settings(db, 'quickbooks_mappings_sandbox', {}) or {}
        attempt = settings(db, SLOT, {}) or {}
        linked = one(db, "SELECT external_id FROM external_records WHERE provider='quickbooks' AND kind='Customer' AND object_id=? AND revision=0", (lead['id'],))
        legacy = one(db, "SELECT external_id FROM external_records WHERE provider='quickbooks' AND kind='Estimate' AND object_id IN (SELECT id FROM quotes WHERE lead_id=?)", (lead['id'],))
        operations = one(db, "SELECT id FROM provider_operations WHERE kind='Estimate' AND object_id IN (SELECT id FROM quotes WHERE lead_id=?)", (lead['id'],))
        delivery = one(db, 'SELECT * FROM qbo_estimate_deliveries WHERE quote_id IN (SELECT id FROM quotes WHERE lead_id=?)', (lead['id'],))
    if (legacy or operations or delivery) and not (attempt.get('quote_id') == qid and attempt.get('external_id')):
        raise HTTPException(409, 'An estimate/export already exists or is unresolved for this service. Reconcile before any write.')
    token, realm, host = _qbo_connection(path)
    if realm != '9341458310238189' or host != 'https://sandbox-quickbooks.api.intuit.com':
        raise HTTPException(409, 'Wrong sandbox connection. No writes are allowed.')
    company = qbo_read(path, 'companyinfo/' + realm).get('CompanyInfo', {})
    if company.get('Country') != 'CA' or company.get('CompanyName') != 'Sandbox Company CA 5634':
        raise HTTPException(409, 'Expected Canadian sandbox identity was not read back.')
    item, tax = str(mappings.get('service_item_id') or ''), str(mappings.get('tax_code_id') or '')
    if not item.isdigit() or not tax.isdigit():
        raise HTTPException(409, 'Save the reviewed sandbox service-item and tax-code mappings first.')
    item_data = qbo_read(path, 'item/' + item).get('Item', {})
    code = qbo_read(path, 'taxcode/' + tax).get('TaxCode', {})
    if item_data.get('Active') is not True or item_data.get('Type') != 'Service' or code.get('Active') is not True:
        raise HTTPException(409, 'The mapped service item and tax code must be active.')
    rates = []
    for detail in code.get('SalesTaxRateList', {}).get('TaxRateDetail', []):
        ref = str(detail.get('TaxRateRef', {}).get('value', ''))
        if not ref.isdigit() or detail.get('TaxTypeApplicable') == 'TaxOnTax' or detail.get('TaxApplicableOn') == 'TaxOnTax':
            raise HTTPException(409, 'The tax mapping must use non-compounded TPS and TVQ.')
        rates.append(Decimal(str(qbo_read(path, 'taxrate/' + ref)['TaxRate']['RateValue'])))
    if sorted(rates) != [Decimal('5'), Decimal('9.975')]:
        raise HTTPException(409, 'The mapped sales tax must contain exactly TPS 5% and TVQ 9.975%.')
    body = estimate_payload(q, lead, terms, 'low', item, tax, '')
    sales = [x for x in body['Line'] if x['DetailType'] == 'SalesItemLineDetail']
    if ([round(x['Amount'] * 100) for x in sales] != [30000, 15000, 3599]
            or sales[0]['SalesItemLineDetail']['Qty'] != 3 or len([x for x in body['Line'] if x['DetailType'] == 'DiscountLineDetail'])):
        raise HTTPException(409, 'Test lines must be exactly 3 hours × $100, fictional transport $150 and fuel $35.99; no discount/materials.')
    body['Line'] = sales + [{'DetailType': 'DescriptionOnly', 'Description': TERMS['text']}]
    body['CustomerMemo'] = {'value': 'TEST ONLY — fictional November 15, 2026 integration test. No acceptance, reservation or payment authorized. Exact subtotal CAD 485.99; expected TPS 24.30, TVQ 48.48; total CAD 558.77.'}
    body['PrivateNote'] = 'TEST ONLY CrownOps ' + qid + ' revision ' + str(q['revision'])
    display_name = lead['client_name'][:65] + ' | ' + lead['id'][-12:]
    customer = None
    if linked:
        customer = qbo_read(path, 'customer/' + linked['external_id']).get('Customer', {})
    else:
        escaped = display_name.replace('\\', '\\\\').replace("'", "\\'")
        found = qbo_query(path, "SELECT * FROM Customer WHERE DisplayName = '" + escaped + "'").get('Customer', [])
        if len(found) > 1:raise HTTPException(409, 'Ambiguous test customer match; reconcile first.')
        customer = found[0] if found else None
    if customer and (customer.get('DisplayName') != display_name or customer.get('PrimaryEmailAddr', {}).get('Address', '').lower() != lead['email'].lower()):
        raise HTTPException(409, 'The mapped test customer identity differs; no write allowed.')
    customer_id = str(customer['Id']) if customer else None
    customer_body = {'DisplayName': display_name, 'PrimaryEmailAddr': {'Address': lead['email']}, 'PrimaryPhone': {'FreeFormNumber': lead['phone']}}
    if customer_id:body['CustomerRef'] = {'value': customer_id}
    review = digest({'fingerprint': fingerprint, 'payload': body, 'customer': customer_id or customer_body, 'mappings': mappings, 'terms': TERMS})
    return {'test_only': True, 'enabled': not attempt or attempt.get('state') == 'Ready', 'state': attempt.get('state', 'Not prepared'),
            'quote_id': qid, 'service_id': lead['id'], 'revision': q['revision'], 'revisions': history,
            'external_id': attempt.get('external_id'), 'customer_id': customer_id,
            'proposed_writes': ([] if attempt else (['Create one fictional sandbox Customer'] if not customer_id else []) + ['Create exactly one unsent sandbox Estimate']),
            'requires_customer_create_confirmation': not customer_id, 'terms': TERMS, 'review_hash': review,
            'subtotal_cents': 48599, 'gst_cents': 2430, 'qst_cents': 4848, 'total_cents': 55877,
            'environment': 'sandbox', 'realm_id': realm, 'company_name': company['CompanyName'], 'country': 'CA',
            '_payload': body, '_customer': customer_body, '_lead_id': lead['id'], '_source_hash': fingerprint, '_mappings': mappings}


def public_preflight(path, qid):
    return {k: v for k, v in preflight(path, qid).items() if not k.startswith('_')}


def _post(path, resource, body, key):
    # Intentionally not a general provider adapter; only called after durable claim.
    t, realm, host = _qbo_connection(path)
    guard(os.environ['CROWNOPS_QBO_TEST_CREATE_QUOTE'])
    if realm != '9341458310238189' or host != 'https://sandbox-quickbooks.api.intuit.com' or resource not in ('customer', 'estimate'):
        raise HTTPException(409, 'Unexpected test accounting target.')
    response = requests.post(host + '/v3/company/' + realm + '/' + resource, json=body,
        params={'minorversion': os.getenv('QBO_MINOR_VERSION', '75'), 'requestid': hashlib.sha256(key.encode()).hexdigest()[:40]},
        headers={'Authorization': 'Bearer ' + t['access_token'], 'Accept': 'application/json'}, timeout=30)
    response.raise_for_status()
    return response.json()


def prepare(path, qid, data, actor):
    guard(qid)
    with closing(connect(path)) as db:
        attempt = settings(db, SLOT, {}) or {}
    if attempt:
        if attempt.get('quote_id') == qid and attempt.get('state') == 'Ready':
            return {'prepared': True, 'state': 'Ready', 'external_id': attempt['external_id'], 'duplicate': True}
        raise HTTPException(409, 'The one-estimate attempt is consumed or unresolved. Reconcile provider records; do not retry creation.')
    from .quote_staging import QUOTE
    if qid == QUOTE:
        raise HTTPException(409, 'Estimate 185 already exists. Reconcile the existing attempt; customer and estimate creation are retired for this quote.')
    p = preflight(path, qid)
    if data.get('confirm') is not True or data.get('review_hash') != p['review_hash']:
        raise HTTPException(409, 'Review the current test-only terms, exact amounts and proposed writes before confirming.')
    if p['requires_customer_create_confirmation'] and data.get('confirm_customer_create') is not True:
        raise HTTPException(409, 'One fictional sandbox Customer must be created first. Explicitly confirm that proposed related write.')
    with transaction(path) as db:
        if settings(db, SLOT):raise HTTPException(409, 'The single preparation attempt has already started.')
        from .estimate_delivery import delivery_source
        if (delivery_source(db,qid)[3] != p['_source_hash']
                or (settings(db,'quickbooks_mappings_sandbox',{}) or {}) != p['_mappings']
                or one(db,'SELECT id FROM quotes WHERE lead_id=? ORDER BY revision DESC LIMIT 1',(p['_lead_id'],))['id'] != qid):
            raise HTTPException(409, 'Quote, service or mappings changed during preflight. Review again before any write.')
        put_setting(db, SLOT, {'quote_id': qid, 'state': 'Preparing', 'actor': actor, 'review_hash': p['review_hash'], 'at': now()})
        insert(db, 'qbo_estimate_deliveries', dict(quote_id=qid, environment='sandbox', realm_id=p['realm_id'], basis='low',
            state='Preparing', source_hash=p['_source_hash'], payload_json=dumps(p['_payload']), recipient=p['_customer']['PrimaryEmailAddr']['Address'], created_by=actor, created_at=now()))
        audit(db, actor, 'Claimed one TEST ONLY sandbox estimate; no send/acceptance authorized', 'quotes', qid)
    body = p['_payload']
    try:
        customer_id = p['customer_id']
        if not customer_id:
            customer_id = str(_post(path, 'customer', p['_customer'], 'sandbox-test-customer:' + p['_lead_id'])['Customer']['Id'])
        if not str(customer_id).isdigit():raise ValueError('Invalid sandbox customer ID')
        customer = qbo_read(path,'customer/'+customer_id)['Customer']
        if (customer.get('DisplayName') != p['_customer']['DisplayName']
                or customer.get('PrimaryEmailAddr',{}).get('Address','').lower() != p['_customer']['PrimaryEmailAddr']['Address'].lower()):
            raise ValueError('Sandbox customer identity changed')
        with transaction(path) as db:
            db.execute('INSERT OR IGNORE INTO external_records(provider,kind,object_id,external_id,updated_at) VALUES(?,?,?,?,?)', ('quickbooks','Customer',p['_lead_id'],customer_id,now()))
        body['CustomerRef'] = {'value': customer_id}
        created = _post(path, 'estimate', body, 'sandbox-test-estimate:' + qid)['Estimate']
        external_id = str(created['Id'])
        # Persist the ID before readback: an unsuccessful read must never cause a new POST.
        with transaction(path) as db:
            put_setting(db, SLOT, {'quote_id': qid, 'state': 'Unknown', 'external_id': external_id, 'at': now()})
            db.execute('UPDATE qbo_estimate_deliveries SET external_id=?,payload_json=?,remote_json=? WHERE quote_id=?', (external_id,dumps(body),dumps(created),qid))
            insert(db, 'external_records', dict(provider='quickbooks',kind='Estimate',object_id=qid,revision=p['revision'],external_id=external_id,sync_token=created.get('SyncToken','0'),amount_cents=48599,updated_at=now()))
        remote = qbo_read(path, 'estimate/' + external_id)['Estimate']
        from .estimate_delivery import validate_remote
        validate_remote({'payload_json': dumps(body), 'external_id': external_id, 'recipient': p['_customer']['PrimaryEmailAddr']['Address']}, remote, require_quebec=True)
        if str(remote.get('Id')) != external_id or remote.get('TxnStatus') != 'Pending' or remote.get('EmailStatus') == 'EmailSent':
            raise ValueError('Unexpected status or estimate identity')
        with transaction(path) as db:
            put_setting(db, SLOT, {'quote_id': qid, 'state': 'Ready', 'external_id': external_id, 'at': now()})
            db.execute("UPDATE qbo_estimate_deliveries SET state='Ready',remote_json=?,checked_at=? WHERE quote_id=?", (dumps(remote),now(),qid))
            audit(db,actor,'TEST ONLY estimate read back; not sent; no acceptance or booking','quotes',qid,{'external_id':external_id,'total_cents':55877})
        return {'prepared': True, 'state': 'Ready', 'external_id': external_id, 'total_cents': 55877}
    except Exception:
        with transaction(path) as db:
            attempt = settings(db,SLOT,{})
            put_setting(db,SLOT,{**attempt,'state':'Unknown'})
            db.execute("UPDATE qbo_estimate_deliveries SET state='Unknown',error='Test preparation/readback unresolved. Reconcile QuickBooks; no automatic retry.' WHERE quote_id=?",(qid,))
        raise HTTPException(502, 'Test preparation/readback unresolved. Inspect existing customer and estimate records; never create a replacement on retry.')
