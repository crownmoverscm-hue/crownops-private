"""Fail-closed boundaries for the existing isolated quote acceptance service.

No mailbox is approved here: use synthetic fixtures, never company Gmail.
Recognize independent deployment identifiers so changing one flag cannot bypass it.
"""
import os
from urllib.parse import urlparse
from fastapi import HTTPException

SERVICE = '5de22c3a-ea7e-4fa6-bc96-2f9472431c7c'
ENVIRONMENT = '53900324-c678-40b0-91f0-b6560316a5d2'
HOST = 'crownops-quote-acceptance-staging.up.railway.app'
DATABASE = '/data/crownops-quote-acceptance.sqlite3'
QUOTE = 'QUOTE-08dd48a364a34f66'
REALM = '9341458310238189'
ESTIMATE = '185'


def isolated():
    return (os.getenv('RAILWAY_SERVICE_ID') == SERVICE
            or os.getenv('RAILWAY_PUBLIC_DOMAIN') == HOST
            or urlparse(os.getenv('CROWNOPS_PUBLIC_URL', '')).hostname == HOST
            or os.getenv('CROWNOPS_DB') == DATABASE)


def require_isolated():
    expected = {'CROWNOPS_ENV': 'staging', 'QBO_ENV': 'sandbox',
                'RAILWAY_SERVICE_ID': SERVICE, 'RAILWAY_ENVIRONMENT_ID': ENVIRONMENT,
                'CROWNOPS_DB': DATABASE, 'QBO_SANDBOX_REALM_ID': REALM}
    if (any(os.getenv(k) != v for k, v in expected.items())
            or urlparse(os.getenv('CROWNOPS_PUBLIC_URL', '')).hostname != HOST):
        raise HTTPException(503, 'This read-only test requires the exact isolated Canadian quote sandbox.')


def deny_mailbox():
    if isolated():
        raise HTTPException(403, 'Quote staging uses synthetic test data only. Company Gmail reads and imports are blocked; production mail processing is unchanged.')


def deny_side_effects():
    if isolated():
        raise HTTPException(409, 'Quote staging: acceptance, booking, invoice, deposit, payment and general provider writes are disabled. Send preflight is read-only.')


def blocked_mutation(path):
    """Route-level defence; internal helpers also guard worker/direct calls."""
    if not isolated():
        return False
    import re
    return bool(re.search(r'^/api/(?:jobs(?:/|$)|crm/jobs/|invoices(?:/|$)|payments(?:/|$)|outbound/|webhooks/(?:square|intake)$|imports/)', path)
                or re.search(r'/(?:accept|approve|finalize|manual-deposit|deposit-reminder|sync-acceptance|send-email|square-link)$', path)
                or '/booking-workflow/' in path)
