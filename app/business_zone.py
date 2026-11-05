"""The company's business time zone (COM-3).

Timestamps are stored in UTC. Local business concepts (service dates, arrival
windows, business days, payroll weeks, schedule imports, reminders) are
interpreted in the company's own zone. Each company has its own installation and
process, so the zone is held once per process: create_app() loads it from the
company profile and a profile change updates it.

BUSINESS_ZONE is a tzinfo that delegates every calculation to the current
ZoneInfo, including `fold` for the repeated hour when clocks fall back, so call
sites behave exactly as they did with ZoneInfo('America/Toronto').
"""
from datetime import tzinfo
from zoneinfo import ZoneInfo

DEFAULT_ZONE = 'America/Toronto'
_current = ZoneInfo(DEFAULT_ZONE)


def zone_name():
    return _current.key


def current_zone():
    return _current


def set_business_zone(name):
    """Validate and activate a zone; unknown names raise ZoneInfoNotFoundError/ValueError."""
    global _current
    _current = ZoneInfo(name)
    return _current


class _BusinessZone(tzinfo):
    def utcoffset(self, dt):
        return _current.utcoffset(dt)

    def dst(self, dt):
        return _current.dst(dt)

    def tzname(self, dt):
        return _current.tzname(dt)

    def fromutc(self, dt):
        local = _current.fromutc(dt.replace(tzinfo=_current))
        return local.replace(tzinfo=self)

    def __repr__(self):
        return f'BusinessZone({_current.key})'

    @property
    def key(self):
        return _current.key


BUSINESS_ZONE = _BusinessZone()
