"""Shared rules for the CrownOps v4 application. Secrets are never returned."""
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_HALF_UP
import json, hashlib, os, secrets, re
from fastapi import HTTPException
from .store import connect, transaction, rows, one, insert, audit, uid, now, dumps
from .job_planning import can_plan_jobs

MANAGERS={'owner','manager'}
OFFICE={'owner','manager','sales','dispatch','accounting'}
SALES={'owner','manager','sales'}

def settings(db,key,default=None):
    r=one(db,'SELECT value_json FROM settings WHERE key=?',(key,))
    return json.loads(r['value_json']) if r else default

def put_setting(db,key,value):
    db.execute('INSERT INTO settings(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',(key,dumps(value)))

def text(value,label='Text',limit=10000):
    if not isinstance(value,str) or len(value)>limit:raise HTTPException(422,f'{label} must be text under {limit} characters.')
    return value.strip()

def require_role(u,roles):
    if u['role'] not in roles:raise HTTPException(403,'Your role does not allow this action.')

def staff_read_project(db,lid,u):
    l=one(db,'SELECT * FROM leads WHERE id=?',(lid,))
    if not l:raise HTTPException(404,'Client project not found.')
    if u['role']=='crew' and not can_plan_jobs(db,u) and not one(db,"""SELECT j.id FROM jobs j JOIN assignments a ON a.job_id=j.id
            WHERE j.lead_id=? AND a.resource_type='employee' AND a.resource_id=?""",(lid,u.get('employee_id') or '')):
        raise HTTPException(403,'Crew can view their assigned client projects only.')
    return l

def staff_project_attachments(db,lid,u):
    """List only files the current staff account can open, without raising for All Jobs."""
    query='SELECT a.* FROM attachments a WHERE a.lead_id=? AND a.deleted_at IS NULL'
    params=[lid]
    if u['role']=='crew' and not can_plan_jobs(db,u):
        query+=""" AND EXISTS (SELECT 1 FROM jobs j JOIN assignments own ON own.job_id=j.id
            WHERE j.lead_id=a.lead_id AND own.resource_type='employee' AND own.resource_id=?)
            AND (a.job_id IS NULL OR EXISTS (SELECT 1 FROM assignments own
                WHERE own.job_id=a.job_id AND own.resource_type='employee' AND own.resource_id=?))"""
        params.extend([u.get('employee_id') or '']*2)
    return rows(db,query+' ORDER BY a.created_at DESC,a.id',params)

def assigned(db,jid,u):
    j=one(db,'SELECT * FROM jobs WHERE id=?',(jid,))
    if not j:raise HTTPException(404,'Job not found.')
    if u['role']=='crew' and not can_plan_jobs(db,u) and not one(db,"SELECT job_id FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id') or '')):
        raise HTTPException(403,'Only the assigned team can change this job.')
    return j

def invoice_job(db,jid,u):
    """Planning authority alone never grants a Crew account another team's billing."""
    j=assigned(db,jid,u)
    if u['role']=='crew' and not one(db,"SELECT job_id FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(jid,u.get('employee_id') or '')):
        raise HTTPException(403,'Only the assigned crew can invoice this move.')
    return j

def project_write(db,lid,jid,u):
    l=staff_read_project(db,lid,u)
    if jid or (u['role']=='crew' and not can_plan_jobs(db,u)):
        j=assigned(db,jid,u)
        if j['lead_id']!=lid:raise HTTPException(403,'Job belongs to another project.')
    return l

def stamp_hash(v):return hashlib.sha256(v.encode()).hexdigest()

def secret_box():
    from cryptography.fernet import Fernet
    key=os.getenv('CROWNOPS_SECRET_KEY','')
    if not key:raise HTTPException(503,'Server encryption key is not configured. Configure CROWNOPS_SECRET_KEY privately.')
    try:return Fernet(key.encode())
    except ValueError:raise HTTPException(503,'Invalid server encryption key.')

def encrypt(v):return secret_box().encrypt(dumps(v).encode()).decode()
def decrypt(v):return json.loads(secret_box().decrypt(v.encode()))

def live_enabled():return os.getenv('CROWNOPS_ALLOW_EXTERNAL','0')=='1'

def uploads_enabled():
    default='0' if os.getenv('CROWNOPS_ENV','test')=='production' else '1'
    return os.getenv('CROWNOPS_UPLOADS_ENABLED',default)=='1'

def external_gate():
    if not live_enabled():raise HTTPException(503,'External actions are disabled. Enable them on the authorized test/server installation first.')

def square_deposit_environment():
    env=os.getenv('SQUARE_ENV','sandbox').strip().lower()
    if env not in ('sandbox','production'):
        raise HTTPException(503,'SQUARE_ENV must be sandbox or production.')
    return env

def square_sandbox_test_recipients():
    return {email.strip().casefold() for email in re.split(r'[,;\n]',os.getenv('CROWNOPS_SQUARE_SANDBOX_TEST_EMAILS','')) if email.strip()}

def square_deposits_enabled(client_email=''):
    try:env=square_deposit_environment()
    except HTTPException:return False
    if live_enabled():return True
    return env=='sandbox' and os.getenv('CROWNOPS_ALLOW_SQUARE_SANDBOX_DEPOSITS','0')=='1' and (client_email or '').strip().casefold() in square_sandbox_test_recipients()

def square_deposit_gate(client_email):
    env=square_deposit_environment()
    if not square_deposits_enabled(client_email):
        raise HTTPException(503,'Card deposits are not enabled for this project. Ask Crown for its approved payment instructions.')
    return env

def square_connection_status():
    env=os.getenv('SQUARE_ENV','sandbox').strip().lower()
    valid=env in ('sandbox','production')
    token=bool(os.getenv('SQUARE_ACCESS_TOKEN'))
    location=bool(os.getenv('SQUARE_LOCATION_ID'))
    sandbox_tests=os.getenv('CROWNOPS_ALLOW_SQUARE_SANDBOX_DEPOSITS','0')=='1'
    recipients=len(square_sandbox_test_recipients())
    return {'configured':token and location,'environment':env,'environment_valid':valid,
        'access_token_present':token,'location_id_present':location,
        'webhook_signature_key_present':bool(os.getenv('SQUARE_WEBHOOK_SIGNATURE_KEY')),
        'webhook_url_present':bool(os.getenv('SQUARE_WEBHOOK_URL')),
        'external_actions_enabled':live_enabled(),'sandbox_tests_enabled':sandbox_tests,
        'sandbox_test_recipient_count':recipients,
        'deposit_creation_enabled':valid and (live_enabled() or (env=='sandbox' and sandbox_tests and recipients>0))}

def quickbooks_environment():
    env=os.getenv('QBO_ENV','sandbox').strip().lower()
    if env not in ('sandbox','production'):
        raise HTTPException(503,'QBO_ENV must be sandbox or production.')
    return env

def quickbooks_write_enabled():
    env=quickbooks_environment()
    if env=='sandbox':
        return os.getenv('CROWNOPS_ALLOW_QBO_SANDBOX_WRITES','0')=='1'
    return os.getenv('CROWNOPS_ENV','test')=='production' and os.getenv('CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES','0')=='1'

def quickbooks_gate():
    from .quote_staging import deny_side_effects
    deny_side_effects()
    env=quickbooks_environment()
    if not quickbooks_write_enabled():
        flag='CROWNOPS_ALLOW_QBO_SANDBOX_WRITES' if env=='sandbox' else 'CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES'
        raise HTTPException(503,f'QuickBooks {env} writes are disabled. Enable {flag}=1 only after mappings and tests are approved.')

def audit_safe_error(e):
    return 'Provider request failed. Review connection, permissions and provider dashboard; no success was confirmed.'

def pending_received(db,iid):
    return db.execute('''SELECT coalesce(sum(p.amount_cents),0) FROM field_collections c JOIN payments p ON p.id=c.payment_id
     WHERE c.invoice_id=? AND p.verified=0 AND NOT EXISTS(SELECT 1 FROM payments r WHERE r.reverses_id=p.id)''',(iid,)).fetchone()[0]

def verified_allocated(db,iid):
    return db.execute('''SELECT coalesce(sum(a.amount_cents),0) FROM invoice_payment_allocations a JOIN payments p ON p.id=a.payment_id
      WHERE a.invoice_id=? AND p.verified=1 AND NOT EXISTS(SELECT 1 FROM payments r WHERE r.reverses_id=p.id)''',(iid,)).fetchone()[0]

def invoice_current(db,iid):
    from .invoices import calculate_invoice
    inv=one(db,'SELECT * FROM job_invoices WHERE id=?',(iid,))
    if not inv:raise HTTPException(404,'Invoice not found.')
    calc=calculate_invoice(json.loads(inv['terms_json']),json.loads(inv['actual_json']),verified_allocated(db,iid))
    return inv,calc

def business_day(at=None):
    moment=datetime.fromisoformat(at) if isinstance(at,str) else at or datetime.now(timezone.utc)
    return moment.astimezone(ZoneInfo('America/Toronto')).date().isoformat()

def add_task(db,lid,title,owner='',key=None):
    if key and one(db,'SELECT id FROM tasks WHERE legacy_id=?',(key,)):return
    insert(db,'tasks',dict(id=uid('TASK'),legacy_id=key,lead_id=lid,title=title,due_date=business_day(),owner=owner,created_at=now()))

def queue_message(db,lid,subject,body,category,actor,key,jid=None,version=None):
    l=one(db,'SELECT * FROM leads WHERE id=?',(lid,))
    if not l or not l['email']:raise HTTPException(422,'A valid client email is required.')
    old=one(db,'SELECT * FROM outbound_messages WHERE event_key=?',(key,))
    if old:return old
    r=dict(id=uid('MAIL'),event_key=key,lead_id=lid,job_id=jid,subject=subject,body=body,recipient=l['email'],category=category,
           source_version=version,created_by=actor,created_at=now())
    insert(db,'outbound_messages',r)
    return r
