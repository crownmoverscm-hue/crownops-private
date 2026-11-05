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
CREATED_IN_TEST_KEY = 'database_created_in_test'
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
    'brand_color': '',
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
EDITABLE = ('name', 'short_name', 'phone', 'email', 'timezone', 'local_time_label', 'local_time_label_fr', 'brand_color')
LOGO_KEY = 'company_logo'
COLOR = re.compile(r'#[0-9a-fA-F]{6}')


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


def _brand_color(value):
    value = _clean(value, 'Brand colour', 7)
    if value and not COLOR.fullmatch(value):
        raise HTTPException(422, 'Enter the brand colour as #RRGGBB, for example #1f6feb.')
    return value.lower()


def store_logo(db, raw, actor):
    """Re-encode an uploaded PNG/JPEG/WebP as a small PNG; SVG and other formats are refused."""
    from io import BytesIO
    from PIL import Image, UnidentifiedImageError
    if not raw or len(raw) > 2_000_000:
        raise HTTPException(422, 'Upload a PNG, JPEG or WebP logo smaller than 2 MB.')
    try:
        with Image.open(BytesIO(raw)) as img:
            if img.format not in ('PNG', 'JPEG', 'WEBP'):
                raise HTTPException(422, 'Upload a PNG, JPEG or WebP logo. Other formats, including SVG, are not accepted.')
            if img.width * img.height > 16_000_000 or max(img.size) > 6000:
                raise HTTPException(422, 'The logo image is too large. Use an image under 6000 pixels per side.')
            img.load()
            logo = img.convert('RGBA')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(422, 'The logo could not be read as an image.')
    logo.thumbnail((512, 512))
    out = BytesIO(); logo.save(out, 'PNG', optimize=True)  # re-encoding drops metadata and any trailing data
    import base64, hashlib
    data = out.getvalue()
    put_setting(db, LOGO_KEY, {'png_b64': base64.b64encode(data).decode(), 'width': logo.width, 'height': logo.height,
                               'sha256': hashlib.sha256(data).hexdigest(), 'updated_at': now()})
    audit(db, actor, 'Company logo updated', 'settings', LOGO_KEY, {'bytes': len(data), 'width': logo.width, 'height': logo.height})
    return {'width': logo.width, 'height': logo.height, 'bytes': len(data)}


def setup_status(db):
    """One owner-facing checklist: incomplete, pending approval or approved for each area."""
    from .pricing_policy import policy_status
    from .business_zone import current_zone
    from datetime import datetime
    profile = company_profile(db)
    items = []
    legacy = profile['mode'] == LEGACY_MODE
    items.append({'key': 'identity', 'label': 'Company identity and contact',
                  'state': 'approved' if profile['configured'] else ('legacy' if legacy else 'incomplete')})
    items.append({'key': 'logo', 'label': 'Logo (optional)', 'state': 'approved' if settings(db, LOGO_KEY) else 'optional'})
    pricing = policy_status(db)
    items.append({'key': 'pricing', 'label': 'Pricing policy', 'state': ('legacy' if pricing['source'] == LEGACY_MODE else 'approved') if pricing['configured']
                  else ('pending approval' if pricing.get('draft') else 'incomplete')})
    today = datetime.now(current_zone()).date().isoformat()
    rates = db.execute('SELECT count(*) FROM annual_rate_periods WHERE active=1 AND end_date>=?', (today,)).fetchone()[0]
    items.append({'key': 'rates', 'label': 'Hourly crew rates', 'state': 'approved' if rates else 'incomplete', 'count': rates})
    billing = settings(db, 'billing_policy', {}) or {}
    terms = billing.get('terms') or {}
    items.append({'key': 'billing', 'label': 'Tax and invoice settings',
                  'state': 'approved' if billing.get('approved') and (terms.get('issuer') or {}).get('name') else ('pending approval' if billing else 'incomplete')})
    required = [i for i in items if i['key'] != 'logo']
    return {'items': items, 'ready': all(i['state'] in ('approved', 'legacy') for i in required), 'mode': profile['mode']}


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
        'brand_color': _brand_color(data.get('brand_color')),
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
    if os.getenv('CROWNOPS_ENV', 'test') == 'test':
        # Remember that this database began life in the test environment, so a trial
        # database later promoted to production is never mistaken for Crown's install.
        if settings(db, CREATED_IN_TEST_KEY) is None:
            put_setting(db, CREATED_IN_TEST_KEY, True)
        return
    existing = db.execute('SELECT 1 FROM users LIMIT 1').fetchone() or db.execute('SELECT 1 FROM leads LIMIT 1').fetchone()
    put_setting(db, MODE_KEY, LEGACY_MODE if existing and not settings(db, CREATED_IN_TEST_KEY) else UNCONFIGURED_MODE)


def set_identity_mode(db, mode, actor, reason):
    """Explicit, audited owner decision (manage.py installation-identity). Overrides detection."""
    if mode not in (LEGACY_MODE, UNCONFIGURED_MODE):
        raise HTTPException(422, 'Choose legacy-crown or unconfigured.')
    if not isinstance(reason, str) or len(reason.strip()) < 12:
        raise HTTPException(422, 'Record why this installation identity is being set (12+ characters).')
    previous = settings(db, MODE_KEY)
    put_setting(db, MODE_KEY, mode)
    audit(db, actor, 'Installation identity set', 'settings', MODE_KEY, {'from': previous, 'to': mode, 'reason': reason.strip()})
    return mode


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
            'timezone': 'America/Toronto', 'local_time_label': '', 'local_time_label_fr': '', 'brand_color': '',
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


def scheduling_data_exists(db):
    for table in ('jobs', 'schedule_days', 'imported_schedule_events'):
        try:
            if db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                return True
        except Exception:  # table absent in minimal databases
            continue
    return False


def store_profile(db, data, actor):
    current = company_profile(db)
    expected = data.get('version') if isinstance(data, dict) else None
    if type(expected) is not int or expected != current['version']:
        raise HTTPException(409, 'The company profile changed since you opened it. Refresh and review it before saving.')
    profile = validate_profile({k: v for k, v in data.items() if k != 'version'})
    if profile['timezone'] != current['timezone'] and scheduling_data_exists(db):
        # Stored instants are UTC; a new zone would silently re-label every booked arrival time.
        raise HTTPException(409, 'The time zone cannot change after jobs, availability or imported schedules exist. '
                                 'Booked arrival times would shift for clients and crews. Contact support to plan a reviewed migration.')
    profile['version'] = current['version'] + 1
    profile['updated_at'] = now()
    put_setting(db, SETTING_KEY, profile)
    from .business_zone import set_business_zone
    set_business_zone(profile['timezone'])  # one installation per process (COM-3)
    audit(db, actor, 'Company profile updated', 'settings', SETTING_KEY,
          {'fields': sorted(k for k in EDITABLE if profile.get(k) != current.get(k)), 'version': profile['version']})
    return company_profile(db)


def register_company_profile(app, path, auth):
    from fastapi import File, UploadFile
    from fastapi.responses import Response

    @app.get('/api/company/setup-status')
    def read_setup_status(u=Depends(auth)):
        require_role(u, OFFICE)
        with closing(connect(path)) as db:
            return setup_status(db)

    @app.post('/api/company/logo')
    async def upload_logo(file: UploadFile = File(...), u=Depends(auth)):
        require_role(u, MANAGERS)
        raw = await file.read(2_000_001)
        with transaction(path) as db:
            return store_logo(db, raw, u['id'])

    @app.delete('/api/company/logo')
    def remove_logo(u=Depends(auth)):
        require_role(u, MANAGERS)
        with transaction(path) as db:
            db.execute('DELETE FROM settings WHERE key=?', (LOGO_KEY,))
            audit(db, u['id'], 'Company logo removed', 'settings', LOGO_KEY)
        return {'removed': True}

    @app.get('/api/company/logo')
    def read_logo():
        import base64
        with closing(connect(path)) as db:
            logo = settings(db, LOGO_KEY)
        if not logo:
            raise HTTPException(404, 'No company logo is set.')
        return Response(base64.b64decode(logo['png_b64']), media_type='image/png',
                        headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff', 'ETag': '"' + logo['sha256'][:32] + '"'})

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

    @app.get('/business-zone.js')
    def business_zone_script():
        """Browser pages format dates in the company zone; Toronto keeps the original 'ET' label."""
        import json
        from fastapi.responses import Response
        from .business_zone import zone_name
        with closing(connect(path)) as db:
            c = company_profile(db)
        zone = zone_name()
        label = 'ET' if zone == 'America/Toronto' else (c.get('local_time_label') or zone.rsplit('/', 1)[-1].replace('_', ' ') + ' time')
        body = 'window.CROWNOPS_TZ=' + json.dumps(zone) + ';window.CROWNOPS_TZ_LABEL=' + json.dumps(label) + ';'
        return Response(body, media_type='application/javascript', headers={'Cache-Control': 'no-store'})

    @app.get('/api/company/public-profile')
    def public_profile():
        """Contact details already printed in every client email; no internal data."""
        with closing(connect(path)) as db:
            c = company_profile(db)
        return {'name': c['name'], 'phone': c['phone'], 'phone_tel': c['phone_tel'], 'email': c['email']}
