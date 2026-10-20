"""Company payroll calendar; changing it never rewrites worked hours or approvals."""
from contextlib import closing
from datetime import date, timedelta
import hashlib
from fastapi import Depends, HTTPException
from .store import connect, transaction, audit, now, dumps
from .platform_common import settings, put_setting, require_role, business_day, OFFICE, MANAGERS


def validate_policy(data):
    days=data.get('period_days',14)
    if type(days) is not int or days not in (7,14):
        raise HTTPException(422,'Choose weekly (7 days) or every two weeks (14 days).')
    try:anchor=date.fromisoformat(data.get('period_anchor','')).isoformat()
    except (ValueError,TypeError):raise HTTPException(422,'Choose the first day of a payroll period.')
    return {**data,'period_days':days,'period_anchor':anchor}


def period_for(policy,day):
    cfg=validate_policy(policy);anchor=date.fromisoformat(cfg['period_anchor']);days=cfg['period_days']
    day=date.fromisoformat(day) if isinstance(day,str) else day
    start=anchor+timedelta(days=((day-anchor).days//days)*days)
    return {'start':start.isoformat(),'end':(start+timedelta(days=days-1)).isoformat()}


def configured(policy):
    if not policy.get('approved') or not policy.get('period_anchor'):return False
    try:validate_policy(policy);return True
    except HTTPException:return False


def policy_token(policy):return hashlib.sha256(dumps(policy).encode()).hexdigest()


def period_range(policy,start):
    if not configured(policy):raise HTTPException(409,'Set up the company payroll period first.')
    period=period_for(policy,start)
    if str(start)!=period['start']:raise HTTPException(422,'Choose the first day of a configured payroll period.')
    return date.fromisoformat(period['end'])


def default_range(db):
    cfg=settings(db,'payroll_policy',{})
    return period_for(cfg,business_day()) if configured(cfg) else None


def register_payroll_periods(app,path,auth):
    @app.get('/api/payroll-periods')
    def read(on:str='',u=Depends(auth)):
        require_role(u,OFFICE|{'crew'})
        try:day=date.fromisoformat(on or business_day())
        except (ValueError,TypeError):raise HTTPException(422,'Choose a valid payroll date.')
        with closing(connect(path)) as db:cfg=settings(db,'payroll_policy',{})
        ready=configured(cfg);days=cfg.get('period_days',14)
        result={'configured':ready,'period_days':days,'period_anchor':cfg.get('period_anchor',''),
                'review_token':policy_token(cfg),'can_configure':u['role'] in MANAGERS,'timezone':'America/Toronto'}
        if ready:
            current=period_for(cfg,day);start=date.fromisoformat(current['start'])
            result.update(selected=current,current=period_for(cfg,business_day()),
                          previous=period_for(cfg,start-timedelta(days=days)),next=period_for(cfg,start+timedelta(days=days)))
        return result

    @app.post('/api/payroll-periods')
    def save(data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        if set(data)-{'period_days','period_anchor','review_token'}:raise HTTPException(422,'Unknown payroll-period field.')
        valid=validate_policy(data)
        with transaction(path) as db:
            previous=settings(db,'payroll_policy',{})
            if data.get('review_token')!=policy_token(previous):raise HTTPException(409,'Another manager changed the payroll schedule. Reopen the settings before saving.')
            cfg={**previous,'period_days':valid['period_days'],'period_anchor':valid['period_anchor'],
                 'approved':True,'review_note':'Payroll calendar configured by '+u['name'],
                 'updated_by':u['id'],'updated_at':now()}
            put_setting(db,'payroll_policy',cfg)
            audit(db,u['id'],'Payroll calendar updated; worked hours and approvals unchanged','settings','payroll_policy',
                  {'before':previous,'after':cfg})
        return {'ok':True,'period':period_for(cfg,business_day()),'hours_changed':False}
