"""Reviewed, reversible archiving of untouched Gmail callback imports."""
from datetime import date, datetime
from zoneinfo import ZoneInfo
import hashlib
import json

from fastapi import HTTPException

from .store import rows, one, audit, dumps, now
from .business_zone import BUSINESS_ZONE, zone_name

TORONTO = BUSINESS_ZONE
CALL_TITLES = ('Return missed call', 'Review voicemail')


def _meta(value):
    try:
        result = json.loads(value or '{}')
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def _call_day(call):
    # Only a timezone-aware source timestamp establishes age for cleanup.
    detail = _meta(call.get('payload_json'))
    try:
        stamp = datetime.fromisoformat(detail.get('occurred_at') or call['created_at'])
        return stamp.astimezone(TORONTO).date().isoformat() if stamp.tzinfo else ''
    except (ValueError, TypeError, KeyError):
        return ''


def callback_backlog_preview(db, before):
    try:
        cutoff = date.fromisoformat(str(before))
    except (ValueError, TypeError):
        raise HTTPException(422, 'Choose a valid callback cutoff date.')
    if cutoff > datetime.now(TORONTO).date():
        raise HTTPException(422, 'Callback cutoff cannot be in the future.')
    before = cutoff.isoformat()
    candidates = []
    tasks = rows(db, """SELECT t.*,l.client_name,l.phone,l.version lead_version
      FROM tasks t JOIN leads l ON l.id=t.lead_id
      WHERE t.status='Open' AND t.historical=0 AND t.owner='' AND l.assigned_to=''
        AND l.stage='New Lead' AND t.title IN ('Return missed call','Review voicemail')
        AND NOT EXISTS (SELECT 1 FROM quotes q WHERE q.lead_id=l.id)
        AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.lead_id=l.id)
        AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.lead_id=l.id)
        AND NOT EXISTS (SELECT 1 FROM action_outcomes o WHERE o.lead_id=l.id)
      ORDER BY t.created_at,t.id""")
    for task in tasks:
        meta = _meta(task.get('raw_json'))
        if not meta.get('call_id') or not meta.get('gmail_id'):
            continue
        calls = rows(db, 'SELECT * FROM calls WHERE lead_id=?', (task['lead_id'],))
        call = next((c for c in calls if c['id'] == meta['call_id']), None)
        if not call:
            continue
        detail = _meta(call.get('payload_json'))
        if detail.get('source') != 'Gmail / UniFi' or detail.get('gmail_id') != meta['gmail_id']:
            continue
        if not calls or any(not _call_day(c) or _call_day(c) >= before for c in calls):
            continue
        active = rows(db, "SELECT title,raw_json FROM tasks WHERE lead_id=? AND historical=0 AND status!='Done'", (task['lead_id'],))
        if any(t['title'] not in CALL_TITLES or not _meta(t['raw_json']).get('call_id') for t in active):
            continue
        candidates.append(dict(id=task['id'], lead_id=task['lead_id'], client_name=task['client_name'],
            phone=task['phone'], title=task['title'], due_date=task['due_date'],
            call_at=detail.get('occurred_at') or call['created_at'], source='Gmail / UniFi',
            lead_version=task['lead_version'], raw_json=task['raw_json']))
    token = hashlib.sha256(dumps({'before': before, 'candidates': candidates}).encode()).hexdigest()
    return {'before': before, 'candidates': candidates, 'review_token': token}


def archive_callbacks(db, data, actor):
    preview = callback_backlog_preview(db, data.get('before', ''))
    if data.get('review_token') != preview['review_token']:
        raise HTTPException(409, 'The callback list changed. Review it again before archiving.')
    selected = data.get('task_ids')
    if not isinstance(selected, list) or not selected or len(selected) > 500 or any(not isinstance(x, str) for x in selected):
        raise HTTPException(422, 'Select between 1 and 500 reviewed callbacks.')
    selected = set(selected)
    eligible = {c['id']: c for c in preview['candidates']}
    if not selected.issubset(eligible):
        raise HTTPException(409, 'Only callbacks from the reviewed list can be archived.')
    for tid in sorted(selected):
        meta = _meta(eligible[tid]['raw_json'])
        meta['callback_archive'] = {'actor': actor, 'at': now(), 'before': preview['before']}
        db.execute('UPDATE tasks SET historical=1,raw_json=? WHERE id=?', (dumps(meta), tid))
        audit(db, actor, 'Reviewed callback archived', 'tasks', tid, {'before': preview['before'], 'lead_id': eligible[tid]['lead_id']})
    return {'ok': True, 'archived': len(selected)}


def restore_callback(db, tid, actor):
    task = one(db, 'SELECT * FROM tasks WHERE id=?', (tid,))
    if not task:
        raise HTTPException(404, 'Callback not found.')
    meta = _meta(task.get('raw_json'))
    if not task['historical'] or task['status'] == 'Done' or not meta.get('callback_archive'):
        raise HTTPException(409, 'This callback is not an open archived callback.')
    active = one(db, """SELECT id FROM tasks WHERE lead_id=? AND historical=0 AND status!='Done'
      AND title IN ('Return missed call','Review voicemail') LIMIT 1""", (task['lead_id'],))
    if active:
        raise HTTPException(409, 'This client already has an active callback. Open that callback first.')
    lead = one(db, 'SELECT assigned_to FROM leads WHERE id=?', (task['lead_id'],)) or {}
    owner = lead.get('assigned_to') or task['owner']
    archived = meta.pop('callback_archive')
    meta['callback_restored'] = {'actor': actor, 'at': now()}
    db.execute('UPDATE tasks SET historical=0,raw_json=?,owner=? WHERE id=?', (dumps(meta), owner, tid))
    audit(db, actor, 'Archived callback restored', 'tasks', tid, {'archive': archived})
    return {'ok': True}
