"""Staff account administration and narrowly authorized recovery emails."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlparse
import base64
import os
import re
import secrets
import requests
from fastapi import BackgroundTasks, Depends, HTTPException, Request
from .store import connect, transaction, one, rows, insert, audit, now, uid, dumps
from .platform_common import settings, put_setting, stamp_hash, secret_box, decrypt, require_role


def master_owner_id(db):
    saved = settings(db, 'master_owner', {}) or {}
    if saved.get('user_id'):
        return saved['user_id']
    first = one(db, "SELECT id FROM users WHERE role='owner' ORDER BY created_at,id LIMIT 1")
    return first['id'] if first else None


def remember_master_owner(db):
    owner = master_owner_id(db)
    if owner and not (settings(db, 'master_owner', {}) or {}).get('user_id'):
        put_setting(db, 'master_owner', {'user_id': owner})
    return owner


def protect_owner_account(db, actor, target):
    if target['id'] == master_owner_id(db) and actor['id'] != target['id']:
        raise HTTPException(403, 'Only the master owner can manage the master owner account.')
    if target['role'] == 'owner' and actor['id'] != target['id'] and actor['id'] != master_owner_id(db):
        raise HTTPException(403, 'Only the master owner can manage another owner account.')


def recovery_email_status(path, public_url):
    enabled = os.getenv('CROWNOPS_ALLOW_AUTH_EMAIL', '0') == '1'
    allowed = {x.strip().lower() for x in os.getenv('CROWNOPS_AUTH_EMAIL_TEST_ALLOWLIST', '').split(',') if x.strip()}
    production = os.getenv('CROWNOPS_ENV', 'test') == 'production'
    encrypted = False
    with closing(connect(path)) as db:
        credential = one(db, "SELECT ciphertext FROM oauth_credentials WHERE provider='gmail'")
    if credential:
        try:
            secret_box()
            token = decrypt(credential['ciphertext'])
            encrypted = bool(token.get('access_token') or token.get('refresh_token'))
        except Exception:
            pass
    https = urlparse(public_url).scheme == 'https'
    configured = bool(os.getenv('GOOGLE_CLIENT_ID') and os.getenv('GOOGLE_CLIENT_SECRET'))
    ready = bool(enabled and credential and encrypted and configured and https and (production or allowed))
    return {'available': ready, 'enabled': enabled, 'gmail_connected': bool(credential),
            'credentials_ready': encrypted and configured, 'https': https,
            'mode': 'all active staff accounts' if production else 'approved test email addresses',
            'test_allowlist_configured': bool(allowed)}


def recovery_email_allowed(path, public_url, email):
    if not recovery_email_status(path, public_url)['available']:
        return False
    if os.getenv('CROWNOPS_ENV', 'test') == 'production':
        return True
    allowed = {x.strip().lower() for x in os.getenv('CROWNOPS_AUTH_EMAIL_TEST_ALLOWLIST', '').split(',') if x.strip()}
    return email.lower() in allowed


def recovery_rate_limit(db, request, email='', purpose='request'):
    cutoff = (datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat(timespec='seconds')
    db.execute('DELETE FROM account_recovery_attempts WHERE attempted_at<?', (cutoff,))
    ip = stamp_hash(request.client.host if request.client else 'unknown')
    email_hash = stamp_hash(email.lower()) if email else ''
    count = db.execute('SELECT count(*) FROM account_recovery_attempts WHERE purpose=? AND ip_hash=? AND attempted_at>=?', (purpose, ip, cutoff)).fetchone()[0]
    limit = 10 if purpose == 'request' else 20
    if count >= limit:
        return False
    if email_hash and db.execute('SELECT count(*) FROM account_recovery_attempts WHERE purpose=? AND email_hash=? AND attempted_at>=?', (purpose,email_hash,cutoff)).fetchone()[0] >= 3:
        return False
    insert(db, 'account_recovery_attempts', {'purpose':purpose,'ip_hash':ip,'email_hash':email_hash,'attempted_at':now()})
    return True


def send_account_email(path, person, public_url, body, subject, message_id):
    """This adapter cannot send client drafts or accept an arbitrary recipient."""
    with closing(connect(path)) as db:
        person = one(db, 'SELECT id,email FROM users WHERE id=? AND active=1', (person['id'],))
    if not person:
        raise RuntimeError('This staff account is no longer active.')
    if not recovery_email_allowed(path, public_url, person['email']):
        raise RuntimeError('Account emails are not enabled for this staff account.')
    from .providers import provider_token
    token = provider_token(path, 'gmail', require_external=False)
    headers = {'Authorization': 'Bearer '+token['access_token']}
    profile = requests.get('https://gmail.googleapis.com/gmail/v1/users/me/profile', headers=headers, timeout=10)
    profile.raise_for_status()
    sender = profile.json().get('emailAddress', '')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', sender):
        raise RuntimeError('Gmail sender identity was not confirmed.')
    message = EmailMessage()
    message['From'] = sender
    message['To'] = person['email']
    message['Subject'] = subject
    message['Message-ID'] = '<'+message_id.lower()+'@crownops.local>'
    message.set_content(body)
    response = requests.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                             json={'raw':base64.urlsafe_b64encode(message.as_bytes()).decode()}, headers=headers, timeout=20)
    response.raise_for_status()
    provider_id = response.json().get('id')
    if not provider_id:
        raise RuntimeError('Gmail did not confirm account email delivery.')
    return provider_id


def deliver_recovery(path, email, public_url):
    # Runs after the public response; account existence does not affect its body.
    with closing(connect(path)) as db:
        person = one(db, 'SELECT id,name,email,role,active FROM users WHERE email=? AND active=1', (email,))
    if not person or not recovery_email_allowed(path, public_url, person['email']):
        return
    raw = secrets.token_urlsafe(40)
    key = uid('AUTHMAIL')
    with transaction(path) as db:
        if not one(db, 'SELECT id FROM users WHERE id=? AND active=1', (person['id'],)):
            return
        db.execute('DELETE FROM account_resets WHERE user_id=?', (person['id'],))
        insert(db, 'account_resets', {'token_hash':stamp_hash(raw),'user_id':person['id'],
                                     'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(timespec='seconds')})
        insert(db, 'provider_operations', {'id':key,'provider':'gmail','object_id':person['id'],
                                           'kind':'Staff password recovery','status':'Running','created_at':now(),'updated_at':now()})
    body = ('A password reset was requested for your CrownOps account.\n\n'
            'Use this private link within 30 minutes to choose a new password:\n'
            +public_url+'/#reset='+raw+'\n\n'
            'The link works once. Your existing sign-ins will end after the reset. '
            'Your authenticator is still required if enabled.\n\n'
            'If you did not request this, ignore the email and contact your CrownOps owner.\n\nCrown Movers')
    try:
        provider_id = send_account_email(path, person, public_url, body, 'Reset your CrownOps password', key)
        with transaction(path) as db:
            db.execute("UPDATE provider_operations SET status='Completed',result_json=?,updated_at=? WHERE id=?", (dumps({'provider_id':provider_id}),now(),key))
            audit(db, person['id'], 'Password recovery email sent', 'users', person['id'])
    except Exception:
        with transaction(path) as db:
            db.execute("UPDATE provider_operations SET status='Unknown',error=?,updated_at=? WHERE id=?", ('Account email delivery was not confirmed. Check Gmail Sent before retrying.',now(),key))
            audit(db, person['id'], 'Password recovery email not confirmed', 'users', person['id'])


def notify_password_reset(path, user_id, public_url):
    with closing(connect(path)) as db:
        person = one(db, 'SELECT id,name,email,role,active FROM users WHERE id=? AND active=1', (user_id,))
    if not person or not recovery_email_allowed(path, public_url, person['email']):
        return
    try:
        send_account_email(path, person, public_url,
            'Your CrownOps password was changed. All previous sign-ins have ended.\n\n'
            'If you did not make this change, contact the owner immediately. '
            'Your authenticator protection remains in place.\n\nCrown Movers',
            'Your CrownOps password was changed', uid('AUTHNOTICE'))
    except Exception:
        with transaction(path) as db:
            audit(db, person['id'], 'Password change notification not confirmed', 'users', person['id'])


def register_account_access(app, path, auth, public_url):
    with transaction(path) as db:
        remember_master_owner(db)

    @app.get('/api/recovery-status')
    def public_status():
        return {'available':recovery_email_status(path, public_url)['available']}

    @app.post('/api/forgot-password')
    def forgot(data:dict, request:Request, background:BackgroundTasks):
        email = data.get('email', '')
        if not isinstance(email, str) or len(email)>254:
            raise HTTPException(422, 'Enter the email used for your staff invitation.')
        email = email.strip().lower()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise HTTPException(422, 'Enter the email used for your staff invitation.')
        with transaction(path) as db:
            allowed = recovery_rate_limit(db, request, email)
        if not allowed:
            raise HTTPException(429, 'Too many recovery requests. Wait 15 minutes and try again.')
        if not recovery_email_status(path, public_url)['available']:
            raise HTTPException(503, 'Email recovery is not enabled yet. Ask the owner for a private reset link.')
        background.add_task(deliver_recovery, path, email, public_url)
        return {'ok':True,'message':'If an active account is eligible for email recovery, a private reset link will be sent. Check your inbox and spam folder. If it does not arrive, contact the owner.'}

    @app.get('/api/account-access')
    def access(u=Depends(auth)):
        require_role(u, {'owner'})
        with closing(connect(path)) as db:
            master = master_owner_id(db)
            accounts = rows(db, 'SELECT id,name,email,role,active,employee_id FROM users ORDER BY name')
            invitations = rows(db, 'SELECT token_hash id,name,email,role,employee_id,expires_at FROM invitations WHERE used_at IS NULL AND expires_at>? ORDER BY expires_at DESC', (now(),))
        for person in accounts:
            person['master_owner'] = person['id'] == master
            person['can_manage'] = person['id'] != u['id'] and not person['master_owner'] and (person['role'] != 'owner' or u['id'] == master)
        return {'accounts':accounts,'invitations':invitations,'master_owner_id':master,
                'is_master_owner':u['id']==master,'email_recovery':recovery_email_status(path, public_url)}

    @app.post('/api/account-access/invitations/{invitation_id}/revoke')
    def revoke_invitation(invitation_id:str, u=Depends(auth)):
        require_role(u, {'owner'})
        with transaction(path) as db:
            invite = one(db, 'SELECT * FROM invitations WHERE token_hash=? AND used_at IS NULL', (invitation_id,))
            if not invite:
                raise HTTPException(404, 'Pending invitation not found.')
            if invite['role'] == 'owner' and u['id'] != master_owner_id(db):
                raise HTTPException(403, 'Only the master owner can manage owner invitations.')
            db.execute('DELETE FROM invitations WHERE token_hash=?', (invitation_id,))
            audit(db, u['id'], 'Staff invitation revoked', 'invitations', invite['email'])
        return {'ok':True}
