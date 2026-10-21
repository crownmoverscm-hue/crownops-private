"""Fictional employee portal installation; no real staff or external messages."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
os.environ['CROWNOPS_PUBLIC_URL'] = 'http://127.0.0.1:8095'
from test_staff_portal import StaffPortalTests
from app.store import transaction, insert, now
from app.platform_common import business_day, put_setting
import uvicorn

fixture = StaffPortalTests()
fixture.setUp()
today = business_day()
with transaction(fixture.path) as db:
    db.execute("UPDATE employees SET name='Friday replacement' WHERE id='E3'")
    put_setting(db, 'payroll_policy', {'period_days': 14, 'period_anchor': today, 'approved': True})
    insert(db, 'jobs', dict(id='PAST', lead_id='L1', title='Fictional completed service', phase='Moving',
        start_at=today+'T12:00:00+00:00', end_at=today+'T15:30:00+00:00', status='Completed', created_by='owner', created_at=now()))
    insert(db, 'times', dict(id='HOURS', job_id='PAST', employee_id='E1', service_minutes=180,
        transport_minutes=30, status='Approved', submitted_by='manager', created_at=now()))
uvicorn.run(fixture.app, host='127.0.0.1', port=8095, log_level='warning')
