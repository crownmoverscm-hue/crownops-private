"""Company identity used in client-facing communications.

CrownOps was built for one company. Every installation now reads its business
identity from the `company_profile` setting so another moving company never sends
clients another company's name, phone number or email address.

Installations without a stored profile keep the legacy Crown Movers identity so
existing client messages stay byte-identical. New installations store their own
profile at provisioning (`manage.py init --company-name ...`) or through the
Owner/Manager settings API. `configured` reports which of the two is in effect.
"""
import os
import re
from contextlib import closing
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, HTTPException

from .platform_common import MANAGERS, OFFICE, put_setting, require_role, settings
from .store import audit, connect, now, transaction

SETTING_KEY = 'company_profile'
MODE_KEY = 'company_identity_mode'
LEGACY_MODE = 'legacy-crown'
UNCONFIGURED_MODE = 'unconfigured'

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

# Legacy-only defaults for the original Crown Movers installation. They are used ONLY
# while no company profile is configured, so another company never inherits them.
LEGACY_ISSUER = {
    'name': '9411-8213 Quebec Inc. / Déménagement Crown / Crown Movers',
    'address': '4030 Blvd. de la Côte-Vertu, Suite 111, Montréal, QC H4R 1V4',
    'email': 'info@crownmovers.ca',
    'phone': '514-606-4030',
}
LEGACY_TAXES = [
    {'name': 'GST', 'rate_percent': 5.0, 'registration': '737446534RT001'},
    {'name': 'QST', 'rate_percent': 9.975, 'registration': '1227197265TQ001'},
]
LEGACY_INVOICE_FOOTER = ('Thank you for choosing Crown Movers. Questions? Call 514-606-4030.',
                         'Merci d’avoir choisi Crown Movers. Une question? Appelez le 514-606-4030.')
LEGACY_REVIEW_PROFILES = [
    {'id': 'main', 'label': 'Crown Movers — main profile', 'url': 'https://g.page/r/CacaJ3Q-wayMEBM/review'},
    {'id': 'french', 'label': 'Crown Movers — French profile', 'url': 'https://g.page/r/CUQdPArzzqz_EAE/review'},
]
LEGACY_COMMUNICATIONS = {
    'connected_mailbox': 'crownmovers.cm@gmail.com',
    'send_as': 'info@crownmovers.ca',
    'reply_to': 'info@crownmovers.ca',
    'default_from': 'info@crownmovers.ca',
    'secondary_inbox': 'crownmovers.cm@gmail.com',
    'client_email_rule': 'Use the paid Gmail mailbox for storage and send client mail as the Crown Movers domain address when the alias is verified.',
    'approved': False,
    'note': 'Review build default. Connect and verify both mailboxes before live use.',
}

EMAIL = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')
PHONE_CHARS = re.compile(r'[0-9+()\-. ]+')
# Scheduling, availability and payroll still interpret local times as America/Toronto (COM-3).
# Until every site uses the company zone, another zone would shift booked times, so it is refused.
SUPPORTED_TIMEZONES = ('America/Toronto',)
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
    if zone not in SUPPORTED_TIMEZONES:
        raise HTTPException(422, 'CrownOps schedules currently run on Eastern time (America/Toronto) only. Other time zones are not supported yet.')
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


def record_identity_mode(db):
    """Decide once, on the first startup of this release, which identity an unconfigured install uses.

    A database that already holds users or leads is the original Crown Movers installation and keeps
    its legacy identity. An empty database is a new company installation: it stays unconfigured and
    cannot send client messages or issue invoices until its own profile is stored. Disposable test
    databases are not recorded (see identity_mode).
    """
    if settings(db, SETTING_KEY) or settings(db, MODE_KEY):
        return
    existing = db.execute('SELECT 1 FROM users LIMIT 1').fetchone() or db.execute('SELECT 1 FROM leads LIMIT 1').fetchone()
    if existing:
        put_setting(db, MODE_KEY, LEGACY_MODE)
    elif os.getenv('CROWNOPS_ENV', 'test') != 'test':
        put_setting(db, MODE_KEY, UNCONFIGURED_MODE)


def identity_mode(db):
    mode = settings(db, MODE_KEY)
    if mode in (LEGACY_MODE, UNCONFIGURED_MODE):
        return mode
    # Unrecorded databases only exist in the disposable test environment, whose fixtures model Crown.
    return LEGACY_MODE if os.getenv('CROWNOPS_ENV', 'test') == 'test' else UNCONFIGURED_MODE


def company_profile(db):
    """The effective identity: the stored profile, the legacy Crown identity, or an unusable placeholder."""
    stored = settings(db, SETTING_KEY)
    if isinstance(stored, dict) and stored.get('name'):
        profile = {**LEGACY_PROFILE, 'local_time_label': '', 'local_time_label_fr': ''}
        profile.update({k: stored[k] for k in LEGACY_PROFILE if k in stored})
        profile['configured'] = True
        profile['ready'] = True
        profile['mode'] = 'configured'
        profile['version'] = int(stored.get('version') or 1)
        profile['updated_at'] = stored.get('updated_at', '')
        return profile
    if identity_mode(db) == LEGACY_MODE:
        return {**LEGACY_PROFILE, 'configured': False, 'ready': True, 'mode': LEGACY_MODE, 'version': 0, 'updated_at': ''}
    return {'name': 'Moving company', 'short_name': 'the moving company', 'phone': '', 'phone_tel': '', 'email': '',
            'timezone': 'America/Toronto', 'local_time_label': '', 'local_time_label_fr': '',
            'configured': False, 'ready': False, 'mode': UNCONFIGURED_MODE, 'version': 0, 'updated_at': ''}


NOT_READY = ('Set up your company profile before {action}. An Owner or Manager can do this in '
             'company settings, or run manage.py company-profile.')


def require_ready(company, action):
    if not company.get('ready', True):
        raise HTTPException(409, NOT_READY.format(action=action))
    return company


def ready_profile(db, action):
    return require_ready(company_profile(db), action)


def profile_for_path(path):
    with closing(connect(path)) as db:
        return company_profile(db)


def client_name_of(company):
    """Name for client-visible copy; never another company's name when unconfigured."""
    return company['name'] if company.get('ready') else 'the moving company'


def short_of(company):
    return company['short_name'] if company.get('ready') else 'the moving company'


def call_hint(company, prefix):
    """' or call 514-…' style fragment, empty when no usable phone is configured."""
    return prefix + company['phone'] if company.get('ready') and company.get('phone') else ''


def is_legacy(company):
    return company is None or company.get('mode') == LEGACY_MODE


def profile_or_legacy(company):
    """Templates: an explicit profile must be usable; None means a legacy caller."""
    if company is None:
        return {**LEGACY_PROFILE, 'configured': False, 'ready': True}
    return require_ready(company, 'preparing client communications')


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
