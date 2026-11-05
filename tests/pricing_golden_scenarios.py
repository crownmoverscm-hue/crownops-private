"""Fixed Crown Movers pricing scenarios for COM-4 golden-value regression tests.

The expected outputs in tests/fixtures/crown_pricing_golden.json were generated from
the code BEFORE pricing policy became configurable (branch tip b62757c). They must
never be regenerated to make a failing test pass.
"""
import tempfile
from pathlib import Path

RATE_ROW = """INSERT INTO annual_rate_periods
  (id,label,start_date,end_date,rate_2_cents,rate_3_cents,rate_4_cents,rate_5_cents,rate_6_cents,active,version,note,approved_by,created_at)
  VALUES ('RATE-GOLDEN','Golden season','2026-01-01','2027-12-31',13500,17500,22000,26000,31500,1,1,'golden','golden','2026-01-01T00:00:00+00:00')"""

BASE = {'move_date': '2026-11-20', 'language': 'English'}
SCENARIOS = {
    'local_2_movers_default': {**BASE},
    'local_3_movers_hours_range': {**BASE, 'movers': 3, 'hours_low': 4, 'hours_high': 6, 'transport_hours': 1.5},
    'local_4_movers_two_trucks': {**BASE, 'movers': 4, 'trucks': 2, 'hours_low': 5, 'hours_high': 7},
    'local_6_movers_three_trucks': {**BASE, 'movers': 6, 'trucks': 3, 'hours_low': 6, 'hours_high': 8, 'transport_hours': 2},
    'local_below_minimum_hours': {**BASE, 'hours_low': 1, 'hours_high': 2},
    'local_fixed_transport_and_fuel_override': {**BASE, 'fixed_transport': 120, 'transport_mode': 'local_amount', 'local_fuel_rate': 40},
    'local_other_and_discount': {**BASE, 'hours_low': 3, 'hours_high': 5, 'other_amount': 75.5, 'discount_amount': 50},
    'local_rate_override': {**BASE, 'hourly_rate': 149.99, 'hours_low': 3.25, 'hours_high': 4.75},
    'small_afternoon_moving': {**BASE, 'small_afternoon_job': True, 'start_window': 'afternoon', 'hours_low': 1, 'hours_high': 2},
    'packing_minimum': {**BASE, 'service_type': 'Packing', 'hours_low': 2, 'hours_high': 2},
    'french_local': {**BASE, 'language': 'French', 'movers': 3, 'hours_low': 4, 'hours_high': 5},
    'long_distance': {**BASE, 'service_type': 'Long Distance', 'movers': 3, 'trucks': 1, 'hours_low': 5, 'hours_high': 7,
                      'transport_hours': 6, 'distance_km': 1100},
    'long_distance_short_drive': {**BASE, 'service_type': 'Long Distance', 'hours_low': 4, 'hours_high': 5,
                                  'transport_hours': 0.5, 'distance_km': 90, 'distance_rate': 1.1},
    'junk_quarter': {'service_type': 'Junk Removal', 'junk_tier': 'quarter'},
    'junk_full_extra_labour_discount': {'service_type': 'Junk Removal', 'junk_tier': 'full', 'junk_extra_half_hours': 3, 'discount_amount': 100, 'language': 'French'},
}
REFUSED = {
    'six_movers_one_truck': {**BASE, 'movers': 6, 'trucks': 1},
    'four_movers_one_truck': {**BASE, 'movers': 4, 'trucks': 1},
    'small_afternoon_morning': {**BASE, 'small_afternoon_job': True, 'start_window': 'morning'},
}


def golden_database(tmp):
    from app.store import initialize, transaction
    from app.sales_crm_v6 import schema
    path = Path(tmp) / 'golden.sqlite3'
    initialize(path); schema(path)
    with transaction(path) as db:
        db.execute(RATE_ROW)
    return path


def compute_all(path):
    from contextlib import closing
    from fastapi import HTTPException
    from app.store import connect
    from app.sales_crm_v6 import calculate
    out = {}
    with closing(connect(path)) as db:
        for name, data in SCENARIOS.items():
            out[name] = calculate(db, dict(data))
        for name, data in REFUSED.items():
            try:
                calculate(db, dict(data)); out[name] = 'ACCEPTED'
            except HTTPException as ex:
                out[name] = {'status': ex.status_code, 'detail': ex.detail}
    return out


if __name__ == '__main__':
    import json, os, sys
    os.environ.setdefault('CROWNOPS_ENV', 'test')
    with tempfile.TemporaryDirectory() as td:
        json.dump(compute_all(golden_database(td)), sys.stdout, indent=1, sort_keys=True, ensure_ascii=False)
