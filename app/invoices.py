"""End-of-job invoices for the private pilot, not an accounting-system replacement.

Crew can edit actuals and issue/correct their assigned-job invoice. Customer rates,
taxes and allowed unit prices are copied into a manager-approved billing profile.
No quote, payroll, payment verification or global pricing access is granted.
"""
from contextlib import closing
from decimal import Decimal, ROUND_HALF_UP
from html import escape
import json
import os
import re
from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse
from .store import connect,transaction,rows,one,insert,audit,uid,now,dumps
from .pricing import number,cents


def short(v,label,maximum=4000):
    if not isinstance(v,str) or len(v)>maximum:
        raise HTTPException(422,label+' must be text within the permitted length.')
    return v.strip()


def reject_extra(data,allowed):
    unknown=set(data)-set(allowed)
    if unknown:raise HTTPException(422,'Fields not allowed: '+', '.join(sorted(unknown)))


def validate_terms(data):
    """These are explicit manager-supplied terms, not tax/legal recommendations."""
    reject_extra(data,{'issuer','customer','hourly_rate','minimum_hours','transport_amount','fuel_amount',
                       'discount_amount','other_amount','taxes','catalogue','approval_reference'})
    issuer=data.get('issuer',{});customer=data.get('customer',{})
    if not isinstance(issuer,dict) or not isinstance(customer,dict):raise ValueError('Issuer and customer are required.')
    if not issuer.get('name') or not issuer.get('address') or not customer.get('name'):
        raise ValueError('Enter the legal billing name/address and customer name before approval.')
    out={}
    out['issuer']={k:short(issuer.get(k,''),'Issuer '+k,500) for k in ('name','address','email','phone')}
    out['customer']={k:short(customer.get(k,''),'Customer '+k,700) for k in ('name','address','email')}
    for key,low,high in [('hourly_rate',1,5000),('transport_amount',0,100000),('fuel_amount',0,100000),('discount_amount',0,100000)]:
        out[key+'_cents']=cents(number(data.get(key),key,low,high))
    out['other_amount_cents']=cents(number(data.get('other_amount',0),'Other agreed amount',0,100000))
    out['minimum_hours']=float(number(data.get('minimum_hours'), 'Minimum hours',0,24))
    if 'taxes' not in data or not isinstance(data['taxes'],list) or len(data['taxes'])>5:
        raise ValueError('Explicitly configure tax lines, or an empty list if reviewed as not applicable.')
    out['taxes']=[]
    for tax in data['taxes']:
        if not isinstance(tax,dict):raise ValueError('Invalid tax line.')
        name=short(tax.get('name',''),'Tax name',80);registration=short(tax.get('registration',''),'Tax registration',120)
        rate=number(tax.get('rate_percent'),'Tax percent',0,30)
        if not name or (rate>0 and not registration):raise ValueError('Name and registration are required for a tax line.')
        out['taxes'].append({'name':name,'rate_percent':str(rate),'registration':registration})
    cat=data.get('catalogue',[])
    if not isinstance(cat,list) or len(cat)>100:raise ValueError('Invalid approved material/extra catalogue.')
    out['catalogue']=[];seen=set()
    for item in cat:
        code=short(item.get('code',''),'Item code',60);name=short(item.get('name',''),'Item name',150)
        if not re.fullmatch(r'[A-Za-z0-9_-]+',code) or code in seen or not name:raise ValueError('Catalogue item codes must be unique.')
        seen.add(code)
        out['catalogue'].append({'code':code,'name':name,'unit_cents':cents(number(item.get('unit_price'),'Unit price',0,10000))})
    out['approval_reference']=short(data.get('approval_reference',''),'Accepted terms reference',2000)
    if not out['approval_reference']:raise ValueError('Record the accepted terms/source and your tax/pricing review.')
    return out


def validate_actuals(data,terms):
    reject_extra(data,{'service_hours','quantities','notes','reason'})
    h=number(data.get('service_hours'),'Actual customer service hours',0,100)
    q=data.get('quantities',{})
    if not isinstance(q,dict):raise ValueError('Material quantities must be an object.')
    codes={c['code'] for c in terms['catalogue']}
    if set(q)-codes:raise ValueError('A material/extra has not been approved by management.')
    out={}
    for k,v in q.items():
        n=number(v,'Quantity',0,10000)
        if n!=n.to_integral():raise ValueError('Material/extra quantities must be whole units.')
        out[k]=int(n)
    reason=short(data.get('reason',''),'Change reason',2000)
    if not reason:raise ValueError('Briefly explain the actuals or correction.')
    return {'service_hours':float(h),'quantities':out,'notes':short(data.get('notes',''),'Customer notes'), 'reason':reason}


def calculate_invoice(terms,actual,deposit_cents=0):
    raw=actual.get('service_hours')
    recorded=number(raw if raw is not None else 0,'Service hours',0,100)
    billed=max(recorded,Decimal(str(terms['minimum_hours'])))
    amount=int((billed*terms['hourly_rate_cents']).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    lines=[{'description':'Moving service','quantity':str(billed),'unit_cents':terms['hourly_rate_cents'],'amount_cents':amount},
           {'description':'Agreed transportation fee','quantity':'1','unit_cents':terms['transport_amount_cents'],'amount_cents':terms['transport_amount_cents']},
           {'description':'Agreed fuel fee','quantity':'1','unit_cents':terms['fuel_amount_cents'],'amount_cents':terms['fuel_amount_cents']}]
    if terms.get('other_amount_cents',0):lines.append({'description':'Other accepted services','quantity':'1','unit_cents':terms['other_amount_cents'],'amount_cents':terms['other_amount_cents']})
    quantities=actual.get('quantities',{})
    for item in terms['catalogue']:
        n=quantities.get(item['code'],0)
        if n:lines.append({'description':item['name'],'quantity':str(n),'unit_cents':item['unit_cents'],'amount_cents':n*item['unit_cents']})
    testimonial=int(terms.get('testimonial_credit_cents',0))
    before_credit=testimonial if terms.get('testimonial_credit_mode')=='before_tax' else 0
    after_credit=testimonial if terms.get('testimonial_credit_mode')=='after_tax' else 0
    subtotal=sum(l['amount_cents'] for l in lines)-terms['discount_amount_cents']-before_credit
    if subtotal<0:raise ValueError('Discount exceeds this invoice. Ask management to review the terms.')
    taxes=[]
    for t in terms['taxes']:
        tax=int((Decimal(subtotal)*Decimal(t['rate_percent'])/100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        taxes.append({**t,'amount_cents':tax})
    total=subtotal+sum(t['amount_cents'] for t in taxes)-after_credit
    if total<0:raise ValueError('Credit exceeds invoice total.')
    return {'lines':lines,'actual_hours_recorded':raw is not None,'actual_service_hours':float(recorded),'billable_service_hours':float(billed),
            'minimum_hours':terms['minimum_hours'],'testimonial_credit_cents':testimonial,'testimonial_credit_mode':terms.get('testimonial_credit_mode'),'discount_cents':terms['discount_amount_cents'],'subtotal_cents':subtotal,
            'taxes':taxes,'total_cents':total,'deposit_cents':deposit_cents,'balance_cents':total-deposit_cents,'currency':'CAD'}


def invoice_html(document):
    e=lambda x:escape(str(x),quote=True)
    money=lambda n:'CA$'+f'{n/100:,.2f}'
    terms=document['terms'];r=document['calculation'];issuer=terms['issuer'];customer=terms['customer']
    markup = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'''+e(document['number'])+'''</title><style>
body{font:15px/1.6 Arial,sans-serif;color:#172d4b;max-width:820px;padding:30px;margin:auto}h1{margin-bottom:0}h2{font-size:18px}.pilot{padding:10px;background:#fff0cb;color:#60450b}.meta{display:flex;gap:36px;justify-content:space-between}table{width:100%;border-collapse:collapse;margin:24px 0}td,th{border-bottom:1px solid #dbe1e8;padding:10px;text-align:right}td:first-child,th:first-child{text-align:left}.totals{margin-left:auto;max-width:390px}.totals p{display:flex;justify-content:space-between}strong{font-weight:700}@media print{body{padding:0}}
</style></head><body><p class="pilot">PRIVATE PILOT / TEST DOCUMENT. Not synchronized with accounting. Do not use with real clients until billing setup is approved for production.</p><h1>'''+e(issuer['name'])+'''</h1><p>'''+e(issuer['address'])+'''<br>'''+e(issuer['email'])+''' | '''+e(issuer['phone'])+'''</p><div class="meta"><div><h2>Bill to</h2>'''+e(customer['name'])+'''<br>'''+e(customer['address'])+'''<br>'''+e(customer['email'])+'''</div><div><h2>Invoice '''+e(document['number'])+'''</h2>Revision '''+e(document['revision'])+''' | '''+e(document['status'])+'''<br>Service date: '''+e(document['service_date'])+'''<br>Issued: '''+e(document.get('issued_at','Not issued'))+'''</div></div><table><thead><tr><th>Description</th><th>Quantity</th><th>Rate</th><th>Amount</th></tr></thead><tbody>'''+''.join('<tr><td>'+e(l['description'])+'</td><td>'+e(l['quantity'])+'</td><td>'+money(l['unit_cents'])+'</td><td>'+money(l['amount_cents'])+'</td></tr>' for l in r['lines'])+'''</tbody></table><div class="totals"><p><span>Agreed discount</span><span>-'''+money(r['discount_cents'])+'''</span></p><p><span>Subtotal</span><span>'''+money(r['subtotal_cents'])+'''</span></p>'''+''.join('<p><span>'+e(t['name'])+' ('+e(t['rate_percent'])+'%)</span><span>'+money(t['amount_cents'])+'</span></p>' for t in r['taxes'])+'''<p><strong>Total</strong><strong>'''+money(r['total_cents'])+'''</strong></p><p><span>Verified payments allocated</span><span>-'''+money(r['deposit_cents'])+'''</span></p><p><strong>Balance at issue</strong><strong>'''+money(r['balance_cents'])+'''</strong></p></div><p>Recorded service: '''+e(r['actual_service_hours'])+''' hours. Agreed minimum: '''+e(r['minimum_hours'])+''' hours. Transport is billed separately as agreed.</p><p>'''+e(document['actual'].get('notes',''))+'''</p><p>'''+e(' | '.join(t['name']+' registration: '+t['registration'] for t in terms['taxes']))+'''</p><p>Printed revisions are preserved. A later correction is a new revision, not a deletion. This document does not verify collection of unallocated or unconfirmed payments.</p></body></html>'''


    if not document.get('test_mode',True):
        markup=markup.replace('PRIVATE PILOT / TEST DOCUMENT. Not synchronized with accounting. Do not use with real clients until billing setup is approved for production.','Issued by CrownOps. See receipt/payment record for the current balance.')
    if r.get('testimonial_credit_cents'):
        label='Optional testimonial participation credit ('+e(r.get('testimonial_credit_mode',''))+')'
        markup=markup.replace('<p><strong>Total</strong>', '<p><span>'+label+'</span><span>-'+money(r['testimonial_credit_cents'])+'</span></p><p><strong>Total</strong>')
    return markup


def register_invoices(app,path,auth,visible_job,require):
    def access(db,iid,u):
        inv=one(db,'SELECT * FROM job_invoices WHERE id=?',(iid,))
        if not inv:raise HTTPException(404,'Invoice not found.')
        j=visible_job(db,inv['job_id'],u)
        return inv,j
    def credits(db,iid):
        return db.execute('''SELECT coalesce(sum(a.amount_cents),0) FROM invoice_payment_allocations a
           JOIN payments p ON p.id=a.payment_id WHERE a.invoice_id=? AND p.verified=1
           AND NOT EXISTS (SELECT 1 FROM payments rev WHERE rev.reverses_id=p.id)''',(iid,)).fetchone()[0]
    def decorate(db,inv,j):
        terms=json.loads(inv['terms_json']);actual=json.loads(inv['actual_json'])
        calc=calculate_invoice(terms,actual,credits(db,inv['id']))
        return {'id':inv['id'],'job_id':j['id'],'number':inv['number'],'status':inv['status'],'revision':inv['revision'],'version':inv['version'],
                'service_date':__import__('datetime').datetime.fromisoformat(j['start_at']).astimezone(__import__('zoneinfo').ZoneInfo('America/Toronto')).date().isoformat(),'job_status':j['status'],'client_name':terms['customer']['name'],
                'terms':terms,'actual':actual,'calculation':calc,'updated_at':inv['updated_at'],
                'history':rows(db,'SELECT revision,issued_at FROM invoice_revisions WHERE invoice_id=? ORDER BY revision',(inv['id'],))}

    @app.get('/api/invoices')
    def list_invoices(u=Depends(auth)):
        with closing(connect(path)) as db:
            recs=rows(db,'SELECT * FROM job_invoices ORDER BY created_at DESC')
            out=[]
            for inv in recs:
                if u['role']=='crew' and not one(db,"SELECT job_id FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(inv['job_id'],u.get('employee_id') or '')):continue
                j=visible_job(db,inv['job_id'],u);doc=decorate(db,inv,j)
                out.append({k:doc[k] for k in ('id','job_id','number','status','revision','version','service_date','client_name','calculation')})
            return out

    @app.get('/api/jobs/{jid}/invoice')
    def job_invoice(jid:str,u=Depends(auth)):
        with closing(connect(path)) as db:
            j=visible_job(db,jid,u);inv=one(db,'SELECT * FROM job_invoices WHERE job_id=?',(jid,))
            return decorate(db,inv,j) if inv else {'ready':False,'job_id':jid,'message':'Manager must approve billing terms before the team can invoice.'}

    @app.post('/api/jobs/{jid}/invoice-setup')
    def setup_invoice(jid:str,data:dict,u=Depends(auth)):
        require(u,{'owner'})
        try:terms=validate_terms(data)
        except (ValueError,TypeError,KeyError) as ex:raise HTTPException(422,str(ex))
        with transaction(path) as db:
            j=visible_job(db,jid,u)
            if j['status'] not in ('Confirmed','In progress','Completed'):raise HTTPException(409,'Confirm the job first.')
            if one(db,'SELECT id FROM job_invoices WHERE job_id=?',(jid,)):raise HTTPException(409,'Billing is already set up for this job. No rates were overwritten.')
            iid=uid('INV');t=now()
            inv=dict(id=iid,job_id=jid,number=iid,status='Draft',terms_json=dumps(terms),actual_json='{}',terms_approved_by=u['id'],terms_approved_at=t,created_by=u['id'],created_at=t,updated_at=t)
            insert(db,'job_invoices',inv);audit(db,u['id'],'Invoice terms approved','job_invoices',iid,{'terms':terms})
        return {'id':iid,'job_id':jid}

    @app.get('/api/invoices/{iid}')
    def get_invoice(iid:str,u=Depends(auth)):
        with closing(connect(path)) as db:
            inv,j=access(db,iid,u);return decorate(db,inv,j)

    @app.patch('/api/invoices/{iid}')
    def edit_invoice(iid:str,data:dict,u=Depends(auth)):
        reject_extra(data,{'version','service_hours','quantities','notes','reason'})
        with transaction(path) as db:
            inv,j=access(db,iid,u)
            if j['status'] not in ('In progress','Completed'):raise HTTPException(409,'Start the job before entering actual invoice hours.')
            if inv['status']!='Draft':raise HTTPException(409,'Create a correction revision before editing an issued invoice.')
            if data.get('version')!=inv['version']:raise HTTPException(409,'A teammate changed this invoice. Reload before saving.')
            terms=json.loads(inv['terms_json'])
            try:actual=validate_actuals({k:v for k,v in data.items() if k!='version'},terms);result=calculate_invoice(terms,actual,credits(db,iid))
            except (ValueError,TypeError,KeyError) as ex:raise HTTPException(422,str(ex))
            if result['balance_cents']<0:raise HTTPException(409,'Payments exceed the revised total. Management must reconcile the credit first.')
            db.execute('UPDATE job_invoices SET actual_json=?,version=version+1,updated_at=? WHERE id=?',(dumps(actual),now(),iid))
            audit(db,u['id'],'Invoice actuals updated','job_invoices',iid,{'before':json.loads(inv['actual_json']),'after':actual})
            return decorate(db,one(db,'SELECT * FROM job_invoices WHERE id=?',(iid,)),j)

    @app.post('/api/invoices/{iid}/issue')
    def issue_invoice(iid:str,data:dict,u=Depends(auth)):
        reject_extra(data,{'version','confirmed'})
        if data.get('confirmed') is not True:raise HTTPException(422,'Confirm you checked the final invoice with the client.')
        with transaction(path) as db:
            inv,j=access(db,iid,u)
            if data.get('version')!=inv['version']:raise HTTPException(409,'Invoice changed. Reload and review it.')
            if inv['status']!='Draft':raise HTTPException(409,'This revision is already issued.')
            if j['status']!='Completed':raise HTTPException(409,'Mark the job completed before issuing its final invoice.')
            doc=decorate(db,inv,j)
            if not doc['calculation']['actual_hours_recorded']:raise HTTPException(422,'Enter actual hours first, not quoted hours.')
            if doc['calculation']['balance_cents']<0:raise HTTPException(409,'Management must review this credit before issue.')
            doc['status']='Issued';doc['issued_at']=now();doc['version']+=1;doc['test_mode']=os.getenv('CROWNOPS_ENV','test')!='production'
            insert(db,'invoice_revisions',dict(invoice_id=iid,revision=inv['revision'],document_json=dumps(doc),issued_by=u['id'],issued_at=doc['issued_at']))
            db.execute("UPDATE job_invoices SET status='Issued',version=version+1,updated_at=? WHERE id=?",(now(),iid))
            from .operations import prefill_payroll
            prefill_payroll(db,inv,doc,u['id'])
            audit(db,u['id'],'Invoice issued','job_invoices',iid,{'revision':inv['revision'],'total_cents':doc['calculation']['total_cents']})
            return {'ok':True,'id':iid,'revision':inv['revision'],'sent':False,'accounting_synced':False}

    @app.post('/api/invoices/{iid}/correct')
    def correct_invoice(iid:str,data:dict,u=Depends(auth)):
        reject_extra(data,{'version','reason'})
        reason=short(data.get('reason',''),'Correction reason',2000)
        if not reason:raise HTTPException(422,'Enter a reason for the correction.')
        with transaction(path) as db:
            inv,j=access(db,iid,u)
            if data.get('version')!=inv['version'] or inv['status']!='Issued':raise HTTPException(409,'Reload the current issued invoice before correcting it.')
            if j['status']!='Completed':raise HTTPException(409,'Only a completed job invoice can be corrected.')
            if one(db,"SELECT id FROM payment_links WHERE invoice_id=? AND status IN ('Creating','Active','Unknown','Cancelling')",(iid,)):raise HTTPException(409,'Cancel or reconcile the open checkout link before correcting the invoice.')
            if one(db,"SELECT id FROM provider_operations WHERE object_id=? AND status IN ('Running','Unknown','Mismatch')",(iid,)):raise HTTPException(409,'Reconcile the pending accounting operation before changing this invoice.')
            db.execute("UPDATE outbound_messages SET status='Cancelled',error='Invoice revised; recreate message' WHERE event_key LIKE ? AND status IN ('Draft','Approved')",('invoice-mail:'+iid+':%',))
            db.execute("UPDATE job_invoices SET status='Draft',revision=revision+1,version=version+1,updated_at=? WHERE id=?",(now(),iid))
            audit(db,u['id'],'Invoice correction opened','job_invoices',iid,{'prior_revision':inv['revision'],'reason':reason})
            return {'ok':True}

    @app.post('/api/invoices/{iid}/allocate-payment')
    def allocate_payment(iid:str,data:dict,u=Depends(auth)):
        require(u,{'owner','accounting'})
        reject_extra(data,{'payment_id','amount'})
        try:amt=cents(number(data.get('amount'),'Allocated payment',.01,1000000))
        except ValueError as ex:raise HTTPException(422,str(ex))
        with transaction(path) as db:
            inv,j=access(db,iid,u);pay=one(db,'SELECT * FROM payments WHERE id=?',(data.get('payment_id'),))
            if not pay or pay['lead_id']!=j['lead_id'] or not pay['verified'] or pay['amount_cents']<=0 or pay['reverses_id']:
                raise HTTPException(422,'Select a verified positive payment from this client project.')
            if one(db,'SELECT id FROM payments WHERE reverses_id=?',(pay['id'],)):raise HTTPException(409,'This payment has been reversed.')
            used=db.execute('SELECT coalesce(sum(amount_cents),0) FROM invoice_payment_allocations WHERE payment_id=?',(pay['id'],)).fetchone()[0]
            if used+amt>pay['amount_cents']:raise HTTPException(409,'This payment has already been allocated in whole or part.')
            if amt>decorate(db,inv,j)['calculation']['balance_cents']:raise HTTPException(409,'Allocation exceeds the remaining invoice balance.')
            insert(db,'invoice_payment_allocations',dict(id=uid('ALLOC'),invoice_id=iid,payment_id=pay['id'],amount_cents=amt,allocated_by=u['id'],created_at=now()))
            db.execute('UPDATE job_invoices SET version=version+1,updated_at=? WHERE id=?',(now(),iid))
            audit(db,u['id'],'Verified payment allocated','job_invoices',iid,{'payment_id':pay['id'],'amount_cents':amt})
            return {'ok':True}

    @app.get('/api/invoices/{iid}/document')
    def document(iid:str,revision:int=0,u=Depends(auth)):
        with closing(connect(path)) as db:
            inv,j=access(db,iid,u)
            # Never silently substitute an old final document while a correction is open.
            if not revision and inv['status']!='Issued':raise HTTPException(409,'Issue this revision before downloading its final document.')
            rev=revision or inv['revision'];saved=one(db,'SELECT document_json FROM invoice_revisions WHERE invoice_id=? AND revision=?',(iid,rev))
            if not saved:raise HTTPException(404,'Issued revision not found.')
            doc=json.loads(saved['document_json'])
            return HTMLResponse(invoice_html(doc),headers={'Content-Disposition':f'attachment; filename="{inv["number"]}-r{rev}.html"'})
