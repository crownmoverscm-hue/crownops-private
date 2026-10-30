"""Prepared, default-disabled staging-only credential reissue.
Exact Muse-verified target IDs are in the manifest; execution remains default-disabled.
No provider clients or email hooks.
Never select existing credential material; accept replacements only in the owner form.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import re
import secrets
import tempfile

from argon2 import PasswordHasher
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse
from .account_access import master_owner_id
from .estimate_delivery import digest
from .store import audit, connect, dumps, now, one, rows, transaction

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / 'config' / 'wix_synthetic_accounts_verified.json'
EXPECTED = {
    'wix-pilot-nonmaster-owner@example.invalid': ('owner', 1),
    'wix-pilot-manager@example.invalid': ('manager', 1),
    'wix-pilot-sales@example.invalid': ('sales', 1),
    'wix-pilot-crew@example.invalid': ('crew', 1),
    'wix-pilot-inactive@example.invalid': ('sales', 0),
}
PREFIX = '/api/wix-drafts/credential-reissue'


def scope(path):
    # The test exception cannot be used on the mounted staging database or Railway.
    if (os.getenv('CROWNOPS_ENV') == 'test'
            and not os.getenv('RAILWAY_SERVICE_ID')
            and Path(path).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())):
        return
    from .quote_staging import require_isolated, DATABASE
    require_isolated()
    if Path(path).resolve() != Path(DATABASE):
        raise HTTPException(503, 'Exact isolated staging database required.')


def owner(db, user):
    person = one(db, 'SELECT id,role,active FROM users WHERE id=?', (user['id'],))
    if not person or not person['active'] or person['role'] != 'owner' or person['id'] != master_owner_id(db):
        raise HTTPException(403, 'Active Master owner required.')
    if not one(db, 'SELECT user_id FROM sessions WHERE user_id=? AND csrf=? AND expires_at>?',
               (user['id'], user.get('csrf', ''), now())):
        raise HTTPException(403, 'Current owner session required.')


def verified_manifest():
    try:
        data = json.loads(MANIFEST_PATH.read_text())
        accounts = data.get('accounts')
        if (data.get('status') != 'OWNER_REVIEWED_MUSE_METADATA'
                or not isinstance(data.get('verification_reference'), str)
                or len(data['verification_reference'].strip()) < 10
                or not isinstance(accounts, list) or len(accounts) != 5):
            return None
        if {a['email'] for a in accounts} != set(EXPECTED):
            return None
        ids = set()
        for a in accounts:
            if set(a) != {'id', 'email', 'role', 'active'}:
                return None
            if (not isinstance(a['id'], str) or not a['id'] or a['id'] in ids
                    or type(a['active']) is not int
                    or (a['role'], a['active']) != EXPECTED[a['email']]):
                return None
            ids.add(a['id'])
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def inventory(db):
    result = []
    for email in EXPECTED:
        person = one(db, 'SELECT id,name,email,role,active,employee_id,created_at FROM users WHERE email=?', (email,))
        if not person:
            raise HTTPException(409, 'An expected account is missing; do not recreate it.')
        factor = one(db, 'SELECT enabled,created_at FROM auth_factors WHERE user_id=?', (person['id'],))
        latest = one(db, "SELECT MAX(id) AS event_id FROM audit WHERE object_type='users' AND object_id=?", (person['id'],))
        person.update(mfa_enabled=bool(factor and factor['enabled']),
                      mfa_present=bool(factor), mfa_created_at=factor['created_at'] if factor else None,
                      last_account_audit_id=latest['event_id'])
        result.append(person)
    return result


def confirmed_inventory(db, manifest):
    actual = inventory(db)
    approved = {a['email']: a for a in manifest['accounts']}
    master = master_owner_id(db)
    for person in actual:
        expected = approved.get(person['email'])
        if (not expected or person['id'] == master
                or any(person[k] != expected[k] for k in ('id', 'email', 'role', 'active'))):
            raise HTTPException(409, 'Account identity, role or active status differs from the reviewed manifest.')
    return actual


def mutation_gate(path, db, user):
    scope(path)
    owner(db, user)
    if os.getenv('CROWNOPS_WIX_TEST_REISSUE_ENABLED') != '1':
        raise HTTPException(503, 'Credential reissue is disabled; no reset is authorized.')
    manifest = verified_manifest()
    if not manifest:
        raise HTTPException(503, 'Five independently verified account IDs and owner review are required.')
    return manifest


def setup(path):
    with transaction(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS wix_test_credential_reissues(
            id TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL UNIQUE,
            inventory_hash TEXT NOT NULL, inventory_json TEXT NOT NULL,
            owner_id TEXT NOT NULL, reason TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('Planned','Completed')), completed_at TEXT);
        CREATE TRIGGER IF NOT EXISTS wix_test_reissue_no_delete
            BEFORE DELETE ON wix_test_credential_reissues
            BEGIN SELECT RAISE(ABORT,'Permanent reissue evidence'); END;
        CREATE TRIGGER IF NOT EXISTS wix_test_reissue_frozen
            BEFORE UPDATE ON wix_test_credential_reissues
            WHEN NEW.id IS NOT OLD.id OR NEW.manifest_hash IS NOT OLD.manifest_hash
              OR NEW.inventory_hash IS NOT OLD.inventory_hash OR NEW.inventory_json IS NOT OLD.inventory_json
              OR NEW.owner_id IS NOT OLD.owner_id OR NEW.reason IS NOT OLD.reason
              OR NEW.created_at IS NOT OLD.created_at OR NEW.expires_at IS NOT OLD.expires_at
              OR OLD.state!='Planned' OR NEW.state!='Completed' OR NEW.completed_at IS NULL
            BEGIN SELECT RAISE(ABORT,'Immutable or consumed reissue'); END;
        ''')


def prepare(path, user, data):
    if (set(data) != {'confirm', 'reason', 'inventory_hash'} or data.get('confirm') is not True
            or not isinstance(data.get('reason'), str) or not 10 <= len(data['reason'].strip()) <= 500):
        raise HTTPException(422, 'Explicit confirmation, current inventory hash and reason required.')
    with transaction(path) as db:
        manifest = mutation_gate(path, db, user)
        actual = confirmed_inventory(db, manifest)
        fingerprint = digest(actual)
        if data['inventory_hash'] != fingerprint:
            raise HTTPException(409, 'Account metadata changed; reopen preflight.')
        prior = one(db, 'SELECT id,state,expires_at,owner_id FROM wix_test_credential_reissues WHERE manifest_hash=?', (digest(manifest),))
        if prior:
            if prior['owner_id'] != user['id']:
                raise HTTPException(409, 'Existing operation belongs to a different owner.')
            if prior['state'] == 'Planned' and prior['expires_at'] <= now():
                raise HTTPException(409, 'Operation expired; a fresh reviewed manifest is required.')
            return {'operation_id': prior['id'], 'state': prior['state'], 'duplicate': True,
                    'expires_at': prior['expires_at'], 'credentials_changed': False}
        op = {'id': 'WIX-REISSUE-' + secrets.token_hex(16), 'manifest_hash': digest(manifest),
              'inventory_hash': fingerprint, 'inventory_json': dumps(actual), 'owner_id': user['id'],
              'reason': data['reason'].strip(), 'created_at': now(),
              'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec='seconds')}
        db.execute('''INSERT INTO wix_test_credential_reissues
            (id,manifest_hash,inventory_hash,inventory_json,owner_id,reason,created_at,expires_at,state)
            VALUES(:id,:manifest_hash,:inventory_hash,:inventory_json,:owner_id,:reason,:created_at,:expires_at,'Planned')''', op)
        audit(db, user['id'], 'Synthetic credential reissue preflight confirmed; no credentials changed',
              'wix_test_credential_reissues', op['id'], {'target_ids': [p['id'] for p in actual], 'inventory_hash': fingerprint})
    return {'operation_id': op['id'], 'state': 'Planned', 'duplicate': False,
            'expires_at': op['expires_at'], 'credentials_changed': False}


def execute(path, user, data):
    # Deliberately avoid returning or interpolating any supplied credential values.
    if (set(data) != {'operation_id', 'confirm', 'passwords'} or data.get('confirm') is not True
            or not isinstance(data.get('operation_id'), str)
            or not re.fullmatch(r'WIX-REISSUE-[a-f0-9]{32}', data['operation_id'])):
        raise HTTPException(422, 'Exact operation and explicit confirmation required.')
    with transaction(path) as db:
        manifest = mutation_gate(path, db, user)
        op = one(db, 'SELECT * FROM wix_test_credential_reissues WHERE id=?', (data['operation_id'],))
        if not op or op['owner_id'] != user['id'] or op['manifest_hash'] != digest(manifest):
            raise HTTPException(409, 'Operation is missing or does not match the reviewed owner and accounts.')
        if op['state'] == 'Completed':
            return {'operation_id': op['id'], 'state': 'Completed', 'duplicate': True,
                    'new_changes': False, 'notifications_sent': 0, 'provider_writes': 0}
        if op['expires_at'] <= now():
            raise HTTPException(409, 'Operation expired; no passwords changed.')
        actual = confirmed_inventory(db, manifest)
        if digest(actual) != op['inventory_hash']:
            raise HTTPException(409, 'Account or MFA metadata changed; no passwords changed.')
        items = data.get('passwords')
        if (not isinstance(items, list) or len(items) != 5
                or any(not isinstance(p, dict) or set(p) != {'id', 'password', 'confirmation'} for p in items)):
            raise HTTPException(422, 'Exactly five password-and-confirmation entries are required.')
        if (len({p['id'] for p in items if isinstance(p['id'], str)}) != 5
                or {p['id'] for p in items if isinstance(p['id'], str)} != {a['id'] for a in actual}):
            raise HTTPException(422, 'Password targets must exactly match the five reviewed IDs.')
        values = [p['password'] for p in items]
        if (any(not isinstance(v, str) or not 16 <= len(v) <= 200 for v in values)
                or any(p['password'] != p['confirmation'] for p in items)
                or len(set(values)) != 5):
            raise HTTPException(422, 'Use five distinct passwords of 16-200 characters with matching confirmations.')
        hasher = PasswordHasher()
        for item in items:
            # UPDATE only: existing credential values are never read by this mechanism.
            db.execute('UPDATE users SET password_hash=? WHERE id=?', (hasher.hash(item['password']), item['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?', (item['id'],))
            db.execute('DELETE FROM account_resets WHERE user_id=?', (item['id'],))
            audit(db, user['id'], 'Synthetic account credential reissued; sessions and recovery grants revoked',
                  'users', item['id'], {'operation_id': op['id'], 'role_and_active_preserved': True, 'mfa_preserved': True})
        db.execute("UPDATE wix_test_credential_reissues SET state='Completed',completed_at=? WHERE id=?", (now(), op['id']))
        audit(db, user['id'], 'Synthetic credential reissue completed; no messages or providers',
              'wix_test_credential_reissues', op['id'], {'target_ids': [a['id'] for a in actual]})
    return {'operation_id': op['id'], 'state': 'Completed', 'duplicate': False,
            'new_changes': True, 'notifications_sent': 0, 'provider_writes': 0}


def register(app, path, auth):
    if os.getenv('CROWNOPS_ENV') not in ('test', 'staging'):
        return
    setup(path)

    def check(user):
        scope(path)
        with closing(connect(path)) as db:
            owner(db, user)

    @app.get('/wix-test-credential-reissue')
    def page(user=Depends(auth)):
        check(user)
        return FileResponse(ROOT / 'app' / 'static' / 'wix_test_credential_reissue.html', headers={'Cache-Control': 'no-store'})

    @app.get(PREFIX + '/preflight')
    def preflight(user=Depends(auth)):
        check(user)
        with closing(connect(path)) as db:
            actual = inventory(db)
            manifest = verified_manifest()
            if manifest:
                actual = confirmed_inventory(db, manifest)
            prior = one(db, 'SELECT id,state,expires_at,completed_at,owner_id,inventory_hash FROM wix_test_credential_reissues WHERE manifest_hash=?', (digest(manifest),)) if manifest else None
        return {'accounts': actual, 'inventory_hash': digest(actual), 'verified_manifest': bool(manifest),
                'verification_reference': manifest['verification_reference'] if manifest else None,
                'can_prepare': bool(manifest and os.getenv('CROWNOPS_WIX_TEST_REISSUE_ENABLED') == '1' and not prior),
                'can_execute': bool(manifest and os.getenv('CROWNOPS_WIX_TEST_REISSUE_ENABLED') == '1' and prior
                                    and prior['state'] == 'Planned' and prior['expires_at'] > now()
                                    and prior['owner_id'] == user['id'] and prior['inventory_hash'] == digest(actual)),
                'operation': prior, 'notifications_sent': 0, 'provider_writes': 0,
                'boundary': 'Only five local credentials; roles, active status, MFA and business records unchanged.'}

    @app.post(PREFIX + '/plan')
    def plan(data: dict, user=Depends(auth)):
        check(user)
        return prepare(path, user, data)

    @app.post(PREFIX + '/execute')
    async def run(request: Request, user=Depends(auth)):
        check(user)
        try:
            try:
                data = await request.json()
            except ValueError:
                raise HTTPException(422, 'Invalid credential reissue request body.') from None
            if not isinstance(data, dict):
                raise HTTPException(422, 'Invalid credential reissue request body.')
            return execute(path, user, data)
        except HTTPException:
            raise
        except Exception:
            # No raw exception, payload or new credential material in the response.
            raise HTTPException(503, 'Completion was not confirmed. Inspect operation state before any retry.') from None
