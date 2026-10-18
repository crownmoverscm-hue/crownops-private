"""Employee incident submissions and authenticated manager review history."""
from contextlib import closing
from datetime import datetime
import hashlib,re
from fastapi import Depends,HTTPException
from .store import connect,transaction,rows,one,insert,audit,uid,now,dumps
from .platform_common import text,OFFICE
from .job_planning import can_plan_jobs

CATEGORIES={'Client move','Property damage','Company truck','Other vehicle','Other incident'}
STATUSES={'Submitted','In review','Verified','Completed'}
REVIEWERS={'owner','manager','dispatch'}

def read_all(db,user):return user['role'] in OFFICE or can_plan_jobs(db,user)

def visible(db,iid,user):
    report=one(db,'SELECT * FROM incident_reports WHERE id=?',(iid,))
    if not report:raise HTTPException(404,'Incident report not found.')
    if not read_all(db,user) and report['reported_by']!=user['id']:raise HTTPException(403,'You can view your own incident reports. Managers review all reports.')
    return report

def joined_reports(db,clause='',args=(),limit=100,offset=0):
    return rows(db,"""SELECT r.*,coalesce(author.name,r.reported_by) reporter_name,
        reviewer.name reviewer_name,verifier.name verifier_name,closer.name completer_name,
        j.title job_title,l.client_name,v.name vehicle_name
        FROM incident_reports r LEFT JOIN users author ON author.id=r.reported_by
        LEFT JOIN users reviewer ON reviewer.id=r.reviewed_by LEFT JOIN users verifier ON verifier.id=r.verified_by
        LEFT JOIN users closer ON closer.id=r.completed_by LEFT JOIN jobs j ON j.id=r.job_id
        LEFT JOIN leads l ON l.id=coalesce(j.lead_id,r.lead_id) LEFT JOIN vehicles v ON v.id=r.vehicle_id"""+clause+
        ' ORDER BY r.submitted_at DESC,r.id DESC LIMIT ? OFFSET ?',(*args,limit,offset))

def register_incidents(app,path,auth,local_to_utc):
    # Carry earlier client cases forward without fabricating a historical verifier.
    with transaction(path) as db:
        for case in rows(db,'SELECT * FROM cases'):
            iid='INC-LEGACY-'+case['id']
            if one(db,'SELECT id FROM incident_reports WHERE legacy_case_id=?',(case['id'],)):continue
            insert(db,'incident_reports',dict(id=iid,category='Client move',title=case['title'],detail=case['detail'],
                occurred_at='',lead_id=case['lead_id'],job_id=case['job_id'],reported_by=case['created_by'],submitted_at=case['created_at'],
                status='Completed' if case['status']=='Resolved' else 'In review' if case['status']=='In review' else 'Submitted',
                resolution=case['resolution'],request_key='legacy-'+case['id'],payload_hash='legacy',legacy_case_id=case['id']))
            insert(db,'incident_events',dict(incident_id=iid,actor='System import',action='Earlier client case preserved',
                note='Historical review account was not recorded. No verifier or completer has been inferred.',created_at=now()))

    @app.get('/api/incidents/options')
    def options(u=Depends(auth)):
        with closing(connect(path)) as db:
            all_jobs=read_all(db,u)
            jobs=rows(db,"""SELECT j.id,j.title,j.start_at,l.client_name FROM jobs j JOIN leads l ON l.id=j.lead_id"""+
                ('' if all_jobs else " WHERE EXISTS(SELECT 1 FROM assignments a WHERE a.job_id=j.id AND a.resource_type='employee' AND a.resource_id=?)")+
                ' ORDER BY j.start_at DESC LIMIT 300',() if all_jobs else (u.get('employee_id') or '',))
            vehicles=rows(db,'SELECT id,name FROM vehicles ORDER BY name')
        return {'jobs':jobs,'vehicles':vehicles,'can_review':u['role'] in REVIEWERS,'can_read_all':all_jobs}

    @app.get('/api/incidents')
    def listing(status:str='',category:str='',q:str='',limit:int=50,offset:int=0,u=Depends(auth)):
        if status and status not in STATUSES:raise HTTPException(422,'Choose a valid report status.')
        if category and category not in CATEGORIES:raise HTTPException(422,'Choose a valid incident category.')
        if not 1<=limit<=100 or offset<0:raise HTTPException(422,'Invalid report page.')
        with closing(connect(path)) as db:
            where=[];args=[];all_reports=read_all(db,u)
            if not all_reports:where.append('r.reported_by=?');args.append(u['id'])
            if status:where.append('r.status=?');args.append(status)
            if category:where.append('r.category=?');args.append(category)
            if q:
                query='%'+text(q,'Search',200)+'%';where.append('(r.title LIKE ? OR r.location LIKE ? OR r.detail LIKE ?)');args.extend([query]*3)
            clause=' WHERE '+' AND '.join(where) if where else ''
            data=joined_reports(db,clause,args,limit,offset)
            total=db.execute('SELECT count(*) FROM incident_reports r'+clause,args).fetchone()[0]
        return {'items':data,'total':total,'limit':limit,'offset':offset,'can_review':u['role'] in REVIEWERS,'can_read_all':all_reports}

    @app.post('/api/incidents')
    def submit(data:dict,u=Depends(auth)):
        category=data.get('category')
        if not isinstance(category,str) or category not in CATEGORIES:raise HTTPException(422,'Choose an incident category.')
        title=text(data.get('title',''),'Title',200);detail=text(data.get('detail',''),'Incident details',12000)
        if not title or not detail:raise HTTPException(422,'Enter a title and describe what happened.')
        key=data.get('request_key','')
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',key):raise HTTPException(422,'Reopen the report form and try again.')
        for field in ('job_id','vehicle_id'):
            if data.get(field) is not None and not isinstance(data[field],str):raise HTTPException(422,'Choose a valid related move or truck.')
        values={'category':category,'title':title,'detail':detail,'location':text(data.get('location',''),'Location',1000),
            'other_vehicle':text(data.get('other_vehicle',''),'Other vehicle',1000),'job_id':text(data.get('job_id') or '','Related move',150) or None,'vehicle_id':text(data.get('vehicle_id') or '','Company truck',150) or None,
            'occurred_at':local_to_utc(data['occurred_at']) if data.get('occurred_at') else ''}
        fingerprint=hashlib.sha256(dumps(values).encode()).hexdigest()
        with transaction(path) as db:
            old=one(db,'SELECT id,payload_hash FROM incident_reports WHERE reported_by=? AND request_key=?',(u['id'],key))
            if old:
                if old['payload_hash']!=fingerprint:raise HTTPException(409,'This report request changed. Reopen the form before submitting another report.')
                return {'id':old['id'],'duplicate':True}
            if values['job_id']:
                job=one(db,'SELECT id,lead_id FROM jobs WHERE id=?',(values['job_id'],))
                if not job:raise HTTPException(422,'Related move does not exist.')
                if not read_all(db,u) and not one(db,"SELECT job_id FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(job['id'],u.get('employee_id') or '')):
                    raise HTTPException(403,'Choose one of your assigned moves, or submit a report without linking a move.')
            if values['vehicle_id'] and not one(db,'SELECT id FROM vehicles WHERE id=?',(values['vehicle_id'],)):raise HTTPException(422,'Choose a company truck that exists.')
            if values['job_id']:values['lead_id']=job['lead_id']
            stamp=now()
            record=dict(id=uid('INC'),**values,reported_by=u['id'],submitted_at=stamp,request_key=key,payload_hash=fingerprint)
            insert(db,'incident_reports',record);insert(db,'incident_events',dict(incident_id=record['id'],actor=u['id'],action='Submitted',created_at=stamp))
            audit(db,u['id'],'Incident report submitted','incident_reports',record['id'],{'category':category,'job_id':values['job_id'],'vehicle_id':values['vehicle_id']})
        return {'id':record['id'],'status':'Submitted'}

    @app.get('/api/incidents/{iid}')
    def detail(iid:str,u=Depends(auth)):
        with closing(connect(path)) as db:
            visible(db,iid,u);report=joined_reports(db,' WHERE r.id=?',(iid,),1)[0]
            history=rows(db,"""SELECT e.*,coalesce(u.name,e.actor) actor_name FROM incident_events e LEFT JOIN users u ON u.id=e.actor
                WHERE e.incident_id=? ORDER BY e.created_at,e.id""",(iid,))
        return {'report':report,'history':history,'can_review':u['role'] in REVIEWERS}

    @app.post('/api/incidents/{iid}/review')
    def review(iid:str,data:dict,u=Depends(auth)):
        if u['role'] not in REVIEWERS:raise HTTPException(403,'Only active owners and managers can verify or complete incident reports.')
        status=data.get('status')
        if not isinstance(status,str) or status not in {'In review','Verified','Completed'}:raise HTTPException(422,'Choose In review, Verified or Completed.')
        note=text(data.get('note',''),'Resolution / review note',5000)
        with transaction(path) as db:
            actor=one(db,'SELECT id,role FROM users WHERE id=? AND active=1',(u['id'],))
            if not actor or actor['role'] not in REVIEWERS:raise HTTPException(403,'Your account no longer has incident review access.')
            report=visible(db,iid,u)
            if report['version']!=data.get('version'):raise HTTPException(409,'This report changed. Reopen it before reviewing.')
            if report['status']==status:raise HTTPException(409,'This status is already recorded. Refresh to see the reviewing account.')
            stamp=now();updates={'status':status,'reviewed_by':u['id'],'reviewed_at':stamp,'resolution':note or report['resolution']}
            if status=='Verified':updates.update(verified_by=u['id'],verified_at=stamp)
            if status=='Completed':updates.update(completed_by=u['id'],completed_at=stamp)
            if report['status']=='Completed' and status=='In review':updates.update(completed_by=None,completed_at=None,verified_by=None,verified_at=None)
            db.execute('UPDATE incident_reports SET '+','.join(k+'=?' for k in updates)+',version=version+1 WHERE id=?',(*updates.values(),iid))
            insert(db,'incident_events',dict(incident_id=iid,actor=u['id'],action='Reopened for review' if report['status']=='Completed' and status=='In review' else status,note=note,created_at=stamp))
            audit(db,u['id'],'Incident '+status.lower(),'incident_reports',iid,{'before_status':report['status'],'status':status})
        return {'ok':True,'status':status,'reviewed_by':u['id'],'reviewed_at':stamp}
