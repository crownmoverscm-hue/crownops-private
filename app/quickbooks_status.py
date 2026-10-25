"""Account verification is distinct from a saved OAuth grant or enabled writes."""
from contextlib import closing
import os
from .store import connect, one
from .platform_common import settings, decrypt, quickbooks_environment, quickbooks_write_enabled


def account_status(path, mappings_complete):
    env=quickbooks_environment()
    with closing(connect(path)) as db:
        credential=one(db,"SELECT ciphertext FROM oauth_credentials WHERE provider='quickbooks'")
        check=settings(db,'quickbooks_account_check_'+env,{}) or {}
        health=settings(db,'connection_health_quickbooks',{}) or {}
        receipt_sync=settings(db,'quickbooks_receipt_sync_status',{}) or {}
    realm=''
    if credential:
        try:realm=str(decrypt(credential['ciphertext']).get('realm_id') or '')
        except Exception:pass
    matching=bool(realm and realm==str(check.get('realm_id')))
    receipt_matching=receipt_sync.get('environment')==env and str(receipt_sync.get('realm_id'))==realm
    verified=bool(credential and matching and check.get('ok') is True and health.get('state') not in ('reconnect_required','retrying'))
    expected=os.getenv('QBO_SANDBOX_REALM_ID','').strip()
    if expected:verified=bool(verified and env=='sandbox' and realm==expected and check.get('country')=='CA')
    configured=bool(os.getenv('QBO_CLIENT_ID') and os.getenv('QBO_CLIENT_SECRET') and os.getenv('CROWNOPS_SECRET_KEY'))
    state=('Not connected' if not credential else 'Permission needed' if health.get('state')=='reconnect_required' else
           'Temporary problem — connection retained' if health.get('state')=='retrying' else
           str(check.get('state') or 'Connection needs checking') if matching and check.get('ok') is False else
           'Account verified' if verified else 'Authorization saved — account not checked')
    writes=quickbooks_write_enabled()
    return {'state':state,'account_verified':verified,'company_name':check.get('company_name','') if matching else '',
        'realm_id':realm,'country':check.get('country','') if matching and verified else '',
        'checked_at':check.get('checked_at') if matching else None,'check_error':check.get('message','') if matching and not check.get('ok') else '',
        'configured':configured,'environment':env,'mappings_complete':bool(mappings_complete),'writes_enabled':writes,
        'estimate_email_enabled':os.getenv('CROWNOPS_ALLOW_QBO_ESTIMATE_DELIVERY')=='1',
        'invoice_email_enabled':os.getenv('CROWNOPS_ALLOW_QBO_INVOICE_DELIVERY')=='1',
        'receipt_sync_enabled':os.getenv('CROWNOPS_ALLOW_QBO_RECEIPT_SYNC')=='1',
        'receipt_sync_checked_at':receipt_sync.get('checked_at') if receipt_matching else None,
        'receipt_sync_needs_review':receipt_sync.get('needs_review',0) if receipt_matching else 0,
        'end_to_end_verified':False,
        'note':'An account check reads company details only. Quote acceptance, invoice email and payment still require their own walkthrough.'}
