"""Staff account administration and narrowly authorized recovery emails."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlparse
import base64
import json
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


def access_revision(person):
    return stamp_hash(dumps({key:person.get(key) for key in ('id','role','active','employee_id')}))


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


def _company_name(path):
    from .company_profile import company_profile
    with closing(connect(path)) as db:
        return company_profile(db)['name']


def send_account_email(path, person, public_url, body, subject, message_id):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("send account email")
    """This adapter cannot send client drafts or accept an arbitrary recipient."""
    with closing(connect(path)) as db:
        person = one(db, 'SELECT id,email FROM users WHERE id=? AND active=1', (person['id'],))
    if not person:
        raise RuntimeError('This staff account is no longer active.')
    return _send_account_message(path, person['email'], public_url, body, subject, message_id)


def _send_account_message(path, recipient, public_url, body, subject, message_id, html=None):
    from .core_v1 import deny_if_enabled
    deny_if_enabled(" send account message")
    from .company_profile import ready_profile
    with closing(connect(path)) as db:
        ready_profile(db, 'sending staff account email')
    # Called only after the staff account or pending invitation is resolved from the database.
    if not recovery_email_allowed(path, public_url, recipient):
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
    message['To'] = recipient
    message['Subject'] = subject
    message['Message-ID'] = '<'+message_id.lower()+'@crownops.local>'
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype='html')
    response = requests.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                             json={'raw':base64.urlsafe_b64encode(message.as_bytes()).decode()}, headers=headers, timeout=20)
    response.raise_for_status()
    provider_id = response.json().get('id')
    if not provider_id:
        raise RuntimeError('Gmail did not confirm account email delivery.')
    return provider_id


def check_invitation_email(path, public_url, email):
    if not recovery_email_status(path, public_url)['available']:
        raise HTTPException(503, 'Staff emails are not enabled yet. Connect Gmail with sending permission and enable account emails, or choose Create link only.')
    if not recovery_email_allowed(path, public_url, email):
        raise HTTPException(403, 'This invitation email is not on the staging test allowlist. Add this exact address to the approved account-email test addresses, or choose Create link only.')


def invitation_request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', value):
        raise HTTPException(422, 'Reopen the invitation form and try again.')
    return value


def invitation_fingerprint(person):
    return stamp_hash(dumps({key:person.get(key) for key in ('name','email','role','employee_id')}))


def previous_invitation_email(db, request_id, actor, person=None):
    operation = one(db, "SELECT * FROM provider_operations WHERE id=? AND kind='Staff invitation'", ('INVMAIL-'+request_id,))
    if operation:
        meta = json.loads(operation['result_json'])
        if meta.get('actor_id') != actor['id']:
            raise HTTPException(403, 'This invitation request belongs to another owner.')
        if person and meta.get('fingerprint') != invitation_fingerprint(person):
            raise HTTPException(409, 'This invitation request has changed. Reopen the form to create a new invitation.')
    return operation


def invitation_email_result(operation):
    meta = json.loads(operation['result_json'])
    return {'sent':operation['status']=='Completed', 'delivery_status':operation['status'],
            'email_requested':True, 'recipient':meta['email'], 'name':meta['name'], 'role':meta['role'],
            'invitation_id':operation['object_id'], 'expires_at':meta['expires_at'],
            'url':None, 'email_preview':None}


def reserve_invitation_email(db, invitation, request_id, actor):
    old = previous_invitation_email(db, request_id, actor, invitation)
    if old:
        return old
    # One send attempt per link, even if a second form uses a different request ID.
    old = one(db, "SELECT * FROM provider_operations WHERE provider='gmail' AND kind='Staff invitation' AND object_id=? ORDER BY created_at DESC LIMIT 1", (invitation['token_hash'],))
    if old:
        return old
    meta = {key:invitation[key] for key in ('name','email','role','expires_at')}
    meta.update(actor_id=actor['id'], fingerprint=invitation_fingerprint(invitation))
    operation = {'id':'INVMAIL-'+request_id,'provider':'gmail','object_id':invitation['token_hash'],
                 'kind':'Staff invitation','status':'Pending','result_json':dumps(meta),
                 'created_at':now(),'updated_at':now()}
    insert(db, 'provider_operations', operation)
    return operation


def send_invitation_email(path, invitation_id, raw_token, public_url, message_id):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("send invitation email")
    from .staff_onboarding import invitation_content
    if stamp_hash(raw_token) != invitation_id:
        raise HTTPException(401, 'The private invitation link does not match.')
    from .company_profile import company_profile
    with closing(connect(path)) as db:
        person = one(db, 'SELECT * FROM invitations WHERE token_hash=? AND used_at IS NULL AND expires_at>?', (invitation_id,now()))
        company = company_profile(db)
    if not person:
        raise HTTPException(409, 'This invitation is no longer available. Create a new invitation if needed.')
    content = invitation_content(person, public_url, public_url+'/#invite='+raw_token, company)
    return _send_account_message(path, person['email'], public_url, content['body'], content['subject'], message_id, content['html'])


def deliver_invitation_email(path, invitation_id, raw_token, public_url, request_id, actor):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("deliver invitation email")
    invitation_request_id(request_id)
    require_role(actor, {'owner','manager'})
    with transaction(path) as db:
        previous = previous_invitation_email(db, request_id, actor)
        if previous and previous['object_id'] != invitation_id:
            raise HTTPException(409, 'This request belongs to a different invitation.')
        invitation = one(db, 'SELECT * FROM invitations WHERE token_hash=?', (invitation_id,))
        if not invitation or stamp_hash(raw_token) != invitation_id:
            raise HTTPException(404, 'Private invitation not found.')
        if invitation['role']=='owner' and actor['id']!=master_owner_id(db):
            raise HTTPException(403, 'Only the master owner can send owner invitations.')
        if previous and previous['status']!='Pending':
            return invitation_email_result(previous)
        if invitation['used_at'] or invitation['expires_at']<=now():
            raise HTTPException(409, 'This invitation was used or expired. Do not resend it.')
        if one(db, 'SELECT id FROM users WHERE email=?', (invitation['email'],)):
            raise HTTPException(409, 'An account already exists for this email. Use password recovery if needed.')
        check_invitation_email(path, public_url, invitation['email'])
        operation = reserve_invitation_email(db, invitation, request_id, actor)
        if operation['status']!='Pending':
            return invitation_email_result(operation)
        db.execute("UPDATE provider_operations SET status='Running',updated_at=? WHERE id=? AND status='Pending'", (now(),operation['id']))
    try:
        provider_id = send_invitation_email(path, invitation_id, raw_token, public_url, operation['id'])
        with transaction(path) as db:
            meta = json.loads(operation['result_json']); meta['provider_id'] = provider_id
            db.execute("UPDATE provider_operations SET status='Completed',result_json=?,updated_at=? WHERE id=?", (dumps(meta),now(),operation['id']))
            audit(db, actor['id'], 'Staff invitation email sent', 'invitations', invitation_id)
    except Exception:
        with transaction(path) as db:
            db.execute("UPDATE provider_operations SET status='Unknown',error=?,updated_at=? WHERE id=?", ('Invitation email sending was not confirmed. Check Gmail Sent and the recipient inbox before creating another invitation.',now(),operation['id']))
            audit(db, actor['id'], 'Staff invitation email not confirmed', 'invitations', invitation_id)
    with closing(connect(path)) as db:
        return invitation_email_result(one(db, 'SELECT * FROM provider_operations WHERE id=?', (operation['id'],)))


def deliver_recovery(path, email, public_url):
    from .core_v1 import deny_if_enabled
    deny_if_enabled("deliver recovery")
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
            'If you did not request this, ignore the email and contact your CrownOps owner.\n\n'+_company_name(path))
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
    from .core_v1 import deny_if_enabled
    deny_if_enabled("notify password reset")
    with closing(connect(path)) as db:
        person = one(db, 'SELECT id,name,email,role,active FROM users WHERE id=? AND active=1', (user_id,))
    if not person or not recovery_email_allowed(path, public_url, person['email']):
        return
    try:
        send_account_email(path, person, public_url,
            'Your CrownOps password was changed. All previous sign-ins have ended.\n\n'
            'If you did not make this change, contact the owner immediately. '
            'Your authenticator protection remains in place.\n\n'+_company_name(path),
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
        require_role(u, {'owner','manager'})
        with closing(connect(path)) as db:
            master = master_owner_id(db)
            accounts = rows(db, 'SELECT id,name,email,role,active,employee_id FROM users ORDER BY name')
            invitations = rows(db, """SELECT token_hash id,name,email,role,employee_id,expires_at,
                (SELECT status FROM provider_operations p WHERE p.kind='Staff invitation' AND p.object_id=invitations.token_hash ORDER BY p.created_at DESC LIMIT 1) email_status
                FROM invitations WHERE used_at IS NULL AND expires_at>? ORDER BY expires_at DESC""", (now(),))
        for person in accounts:
            person['master_owner'] = person['id'] == master
            person['can_manage'] = person['id'] != u['id'] and not person['master_owner'] and (person['role'] != 'owner' or u['id'] == master)
            person['access_revision'] = access_revision(person)
        return {'accounts':accounts,'invitations':invitations,'master_owner_id':master,
                'is_master_owner':u['id']==master,'email_recovery':recovery_email_status(path, public_url)}

    @app.post('/api/users/{user_id}/access')
    def update_access(user_id:str, data:dict, u=Depends(auth)):
        require_role(u, {'owner','manager'})
        from .main import ROLES
        role, active = data.get('role'), data.get('active')
        employee = data.get('employee_id')
        revision = data.get('expected_revision')
        if not isinstance(role,str) or role not in ROLES or not isinstance(active,bool):
            raise HTTPException(422, 'Choose an access role and whether access is active.')
        if employee is not None and (not isinstance(employee,str) or len(employee)>150):
            raise HTTPException(422, 'Choose an employee record.')
        employee = employee or None
        if not isinstance(revision,str) or len(revision)!=64:
            raise HTTPException(422, 'Refresh Team access and open this account again.')
        with transaction(path) as db:
            # Recheck authority inside the write transaction in case another owner changed it.
            actor = one(db, 'SELECT * FROM users WHERE id=? AND active=1', (u['id'],))
            if not actor or actor['role'] not in ('owner','manager') or not one(db,
                    'SELECT user_id FROM sessions WHERE user_id=? AND csrf=? AND expires_at>?',
                    (u['id'],u['csrf'],now())):
                raise HTTPException(403, 'Your account no longer has owner access. Sign in again.')
            target = one(db, 'SELECT * FROM users WHERE id=?', (user_id,))
            if not target:
                raise HTTPException(404, 'Account not found.')
            protect_owner_account(db, actor, target)
            if user_id==actor['id']:
                raise HTTPException(422, 'Another authorized owner must change your role or access.')
            if role=='owner' and actor['id']!=master_owner_id(db):
                raise HTTPException(403, 'Only the master owner can assign Owner access.')
            if revision!=access_revision(target):
                raise HTTPException(409, 'This account changed while you were editing it. Refresh Team access and review it again.')
            record = one(db, 'SELECT id,active FROM employees WHERE id=?', (employee,)) if employee else None
            if employee and not record:
                raise HTTPException(422, 'Employee record does not exist.')
            if role=='crew' and (not record or (active and not record['active'])):
                raise HTTPException(422, 'Link an active crew login to an active employee record first.')
            before = {key:target[key] for key in ('role','active','employee_id')}
            after = {'role':role,'active':int(active),'employee_id':employee}
            changed = before!=after
            if changed:
                db.execute('UPDATE users SET role=?,active=?,employee_id=? WHERE id=?',
                           (role,int(active),employee,user_id))
                db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
                db.execute('DELETE FROM account_resets WHERE user_id=?', (user_id,))
                db.execute('DELETE FROM invitations WHERE email=? AND used_at IS NULL', (target['email'],))
                action = 'Access restored' if active and not target['active'] else 'Access revoked' if not active and target['active'] else 'Account access changed'
                audit(db, actor['id'], action, 'users', user_id, {'before':before,'after':after})
        return {'ok':True,'changed':changed,'active':active,'role':role,'employee_id':employee,
                'restored':bool(changed and active and not target['active'])}

    @app.post('/api/account-access/invitation-preview')
    def preview_invitation(data:dict, u=Depends(auth)):
        require_role(u, {'owner','manager'})
        from .staff_onboarding import ROLE_LABELS, invitation_content
        role = data.get('role','crew')
        if not isinstance(role,str) or role not in ROLE_LABELS:
            raise HTTPException(422, 'Choose an access role.')
        with closing(connect(path)) as db:
            if role=='owner' and u['id']!=master_owner_id(db):
                raise HTTPException(403, 'Only the master owner can invite another owner.')
        person = {'role':role}
        for key, limit in (('name',150),('email',254)):
            value = data.get(key,'')
            if not isinstance(value,str) or len(value)>limit:
                raise HTTPException(422, 'Check the invitation name and email.')
            person[key] = value.strip()
        if person['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',person['email']):
            raise HTTPException(422, 'Enter a valid invitation email.')
        from .company_profile import company_profile
        with closing(connect(path)) as db:
            company = company_profile(db)
        return invitation_content(person, public_url, company=company)

    @app.post('/api/account-access/invitations/{invitation_id}/send-email')
    def email_invitation(invitation_id:str, data:dict, u=Depends(auth)):
        require_role(u, {'owner','manager'})
        raw = data.get('token','')
        if not isinstance(raw,str) or len(raw)>180:
            raise HTTPException(422, 'Check the private invitation link.')
        return deliver_invitation_email(path,invitation_id,raw,public_url,data.get('request_id'),u)

    @app.post('/api/account-access/invitations/{invitation_id}/revoke')
    def revoke_invitation(invitation_id:str, u=Depends(auth)):
        require_role(u, {'owner','manager'})
        with transaction(path) as db:
            invite = one(db, 'SELECT * FROM invitations WHERE token_hash=? AND used_at IS NULL', (invitation_id,))
            if not invite:
                raise HTTPException(404, 'Pending invitation not found.')
            if invite['role'] == 'owner' and u['id'] != master_owner_id(db):
                raise HTTPException(403, 'Only the master owner can manage owner invitations.')
            db.execute('DELETE FROM invitations WHERE token_hash=?', (invitation_id,))
            audit(db, u['id'], 'Staff invitation revoked', 'invitations', invite['email'])
        return {'ok':True}
