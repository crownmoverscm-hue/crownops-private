"""Explicit isolated quote-test rate. Never enables business pricing or sending."""
import os
from urllib.parse import urlparse
from decimal import Decimal
from fastapi import HTTPException

DAY = '2026-11-15'
LABEL = 'TEST ONLY — fictional $100/hour — 2026-11-15'

def rate(day):
    if os.getenv('CROWNOPS_QUOTE_TEST_FIXTURE') != '2026-11-15':
        return None
    if (os.getenv('CROWNOPS_ENV') != 'staging' or os.getenv('QBO_ENV') != 'sandbox'
        or os.getenv('QBO_SANDBOX_REALM_ID') != '9341458310238189'
        or urlparse(os.getenv('CROWNOPS_PUBLIC_URL','')).hostname != 'crownops-quote-acceptance-staging.up.railway.app'):
        raise HTTPException(503, 'TEST pricing fixture requires the isolated Canadian quote sandbox.')
    return (Decimal('100.00'), LABEL) if day == DAY else None
