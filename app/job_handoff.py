"""Assigned-team service briefs, truck-check entry points and reviewed protocols."""
from contextlib import closing
import json
from fastapi import Depends, HTTPException

from .job_routes import job_route
from .media_links import listing as media_links
from .platform_common import MANAGERS, OFFICE, put_setting, require_role, settings, staff_project_attachments, text
from .store import audit, connect, dumps, now, one, rows, transaction


CHECKLIST = [
    ('plan_reviewed', 'Before service', 'Review the route, access, client notes and photos.'),
    ('equipment_ready', 'Before service', 'Check the agreed materials and required equipment with the lead.'),
    ('client_walkthrough', 'Before service', 'Confirm the client’s priorities and any changes before work begins.'),
    ('final_walkthrough', 'After service', 'Complete the final walkthrough with the client.'),
    ('actuals_reviewed', 'After service', 'Review the actual service time and materials used.'),
    ('billing_handoff', 'After service', 'Check the invoice, deposit credit and payment status before the handoff.'),
]
CHECK_KEYS = {item[0] for item in CHECKLIST}
DEFAULT_PROTOCOLS = {
    'version': 0, 'title': 'Crew service guide', 'approved': False,
    'preparation': 'Read the assigned service details, photos and access notes. Ask the manager about missing information, materials or equipment before departure.',
    'service': 'Follow the company’s approved training and procedures. If a condition is unclear or unsafe, pause and contact the manager. Record incidents in Incident reports and notify the manager.',
    'completion': 'Review the completed work with the client. Record actual time and materials, check the invoice and deposit credit, and confirm the payment status with the office.',
    'updated_at': '', 'updated_by_name': '',
}


def _job_access(db, jid, user):
    require_role(user, MANAGERS | {'crew'})
    job = one(db, 'SELECT * FROM jobs WHERE id=?', (jid,))
    if not job:
        raise HTTPException(404, 'Service not found.')
    if user['role'] == 'crew' and not one(db, "SELECT 1 FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?", (jid, user.get('employee_id') or '')):
        raise HTTPException(403, 'Open a service assigned to you to use its crew brief and checklist.')
    return job


def _protocols(db):
    result = {**DEFAULT_PROTOCOLS, **(settings(db, 'crew_protocols', {}) or {})}
    if result.get('updated_by'):
        person = one(db, 'SELECT name FROM users WHERE id=?', (result['updated_by'],))
        result['updated_by_name'] = person['name'] if person else 'Management'
    result.pop('updated_by', None)
    return result


def _checklist(db, job, protocol_version):
    record = one(db, 'SELECT * FROM job_handoffs WHERE job_id=?', (job['id'],))
    checks = json.loads(record['checklist_json']) if record else {}
    saved_protocol_version = checks.pop('_protocol_version', 0)
    person = one(db, 'SELECT name FROM users WHERE id=?', (record['updated_by'],)) if record else None
    return {
        'version': record['version'] if record else 0,
        'job_version': job['version'], 'protocol_version': protocol_version,
        'review_required': bool(record and (record['job_version'] != job['version'] or saved_protocol_version != protocol_version)),
        'checks': {key: checks.get(key) is True for key in CHECK_KEYS},
        'items': [{'key': key, 'group': group, 'label': label} for key, group, label in CHECKLIST],
        'notes': record['notes'] if record else '',
        'updated_at': record['updated_at'] if record else '',
        'updated_by_name': person['name'] if person else '',
    }


def register_job_handoff(app, path, auth):
    @app.get('/api/crew-protocols')
    def get_protocols(u=Depends(auth)):
        require_role(u, OFFICE | {'crew'})
        with closing(connect(path)) as db:
            return {**_protocols(db), 'can_edit': u['role'] in MANAGERS}

    @app.put('/api/crew-protocols')
    def save_protocols(data: dict, u=Depends(auth)):
        require_role(u, MANAGERS)
        if set(data) - {'version', 'title', 'preparation', 'service', 'completion', 'reviewed'}:
            raise HTTPException(422, 'Review the crew guide fields before saving.')
        if data.get('reviewed') is not True:
            raise HTTPException(422, 'Confirm these instructions are approved for your team.')
        prepared = {key: text(data.get(key, ''), label, limit) for key, label, limit in [
            ('title', 'Guide title', 120), ('preparation', 'Before service instructions', 4000),
            ('service', 'During service instructions', 4000), ('completion', 'After service instructions', 4000)]}
        if any(not value for value in prepared.values()):
            raise HTTPException(422, 'Complete each section of the crew guide.')
        with transaction(path) as db:
            current = _protocols(db)
            if type(data.get('version')) is not int or data['version'] != current['version']:
                raise HTTPException(409, 'The crew guide changed. Reopen it before saving.')
            prepared.update(version=current['version'] + 1, approved=True, updated_by=u['id'], updated_at=now())
            put_setting(db, 'crew_protocols', prepared)
            audit(db, u['id'], 'Crew guide reviewed and published', 'settings', 'crew_protocols', {'version': prepared['version']})
            return {**_protocols(db), 'can_edit': True}

    @app.get('/api/jobs/{jid}/handoff')
    def get_handoff(jid: str, u=Depends(auth)):
        with closing(connect(path)) as db:
            job = _job_access(db, jid, u)
            lead = one(db, 'SELECT * FROM leads WHERE id=?', (job['lead_id'],))
            crew = rows(db, """SELECT e.id,e.name,e.driver_verified,coalesce(p.team_lead,0) team_lead
                FROM assignments a JOIN employees e ON e.id=a.resource_id
                LEFT JOIN employee_directory_profiles p ON p.employee_id=e.id
                WHERE a.job_id=? AND a.resource_type='employee' ORDER BY e.name""", (jid,))
            trucks = []
            for truck in rows(db, "SELECT v.id,v.name,v.active FROM assignments a JOIN vehicles v ON v.id=a.resource_id WHERE a.job_id=? AND a.resource_type='vehicle' ORDER BY v.name", (jid,)):
                profile = one(db, 'SELECT plate,odometer_km,check_required FROM fleet_profiles WHERE vehicle_id=?', (truck['id'],)) or {}
                check = one(db, 'SELECT checked_at,severity,notes,cleared_at FROM inspections WHERE vehicle_id=? ORDER BY checked_at DESC LIMIT 1', (truck['id'],))
                trucks.append({**truck, 'plate': profile.get('plate', ''), 'odometer_km': profile.get('odometer_km', 0),
                               'check_required': bool(profile.get('check_required')), 'latest_check': check})
            quote = one(db, "SELECT id,revision,status,result_json FROM quotes WHERE id=? AND lead_id=? AND status='Accepted'", (job.get('quote_id'), job['lead_id']))
            materials = []
            if quote:
                for line in json.loads(quote['result_json']).get('line_items', []):
                    if line.get('kind') == 'material' or line.get('code'):
                        materials.append({'name': line.get('name', 'Material'), 'quantity': line.get('qty', 0), 'note': line.get('note', '')})
            try:
                intake = json.loads(lead.get('intake_json') or '{}')
            except (TypeError, ValueError):
                intake = {}
            if not isinstance(intake, dict):
                intake = {}
            heavy = intake.get('heavy_items')
            special = [lead.get('heavy_items', ''), *(heavy if isinstance(heavy, list) else [])]
            client = {key: lead.get(key, '') for key in ('client_name', 'phone', 'home_size', 'boxes', 'inventory')}
            client['heavy_items'] = ' · '.join(str(value) for value in special if value)
            client['service_details'] = intake.get('service_details') if isinstance(intake.get('service_details'), str) else ''
            extras = intake.get('additional_services')
            client['additional_services'] = [value for value in extras if isinstance(value, str)] if isinstance(extras, list) else []
            files = [f for f in staff_project_attachments(db, job['lead_id'], u) if not f.get('job_id') or f['job_id'] == jid]
            links = [link for link in media_links(db, job['lead_id'], u) if not link.get('job_id') or link['job_id'] == jid]
            protocols = _protocols(db)
            return {
                'job': {key: job[key] for key in ('id', 'lead_id', 'title', 'status', 'start_at', 'end_at', 'version', 'notes')},
                'client': client,
                'route': job_route(job, lead), 'crew': crew, 'leads': [person['name'] for person in crew if person['team_lead']],
                'trucks': trucks, 'materials': materials,
                'quote': {'id': quote['id'], 'revision': quote['revision']} if quote else None,
                'photo_count': sum(str(f.get('mime', '')).startswith('image/') for f in files),
                'video_count': sum(str(f.get('mime', '')).startswith('video/') for f in files),
                'media_links': links, 'protocols': {**protocols, 'can_edit': u['role'] in MANAGERS},
                'checklist': _checklist(db, job, protocols['version']),
                'can_check_truck': job['status'] not in ('Completed', 'Cancelled'),
                'can_update': job['status'] != 'Cancelled',
            }

    @app.put('/api/jobs/{jid}/handoff')
    def save_handoff(jid: str, data: dict, u=Depends(auth)):
        if set(data) - {'version', 'job_version', 'protocol_version', 'checks', 'notes'}:
            raise HTTPException(422, 'Review the service checklist fields before saving.')
        checks = data.get('checks')
        if not isinstance(checks, dict) or set(checks) != CHECK_KEYS or any(type(value) is not bool for value in checks.values()):
            raise HTTPException(422, 'Review each service checklist item.')
        notes = text(data.get('notes', ''), 'Crew handoff notes', 4000)
        with transaction(path) as db:
            job = _job_access(db, jid, u)
            if job['status'] == 'Cancelled':
                raise HTTPException(409, 'This service is cancelled. Its crew checklist is preserved.')
            protocols = _protocols(db)
            current = _checklist(db, job, protocols['version'])
            if any(type(data.get(key)) is not int or data[key] != current[key] for key in ('version', 'job_version', 'protocol_version')):
                raise HTTPException(409, 'The service or checklist changed. Reopen the crew brief before saving.')
            saved = {**checks, '_protocol_version': protocols['version']}
            db.execute('''INSERT INTO job_handoffs(job_id,job_version,checklist_json,notes,version,updated_by,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET job_version=excluded.job_version,
                checklist_json=excluded.checklist_json,notes=excluded.notes,version=excluded.version,
                updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                (jid, job['version'], dumps(saved), notes, current['version'] + 1, u['id'], now()))
            audit(db, u['id'], 'Crew service checklist saved', 'job_handoffs', jid,
                  {'job_version': job['version'], 'checked': sorted(key for key in CHECK_KEYS if checks[key]), 'protocol_version': protocols['version']})
            return _checklist(db, job, protocols['version'])
