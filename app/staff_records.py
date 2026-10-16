"""Private wage access and automatic records for new Crew invitations."""
import json
from fastapi import HTTPException
from .store import one, rows, insert, audit, uid, now

PAY_PENDING='contact_import_pay_pending'

def can_view_pay(user,employee_id):
    return user['role']=='owner' or bool(employee_id and user.get('employee_id')==employee_id)

def resolve_invited_employee(db,person,actor):
    if person.get('employee_id') or person['role']!='crew':return person.get('employee_id')
    matches=rows(db,'SELECT employee_id FROM employee_directory_profiles WHERE lower(trim(email))=lower(?)',(person['email'],))
    ids={r['employee_id'] for r in matches}
    if len(ids)>1:raise HTTPException(409,'This email matches more than one employee. Choose the correct existing employee record.')
    if ids:return next(iter(ids))
    eid=uid('EMP')
    insert(db,'employees',dict(id=eid,name=person['name'],role='Mover',active=1,driver_verified=0,hourly_cents=0,
        raw_json=json.dumps({PAY_PENDING:True,'created_from_staff_invitation':True})))
    insert(db,'employee_directory_profiles',dict(employee_id=eid,email=person['email'],updated_by=actor['id'],updated_at=now()))
    audit(db,actor['id'],'Employee record created automatically for Crew invitation','employees',eid)
    return eid
