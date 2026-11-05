"""Company pricing and operational policy (COM-4).

Crown Movers' rules used to be module constants. Each installation now prices from
its own validated, owner-approved `pricing_policy` setting:

* The original Crown installation (identity mode legacy-crown) keeps LEGACY_POLICY,
  whose values are exactly the former constants, so its estimates are unchanged
  (tests/fixtures/crown_pricing_golden.json).
* A new company has no policy until an Owner/Manager saves and approves one. Until
  then estimates are refused with a clear message; nothing falls back to Crown.

Hourly crew rates are not part of this policy: they live in the owner-managed rate
calendar (annual_rate_periods / daily_rates), which new companies fill themselves.
Saved estimates keep their own inputs and results (quotes.inputs_json/result_json),
and new-company estimates record the policy version they used.
"""
from contextlib import closing
from decimal import Decimal, InvalidOperation

from fastapi import Depends, HTTPException

from .platform_common import MANAGERS, OFFICE, put_setting, require_role, settings
from .store import audit, connect, now, transaction

SETTING_KEY = 'pricing_policy'
MONEY_FIELDS = ('local_fuel_per_truck', 'extra_truck_fee', 'long_distance_km_rate', 'junk_extra_labour', 'default_deposit')
HOURS_FIELDS = ('default_minimum_hours', 'moving_packing_minimum_hours', 'small_afternoon_minimum_hours')
FACTOR_FIELDS = ('long_distance_km_buffer', 'long_distance_transport_multiplier')
JUNK_KEYS = ('single', 'quarter', 'half', 'three_quarter', 'full')

LEGACY_POLICY = {
    'truck_minimums': [[6, 3], [4, 2]],          # crew of >= 6 needs 3 trucks; >= 4 needs 2; else 1
    'default_minimum_hours': '3',
    'moving_packing_minimum_hours': '3',
    'small_afternoon_minimum_hours': '1.5',
    'local_fuel_per_truck': '35.00',
    'extra_truck_fee': '25.00',
    'long_distance_km_rate': '0.94',
    'long_distance_km_buffer': '1.05',
    'long_distance_transport_multiplier': '1.15',
    'junk_tiers': {
        'single': ['Single Item Disposal', '175.00'],
        'quarter': ['1/4 Truck - approx. 5 ft', '400.00'],
        'half': ['1/2 Truck - approx. 10 ft', '800.00'],
        'three_quarter': ['3/4 Truck - approx. 15 ft', '1200.00'],
        'full': ['Full Truck - 20 ft container', '1600.00'],
    },
    'junk_extra_labour': '80.00',
    'default_deposit': '300',
    'limited_liability_text': '$0.60/lb per article',
    'testimonial_credit': '50.00',
}


def _decimal(value, label, low, high):
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise HTTPException(422, f'{label} must be a number.')
    if not d.is_finite() or d < Decimal(str(low)) or d > Decimal(str(high)):
        raise HTTPException(422, f'{label} must be between {low} and {high}.')
    return str(d)


def validate_policy(data):
    """Every essential value must be supplied explicitly; no industry defaults are invented."""
    if not isinstance(data, dict):
        raise HTTPException(422, 'Send the pricing policy as an object.')
    allowed = set(LEGACY_POLICY) | {'version', 'approved'}
    unknown = set(data) - allowed
    if unknown:
        raise HTTPException(422, 'Unknown pricing policy field: ' + sorted(unknown)[0] + '.')
    missing = [k for k in LEGACY_POLICY if k not in ('testimonial_credit',) and data.get(k) in (None, '')]
    if missing:
        raise HTTPException(422, 'Set every pricing value before saving. Missing: ' + ', '.join(missing) + '.')
    if not isinstance(data.get('approved', False), bool):
        raise HTTPException(422, 'Approval must be true or false.')
    out = {}
    for k in MONEY_FIELDS:
        out[k] = _decimal(data[k], k.replace('_', ' ').capitalize(), 0, 100000)
    for k in HOURS_FIELDS:
        out[k] = _decimal(data[k], k.replace('_', ' ').capitalize(), 0, 24)
    for k in FACTOR_FIELDS:
        out[k] = _decimal(data[k], k.replace('_', ' ').capitalize(), 1, 3)
    rules = data['truck_minimums']
    if not isinstance(rules, list) or len(rules) > 6:
        raise HTTPException(422, 'Truck minimums must be a list of [crew size, trucks] rules.')
    clean = []
    for rule in rules:
        if not (isinstance(rule, list) and len(rule) == 2 and all(type(x) is int for x in rule)
                and 2 <= rule[0] <= 6 and 1 <= rule[1] <= 8):
            raise HTTPException(422, 'Each truck minimum must be [crew size 2-6, trucks 1-8].')
        clean.append(rule)
    out['truck_minimums'] = sorted(clean, key=lambda r: -r[0])
    tiers = data['junk_tiers']
    if not isinstance(tiers, dict) or set(tiers) != set(JUNK_KEYS):
        raise HTTPException(422, 'Junk removal needs a name and price for each tier: ' + ', '.join(JUNK_KEYS) + '.')
    out['junk_tiers'] = {}
    for key in JUNK_KEYS:
        tier = tiers[key]
        if not (isinstance(tier, list) and len(tier) == 2 and isinstance(tier[0], str) and 0 < len(tier[0].strip()) <= 80):
            raise HTTPException(422, f'Junk tier {key} needs a name and a price.')
        out['junk_tiers'][key] = [tier[0].strip(), _decimal(tier[1], f'Junk tier {key} price', 0, 100000)]
    text = data['limited_liability_text']
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 200 or any(c in text for c in '<>'):
        raise HTTPException(422, 'Enter the limited liability wording (up to 200 characters).')
    out['limited_liability_text'] = text.strip()
    credit = data.get('testimonial_credit')
    out['testimonial_credit'] = _decimal(credit, 'Testimonial credit', 0, 10000) if credit not in (None, '') else None
    out['approved'] = data.get('approved') is True  # a draft is saved but never used for pricing
    return out


def pricing_policy(db):
    """The policy to price with, or a 409 explaining that pricing is not set up yet."""
    stored = settings(db, SETTING_KEY)
    if isinstance(stored, dict) and stored.get('approved'):
        return {**stored, 'source': 'company'}
    from .company_profile import company_profile
    if company_profile(db)['mode'] == 'legacy-crown':
        return {**LEGACY_POLICY, 'approved': True, 'version': 0, 'source': 'legacy-crown'}
    raise HTTPException(409, 'Set up and approve your company pricing policy (Pricing settings) before preparing estimates.')


def policy_status(db):
    try:
        policy = pricing_policy(db)
        stored = settings(db, SETTING_KEY) or {}
        draft = stored if stored and not stored.get('approved') else None
        return {'configured': True, 'source': policy['source'], 'version': stored.get('version', policy.get('version', 0)), 'policy': policy, 'draft': draft}
    except HTTPException as ex:
        stored = settings(db, SETTING_KEY) or {}
        return {'configured': False, 'source': None, 'version': stored.get('version', 0), 'policy': None,
                'draft': stored if stored and not stored.get('approved') else None, 'message': ex.detail}


def D(policy, key):
    return Decimal(policy[key])


def minimum_trucks(policy, movers):
    for crew, trucks in policy['truck_minimums']:
        if movers >= crew:
            return trucks
    return 1


def testimonial_credit_cents(policy):
    value = policy.get('testimonial_credit')
    if value in (None, ''):
        raise HTTPException(409, 'Set the testimonial credit amount in the pricing policy before offering it.')
    return int((Decimal(value) * 100).to_integral_value())


def disclosure_text(policy):
    cents = testimonial_credit_cents(policy)
    amount = f'${cents // 100}' if cents % 100 == 0 else f'${cents / 100:.2f}'
    return f'The customer received a {amount} moving discount for participating in this testimonial.'


def register_pricing_policy(app, path, auth):
    @app.get('/api/company/pricing-policy')
    def read_policy(u=Depends(auth)):
        require_role(u, OFFICE)
        with closing(connect(path)) as db:
            return policy_status(db)

    @app.put('/api/company/pricing-policy')
    def save_policy(data: dict, u=Depends(auth)):
        require_role(u, MANAGERS)
        with transaction(path) as db:
            current = settings(db, SETTING_KEY) or {}
            if type(data.get('version')) is not int or data['version'] != int(current.get('version', 0)):
                raise HTTPException(409, 'The pricing policy changed since you opened it. Refresh and review it before saving.')
            policy = validate_policy(data)
            policy['version'] = int(current.get('version', 0)) + 1
            if policy['approved']:
                policy['approved_by'] = u['id']; policy['approved_at'] = now()
                # Audit snapshot: estimates reference the latest approved pricing_versions row, so each
                # estimate records the exact approved policy it was priced with. Hourly rates stay in the
                # owner's rate calendar, so this snapshot carries no seasonal workbook rates.
                import json as _json
                db.execute('INSERT INTO pricing_versions(id,label,rates_json,settings_json,approved,approved_by,approved_at,created_at) VALUES(?,?,?,?,1,?,?,?)',
                           ('PRICE-POLICY-' + str(policy['version']), 'Company pricing policy v' + str(policy['version']), '[]',
                            _json.dumps(policy, sort_keys=True), u['id'], policy['approved_at'], now()))
            elif current.get('approved'):
                # Keep pricing with the approved policy while a new draft is reviewed.
                raise HTTPException(409, 'An approved pricing policy is in use. Save changes with approval, or keep the current policy.')
            put_setting(db, SETTING_KEY, policy)
            audit(db, u['id'], 'Pricing policy approved' if policy['approved'] else 'Pricing policy draft saved', 'settings', SETTING_KEY,
                  {'version': policy['version']})
            return policy_status(db)
