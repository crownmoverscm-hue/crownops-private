"""Accountant-approved deltas preserve prior approved time; no payroll payout."""
from contextlib import closing
from datetime import date,timedelta
from decimal import Decimal,ROUND_HALF_UP
import io,csv
from fastapi import Depends,HTTPException
from fastapi.responses import Response
from .store import connect,transaction,rows,one,insert,audit,now,uid
from .platform_common import require_role,settings,text
from .pricing import number
from .staff_records import can_view_pay


def register_payroll_adjustments(app,path,auth):
    @app.get('/api/payroll-adjustments')
    def listing(u=Depends(auth)):
        with closing(connect(path)) as db:
            data=rows(db,'SELECT a.*,e.name employee_name FROM payroll_adjustments a JOIN employees e ON e.id=a.employee_id'+(' WHERE a.employee_id=?' if u['role'] not in ('owner','manager') else '')+' ORDER BY approved_at DESC',(u.get('employee_id'),) if u['role'] not in ('owner','manager') else ())
        for r in data:
            if not can_view_pay(u,r['employee_id']):r.pop('pay_delta_cents',None)
        return data

    @app.post('/api/payroll-drafts/{pid}/reconcile')
    def reconcile(pid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','manager'})
        if data.get('reviewed') is not True:raise HTTPException(422,'Explicit payroll reconciliation approval is required.')
        note=text(data.get('note',''),'Reconciliation explanation',3000)
        if len(note)<12:raise HTTPException(422,'Explain the actual work adjustment.')
        try:
            effective=date.fromisoformat(data.get('effective_date',''))
            minutes=[number(data.get(k),k,0,24)*60 for k in ('service_hours','transport_hours','break_hours','extra_hours')]
            if any(m!=int(m) for m in minutes):raise ValueError('Enter whole-minute work intervals.')
            v=[int(m) for m in minutes]
            if not 0<v[0]+v[1]+v[3]-v[2]<=1440:raise ValueError('Review work minutes.')
        except (ValueError,TypeError) as ex:raise HTTPException(422,str(ex))
        with transaction(path) as db:
            draft=one(db,'SELECT * FROM payroll_drafts WHERE id=?',(pid,))
            if not draft:raise HTTPException(404,'Time draft not found.')
            old=one(db,'SELECT id FROM payroll_adjustments WHERE draft_id=?',(pid,))
            if old:return {'id':old['id'],'duplicate':True,'payroll_paid':False}
            if draft['status']!='Adjustment review' or data.get('version')!=draft['version']:raise HTTPException(409,'Refresh the adjustment review first.')
            inv=one(db,'SELECT * FROM job_invoices WHERE id=?',(draft['invoice_id'],))
            if not inv or inv['revision']!=draft['invoice_revision'] or inv['status']!='Issued':raise HTTPException(409,'Use the current finalized invoice review.')
            base=one(db,"SELECT * FROM payroll_drafts WHERE job_id=? AND employee_id=? AND status IN ('Approved','Locked') ORDER BY invoice_revision LIMIT 1",(draft['job_id'],draft['employee_id']))
            if not base:raise HTTPException(409,'No earlier approved time exists to reconcile.')
            prior=rows(db,'SELECT * FROM payroll_adjustments WHERE job_id=? AND employee_id=?',(draft['job_id'],draft['employee_id']))
            keys=['service','transport','break','extra'];deltas=[v[i]-base[k+'_minutes']-sum(r[k+'_delta'] for r in prior) for i,k in enumerate(keys)]
            newpay=int((Decimal(v[0]+v[1]+v[3]-v[2])*draft['hourly_cents']/60).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            basepay=int((Decimal(base['service_minutes']+base['transport_minutes']+base['extra_minutes']-base['break_minutes'])*base['hourly_cents']/60).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            paydelta=newpay-basepay-sum(r['pay_delta_cents'] for r in prior)
            if paydelta<0 and data.get('negative_adjustment_reviewed') is not True:raise HTTPException(409,'A negative adjustment is not automatic permission to deduct wages. Obtain payroll/legal review and explicitly acknowledge it.')
            rec=dict(id=uid('PADJ'),draft_id=pid,base_draft_id=base['id'],job_id=draft['job_id'],employee_id=draft['employee_id'],effective_date=effective.isoformat(),pay_delta_cents=paydelta,note=note,approved_by=u['id'],approved_at=now(),**{k+'_delta':deltas[i] for i,k in enumerate(keys)})
            insert(db,'payroll_adjustments',rec);db.execute("UPDATE payroll_drafts SET status='Reconciled',version=version+1 WHERE id=?",(pid,));audit(db,u['id'],'Payroll difference approved; original pay preserved; no payout','payroll_adjustments',rec['id'],{'pay_delta_cents':paydelta})
        return {'id':rec['id'],'pay_delta_cents':paydelta,'payroll_paid':False}

    @app.get('/api/payroll-adjustments/export')
    def export(period_start:str,u=Depends(auth)):
        require_role(u,{'owner','manager'})
        try:start=date.fromisoformat(period_start)
        except ValueError:raise HTTPException(422,'Invalid period date.')
        with closing(connect(path)) as db:
            cfg=settings(db,'payroll_policy',{})
            if not cfg.get('approved') or not cfg.get('period_anchor'):raise HTTPException(409,'Approve a payroll period first.')
            if (start-date.fromisoformat(cfg['period_anchor'])).days%14:raise HTTPException(422,'Choose a configured biweekly period start.')
            end=start+timedelta(days=13)
            rr=rows(db,'SELECT a.*,e.name FROM payroll_adjustments a JOIN employees e ON e.id=a.employee_id WHERE effective_date BETWEEN ? AND ? ORDER BY effective_date',(str(start),str(end)))
        buf=io.StringIO();w=csv.writer(buf);w.writerow(['Adjustment ID','Employee','Job','Effective date','Service delta hours','Transport delta hours','Break delta hours','Extra delta hours','Gross pay delta CAD','Note'])
        for r in rr:
            name=r['name'];note=r['note']
            if name.startswith(('=','+','-','@')):name="'"+name
            if note.startswith(('=','+','-','@')):note="'"+note
            w.writerow([r['id'],name,r['job_id'],r['effective_date'],r['service_delta']/60,r['transport_delta']/60,r['break_delta']/60,r['extra_delta']/60,str(Decimal(r['pay_delta_cents'])/100),note])
        return Response(buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="payroll-adjustments-{start}.csv"'})
