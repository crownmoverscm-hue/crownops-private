"""Private staff messaging; never writes client Inbox or booking records."""
from contextlib import closing
from datetime import datetime,timezone,timedelta
import hashlib
import json
from fastapi import Depends, HTTPException
from .store import connect, transaction, one, rows, now, uid, insert, audit

SCHEMA='''
CREATE TABLE IF NOT EXISTS team_threads(id TEXT PRIMARY KEY,title TEXT NOT NULL,dm_key TEXT UNIQUE,created_by TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS team_members(thread_id TEXT NOT NULL REFERENCES team_threads(id),user_id TEXT NOT NULL REFERENCES users(id),read_seq INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(thread_id,user_id));
CREATE TABLE IF NOT EXISTS team_messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,thread_id TEXT NOT NULL REFERENCES team_threads(id),sender_id TEXT NOT NULL REFERENCES users(id),body TEXT NOT NULL,client_key TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(sender_id,client_key));
CREATE INDEX IF NOT EXISTS team_messages_thread ON team_messages(thread_id,seq);
'''


def staff(db,u):
    if not one(db,'SELECT id FROM users WHERE id=? AND active=1',(u['id'],)):raise HTTPException(403,'Active staff access required.')


def member(db,u,tid):
    staff(db,u)
    if not one(db,'SELECT user_id FROM team_members WHERE thread_id=? AND user_id=?',(tid,u['id'])):raise HTTPException(404,'Conversation not found.')


def register_team_chat(app,path,auth):
    with closing(connect(path)) as db:db.executescript(SCHEMA)

    @app.get('/api/team-chat/people')
    def people(u=Depends(auth)):
        with closing(connect(path)) as db:
            staff(db,u)
            return rows(db,'SELECT id,name,role FROM users WHERE active=1 AND id!=? ORDER BY name COLLATE NOCASE',(u['id'],))

    @app.get('/api/team-chat/threads')
    def threads(u=Depends(auth)):
        with closing(connect(path)) as db:
            staff(db,u)
            result=rows(db,'''SELECT t.id,t.title,t.dm_key,
                (SELECT MAX(seq) FROM team_messages WHERE thread_id=t.id) AS latest,
                (SELECT COUNT(*) FROM team_messages WHERE thread_id=t.id AND seq>m.read_seq AND sender_id!=?) AS unread
                FROM team_threads t JOIN team_members m ON m.thread_id=t.id WHERE m.user_id=? ORDER BY latest DESC,t.created_at DESC''',(u['id'],u['id']))
            for t in result:
                t['members']=rows(db,'SELECT u.id,u.name FROM users u JOIN team_members m ON m.user_id=u.id WHERE m.thread_id=?',(t['id'],))
                if t.pop('dm_key'):t['title']=' / '.join(p['name'] for p in t['members'] if p['id']!=u['id'])
            return result

    @app.post('/api/team-chat/threads')
    def create(data:dict,u=Depends(auth)):
        ids=data.get('members');title=data.get('title','')
        if not isinstance(ids,list) or not 1<=len(ids)<=30 or any(not isinstance(v,str) for v in ids):raise HTTPException(422,'Choose 1–30 staff members.')
        if not isinstance(title,str) or len(title)>100:raise HTTPException(422,'Use a title of up to 100 characters.')
        ids=sorted(set(ids+[u['id']]))
        if len(ids)<2:raise HTTPException(422,'Choose another staff member.')
        key=hashlib.sha256(json.dumps(ids).encode()).hexdigest() if len(ids)==2 and not title.strip() else None
        with transaction(path) as db:
            staff(db,u)
            for value in ids:
                if not one(db,'SELECT id FROM users WHERE id=? AND active=1',(value,)):raise HTTPException(422,'Choose active staff only.')
            old=one(db,'SELECT id FROM team_threads WHERE dm_key=?',(key,)) if key else None
            if old:return old
            if len(ids)>2 and not title.strip():raise HTTPException(422,'Give the group a name.')
            tid=uid('CHAT');insert(db,'team_threads',dict(id=tid,title=title.strip(),dm_key=key,created_by=u['id'],created_at=now()))
            for value in ids:insert(db,'team_members',dict(thread_id=tid,user_id=value))
            audit(db,u['id'],'Private team conversation created','team_threads',tid,{'member_count':len(ids)})
            return {'id':tid}

    @app.get('/api/team-chat/threads/{tid}/messages')
    def messages(tid:str,before:int=0,u=Depends(auth)):
        with closing(connect(path)) as db:
            member(db,u,tid)
            result=rows(db,'''SELECT m.seq,m.id,m.sender_id,u.name AS sender,m.body,m.created_at FROM team_messages m JOIN users u ON u.id=m.sender_id
                WHERE m.thread_id=? AND (?=0 OR m.seq<?) ORDER BY m.seq DESC LIMIT 100''',(tid,before,before))
            return list(reversed(result))

    @app.post('/api/team-chat/threads/{tid}/messages')
    def send(tid:str,data:dict,u=Depends(auth)):
        body=data.get('body');key=data.get('client_key')
        if not isinstance(body,str) or not body.strip() or len(body)>8000 or not isinstance(key,str) or not 8<=len(key)<=100:raise HTTPException(422,'Enter a message and valid retry identifier.')
        with transaction(path) as db:
            member(db,u,tid)
            old=one(db,'SELECT * FROM team_messages WHERE sender_id=? AND client_key=?',(u['id'],key))
            if old:
                if old['thread_id']!=tid or old['body']!=body.strip():raise HTTPException(409,'Retry identifier already used for another message.')
                return {'id':old['id'],'seq':old['seq'],'duplicate':True}
            count=db.execute("SELECT COUNT(*) FROM team_messages WHERE sender_id=? AND created_at>?",(u['id'],(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat())).fetchone()[0]
            if count>=30:raise HTTPException(429,'Please wait before sending more messages.')
            mid=uid('TM');insert(db,'team_messages',dict(id=mid,thread_id=tid,sender_id=u['id'],body=body.strip(),client_key=key,created_at=now()))
            seq=db.execute('SELECT last_insert_rowid()').fetchone()[0]
            audit(db,u['id'],'Private team message sent','team_messages',mid,{'thread_id':tid})
            return {'id':mid,'seq':seq,'duplicate':False}

    @app.post('/api/team-chat/threads/{tid}/read')
    def read(tid:str,data:dict,u=Depends(auth)):
        seq=data.get('seq')
        if type(seq) is not int or seq<0:raise HTTPException(422,'Choose a displayed message.')
        with transaction(path) as db:
            member(db,u,tid)
            maximum=db.execute('SELECT COALESCE(MAX(seq),0) FROM team_messages WHERE thread_id=?',(tid,)).fetchone()[0]
            db.execute('UPDATE team_members SET read_seq=MAX(read_seq,?) WHERE thread_id=? AND user_id=?',(min(seq,maximum),tid,u['id']))
        return {'ok':True}
