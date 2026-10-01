"""Lossless staging import of the CS worksheet, never a production booking write.

Dates are read from each job's J cell, not guessed from row position. Source
statuses, unknown/afternoon times and comments are preserved. Repeated deposits
are evidence only, not receipt entries. A repeat import is idempotent; changed
source rows require review rather than overwriting prior imports.
"""
from collections import Counter
from datetime import date, datetime, time
from io import BytesIO
import hashlib, json, re, zipfile
from openpyxl import load_workbook
from .store import initialize, transaction, rows, insert, dumps, now, audit

SCHEMA = '''
CREATE TABLE IF NOT EXISTS schedule_import_batches(
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, filename TEXT NOT NULL,
 range_start TEXT NOT NULL, range_end TEXT NOT NULL, created_at TEXT NOT NULL,
 report_json TEXT NOT NULL, UNIQUE(sha256,range_start,range_end));
CREATE TABLE IF NOT EXISTS imported_schedule_events(
 id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES schedule_import_batches(id),
 source_key TEXT NOT NULL UNIQUE, job_date TEXT NOT NULL, client_name TEXT NOT NULL,
 email TEXT, phone TEXT, service TEXT, time_label TEXT, payload_json TEXT NOT NULL,
 disposition TEXT NOT NULL DEFAULT 'Imported - review required',
 promoted_job_id TEXT, notifications_suppressed INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS imported_schedule_notes(
 id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES schedule_import_batches(id),
 source_key TEXT NOT NULL UNIQUE,payload_json TEXT NOT NULL);
'''

def setup(db):
    # Statements run inside caller transaction; executescript would commit it.
    for sql in SCHEMA.split(';'):
        if sql.strip(): db.execute(sql)

def scalar(value):
    if isinstance(value,(datetime,date,time)): return value.isoformat()
    if hasattr(value,'text') and hasattr(value,'ref'):
        return {'kind':type(value).__name__,'formula':value.text,'ref':value.ref}
    if isinstance(value,(str,int,float,bool,type(None))):return value
    return str(value)

def text(value):
    if value is None:return ''
    if isinstance(value,float) and value.is_integer():return str(int(value))
    return str(scalar(value)).strip()

def day(value):
    if isinstance(value,datetime):return value.date()
    if isinstance(value,date):return value
    if isinstance(value,str):
        try:return date.fromisoformat(value.strip()[:10])
        except ValueError:return None
    return None

def hours(value):
    """Only parse unambiguous two-component hour shorthand; never evaluate it."""
    m=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)\s*',text(value))
    return {'service':float(m[1]),'transport':float(m[2])} if m else None

def raw_cell(c, cached):
    out={'cell':c.coordinate,'value':scalar(c.value),'cached':scalar(cached.value),
         'data_type':c.data_type,'number_format':c.number_format}
    if c.comment:out['comment']=c.comment.text
    if c.hyperlink:out['hyperlink']=c.hyperlink.target or c.hyperlink.location
    return out

def parse(content, start='2026-09-27', end='2027-12-31', filename='Crown Schedule (2).xlsx'):
    lo=date.fromisoformat(start);hi=date.fromisoformat(end)
    if lo>hi:raise ValueError('Start must precede end.')
    if len(content)>15_000_000:raise ValueError('Workbook is too large.')
    with zipfile.ZipFile(BytesIO(content)) as z:
        if sum(x.file_size for x in z.infolist())>160_000_000:raise ValueError('Expanded workbook too large.')
    w=load_workbook(BytesIO(content),data_only=False);v=load_workbook(BytesIO(content),data_only=True)
    if 'CS' not in w:raise ValueError('Missing CS worksheet.')
    s=w['CS'];c=v['CS'];events=[];notes=[];undated=[];source_dates=[]
    date_rows=[(r,day(s.cell(r,10).value)) for r in range(3,s.max_row+1) if day(s.cell(r,10).value)]
    cutoff=max([r for r,d in date_rows if lo<=d<=hi],default=2)
    for r in range(3,cutoff+1):
        d=day(s.cell(r,10).value);name=text(s.cell(r,4).value);service=text(s.cell(r,3).value)
        if d and lo<=d<=hi:source_dates.append(d.isoformat())
        is_job=bool(name and service and not name.lower().startswith('team '))
        if not is_job:continue
        raw=[raw_cell(x,c[x.coordinate]) for x in next(s.iter_rows(min_row=r,max_row=r,max_col=46)) if x.value is not None or x.comment or x.hyperlink]
        if not d:
            if r<=cutoff:undated.append({'row':r,'client':name,'service':service,'raw_cells':raw,'reason':'No explicit date; not inserted into dated calendar.'})
            continue
        if not lo<=d<=hi:continue
        lines=name.splitlines();first=lines[0].strip();email=re.search(r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}',name,re.I)
        email=email[0].lower() if email else ''
        n=first if '@' not in first else first.split(email)[0].strip(' |')
        flags=[]
        tm=s.cell(r,11).value
        time_label=tm.strftime('%H:%M') if isinstance(tm,(time,datetime)) else text(tm) or 'Time not supplied'
        if not isinstance(tm,(time,datetime)):flags.append('START_TIME_NOT_EXACT')
        dep=text(s.cell(r,12).value)
        if isinstance(s.cell(r,12).value,time) and s.cell(r,12).value.hour<5 and str(tm).lower()=='afternoon':flags.append('DEPARTURE_AM_PM_AMBIGUOUS')
        worker=text(s.cell(r,18).value); pickup=text(s.cell(r,19).value)
        stops=[{'source_cell':s.cell(r,col).coordinate,'address':text(s.cell(r,col).value)} for col in range(20,26) if text(s.cell(r,col).value)]
        if not pickup or not re.search(r'\d',pickup):flags.append('PICKUP_ADDRESS_INCOMPLETE')
        if not stops:flags.append('DESTINATION_MISSING_OR_INTERNAL_MOVE')
        if any(not re.search(r'\d',x['address']) for x in stops):flags.append('DESTINATION_ADDRESS_INCOMPLETE')
        if worker and re.match(r'^\d+\s',worker):flags.append('ADDRESS_IN_WORKERS_COLUMN')
        if not email:flags.append('CLIENT_EMAIL_MISSING')
        if not s.cell(r,2).value:flags.append('CREW_SIZE_MISSING')
        if not worker:flags.append('CREW_NOT_ASSIGNED')
        if re.search(r'\bOR\b',worker):flags.append('CREW_ALTERNATIVE_UNRESOLVED')
        if 'TBD' in text(s.cell(r,7).value).upper():flags.append('DATE_TBD_IN_SOURCE')
        if s.cell(r,17).value is not None:flags.append('ACTUAL_HOURS_PRESENT_STATUS_NOT_RECONCILED')
        if s.cell(r,14).value is not None or s.cell(r,15).value is not None:flags.append('PAYMENT_EVIDENCE_NOT_BANK_VERIFIED')
        if re.search('video|testimonial',name+' '+text(s.cell(r,7).value),re.I):flags.append('MEDIA_LABEL_IS_NOT_CONSENT')
        # Truck label belongs to the preceding Team heading; do not cross an
        # earlier different-date record or any other Team heading.
        truck='';team='';header_ref=''
        for p in range(r-1,max(2,r-10),-1):
            pd=day(s.cell(p,10).value)
            if pd and pd!=d:break
            pn=text(s.cell(p,4).value)
            if pn.lower().startswith('team '):
                team=pn;candidate=text(s.cell(p,18).value)
                if re.fullmatch(r'(Alpha|Bravo|Charlie|Delta|Echo|Foxtrot|Fuso)(\s*\+\s*(Alpha|Bravo|Charlie|Delta|Echo|Foxtrot|Fuso))*',candidate,re.I):truck=candidate;header_ref='CS!'+s.cell(p,18).coordinate
                break
        if not truck:flags.append('TRUCK_NOT_IDENTIFIED')
        row_payload={'row':r,'raw':raw}
        digest=hashlib.sha256(dumps(row_payload).encode()).hexdigest()
        events.append({'id':'CS-'+digest[:20],'source_key':'CS:'+str(r)+':'+digest,
          'source_ref':f'{filename} / CS!A{r}:AT{r}','source_row':r,'date':d.isoformat(),'timezone':'America/Toronto',
          'client_name':n,'client_label_raw':name,'email':email,'phone':text(s.cell(r,5).value),'service':service,
          'sales_owner':text(s.cell(r,1).value),'crew_size':scalar(s.cell(r,2).value),'crew_raw':worker,
          'team':team,'truck_label':truck,'truck_source':header_ref,
          'time_label':time_label,'start_time':tm.strftime('%H:%M:%S') if isinstance(tm,(time,datetime)) else None,
          'depart_base_raw':dep,'source_status':text(s.cell(r,9).value),'quoted_hours_raw':text(s.cell(r,13).value),
          'quoted_components':hours(s.cell(r,13).value),'actual_hours_raw':text(s.cell(r,17).value),
          'actual_components':hours(s.cell(r,17).value),'deposit_raw':scalar(s.cell(r,14).value),'payment_raw':scalar(s.cell(r,15).value),
          'pickup_raw':pickup,'dropoffs_raw':stops,'notes_raw':text(s.cell(r,7).value),'referral_raw':text(s.cell(r,6).value),
          'raw_cells':raw,'flags':flags,'disposition':'Imported - review required','notifications_suppressed':True,
          'links':[{'cell':x['cell'],'url':x['hyperlink']} for x in raw if x.get('hyperlink')],
          'comments':[{'cell':x['cell'],'text':x['comment']} for x in raw if x.get('comment')]})
    # Keep nearby calendar annotations with neighboring-date evidence, not
    # guessed assignment. Historical content below the scope is still audited
    # by the separate full-cell preservation export.
    event_rows={x['source_row'] for x in events}
    for r in range(1,cutoff+1):
        if r in event_rows or r==2:continue
        if not any(x.value is not None or x.comment or x.hyperlink for x in next(s.iter_rows(min_row=r,max_row=r,max_col=46))):continue
        n=text(s.cell(r,4).value)
        if n.lower().startswith('team ') and not s.cell(r,18).value:continue
        raw=[raw_cell(x,c[x.coordinate]) for x in next(s.iter_rows(min_row=r,max_row=r,max_col=46)) if x.value is not None or x.comment or x.hyperlink]
        significant=[x for x in raw if x['cell'][0] in 'ADR' and x['value'] and not text(x['value']).lower().startswith('team ')]
        if not significant:continue
        previous=next(((rr,dd) for rr,dd in reversed(date_rows) if rr<r),None)
        following=next(((rr,dd) for rr,dd in date_rows if rr>r),None)
        label=' | '.join(text(x['value']) for x in significant)
        category='Operational annotation'
        if 'closed' in label.lower():category='Closure note'
        elif 'off' in label.lower():category='Staff availability note'
        elif 'UCI' in label:category='Historical road advisory - not reverified'
        elif re.search('wedding',label,re.I):category='Private management note'
        elif r in (5,6):category='Undated client/service'
        # simple date-proximate closure notes can retain proposed dates only
        date_candidate=following[1].isoformat() if category=='Closure note' and following and following[0]-r<=3 else None
        notes.append({'id':f'CS-NOTE-{r}','row':r,'source_ref':f'{filename} / CS!{r}:{r}',
          'category':category,'label':label,'date_candidate':date_candidate,'date_confirmed':False,
          'previous_date':previous[1].isoformat() if previous else None,
          'next_date':following[1].isoformat() if following else None,'raw_cells':raw,
          'review':'Proposed date needs review; retain original cell placement.'})
    counts=Counter(x['email'] or x['client_label_raw'] for x in events)
    for e in events:
        if counts[e['email'] or e['client_label_raw']]>1:e['flags'].append('MULTIPLE_SERVICE_ROWS_RECONCILE_PROJECT_AND_DEPOSIT')
    events.sort(key=lambda x:(x['date'],x['start_time'] or '99',x['source_row']))
    return {'schema_version':1,'filename':filename,'sha256':hashlib.sha256(content).hexdigest(),
      'range_start':start,'range_end':end,'events':events,'notes':notes,'undated':undated,
      'summary':{'service_rows':len(events),'distinct_client_keys':len(counts),'by_month':dict(sorted(Counter(e['date'][:7] for e in events).items())),
        'dated_service_rows_2027':sum(e['date'].startswith('2027') for e in events),'calendar_date_last_in_source':max(source_dates,default=None),
        'undated_client_rows':len(undated),'comment_count_in_service_rows':sum(len(e['comments']) for e in events),
        'all_imported_records_require_review':True,'external_messages_sent':0,'financial_postings_created':0}}

def import_schedule(path,content,filename='Crown Schedule (2).xlsx',actor='schedule-importer',start='2026-09-27',end='2027-12-31'):
    initialize(path);p=parse(content,start,end,filename)
    bid='CSI-'+hashlib.sha256((p['sha256']+start+end).encode()).hexdigest()[:20]
    with transaction(path) as db:
        setup(db)
        if db.execute('SELECT 1 FROM schedule_import_batches WHERE id=?',(bid,)).fetchone():
            return {'batch_id':bid,'already_imported':True,'new_events':0,'summary':p['summary']}
        report={'batch_id':bid,'already_imported':False,'new_events':0,'summary':p['summary']}
        insert(db,'schedule_import_batches',dict(id=bid,sha256=p['sha256'],filename=filename,range_start=start,range_end=end,created_at=now(),report_json=dumps(report)))
        for e in p['events']:
            if db.execute('SELECT 1 FROM imported_schedule_events WHERE source_key=?',(e['source_key'],)).fetchone():continue
            old=db.execute("SELECT COUNT(*) FROM imported_schedule_events WHERE source_key LIKE ?",('CS:'+str(e['source_row'])+':%',)).fetchone()[0]
            if old:e['flags'].append('SOURCE_ROW_CHANGED_RECONCILE_PREVIOUS_VERSION')
            insert(db,'imported_schedule_events',dict(id=e['id'],batch_id=bid,source_key=e['source_key'],job_date=e['date'],client_name=e['client_name'],email=e['email'],phone=e['phone'],service=e['service'],time_label=e['time_label'],payload_json=dumps(e)))
            report['new_events']+=1
        for n in p['notes']:
            key=hashlib.sha256(dumps(n).encode()).hexdigest()
            if not db.execute('SELECT 1 FROM imported_schedule_notes WHERE source_key=?',(key,)).fetchone():
                insert(db,'imported_schedule_notes',dict(id='CSN-'+key[:20],batch_id=bid,source_key=key,payload_json=dumps(n)))
        db.execute('UPDATE schedule_import_batches SET report_json=? WHERE id=?',(dumps(report),bid))
        audit(db,actor,'stage-import','schedule',bid,report)
    return report
