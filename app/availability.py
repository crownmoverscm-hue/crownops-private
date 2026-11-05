"""Company-wide date closures, enforced in the same transaction as booking."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import Depends, HTTPException
from .store import connect, transaction, rows, one, now, audit
from .business_zone import BUSINESS_ZONE, zone_name

EDITORS={'owner','manager','sales','dispatch'}
ET=BUSINESS_ZONE

def service_day(value):
    try:
        day=date.fromisoformat(str(value))
        if day.isoformat()!=value:raise ValueError()
        return day.isoformat()
    except (ValueError,TypeError):raise HTTPException(422,'Choose a valid service date.')

def date_status(db,day):
    if not day:return {'day':'','status':'Date to confirm','version':0,'available':False}
    day=service_day(day)
    rec=one(db,'SELECT * FROM schedule_days WHERE day=?',(day,)) or {'day':day,'status':'Open','version':0,'reason':''}
    return {**rec,'available':rec['status']=='Open'}

def assert_day_open(db,day):
    rec=date_status(db,day)
    if rec['status']=='Fully booked':
        raise HTTPException(409,'This service date is fully booked ('+day+'). Choose another date or ask the team to reopen it.')

def assert_dates_open(db,start,end,previous=None):
    def instant(value):
        d=datetime.fromisoformat(value)
        if d.tzinfo is None:d=d.replace(tzinfo=ET)
        return d.astimezone(timezone.utc)
    first,last=instant(start),instant(end)
    if previous and previous['status'] in ('Confirmed','In progress','Completed') and instant(previous['start_at'])==first and instant(previous['end_at'])==last:
        return  # Preserve work that was already booked before the day was closed.
    a=first.astimezone(ET).date().isoformat()
    b=(max(first,last-timedelta(microseconds=1))).astimezone(ET).date().isoformat()
    closed=one(db,"SELECT day FROM schedule_days WHERE status='Fully booked' AND day>=? AND day<=? ORDER BY day LIMIT 1",(a,b))
    if closed:assert_day_open(db,closed['day'])

def register_availability(app,path,auth):
    @app.get('/api/schedule/availability')
    def listing(start:str='',end:str='',u=Depends(auth)):
        start=service_day(start or datetime.now(ET).date().isoformat())
        end=service_day(end or (date.fromisoformat(start)+timedelta(days=31)).isoformat())
        if end<start or (date.fromisoformat(end)-date.fromisoformat(start)).days>370:raise HTTPException(422,'Choose up to one year of availability.')
        with closing(connect(path)) as db:
            items=rows(db,'SELECT * FROM schedule_days WHERE day>=? AND day<=? ORDER BY day',(start,end))
        return {'items':items,'start':start,'end':end,'can_manage':u['role'] in EDITORS}

    @app.put('/api/schedule/availability/{day}')
    def update(day:str,data:dict,u=Depends(auth)):
        if u['role'] not in EDITORS:raise HTTPException(403,'Only Owners, Managers, Sales and Dispatch can close or reopen company dates.')
        day=service_day(day)
        if set(data)-{'status','version','reason'} or data.get('status') not in ('Open','Fully booked'):raise HTTPException(422,'Choose Open or Fully booked.')
        reason=data.get('reason','')
        if not isinstance(reason,str) or len(reason)>1000:raise HTTPException(422,'Use a reason under 1,000 characters.')
        with transaction(path) as db:
            old=date_status(db,day)
            if type(data.get('version')) is not int or data['version']!=old['version']:raise HTTPException(409,'Date availability changed. Reload the schedule before saving.')
            db.execute('INSERT INTO schedule_days(day,status,reason,version,updated_by,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET status=excluded.status,reason=excluded.reason,version=excluded.version,updated_by=excluded.updated_by,updated_at=excluded.updated_at',
                (day,data['status'],reason.strip(),old['version']+1,u['id'],now()))
            audit(db,u['id'],'Company date '+('closed' if data['status']=='Fully booked' else 'reopened'),'schedule_days',day,{'before':old['status'],'after':data['status'],'reason':reason.strip()})
            return date_status(db,day)
