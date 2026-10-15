"""Operational planning permissions and resource availability, without HR data."""
from fastapi import HTTPException
from .store import one, rows

OFFICE_PLANNERS={'owner','sales','dispatch','accounting'}

def can_plan_jobs(db,user):
    if user['role'] in OFFICE_PLANNERS:return True
    return bool(user['role']=='crew' and user.get('employee_id') and one(db,"""SELECT p.employee_id FROM employee_directory_profiles p
        JOIN employees e ON e.id=p.employee_id WHERE p.employee_id=? AND p.team_lead=1 AND e.active=1""",(user['employee_id'],)))

def require_planner(db,user):
    if not can_plan_jobs(db,user):raise HTTPException(403,'Crew cannot change job plans. Ask an owner, manager, sales team member or designated team lead.')

def planning_resources(db,job,start,end):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from .company_ops import fleet_reference_blockers
    first=datetime.fromisoformat(start).astimezone(ZoneInfo('America/Toronto')).date().isoformat()
    last=datetime.fromisoformat(end).astimezone(ZoneInfo('America/Toronto')).date().isoformat()
    result={}
    for key,table,kind in [('crew','employees','employee'),('trucks','vehicles','vehicle')]:
        items=[]
        for record in rows(db,'SELECT * FROM '+table+' ORDER BY name'):
            reasons=[]
            if not record['active']:reasons.append('Inactive — finish setup to make available')
            conflict=one(db,"""SELECT j.title FROM assignments a JOIN jobs j ON j.id=a.job_id
                WHERE a.resource_type=? AND a.resource_id=? AND j.id!=? AND j.status NOT IN ('Cancelled','Completed')
                AND j.start_at<? AND j.end_at>? LIMIT 1""",(kind,record['id'],job['id'],end,start))
            if conflict:reasons.append('Booked on '+conflict['title'])
            if kind=='employee':
                off=one(db,"SELECT id FROM time_off_requests WHERE employee_id=? AND status='Approved' AND start_date<=? AND end_date>=?",(record['id'],last,first))
                if off:reasons.append('Approved time off')
                profile=one(db,'SELECT license_expiry FROM staff_profiles WHERE employee_id=?',(record['id'],))
                if record.get('driver_verified') and profile and profile['license_expiry'] and profile['license_expiry']<first:
                    reasons.append('Driver licence expires before this job')
            else:
                profile=one(db,'SELECT inspection_due FROM fleet_profiles WHERE vehicle_id=?',(record['id'],))
                if profile and profile['inspection_due'] and profile['inspection_due']<first:reasons.append('Truck inspection expires before this job')
                if fleet_reference_blockers(db,record['id']):reasons.append('Truck setup / repair review required')
                if one(db,"SELECT id FROM inspections WHERE vehicle_id=? AND severity='Major' AND cleared_at IS NULL",(record['id'],)):reasons.append('Major defect — unavailable')
            items.append({'id':record['id'],'name':record['name'],'active':bool(record['active']),
                'available':not reasons,'reasons':reasons,'driver_verified':bool(record.get('driver_verified')) if kind=='employee' else False})
        result[key]=items
    return result
