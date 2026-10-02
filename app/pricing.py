"""Deterministic quote arithmetic for Crown Movers.

Rates come from managed seasonal/daily tables. Managers may explicitly override
an hourly rate, transportation amount, or long-distance kilometre charge on a
quote; the selected values are preserved in the quote revision.
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
    row = matches[0]
    key = str(movers) + ' movers'
    if key not in row or row[key] is None:
        raise ValueError('No crew-rate field is configured for this crew size.')
    return number(row[key], 'Crew hourly rate', 1, 5000), row['Season / Date Range']


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


def _default_key(settings, preferred, fallbacks):
    if preferred and preferred in settings:
        return preferred
    for key in fallbacks:
        if key in settings:
            return key
    return None


def calculate(inputs, pricing):
    rates = json.loads(pricing['rates_json']) if isinstance(pricing['rates_json'], str) else pricing['rates_json']
    settings = json.loads(pricing['settings_json']) if isinstance(pricing['settings_json'], str) else pricing['settings_json']
    movers_n = number(inputs.get('movers'), 'Movers', 2, 6)
    trucks_n = number(inputs.get('trucks'), 'Trucks', 1, 8)
    if movers_n != movers_n.to_integral() or trucks_n != trucks_n.to_integral():
        raise ValueError('Movers and trucks must be whole numbers.')
    movers, trucks = int(movers_n), int(trucks_n)
    if movers >= 4 and trucks < 2:
        raise ValueError('Crown planning rule: crews of 4 or more need at least 2 trucks.')

    rate, season = seasonal_rate(rates, str(inputs.get('move_date','')), movers)
    if pricing.get('_annual_override'):
        annual = pricing['_annual_override']
        rate = Decimal(annual[f'rate_{movers}_cents']) / 100
        season = annual['label'] + ' | managed annual rate'
    if pricing.get('_daily_override'):
        rate = Decimal(pricing['_daily_override']['hourly_cents']) / 100
        season = 'Daily override ' + pricing['_daily_override']['service_date']
    if str(inputs.get('hourly_rate_override', '')).strip() not in ('', '0', '0.0'):
        rate = number(inputs.get('hourly_rate_override'), 'Hourly rate override', 1, 5000)
        season += ' | manager-adjusted rate'

    service_type = str(inputs.get('service_type') or 'Moving')
    mk = _default_key(settings, inputs.get('minimum_key'),
                      ('morning_service_minimum','minimum_billable_hours','afternoon_micro_minimum'))
    fk = _default_key(settings, inputs.get('fuel_key'),
                      ('normal_local_fuel','gas_per_truck','extended_local_fuel'))
    if mk not in MINIMUM_KEYS:
        raise ValueError('No usable service minimum is configured for this rate table.')
    if fk not in FUEL_KEYS:
        raise ValueError('No usable fuel setting is configured for this rate table.')

    # Junk removal uses the published volume guide, not moving-service hours.
    if service_type == 'Junk Removal':
        base = number(inputs.get('junk_base_amount', 0), 'Junk-removal base amount', 0, 50000)
        half_hours = number(inputs.get('junk_extra_half_hours', 0), 'Extra half-hour labour blocks', 0, 100)
        labor = half_hours * Decimal('80')
        materials = number(inputs.get('materials_amount', 0), 'Materials allowance', 0, 50000)
        other = number(inputs.get('other_amount', 0), 'Other agreed charges', 0, 50000)
        discount = number(inputs.get('discount_amount', 0), 'Discount', 0, 50000)
        subtotal = base + labor + materials + other - discount
        if subtotal < 0:
            raise ValueError('Discount exceeds the estimate.')
        return {
            'currency':'CAD','before_tax':True,'hourly_rate_cents':0,
            'service_low':0.0,'service_high':0.0,'minimum_hours':0.0,
            'transport_hours':0.0,'transport_cents':0,'fuel_per_truck_cents':0,'fuel_cents':0,
            'materials_cents':cents(materials),'other_cents':cents(other+labor),'discount_cents':cents(discount),
            'service_low_cents':cents(base),'service_high_cents':cents(base),
            'low_cents':cents(subtotal),'high_cents':cents(subtotal),
            'movers':movers,'trucks':trucks,'season':season,'allowance_percent':0.0,
            'pricing_id':pricing['id'],'pricing_approved':bool(pricing.get('approved')),
            'service_type':service_type,'junk_base_cents':cents(base),'junk_extra_half_hours':float(half_hours),
            'scope':'Junk removal fixed-volume estimate. Extra on-site labour is billed in 30-minute blocks when selected.',
            'tax_note':'Before applicable taxes. Tax treatment must be reviewed in accounting; no tax is inferred here.'
        }

    base_low = number(inputs.get('hours_low'), 'Low service hours', .25, 80)
    base_high = number(inputs.get('hours_high'), 'High service hours', .25, 100)
    if base_high < base_low:
        raise ValueError('High service hours cannot be lower than low hours.')
    allowance = number(inputs.get('allowance_percent', 0), 'Planning allowance', 0, 50)
    minimum = number(settings[mk], 'Minimum', 0, 12)
    step = number(settings.get('time_increment_hours', .25), 'Time increment', .01, 1)

    def planned(hours):
        return (max(minimum, hours * (1 + allowance / 100)) / step).quantize(Decimal('1'), rounding=ROUND_CEILING) * step

    lo, hi = planned(base_low), planned(base_high)
    transport = number(inputs.get('transport_hours', 1), 'Separate transport hours', 0, 100)
    fixed_transport_raw = str(inputs.get('transport_fixed_amount', '')).strip()
    if fixed_transport_raw not in ('', '0', '0.0'):
        transport_amount = number(fixed_transport_raw, 'Fixed transportation amount', 0, 100000)
    else:
        transport_amount = transport * rate
        extra_truck_fee = number(inputs.get('extra_truck_transport_fee', 25), 'Extra truck transportation fee', 0, 1000)
        if trucks > 1:
            transport_amount += Decimal(trucks - 1) * extra_truck_fee

    normal_fuel = number(settings[fk], 'Fuel per vehicle', 0, 1000)
    distance_raw = str(inputs.get('long_distance_km', '')).strip()
    if distance_raw not in ('', '0', '0.0'):
        distance_km = number(distance_raw, 'Long-distance kilometres', 0, 10000)
        per_km = number(inputs.get('long_distance_gas_per_km', .94), 'Long-distance gas rate per kilometre', 0, 20)
        fuel_amount = distance_km * per_km * Decimal(trucks)
    else:
        distance_km = Decimal('0')
        per_km = Decimal('0')
        fuel_amount = Decimal(trucks) * normal_fuel

    materials = number(inputs.get('materials_amount', 0), 'Materials allowance', 0, 50000)
    other = number(inputs.get('other_amount', 0), 'Other agreed charges', 0, 50000)
    discount = number(inputs.get('discount_amount', 0), 'Discount', 0, 50000)
    fixed = transport_amount + fuel_amount + materials + other
    if discount > lo * rate + fixed:
        raise ValueError('Discount exceeds the low estimate.')

    return {
        'currency':'CAD','before_tax':True,'hourly_rate_cents':cents(rate),
        'service_low':float(lo),'service_high':float(hi),'minimum_hours':float(minimum),
        'transport_hours':float(transport),'transport_cents':cents(transport_amount),
        'fuel_per_truck_cents':cents(normal_fuel),'fuel_cents':cents(fuel_amount),
        'long_distance_km':float(distance_km),'long_distance_gas_per_km':float(per_km),
        'materials_cents':cents(materials),'other_cents':cents(other),'discount_cents':cents(discount),
        'service_low_cents':cents(lo*rate),'service_high_cents':cents(hi*rate),
        'low_cents':cents(lo*rate+fixed-discount),'high_cents':cents(hi*rate+fixed-discount),
        'movers':movers,'trucks':trucks,'season':season,'allowance_percent':float(allowance),
        'service_type':service_type,'pricing_id':pricing['id'],'pricing_approved':bool(pricing.get('approved')),
        'scope':'Service hours include travel between customer stops. Transportation is displayed as a separate reviewed charge.',
        'tax_note':'Before applicable taxes. Tax treatment must be reviewed in accounting; no tax is inferred here.'
    }


def calculate_with_overrides(inputs, pricing, db):
    from .store import one, rows
    p = dict(pricing)
    move_date = inputs.get('move_date')
    annuals = rows(db, "SELECT * FROM annual_rate_periods WHERE active=1 AND start_date<=? AND end_date>=? ORDER BY version DESC", (move_date, move_date))
    if len(annuals) > 1:
        raise ValueError('Move date matches more than one managed annual rate period. Fix the pricing calendar before quoting.')
    annual = annuals[0] if annuals else None
    if annual:
        p['_annual_override'] = annual
    override = one(db, 'SELECT * FROM daily_rates WHERE service_date=? AND movers=? ORDER BY version DESC LIMIT 1', (move_date, inputs.get('movers')))
    if override:
        p['_daily_override'] = override
    result = calculate(inputs, p)
    if annual:
        result['annual_rate_period_id'] = annual['id']
        result['annual_rate_period_version'] = annual['version']
    if override:
        result['daily_rate_id'] = override['id']
        result['daily_rate_version'] = override['version']
    return result
