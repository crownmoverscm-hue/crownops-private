"""CrownOps v5.4 final review read models.

Adds clean job/client summaries for the private review build. No live email,
payments, QuickBooks or production schedule actions are introduced here.
"""
from contextlib import closing
import json
from fastapi import Depends, HTTPException

from .job_routes import job_route
from .staff_records import schedule_identity
from .store import connect, rows, one
from .platform_common import require_role, OFFICE, invoice_current, staff_project_attachments
from .ux_features import _job_readiness, _local_date

STAFF = OFFICE | {'crew'}


def _payment_summary(db, lead_id, invoice_id=None, deposit_required_cents=0):
    pays = rows(db, "SELECT * FROM payments WHERE lead_id=? ORDER BY created_at", (lead_id,))
    deposits = [p for p in pays if p['purpose'] == 'Deposit' and not p.get('reverses_id')]
    verified_deposit = sum(int(p['amount_cents'] or 0) for p in deposits if p['verified'])
    recorded_deposit = sum(int(p['amount_cents'] or 0) for p in deposits)
    out = {
        'deposit_required_cents': int(deposit_required_cents or 0),
        'deposit_recorded_cents': recorded_deposit,
        'deposit_verified_cents': verified_deposit,
        'deposit_status': 'Not required' if not deposit_required_cents else ('Verified' if verified_deposit >= deposit_required_cents else ('Recorded - verify' if recorded_deposit else 'Missing')),
        'invoice_status': 'Not prepared',
        'balance_cents': None,
        'payment_status': 'Not invoiced',
        'payment_method': '',
    }
    if invoice_id:
        inv = one(db, 'SELECT * FROM job_invoices WHERE id=?', (invoice_id,))
        if inv:
            out['invoice_status'] = inv['status']
            try:
                _, calc = invoice_current(db, invoice_id)
                out['balance_cents'] = int(calc['balance_cents'])
                pending = rows(db, """SELECT p.* FROM field_collections c JOIN payments p ON p.id=c.payment_id
                    WHERE c.invoice_id=? ORDER BY p.created_at DESC""", (invoice_id,))
                verified = [p for p in pending if p['verified']]
                unverified = [p for p in pending if not p['verified']]
                if out['balance_cents'] <= 0:
                    out['payment_status'] = 'PAID'
                elif unverified:
                    out['payment_status'] = 'PAYMENT TO VERIFY'
                elif inv['status'] == 'Issued':
                    out['payment_status'] = 'BALANCE DUE'
                else:
                    out['payment_status'] = 'Invoice draft'
                if pending:
                    out['payment_method'] = pending[0]['method']
                elif verified:
                    out['payment_method'] = verified[0]['method']
            except Exception:
                pass
    return out


def _quote_summary(db, j):
    q = one(db, 'SELECT * FROM quotes WHERE id=?', (j['quote_id'],)) if j.get('quote_id') else None
    if not q:
        return None
    try:
        result = json.loads(q['result_json'])
        inputs = json.loads(q['inputs_json'])
    except Exception:
        result, inputs = {}, {}
    return {
        'id': q['id'], 'revision': q['revision'], 'status': q['status'],
        'accepted_at': q.get('accepted_at'), 'approved_at': q.get('approved_at'),
        'movers': result.get('movers'), 'trucks': result.get('trucks'),
        'service_type': result.get('service_type') or inputs.get('service_type'),
        'service_low': result.get('service_low'), 'service_high': result.get('service_high'),
        'hourly_rate_cents': result.get('hourly_rate_cents'), 'minimum_hours': result.get('minimum_hours'),
        'transport_hours': result.get('transport_hours'), 'transport_cents': result.get('transport_cents'),
        'transport_mode': inputs.get('transport_mode'), 'fixed_transport': inputs.get('fixed_transport'),
        'fuel_cents': result.get('fuel_cents'), 'discount_cents': result.get('discount_cents', 0),
        'low_cents': result.get('low_cents'), 'high_cents': result.get('high_cents'),
        'deposit_required_cents': round(float(inputs.get('deposit_required', 300) or 0) * 100),
    }


def register_final_features(app, path, auth):
    @app.get('/api/ux/final/job/{jid}')
    def final_job(jid: str, u=Depends(auth)):
        if u['role'] not in STAFF:
            raise HTTPException(403, 'Staff only.')
        with closing(connect(path)) as db:
            j = one(db, 'SELECT * FROM jobs WHERE id=?', (jid,))
            if not j:
                raise HTTPException(404, 'Job not found.')
            assigned = rows(db, """SELECT a.resource_type,a.resource_id,
                CASE WHEN a.resource_type='employee' THEN e.name ELSE v.name END name
                FROM assignments a
                LEFT JOIN employees e ON a.resource_type='employee' AND e.id=a.resource_id
                LEFT JOIN vehicles v ON a.resource_type='vehicle' AND v.id=a.resource_id
                WHERE a.job_id=?""", (jid,))
            crew = [r for r in assigned if r['resource_type'] == 'employee']
            trucks = [r for r in assigned if r['resource_type'] == 'vehicle']
            if u['role'] == 'crew' and not any(r['resource_id'] == u.get('employee_id') for r in crew):
                # Crew may view All Jobs, but this endpoint remains read-only by convention.
                pass
            lead = one(db, 'SELECT * FROM leads WHERE id=?', (j['lead_id'],))
            if u['role']=='crew' and not any(r['resource_id']==u.get('employee_id') for r in crew):lead.pop('photo_link',None)
            quote = _quote_summary(db, j)
            inv = one(db, 'SELECT id,number,status,revision,updated_at FROM job_invoices WHERE job_id=?', (jid,))
            pay = _payment_summary(db, j['lead_id'], inv['id'] if inv else None, quote['deposit_required_cents'] if quote else 0)
            files = staff_project_attachments(db, j['lead_id'], u)
            images = [f for f in files if str(f.get('mime') or '').startswith('image/')]
            services = rows(db, 'SELECT id,title,phase,start_at,end_at,status,quote_id FROM jobs WHERE lead_id=? ORDER BY start_at', (j['lead_id'],))
            timeline = []
            for q in rows(db, 'SELECT id,revision,status,created_at,accepted_at FROM quotes WHERE lead_id=? ORDER BY revision', (j['lead_id'],)):
                timeline.append({'at': q.get('accepted_at') or q['created_at'], 'kind': 'Quote', 'title': f"Quote v{q['revision']} - {q['status']}"})
            for s in services:
                timeline.append({'at': s['start_at'], 'kind': 'Service', 'title': f"{s['phase']} - {s['status']}"})
            for p in rows(db, 'SELECT amount_cents,method,purpose,verified,created_at FROM payments WHERE lead_id=? ORDER BY created_at', (j['lead_id'],)):
                timeline.append({'at': p['created_at'], 'kind': 'Payment', 'title': f"{p['purpose']} - {'Verified' if p['verified'] else 'To verify'}", 'detail': f"{p['method']}"})
            timeline.sort(key=lambda x: x.get('at') or '', reverse=True)
            return {
                'job': {k: j[k] for k in ('id','lead_id','title','phase','start_at','end_at','status','notes','capacity_reviewed','version')},
                'client': lead,
                'route_stops':job_route(j,lead),
                'crew': crew,
                'trucks': trucks,
                'quote': quote,
                'invoice': inv,
                'payments': pay,
                'readiness': _job_readiness(db, j),
                'photo_count': len(images),
                'files_count': len(files),
                'services': services,
                'timeline': timeline[:25],
            }


    @app.get('/api/schedule-identity')
    def personal_schedule_identity(u=Depends(auth)):
        require_role(u, STAFF)
        with closing(connect(path)) as db:return schedule_identity(db,u)

    @app.get('/api/ux/final/schedule')
    def final_schedule(start: str='', end: str='', scope: str='all', u=Depends(auth)):
        if u['role'] not in STAFF:
            raise HTTPException(403, 'Staff only.')
        if scope not in ('all','mine'):raise HTTPException(422,'Choose My Jobs or All Jobs.')
        with closing(connect(path)) as db:
            identity=schedule_identity(db,u)
            all_jobs = rows(db, "SELECT * FROM jobs WHERE status!='Cancelled' ORDER BY start_at")
            result=[]
            for j in all_jobs:
                # ISO timestamps sort safely for the review build; the frontend also filters by Montreal-local day.
                day = _local_date(j['start_at'])
                if start and day < start: continue
                if end and day > end: continue
                crew = rows(db, "SELECT e.id,e.name,e.driver_verified FROM assignments a JOIN employees e ON e.id=a.resource_id WHERE a.job_id=? AND a.resource_type='employee' ORDER BY e.name", (j['id'],))
                is_mine=bool(identity['id'] and any(e['id']==identity['id'] for e in crew))
                if scope=='mine' and not is_mine:continue
                trucks = rows(db, "SELECT v.id,v.name,v.active,v.color_hex,v.display_order FROM assignments a JOIN vehicles v ON v.id=a.resource_id WHERE a.job_id=? AND a.resource_type='vehicle' ORDER BY v.name", (j['id'],))
                if u['role']=='crew' and j['id'] and not any(e['id']==u.get('employee_id') for e in crew):
                    # All Jobs remains visible to crew; edits are still controlled elsewhere.
                    pass
                q=_quote_summary(db,j)
                inv=one(db,'SELECT id,number,status FROM job_invoices WHERE job_id=?',(j['id'],))
                pay=_payment_summary(db,j['lead_id'],inv['id'] if inv else None,q['deposit_required_cents'] if q else 0)
                lead=one(db,'SELECT client_name,pickup,dropoff,phone,email FROM leads WHERE id=?',(j['lead_id'],)) or {}
                photos=sum(str(f.get('mime') or '').startswith('image/') for f in staff_project_attachments(db,j['lead_id'],u))
                result.append({
                    'id':j['id'],'lead_id':j['lead_id'],'title':j['title'],'phase':j['phase'],'start_at':j['start_at'],'end_at':j['end_at'],'status':j['status'],
                    'client':lead,'crew':crew,'trucks':trucks,'quote':q,'invoice':inv,'payments':pay,'readiness':_job_readiness(db,j),'photo_count':photos,'assigned_to_me':is_mine
                })
            return result

    @app.get('/api/ux/final/client/{lid}')
    def final_client(lid: str, u=Depends(auth)):
        require_role(u, OFFICE)
        with closing(connect(path)) as db:
            lead = one(db, 'SELECT * FROM leads WHERE id=?', (lid,))
            if not lead:
                raise HTTPException(404, 'Client not found.')
            jobs = rows(db, 'SELECT * FROM jobs WHERE lead_id=? ORDER BY start_at', (lid,))
            out = []
            for j in jobs:
                q = _quote_summary(db, j)
                inv = one(db, 'SELECT id,number,status FROM job_invoices WHERE job_id=?', (j['id'],))
                crew = rows(db, "SELECT e.name FROM assignments a JOIN employees e ON e.id=a.resource_id WHERE a.job_id=? AND a.resource_type='employee' ORDER BY e.name", (j['id'],))
                trucks = rows(db, "SELECT v.name FROM assignments a JOIN vehicles v ON v.id=a.resource_id WHERE a.job_id=? AND a.resource_type='vehicle' ORDER BY v.name", (j['id'],))
                photos = one(db, "SELECT count(*) n FROM attachments WHERE lead_id=? AND deleted_at IS NULL AND mime LIKE 'image/%'", (lid,))
                pay = _payment_summary(db, lid, inv['id'] if inv else None, q['deposit_required_cents'] if q else 0)
                out.append({
                    'id': j['id'], 'phase': j['phase'], 'start_at': j['start_at'], 'end_at': j['end_at'], 'status': j['status'],
                    'crew_names': [r['name'] for r in crew], 'truck_names': [r['name'] for r in trucks],
                    'quote': q, 'invoice': inv, 'payments': pay, 'readiness': _job_readiness(db, j),
                    'photo_count': int((photos or {}).get('n') or 0),
                })
            return {'client': lead, 'services': out}
