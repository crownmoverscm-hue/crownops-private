"""Owner-approved local fee direction; activation is explicit, never calendar driven.

This does not approve transport, regular minimums, labour rates or 2027 rates.
Existing quote snapshots are never migrated or recalculated here.
"""
import os
from decimal import Decimal

VERSION = '2026-10-25'


def enabled():
    return os.getenv('CROWNOPS_LOCAL_PRICING_POLICY', '') == VERSION


def local_fuel_default(previous):
    return Decimal('35.99') if enabled() else Decimal(str(previous))


def afternoon_minimum(previous):
    return Decimal('1') if enabled() else Decimal(str(previous))


def snapshot():
    return {'version': VERSION if enabled() else '', 'fuel_unit': 'per truck',
            'base_address': '700 rue Hodge', 'included_route_km': 40,
            'route_scope': 'Base to every pickup and destination, then back to base',
            'automatic_mileage_surcharge': False}
