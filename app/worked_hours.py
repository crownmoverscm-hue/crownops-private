"""Worked time, review and payroll handoff, independent of customer billing."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import csv
import io
import json
import hashlib
from fastapi import HTTPException
from fastapi.responses import Response
from .store import rows, one, now, audit
from .platform_common import business_day
from .staff_records import can_view_pay
from .employee_contacts import PAY_PENDING


def hour_minutes(value):
    try:
        hours = Decimal(str(value))
        minutes = hours * 60
        if not hours.is_finite() or not 0 <= hours <= 24 or abs(minutes-minutes.to_integral_value()) > Decimal('0.000001'):
            raise ValueError()
        return int(minutes.to_integral_value())
    except (ValueError, InvalidOperation, TypeError):
        raise HTTPException(422, 'Enter hours between 0 and 24 that resolve to whole minutes.')


def pay_cents(minutes, rate):
    return int((Decimal(minutes) * Decimal(rate) / 60).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def date_range(start='', end=''):
    try:
        end_d = date.fromisoformat(end or business_day())
        start_d = date.fromisoformat(start) if start else end_d - timedelta(days=13)
        if end_d < start_d or (end_d - start_d).days > 366:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, 'Choose valid dates, in order, up to one year apart.')
    return start_d.isoformat(), end_d.isoformat()


def rate_pending(employee):
    return bool(json.loads(employee.get('raw_json') or '{}').get(PAY_PENDING)) or employee['hourly_cents'] <= 0


def approve_entry(db, tid, actor):
    record = one(db, '''SELECT t.*,e.hourly_cents,e.raw_json,j.status job_status
        FROM times t JOIN employees e ON e.id=t.employee_id JOIN jobs j ON j.id=t.job_id WHERE t.id=?''', (tid,))
    if not record or record['job_status'] != 'Completed':
        raise HTTPException(409, 'Complete the service before approving its hours.')
    if rate_pending(record):
        raise HTTPException(409, 'An Owner or Manager must set the employee pay rate before approval.')
    if record['status'] == 'Approved' and record['approved_hourly_cents'] is not None:
        return False
    db.execute("""UPDATE times SET status='Approved',approved_by=?,approved_at=?,
        approved_hourly_cents=?,version=version+1 WHERE id=?""", (actor, now(), record['hourly_cents'], tid))
    audit(db, actor, 'Worked hours approved', 'times', tid,
          {'minutes': record['service_minutes'] + record['transport_minutes'], 'hourly_cents': record['hourly_cents']})
    return True


def resolve_range(db,start='',end=''):
    if not start and not end:
        from .payroll_periods import default_range
        selected=default_range(db)
        if selected:start,end=selected['start'],selected['end']
    return date_range(start,end)


def summary(db, user, start='', end=''):
    start,end=resolve_range(db,start,end)
    employees = rows(db, 'SELECT * FROM employees ORDER BY name')
    entries = rows(db, """SELECT t.*,j.title,j.start_at,j.status job_status FROM times t
        JOIN jobs j ON j.id=t.job_id WHERE j.status!='Cancelled' ORDER BY j.start_at,t.id""")
    assignments = rows(db, """SELECT a.resource_id employee_id,j.id job_id,j.title,j.start_at,j.status
        FROM assignments a JOIN jobs j ON j.id=a.job_id
        WHERE a.resource_type='employee' AND j.status IN ('In progress','Completed')""")
    details = {e['id']: [] for e in employees}
    for t in entries:
        day = business_day(t['start_at'])
        if start <= day <= end:
            minutes = t['service_minutes'] + t['transport_minutes']
            item = dict(time_id=t['id'], job_id=t['job_id'], job_title=t['title'], date=day,
                        minutes=minutes, hours=minutes / 60, status=t['status'], job_status=t['job_status'], version=t['version'])
            if can_view_pay(user, t['employee_id']):
                item['approved_hourly_cents'] = t['approved_hourly_cents']
            details[t['employee_id']].append(item)
    missing = {e['id']: [] for e in employees}
    for a in assignments:
        day = business_day(a['start_at'])
        if start <= day <= end and not any(t['job_id'] == a['job_id'] for t in details[a['employee_id']]):
            missing[a['employee_id']].append(dict(job_id=a['job_id'], job_title=a['title'], date=day, status=a['status']))
    result = []
    for employee in employees:
        eid = employee['id']
        if user['role'] == 'crew' and eid != user.get('employee_id'):
            continue
        d, absent = details[eid], missing[eid]
        if not employee['active'] and not d and not absent:
            continue
        minutes = sum(t['minutes'] for t in d)
        approved = sum(t['minutes'] for t in d if t['status'] == 'Approved')
        pending = sum(t['status'] != 'Approved' for t in d)
        record = dict(employee_id=eid, employee_name=employee['name'], active=bool(employee['active']),
                      jobs=len(d), minutes=minutes, hours=round(minutes / 60, 2), approved_minutes=approved,
                      pending_entries=pending, missing_entries=len(absent), missing_jobs=absent, details=d,
                      status='Missing hours' if absent else 'Needs review' if pending else 'Ready' if d else 'No hours')
        if can_view_pay(user, eid):
            record.update(hourly_cents=employee['hourly_cents'], pay_rate_pending=rate_pending(employee),
                          gross_cents=sum(pay_cents(t['minutes'], t['approved_hourly_cents'] if t.get('approved_hourly_cents') is not None else employee['hourly_cents']) for t in d))
        result.append(record)
    fingerprint=hashlib.sha256(json.dumps([(e['employee_id'],e['missing_jobs'],[(t['time_id'],t['version'],t['job_status']) for t in e['details']]) for e in result],sort_keys=True).encode()).hexdigest()
    return dict(review_token=fingerprint,start=start, end=end, private=user['role'] in {'owner','manager'},
                can_manage_hours=user['role'] in {'owner','manager','accounting'}, employees=result,
                total_hours=round(sum(e['minutes'] for e in result) / 60, 2),
                approved_hours=round(sum(e['approved_minutes'] for e in result) / 60, 2),
                missing_entries=sum(e['missing_entries'] for e in result), pending_entries=sum(e['pending_entries'] for e in result))


def export_csv(db, user, start, end):
    if user['role'] not in {'owner', 'manager'}:
        raise HTTPException(403, 'Payroll exports are private to Owners and Managers.')
    report = summary(db, user, start, end)
    if report['missing_entries'] or report['pending_entries']:
        raise HTTPException(409, 'Review missing and submitted hours before exporting this range.')
    details = [(e, t) for e in report['employees'] for t in e['details']]
    if not details:
        raise HTTPException(409, 'There are no approved hours in this range.')
    if any(t['job_status'] != 'Completed' or t.get('approved_hourly_cents') is None for _, t in details):
        raise HTTPException(409, 'Complete services and approve this range to confirm pay rates before exporting.')
    def safe(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(['Employee ID','Employee','Service date','Job ID','Job','Approved minutes','Hours',
                     'Approved hourly rate CAD','Straight-time estimate CAD','Time ID','Version'])
    for e, t in details:
        rate = t['approved_hourly_cents']
        writer.writerow([safe(e['employee_id']), safe(e['employee_name']), t['date'], safe(t['job_id']), safe(t['job_title']),
                         t['minutes'], f"{t['minutes']/60:.6f}", f'{rate/100:.2f}', f"{pay_cents(t['minutes'],rate)/100:.2f}", t['time_id'],t['version']])
    audit(db, user['id'], 'Approved worked hours exported; payroll not paid', 'times', start+'..'+end, {'entries':len(details)})
    return Response(output.getvalue(), media_type='text/csv', headers={'Content-Disposition':
        f'attachment; filename="crownops-approved-hours-{start}-{end}.csv"', 'Cache-Control':'no-store'})
