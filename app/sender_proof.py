"""Owner-authorized test send and read-back of Gmail's stored message headers."""
from contextlib import closing
from email.utils import parseaddr
import hashlib
import os
import re
import requests
from fastapi import Depends, HTTPException
from .store import connect, transaction, now, audit, dumps
from .platform_common import settings, put_setting, require_role


def identity_key(cfg):
    return hashlib.sha256(dumps({k:cfg.get(k,'') for k in ('connected_mailbox','send_as','reply_to')}).encode()).hexdigest()


def verified_company_sender(db):
    cfg=settings(db,'company_communications',{}) or {}
    proof=settings(db,'company_sender_proof',{}) or {}
    return bool(proof.get('identity_hash')==identity_key(cfg) and proof.get('state')=='Sending enabled'
                and cfg.get('approved') is True and proof.get('actual_from')==cfg.get('send_as')
                and proof.get('actual_reply_to')==cfg.get('reply_to'))


def check_proof(path):
    from .providers import provider_token
    with closing(connect(path)) as db:
        cfg=settings(db,'company_communications',{}) or {};proof=settings(db,'company_sender_proof',{}) or {}
    if proof.get('identity_hash')!=identity_key(cfg) or not proof.get('message_id'):
        raise HTTPException(409,'Send one authorized test using the current sender settings first.')
    token=provider_token(path,'gmail')
    response=requests.get('https://gmail.googleapis.com/gmail/v1/users/me/messages/'+proof['message_id'],
        params={'format':'metadata','metadataHeaders':['From','Reply-To']},
        headers={'Authorization':'Bearer '+token['access_token']},timeout=15)
    response.raise_for_status();message=response.json()
    headers={h['name'].lower():h['value'] for h in message.get('payload',{}).get('headers',[])}
    actual_from=parseaddr(headers.get('from',''))[1].lower()
    actual_reply=parseaddr(headers.get('reply-to',''))[1].lower()
    if message.get('id')!=proof['message_id']:raise HTTPException(409,'Gmail returned a different verification message.')
    verified=actual_from==cfg.get('send_as') and actual_reply==cfg.get('reply_to')
    proof.update(state='Verified' if verified else 'Pending',actual_from=actual_from,actual_reply_to=actual_reply,checked_at=now())
    with transaction(path) as db:
        if identity_key(settings(db,'company_communications',{}) or {})!=proof['identity_hash']:
            raise HTTPException(409,'Sender settings changed during verification. Start again.')
        put_setting(db,'company_sender_proof',proof)
    return {**proof,'inbox_delivery_verified':False}


def register_sender_proof(app,path,auth):
    @app.get('/api/company-sender/proof')
    def status(u=Depends(auth)):
        require_role(u,{'owner'})
        with closing(connect(path)) as db:
            cfg=settings(db,'company_communications',{}) or {};proof=settings(db,'company_sender_proof',{}) or {}
            if proof.get('identity_hash')!=identity_key(cfg):return {'state':'Not verified','sending_enabled':False}
            return {**proof,'sending_enabled':verified_company_sender(db)}

    @app.post('/api/company-sender/proof/send-test')
    def send_test(data:dict,u=Depends(auth)):
        require_role(u,{'owner'})
        if data.get('confirm') is not True:raise HTTPException(422,'Explicitly approve sending one verification email.')
        recipient=data.get('recipient','');rid=data.get('request_id','')
        if not isinstance(recipient,str) or not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{12,120}',rid):raise HTTPException(422,'Choose an approved recipient and stable request ID.')
        recipient=recipient.strip().lower()
        allowlist={x.strip().lower() for x in os.getenv('CROWNOPS_TEST_EMAIL_ALLOWLIST','').split(',') if x.strip()}
        if recipient not in allowlist:raise HTTPException(403,'This verification recipient is not on the approved test-email allowlist.')
        from .providers import send_gmail
        from .platform_common import external_gate
        external_gate()
        with transaction(path) as db:
            cfg=settings(db,'company_communications',{}) or {};old=settings(db,'company_sender_proof',{}) or {}
            if old.get('request_id')==rid: return {**old,'duplicate':True,'sent':bool(old.get('message_id'))}
            if old.get('state')=='Pending':raise HTTPException(409,'A verification send is pending or unresolved. Check it before starting another.')
            proof={'state':'Pending','request_id':rid,'identity_hash':identity_key(cfg),'recipient':recipient,'created_at':now()}
            put_setting(db,'company_sender_proof',proof)
        # Never automatically retry a send after a timeout.
        try:
            mid=send_gmail(path,{'id':'SENDER-PROOF-'+rid,'recipient':recipient,'subject':'Crown Movers — sender verification test',
                'body':'TEST — MUSE. Company sender verification only. No quote, booking or payment was created.','category':'Sender verification','event_key':'sender-proof:'+rid},sender_test=True)
        except Exception:
            raise HTTPException(502,'Verification sending is unresolved. Check Gmail Sent before any retry; no automatic retry was made.')
        with transaction(path) as db:
            proof.update(message_id=mid,sent_at=now());put_setting(db,'company_sender_proof',proof)
            audit(db,u['id'],'Company sender verification test sent','settings','company_sender_proof',{'message_id':mid})
        try:return {**check_proof(path),'sent':True}
        except Exception:return {**proof,'sent':True,'verification_pending':True,'inbox_delivery_verified':False}

    @app.post('/api/company-sender/proof/check')
    def check(u=Depends(auth)):
        require_role(u,{'owner'});return check_proof(path)

    @app.post('/api/company-sender/proof/approve')
    def approve(data:dict,u=Depends(auth)):
        require_role(u,{'owner'})
        if data.get('confirm') is not True:raise HTTPException(422,'Review the actual sender headers before approval.')
        with transaction(path) as db:
            cfg=settings(db,'company_communications',{}) or {};proof=settings(db,'company_sender_proof',{}) or {}
            if (proof.get('state')!='Verified' or proof.get('identity_hash')!=identity_key(cfg)
                    or proof.get('actual_from')!=cfg.get('send_as') or proof.get('actual_reply_to')!=cfg.get('reply_to')):
                raise HTTPException(409,'Actual From and Reply-To must first match the current configured company sender.')
            cfg['approved']=True;proof.update(state='Sending enabled',approved_by=u['id'],approved_at=now())
            put_setting(db,'company_communications',cfg);put_setting(db,'company_sender_proof',proof)
            audit(db,u['id'],'Company sender approved from message-header evidence','settings','company_sender_proof')
        return {'state':'Sending enabled','sending_enabled':True}
