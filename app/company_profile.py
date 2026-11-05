"""Company identity used in client-facing communications.

CrownOps was built for one company. Every installation now reads its business
identity from the `company_profile` setting so another moving company never sends
clients another company's name, phone number or email address.

Installations without a stored profile keep the legacy Crown Movers identity so
existing client messages stay byte-identical. New installations store their own
profile at provisioning (`manage.py init --company-name ...`) or through the
Owner/Manager settings API. `configured` reports which of the two is in effect.
"""
import re
from contextlib import closing
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, HTTPException

from .platform_common import MANAGERS, OFFICE, put_setting, require_role, settings
from .store import audit, connect, now, transaction

SETTING_KEY = 'company_profile'

LEGACY_PROFILE = {
    'name': 'Crown Movers',
    'short_name': 'Crown',
    'phone': '514-606-4030',
    'phone_tel': '+15146064030',
    'email': 'info@crownmovers.ca',
    'timezone': 'America/Toronto',
    'local_time_label': 'Montréal time',
    'local_time_label_fr': 'heure de Montréal',
}

EMAIL = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')
PHONE_CHARS = re.compile(r'[0-9+()\-. ]+')
EDITABLE = ('name', 'short_name', 'phone', 'email', 'timezone', 'local_time_label', 'local_time_label_fr')


def _clean(value, label, limit, required=False):
    if value is None:
        value = ''
    if not isinstance(value, str) or len(value) > limit:
        raise HTTPException(422, f'{label} must be text of at most {limit} characters.')
    value = ' '.join(value.split())
    if required and not value:
        raise HTTPException(422, f'{label} is required.')
    if any(ch in value for ch in '<>\r\n'):
        raise HTTPException(422, f'{label} cannot contain angle brackets or line breaks.')
    return value


def phone_tel(phone):
    """Dialable form for tel: links. Ten-digit numbers use the North American +1 prefix."""
    digits = re.sub(r'\D', '', phone)
    if phone.strip().startswith('+'):
        return '+' + digits
    if len(digits) == 10:
        return '+1' + digits
    if len(digits) == 11 and digits.startswith('1'):
        return '+' + digits
    return digits


def validate_profile(data):
    if not isinstance(data, dict):
        raise HTTPException(422, 'Send the company profile as an object.')
    unknown = set(data) - set(EDITABLE) - {'version'}
    if unknown:
        raise HTTPException(422, 'Unknown company profile field: ' + sorted(unknown)[0] + '.')
    name = _clean(data.get('name'), 'Company name', 120, True)
    short_name = _clean(data.get('short_name'), 'Short name', 60) or name
    phone = _clean(data.get('phone'), 'Company phone', 40, True)
    if not PHONE_CHARS.fullmatch(phone) or not 7 <= len(re.sub(r'\D', '', phone)) <= 15:
        raise HTTPException(422, 'Enter the company phone number with 7 to 15 digits.')
    email = _clean(data.get('email'), 'Company email', 240, True).lower()
    if not EMAIL.fullmatch(email):
        raise HTTPException(422, 'Enter a valid company email address.')
    zone = _clean(data.get('timezone'), 'Time zone', 64, True)
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, 'Enter a valid IANA time zone, for example America/Toronto.')
    return {
        'name': name,
        'short_name': short_name,
        'phone': phone,
        'phone_tel': phone_tel(phone),
        'email': email,
        'timezone': zone,
        'local_time_label': _clean(data.get('local_time_label'), 'Local time label', 60),
        'local_time_label_fr': _clean(data.get('local_time_label_fr'), 'French local time label', 60),
    }


def company_profile(db):
    """The effective identity: the stored profile, else the legacy Crown Movers identity."""
    stored = settings(db, SETTING_KEY)
    if isinstance(stored, dict) and stored.get('name'):
        profile = {**LEGACY_PROFILE, 'local_time_label': '', 'local_time_label_fr': ''}
        profile.update({k: stored[k] for k in LEGACY_PROFILE if k in stored})
        profile['configured'] = True
        profile['version'] = int(stored.get('version') or 1)
        profile['updated_at'] = stored.get('updated_at', '')
        return profile
    return {**LEGACY_PROFILE, 'configured': False, 'version': 0, 'updated_at': ''}


def profile_or_legacy(company):
    return company if company else {**LEGACY_PROFILE, 'configured': False}


def time_note(company, fr):
    """' (Montréal time)' or '' when the company has not named its local time."""
    label = company.get('local_time_label_fr' if fr else 'local_time_label') or ''
    return f' ({label})' if label else ''


def team_signoff(company, fr):
    return f"\n\nMerci,\nL’équipe {company['name']}" if fr else f"\n\nThank you,\nThe {company['name']} team"


def help_line(company, fr):
    if fr:
        return f"\n\nBesoin d’aide? Demandez un rappel dans votre dossier ou appelez le {company['phone']}."
    return f"\n\nNeed a hand? Request a callback in your file or call {company['phone']}."


def store_profile(db, data, actor):
    current = company_profile(db)
    expected = data.get('version') if isinstance(data, dict) else None
    if type(expected) is not int or expected != current['version']:
        raise HTTPException(409, 'The company profile changed since you opened it. Refresh and review it before saving.')
    profile = validate_profile({k: v for k, v in data.items() if k != 'version'})
    profile['version'] = current['version'] + 1
    profile['updated_at'] = now()
    put_setting(db, SETTING_KEY, profile)
    audit(db, actor, 'Company profile updated', 'settings', SETTING_KEY,
          {'fields': sorted(k for k in EDITABLE if profile.get(k) != current.get(k)), 'version': profile['version']})
    return company_profile(db)


def register_company_profile(app, path, auth):
    @app.get('/api/company/profile')
    def read_profile(u=Depends(auth)):
        require_role(u, OFFICE)
        with closing(connect(path)) as db:
            return company_profile(db)

    @app.put('/api/company/profile')
    def update_profile(data: dict, u=Depends(auth)):
        require_role(u, MANAGERS)
        with transaction(path) as db:
            return store_profile(db, data, u['id'])

    @app.get('/api/company/public-profile')
    def public_profile():
        """Contact details already printed in every client email; no internal data."""
        with closing(connect(path)) as db:
            c = company_profile(db)
        return {'name': c['name'], 'phone': c['phone'], 'phone_tel': c['phone_tel'], 'email': c['email']}
