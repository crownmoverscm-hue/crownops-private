"""Authenticated read-only migration overlay. Does not confirm imported jobs."""
import json
from contextlib import closing
from fastapi import Depends
from .store import connect,transaction
from .schedule_importer import setup

def register_schedule_imports(app,path,auth):
    with transaction(path) as db:setup(db)
    @app.get('/api/schedule-imports/events')
    def events(u=Depends(auth)):
        # User requested operational calendar/client details for all staff.
        # Payroll, bank data and private staff notes are not in this endpoint.
        with closing(connect(path)) as db:
            events=[json.loads(r[0]) for r in db.execute('SELECT payload_json FROM imported_schedule_events ORDER BY job_date,source_key')]
            promoted={r[0]:r[1] for r in db.execute('SELECT source_key,job_id FROM source_promotions')}
            for e in events:e['promoted_job_id']=promoted.get(e['source_key'])
            return events
    @app.get('/api/schedule-imports/summary')
    def summary(u=Depends(auth)):
        with closing(connect(path)) as db:
            return {'total':db.execute('SELECT COUNT(*) FROM imported_schedule_events').fetchone()[0],
              'state':'Imported source records; review required; no messages or financial postings.',
              'batches':[json.loads(r[0]) for r in db.execute('SELECT report_json FROM schedule_import_batches ORDER BY created_at DESC')]}
