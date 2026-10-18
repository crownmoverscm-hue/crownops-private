"""Shared external media folders; links are never fetched by the server."""
from contextlib import closing
from fastapi import Depends,HTTPException
from .store import connect,transaction,one,rows,insert,audit,uid,now
from .platform_common import staff_read_project,project_write,text
from .inquiry_intake import photo_link

def listing(db,lid,user=None):
    result=rows(db,'SELECT id,lead_id,job_id,label,url,created_at FROM saved_media_links WHERE lead_id=? AND removed_at IS NULL ORDER BY created_at DESC',(lid,))
    if user and user['role']=='crew':
        assigned={x['job_id'] for x in rows(db,"SELECT job_id FROM assignments WHERE resource_type='employee' AND resource_id=?",(user.get('employee_id') or '',))}
        result=[x for x in result if not x['job_id'] or x['job_id'] in assigned]
    lead=one(db,'SELECT photo_link FROM leads WHERE id=?',(lid,))
    try:legacy=photo_link(lead['photo_link']) if lead else ''
    except HTTPException:legacy=''
    if legacy and not any(x['url']==legacy for x in result):result.append({'id':'legacy','lead_id':lid,'job_id':None,'label':'Client photo / video folder','url':legacy,'created_at':''})
    return result

def save_link(db,lid,data,actor,jid=None):
    url=photo_link(data.get('url',''))
    if not url:raise HTTPException(422,'Paste a photo or video folder link.')
    label=text(data.get('label','Photo / video folder'),'Folder label',180) or 'Photo / video folder'
    prior=one(db,'SELECT * FROM saved_media_links WHERE lead_id=? AND job_id IS ? AND url=? AND removed_at IS NULL',(lid,jid,url))
    if prior:return {**prior,'duplicate':True}
    rec={'id':uid('LINK'),'lead_id':lid,'job_id':jid,'label':label,'url':url,'created_by':actor,'created_at':now()}
    insert(db,'saved_media_links',rec);audit(db,actor,'Shared media folder saved','saved_media_links',rec['id'])
    return rec

def register_media_links(app,path,auth):
    @app.get('/api/leads/{lid}/media-links')
    def get_links(lid:str,u=Depends(auth)):
        with closing(connect(path)) as db:staff_read_project(db,lid,u);return listing(db,lid,u)

    @app.post('/api/leads/{lid}/media-links')
    def add_link(lid:str,data:dict,u=Depends(auth)):
        jid=data.get('job_id') or None
        with transaction(path) as db:
            project_write(db,lid,jid,u)
            return save_link(db,lid,data,u['id'],jid)

    @app.post('/api/portal/media-links')
    def client_link(data:dict,c=Depends(app.state.client_auth)):
        with transaction(path) as db:return save_link(db,c['lead_id'],data,'client:'+c['email'])
