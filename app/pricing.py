"""Deterministic INTERNAL quote arithmetic. Not a physical inventory estimator.

Rates are imported verbatim, not guessed from old email examples. Minimum/fuel
settings are selected explicitly per quote because the source has overlapping
settings. Service duration here includes travel BETWEEN client stops. Transport
hours are ONLY the separate base-to-first and last-to-base allowance.
"""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
import json

MINIMUM_KEYS = ('morning_service_minimum', 'afternoon_micro_minimum', 'minimum_billable_hours')
FUEL_KEYS = ('normal_local_fuel', 'extended_local_fuel', 'gas_per_truck')


def number(value, name, low=0, high=100000):
    if isinstance(value, bool):
        raise ValueError(name + ' must be a number.')
    try:
        n = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(name + ' must be a number.')
    if not n.is_finite() or n < Decimal(str(low)) or n > Decimal(str(high)):
        raise ValueError(f'{name} must be between {low} and {high}.')
    return n


def cents(v):
    return int((number(v, 'Amount', -1000000, 1000000) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def seasonal_rate(rates, move_date, movers):
    d = date.fromisoformat(move_date)
    md = (d.month, d.day)
    matches = []
    for r in rates:
        start = (int(r['Start Month']), int(r['Start Day']))
        end = (int(r['End Month']), int(r['End Day']))
        if (start <= end and start <= md <= end) or (start > end and (md >= start or md <= end)):
            matches.append(r)
    if len(matches) != 1:
        raise ValueError('Date must match exactly one seasonal rate row. Review pricing.')
    r = matches[0]
    key = str(movers) + ' movers'
    if key not in r or r[key] is None:
        raise ValueError('No approved crew-rate field for this crew size.')
    return number(r[key], 'Crew hourly rate', 1, 5000), r['Season / Date Range']


def missing_fields(lead):
    req = {'client_name':'Client name', 'email':'Email', 'phone':'Phone', 'move_date':'Move date',
           'pickup':'Pickup address', 'dropoff':'Destination address', 'home_size':'Move size',
           'boxes':'Box count', 'inventory':'Furniture / inventory list',
           'pickup_access':'Pickup access', 'dropoff_access':'Destination access',
           'pickup_walk':'Pickup walk', 'dropoff_walk':'Destination walk'}
    unknown = ('', 'unknown', 'unknown caller', 'not sure', 'none', 'n/a')
    out = [label for k,label in req.items() if str(lead.get(k,'')).strip().lower() in unknown]
    if lead.get('email') and '@' not in lead['email'] and 'Email' not in out:
        out.append('Valid email')
    return out


def calculate(inputs, pricing):
    rates = json.loads(pricing['rates_json']) if isinstance(pricing['rates_json'], str) else pricing['rates_json']
    settings = json.loads(pricing['settings_json']) if isinstance(pricing['settings_json'], str) else pricing['settings_json']
    movers_n = number(inputs.get('movers'), 'Movers', 2, 6)
    trucks_n = number(inputs.get('trucks'), 'Trucks', 1, 8)
    if movers_n != movers_n.to_integral() or trucks_n != trucks_n.to_integral():
        raise ValueError('Movers and trucks must be whole numbers.')
    movers, trucks = int(movers_n), int(trucks_n)
    if movers >= 4 and trucks < 2:
        raise ValueError('Crown planning rule: crews of 4 or more need at least 2 trucks. Confirm any exception with management outside this pilot.')
    # Managed dated rates do not require a second, potentially conflicting
    # yearless seasonal row. Both quote engines use daily > dated > seasonal.
    rate, season = (Decimal(0),'') if pricing.get('_annual_override') or pricing.get('_daily_override') else seasonal_rate(rates, str(inputs.get('move_date','')), movers)
    if pricing.get('_annual_override'):
        a=pricing['_annual_override']
        rate = Decimal(a[f'rate_{movers}_cents']) / 100
        season = a['label'] + ' | managed annual rate'
    if pricing.get('_daily_override'):
        rate = Decimal(pricing['_daily_override']['hourly_cents']) / 100
        season = 'Daily override ' + pricing['_daily_override']['service_date']
    mk, fk = inputs.get('minimum_key'), inputs.get('fuel_key')
    if mk not in MINIMUM_KEYS or mk not in settings:
        raise ValueError('Select an explicit minimum-hours setting from the source.')
    if fk not in FUEL_KEYS or fk not in settings:
        raise ValueError('Select an explicit fuel setting from the source.')
    base_low = number(inputs.get('hours_low'), 'Low service hours', .25, 80)
    base_high = number(inputs.get('hours_high'), 'High service hours', .25, 100)
    if base_high < base_low:
        raise ValueError('High service hours cannot be lower than low hours.')
    allowance = number(inputs.get('allowance_percent', 0), 'Planning allowance', 0, 50)
    minimum = number(settings[mk], 'Minimum', 0, 12)
    step = number(settings.get('time_increment_hours', .25), 'Time increment', .01, 1)
    def planned(h):
        return (max(minimum, h * (1 + allowance / 100)) / step).quantize(Decimal('1'), rounding=ROUND_CEILING) * step
    lo, hi = planned(base_low), planned(base_high)
    transport = number(inputs.get('transport_hours'), 'Separate transport hours', 0, 100)
    fuel = number(settings[fk], 'Fuel per vehicle', 0, 1000)
    materials = number(inputs.get('materials_amount', 0), 'Materials allowance', 0, 50000)
    other = number(inputs.get('other_amount', 0), 'Other agreed charges', 0, 50000)
    discount = number(inputs.get('discount_amount', 0), 'Discount', 0, 50000)
    fixed = transport * rate + trucks * fuel + materials + other
    if discount > lo * rate + fixed:
        raise ValueError('Discount exceeds the low estimate.')
    return {'currency':'CAD','before_tax':True,'hourly_rate_cents':cents(rate),
            'service_low':float(lo),'service_high':float(hi),'minimum_hours':float(minimum),
            'transport_hours':float(transport),'transport_cents':cents(transport*rate),
            'fuel_per_truck_cents':cents(fuel),'fuel_cents':cents(fuel*trucks),
            'materials_cents':cents(materials),'other_cents':cents(other),'discount_cents':cents(discount),
            'service_low_cents':cents(lo*rate),'service_high_cents':cents(hi*rate),
            'low_cents':cents(lo*rate+fixed-discount),'high_cents':cents(hi*rate+fixed-discount),
            'movers':movers,'trucks':trucks,'season':season,'allowance_percent':float(allowance),
            'pricing_id':pricing['id'], 'pricing_approved':bool(pricing.get('approved')),
            'scope':'Local move only. Service hours include travel between customer stops. Separate transport is base travel only.',
            'tax_note':'Before applicable taxes. Tax treatment must be reviewed in accounting; no tax is inferred here.'}


def calculate_with_overrides(inputs,pricing,db):
    from .store import one, rows
    p=dict(pricing)
    move_date=inputs.get('move_date')
    annuals=rows(db,"SELECT * FROM annual_rate_periods WHERE active=1 AND start_date<=? AND end_date>=? AND (?<'2027-01-01' OR approved_by!='migration') ORDER BY version DESC",(move_date,move_date,move_date))
    if len(annuals)>1:
        raise ValueError('Move date matches more than one managed annual rate period. Fix the pricing calendar before quoting.')
    annual=annuals[0] if annuals else None
    if annual:p['_annual_override']=annual
    override=one(db,'SELECT * FROM daily_rates WHERE service_date=? AND movers=? ORDER BY version DESC LIMIT 1',(move_date,inputs.get('movers')))
    if override:p['_daily_override']=override
    if not annual and not override and date.fromisoformat(move_date).year>=2027:
        raise ValueError('Approve an effective-dated rate period for this service year. Current rates are not automatically reused for 2027 or later.')
    result=calculate(inputs,p)
    if annual:result['annual_rate_period_id']=annual['id'];result['annual_rate_period_version']=annual['version']
    if override:result['daily_rate_id']=override['id'];result['daily_rate_version']=override['version']
    return result
