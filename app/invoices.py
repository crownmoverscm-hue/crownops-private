"""End-of-job invoices for the private pilot, not an accounting-system replacement.

Crew can edit actuals and issue/correct their assigned-job invoice. Customer rates,
taxes and allowed unit prices are copied into a manager-approved billing profile.
No quote, payroll, payment verification or global pricing access is granted.
"""
from contextlib import closing
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from .company_profile import LEGACY_INVOICE_FOOTER
import json
import os
import re
from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse
from .store import connect,transaction,rows,one,insert,audit,uid,now,dumps
from .pricing import number,cents
from .platform_common import invoice_job
from .invoice_checks import allocated_credits,actuals_need_review,deposit_review_state,apply_unambiguous_deposits
from .business_zone import BUSINESS_ZONE, zone_name


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
    out['customer']={k:short(customer.get(k,''),'Customer '+k,1500 if k=='address' else 700) for k in ('name','address','email')}
    if out['customer']['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',out['customer']['email']):raise ValueError('Enter a valid customer billing email.')
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
        code=short(item.get('code',''),'Item code',60);name=short(item.get('name',''),'Item name',160)
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
    e=lambda value:escape(str(value),quote=True)
    money=lambda value:'CA$'+f'{value/100:,.2f}'
    fr=str(document.get('language','EN')).lower().startswith('fr')
    t=lambda en,ff:ff if fr else en
    terms=document['terms'];calc=document['calculation'];issuer=terms['issuer'];customer=terms['customer']
    descriptions={'Moving service':'Service de déménagement','Agreed transportation fee':'Frais de transport convenus','Agreed fuel fee':'Frais de carburant convenus','Other accepted services':'Autres services convenus'}
    line_rows=''.join('<tr><td>'+e(descriptions.get(line['description'],line['description']) if fr else line['description'])+'</td><td>'+e(line['quantity'])+'</td><td>'+money(line['unit_cents'])+'</td><td>'+money(line['amount_cents'])+'</td></tr>' for line in calc['lines'] if line['amount_cents'])
    def total_line(label,value,strong=False):
        tag='strong' if strong else 'span'
        return '<p><'+tag+'>'+e(label)+'</'+tag+'><'+tag+'>'+e(value)+'</'+tag+'></p>'
    totals=total_line(t('Agreed discount','Réduction convenue'),'-'+money(calc['discount_cents']))+total_line(t('Subtotal','Sous-total'),money(calc['subtotal_cents']))
    totals+=''.join(total_line(tax['name']+' ('+str(tax['rate_percent'])+'%)',money(tax['amount_cents'])) for tax in calc['taxes'])
    if calc.get('testimonial_credit_cents'):totals+=total_line(t('Approved participation credit','Crédit de participation approuvé'),'-'+money(calc['testimonial_credit_cents']))
    totals+=total_line(t('Invoice total','Total de la facture'),money(calc['total_cents']),True)+total_line(t('Verified payments applied','Paiements vérifiés appliqués'),'-'+money(calc['deposit_cents']))+total_line(t('Balance when issued','Solde à l’émission'),money(calc['balance_cents']),True)
    payment_terms=('<p><strong>'+t('Agreed payment terms','Modalités de paiement convenues')+'</strong><br>'+e(terms['payment_terms'])+'</p>') if terms.get('payment_terms') else ''
    if terms.get('due_date'):payment_terms+='<p><strong>'+t('Payment due','Paiement dû')+'</strong> : '+e(terms['due_date'])+'</p>'
    test=t('TEST DOCUMENT — for review only.','DOCUMENT DE TEST — pour révision seulement.')
    banner=('<p class="notice">'+test+'</p>') if document.get('test_mode',True) else ''
    status=t('Issued','Émise') if document['status']=='Issued' else document['status']
    return ('<!doctype html><html lang="'+('fr' if fr else 'en')+'"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+e(document['number'])+'</title><style>'
        'body{font:16px/1.6 Arial,sans-serif;color:#1d354c;max-width:850px;padding:32px;margin:auto}h1{margin:0;font-size:27px}h2{font-size:19px;margin:0 0 8px}p{margin:8px 0}.meta{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin:30px 0}.address{white-space:pre-line}.muted{color:#526579;font-size:14px}.notice{background:#fff0cb;padding:12px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;margin:24px 0}th,td{text-align:right;padding:12px 8px;border-bottom:1px solid #dce3ea}th:first-child,td:first-child{text-align:left}th{background:#f1f5f8}.totals{max-width:400px;margin-left:auto}.totals p{display:flex;justify-content:space-between;gap:16px}.footer{margin-top:32px;border-top:1px solid #dce3ea;padding-top:18px}@media(max-width:540px){body{padding:18px}.meta{grid-template-columns:1fr}table{font-size:14px}th,td{padding:9px 5px}}@media print{body{padding:0;font-size:12px}.meta{grid-template-columns:1fr 1fr}thead{display:table-header-group}tr{break-inside:avoid}.totals{break-inside:avoid}}'
        '</style></head><body>'+banner+'<header><h1>'+e(issuer['name'])+'</h1><p class="address">'+e(issuer['address'])+'</p><p class="muted">'+e(issuer['email'])+' · '+e(issuer['phone'])+'</p></header><div class="meta"><section><h2>'+t('Bill to','Facturer à')+'</h2><p class="address">'+e(customer['name'])+'\n'+e(customer['address'])+'</p><p>'+e(customer['email'])+'</p></section><section><h2>'+t('Invoice','Facture')+' '+e(document['number'])+'</h2><p>'+t('Revision','Révision')+' '+str(document['revision'])+' · '+e(status)+'</p><p>'+t('Service date','Date du service')+' : '+e(document['service_date'])+'</p><p class="muted">'+t('Issued','Émise le')+' : '+e(document.get('issued_at',t('Not issued','Non émise')))+'</p></section></div><div class="table-wrap"><table><thead><tr><th>'+t('Description','Description')+'</th><th>'+t('Quantity','Quantité')+'</th><th>'+t('Unit price','Prix unitaire')+'</th><th>'+t('Amount','Montant')+'</th></tr></thead><tbody>'+line_rows+'</tbody></table></div><section class="totals">'+totals+'</section><p>'+t('Actual service','Service réel')+' : '+e(calc['actual_service_hours'])+' '+t('hours','heures')+'. '+t('Agreed minimum','Minimum convenu')+' : '+e(calc['minimum_hours'])+' '+t('hours','heures')+'.</p><p class="address">'+e(document['actual'].get('notes',''))+'</p><p class="muted">'+e(' · '.join(tax['name']+' '+t('registration','inscription')+' : '+tax['registration'] for tax in terms['taxes']))+'</p>'+payment_terms+'<footer class="footer"><p>'+(t('Thank you for choosing '+e(document['company']['name'])+'. Questions? Call '+e(document['company']['phone'])+'.','Merci d’avoir choisi '+e(document['company']['name'])+'. Une question? Appelez le '+e(document['company']['phone'])+'.') if document.get('company') else t(*LEGACY_INVOICE_FOOTER))+'</p><p class="muted">'+t('This preserved revision shows the balance when issued. Your private invoice page shows the current verified payments and balance. A correction creates a new revision.','Cette révision conservée indique le solde à l’émission. Votre page de facture privée présente les paiements vérifiés et le solde actuel. Une correction crée une nouvelle révision.')+'</p></footer></body></html>')


def register_invoices(app,path,auth,visible_job,require):
    def access(db,iid,u):
        inv=one(db,'SELECT * FROM job_invoices WHERE id=?',(iid,))
        if not inv:raise HTTPException(404,'Invoice not found.')
        j=invoice_job(db,inv['job_id'],u)
        return inv,j
    def credits(db,iid):
        return allocated_credits(db,iid)
    def decorate(db,inv,j):
        terms=json.loads(inv['terms_json']);actual=json.loads(inv['actual_json'])
        calc=calculate_invoice(terms,actual,credits(db,inv['id']))
        needs_review=actuals_need_review(db,inv)
        if needs_review:calc['actual_hours_recorded']=False
        return {'id':inv['id'],'job_id':j['id'],'number':inv['number'],'status':inv['status'],'revision':inv['revision'],'version':inv['version'],
                'service_date':__import__('datetime').datetime.fromisoformat(j['start_at']).astimezone(BUSINESS_ZONE).date().isoformat(),'job_status':j['status'],'client_name':terms['customer']['name'],
                'terms':terms,'actual':actual,'calculation':calc,'updated_at':inv['updated_at'],
                'actuals_need_review':needs_review,'deposit_review':deposit_review_state(db,inv,j),
                'pending_extras':db.execute("SELECT count(*) FROM invoice_change_orders WHERE invoice_id=? AND status='Requested'",(inv['id'],)).fetchone()[0],
                'history':rows(db,'SELECT revision,issued_at FROM invoice_revisions WHERE invoice_id=? ORDER BY revision',(inv['id'],))}

    @app.get('/api/invoices')
    def list_invoices(u=Depends(auth)):
        with closing(connect(path)) as db:
            recs=rows(db,'SELECT * FROM job_invoices ORDER BY created_at DESC')
            out=[]
            for inv in recs:
                if u['role']=='crew' and not one(db,"SELECT job_id FROM assignments WHERE job_id=? AND resource_type='employee' AND resource_id=?",(inv['job_id'],u.get('employee_id') or '')):continue
                j=invoice_job(db,inv['job_id'],u);doc=decorate(db,inv,j)
                from .document_browser import invoice_tracking
                out.append({**{k:doc[k] for k in ('id','job_id','number','status','revision','version','service_date','client_name','calculation')}, **invoice_tracking(db,inv,doc['calculation'])})
            return out

    @app.get('/api/jobs/{jid}/invoice')
    def job_invoice(jid:str,u=Depends(auth)):
        with closing(connect(path)) as db:
            j=invoice_job(db,jid,u);inv=one(db,'SELECT * FROM job_invoices WHERE job_id=?',(jid,))
            return decorate(db,inv,j) if inv else {'ready':False,'job_id':jid,'message':'Manager must approve billing terms before the team can invoice.'}

    @app.post('/api/jobs/{jid}/invoice-setup')
    def setup_invoice(jid:str,data:dict,u=Depends(auth)):
        require(u,{'owner','manager'})
        try:terms=validate_terms(data)
        except (ValueError,TypeError,KeyError) as ex:raise HTTPException(422,str(ex))
        with transaction(path) as db:
            j=invoice_job(db,jid,u)
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
            apply_unambiguous_deposits(db,one(db,'SELECT * FROM job_invoices WHERE id=?',(iid,)),j,u['id'])
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
            apply_unambiguous_deposits(db,inv,j,u['id'])
            doc=decorate(db,inv,j)
            from .company_profile import ready_profile
            company=ready_profile(db,'issuing invoices')
            # Stamp the issuer's contact into the preserved revision; legacy Crown invoices keep their wording.
            if company['mode']!='legacy-crown':doc['company']={'name':company['name'],'phone':company['phone']}
            if doc['pending_extras']:raise HTTPException(409,'Management must approve or decline the requested extra items before this invoice can be finalized.')
            if doc['deposit_review']['review_required']:raise HTTPException(409,'A verified deposit needs allocation or management review before issuing this invoice.')
            if not doc['calculation']['actual_hours_recorded']:raise HTTPException(422,'Enter actual hours first, not quoted hours.')
            if doc['calculation']['balance_cents']<0:raise HTTPException(409,'Management must review this credit before issue.')
            lead=one(db,'SELECT language FROM leads WHERE id=?',(j['lead_id'],))
            doc['language']=lead['language'] if lead else 'EN'
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
            db.execute("UPDATE outbound_messages SET status='Cancelled',error='Invoice revised; recreate message' WHERE (event_key LIKE ? OR event_key LIKE ?) AND status IN ('Draft','Approved')",('invoice-mail:'+iid+':%','invoice-delivery:'+iid+':%'))
            db.execute("UPDATE job_invoices SET status='Draft',revision=revision+1,version=version+1,updated_at=? WHERE id=?",(now(),iid))
            audit(db,u['id'],'Invoice correction opened','job_invoices',iid,{'prior_revision':inv['revision'],'reason':reason})
            return {'ok':True}

    @app.post('/api/invoices/{iid}/deposit-review')
    def review_deposit(iid:str,data:dict,u=Depends(auth)):
        require(u,{'owner','manager'})
        reject_extra(data,{'version','reason'})
        reason=short(data.get('reason',''),'Deposit allocation / remaining credit review',2000)
        if len(reason)<10:raise HTTPException(422,'Explain which service receives the remaining deposit or how its credit will be handled.')
        with transaction(path) as db:
            inv,j=access(db,iid,u)
            if inv['status']!='Draft' or data.get('version')!=inv['version']:raise HTTPException(409,'Reload the current draft before reviewing its deposit.')
            state=deposit_review_state(db,inv,j)
            db.execute('INSERT INTO invoice_deposit_reviews (invoice_id,fingerprint,reason,reviewed_by,reviewed_at) VALUES (?,?,?,?,?) ON CONFLICT(invoice_id) DO UPDATE SET fingerprint=excluded.fingerprint,reason=excluded.reason,reviewed_by=excluded.reviewed_by,reviewed_at=excluded.reviewed_at',(iid,state['fingerprint'],reason,u['id'],now()))
            db.execute('UPDATE job_invoices SET version=version+1,updated_at=? WHERE id=?',(now(),iid))
            audit(db,u['id'],'Remaining verified deposit reviewed','job_invoices',iid,{'unallocated_cents':state['unallocated_cents'],'reason':reason})
        return {'ok':True}

    @app.post('/api/invoices/{iid}/allocate-payment')
    def allocate_payment(iid:str,data:dict,u=Depends(auth)):
        require(u,{'owner','manager','accounting'})
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
