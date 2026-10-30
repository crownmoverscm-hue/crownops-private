"""Keep isolated synthetic test accounts out of general recovery; no production policy change."""
import os
import tempfile
from pathlib import Path
from fastapi import HTTPException

TARGETS = {
    'WIX-PILOT-' + suffix: 'wix-pilot-' + suffix + '@example.invalid'
    for suffix in ('nonmaster-owner', 'manager', 'sales', 'crew', 'inactive')
}
MESSAGE = 'Synthetic staging account: use the separately authorized Master-owner five-account reissue. General recovery is blocked.'


def restricted(path, person):
    if not person or (person.get('id') not in TARGETS
                      and person.get('email', '').strip().lower() not in TARGETS.values()):
        return False
    mode = os.getenv('CROWNOPS_ENV')
    if mode == 'test' and not os.getenv('RAILWAY_SERVICE_ID'):
        return Path(path).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    if mode != 'staging':
        return False
    from .quote_staging import isolated, require_isolated, DATABASE
    if not isolated():
        return False  # Other environments (including Staff AI) retain their existing policy.
    require_isolated()  # A partly identified/misconfigured quote environment fails closed.
    if Path(path).resolve() != Path(DATABASE):
        raise HTTPException(503, 'Exact isolated staging database required.')
    return True


def require_general_recovery(path, person):
    if restricted(path, person):
        raise HTTPException(503, MESSAGE)
