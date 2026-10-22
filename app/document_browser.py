"""Document-list tracking and reviewed shortcuts. No sending or accounting writes."""
from contextlib import closing
from datetime import date
from html import escape
import json
from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse
from .store import connect, transaction, rows, one, audit, now, dumps
from .platform_common import OFFICE, SALES, MANAGERS, require_role, invoice_job, invoice_current, business_day
from .invoice_checks import allocated_credits


def invoice_tracking(db, inv, calculation):
    saved = one(db, 'SELECT * FROM invoice_revisions WHERE invoice_id=? ORDER BY revision DESC LIMIT 1', (inv['id'],))
    prior = json.loads(saved['document_json']) if saved else None
    # An unfinished correction must not make the last issued receivable disappear.
    terms = prior['terms'] if prior else json.loads(inv['terms_json'])
    total = prior['calculation']['total_cents'] if prior else calculation['total_cents']
    credits = allocated_credits(db, inv['id'])
    balance = max(0, total - credits)
    due = terms.get('due_date', '')
    issued = bool(saved)
    overdue = bool(issued and balance > 0 and due and due < business_day())
    state = ('Correction draft' if issued else 'Draft') if inv['status'] == 'Draft' else 'Paid' if balance == 0 else 'Overdue' if overdue else 'Partial' if credits > 0 else 'Balance due'
    message = one(db, "SELECT status,sent_at FROM outbound_messages WHERE event_key IN (?,?) ORDER BY CASE WHEN status='Sent' THEN 0 ELSE 1 END,created_at DESC LIMIT 1", ('invoice-delivery:'+inv['id']+':'+str(inv['revision']), 'invoice-mail:'+inv['id']+':'+str(inv['revision'])))
    native=one(db,'SELECT status,updated_at FROM provider_operations WHERE id=?',('qbo:InvoiceEmail:'+inv['id']+':'+str(inv['revision']),))
    if native:message={'status':'Sent' if native['status']=='Completed' else 'Sending' if native['status']=='Running' else 'Unknown','sent_at':native['updated_at'] if native['status']=='Completed' else None}
    return dict(created_at=inv['created_at'], issued_at=saved['issued_at'] if saved else None,
                due_date=due, display_state=state, overdue=overdue, has_issued_revision=issued,
                billing_total_cents=total, verified_paid_cents=credits if issued else 0,
                outstanding_cents=balance if issued else 0,
                delivery_provider='QuickBooks' if native else 'CrownOps',email_status=message['status'] if message else 'Not sent', sent_at=message['sent_at'] if message else None)


def print_controls(html):
    return html.replace('</head>', '<style>.document-print{padding:12px;background:#fff3e9;margin-bottom:24px}.document-print button{padding:12px 18px;font:inherit;cursor:pointer}@media print{.document-print{display:none}}</style><script src="/static/document_print.js" defer></script></head>').replace('<body>', '<body><div class="document-print"><button id="document-print">Print / save as PDF</button><p>Choose “Save as PDF” in your browser’s print window.</p></div>')


def quote_print(q):
    """Render saved client-facing values only; never include internal notes or links."""
    e=lambda x:escape(str(x or ''), quote=True)
    cash=lambda x:'CA$'+format(int(x or 0)/100, ',.2f')
    inp=json.loads(q['inputs_json']);r=json.loads(q['result_json']);l=json.loads(q['lead_snapshot_json']);b=inp.get('billing') or {}
    fr=str(inp.get('language') or l.get('language','')).lower().startswith('fr')
    t=lambda en,ff:ff if fr else en
    pairs=[(t('Client','Client'), b.get('name') or l.get('client_name')),
           (t('Billing address','Adresse de facturation'), b.get('address')),
           (t('Service date','Date du service'), inp.get('move_date') or l.get('move_date')),
           (t('Arrival window','Fenêtre d’arrivée'), inp.get('start_window_label') or inp.get('start_time')),
           (t('Pickup','Départ'), l.get('pickup')), (t('Destination','Destination'), l.get('dropoff')),
           (t('Movers / trucks','Déménageurs / camions'), str(r.get('movers','—'))+' / '+str(r.get('trucks','—'))),
           (t('Hourly rate','Tarif horaire'), cash(r.get('hourly_rate_cents'))),
           (t('Estimated service hours','Heures estimées'), str(r.get('service_low','—'))+' – '+str(r.get('service_high','—'))),
           (t('Transportation','Transport'), cash(r.get('transport_cents'))),
           (t('Fuel','Carburant'), cash(r.get('fuel_cents'))),
           (t('Materials','Matériaux'), cash(r.get('materials_cents'))),
           (t('Other agreed charges','Autres frais convenus'), cash(r.get('other_cents'))),
           (t('Discount','Réduction'), cash(r.get('discount_cents'))),
           (t('Expires','Expiration'), inp.get('expiry_date'))]
    details=''.join('<tr><th>'+e(k)+'</th><td>'+e(v)+'</td></tr>' for k,v in pairs if v)
    materials=''.join('<p>'+e(x.get('name'))+' × '+e(x.get('qty'))+' · '+cash(x.get('unit_cents'))+'<br>'+e(x.get('note'))+'</p>' for x in r.get('line_items',[]) if x.get('kind')=='material')
    template=inp.get('template_snapshot') or {}
    terms=''.join('<p>'+e(inp.get(key))+'</p>' for key in ('client_note','service_line_note','payment_terms') if inp.get(key))
    terms+=''.join('<p>'+e(template.get(key))+'</p>' for key in ('liability','fragile','blank_notes') if template.get(key))
    html='<!doctype html><html lang="'+('fr' if fr else 'en')+'"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+e(q['id'])+'</title><style>body{font:17px/1.6 Arial,sans-serif;color:#242424;max-width:850px;margin:auto;padding:24px}h1{border-bottom:4px solid #b84a0b;padding-bottom:16px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #ddd;vertical-align:top}p,td{white-space:pre-line;overflow-wrap:anywhere}.total{font-size:26px;font-weight:700}@media print{tr{break-inside:avoid}}</style></head><body><h1>Crown Movers</h1><h2>'+t('Moving estimate','Devis de déménagement')+'</h2><p>'+e(inp.get('quote_title'))+'</p><p>'+e(q['id'])+' · '+t('Revision','Révision')+' '+str(q['revision'])+' · '+e(q['status'])+'</p>'
    if q['status']=='Draft':html+='<strong>'+t('DRAFT — for review only','BROUILLON — pour révision seulement')+'</strong>'
    html+='<table>'+details+'</table>'+materials+'<p class="total">'+cash(r.get('low_cents'))+' – '+cash(r.get('high_cents'))+'</p><p>'+t('Estimate before applicable taxes. Actual charges depend on agreed terms and completed service.','Estimation avant les taxes applicables. Le montant final dépend des modalités convenues et du service effectué.')+'</p>'+terms+'<p>'+t('Quote acceptance is separate from booking confirmation.','L’acceptation du devis est distincte de la confirmation de réservation.')+'</p></body></html>'
    return print_controls(html)


def register_document_browser(app,path,auth,public_url):
    @app.get('/api/documents/quotes')
    def quotes(u=Depends(auth)):
        require_role(u,OFFICE)
        from .sales_crm_v6 import payload
        from .quote_options import expired_quote
        with closing(connect(path)) as db:
            messages={m['event_key']:m for m in rows(db,"SELECT event_key,status,sent_at FROM outbound_messages WHERE event_key LIKE 'quote-delivery:%'")}
            qbo_deliveries={d['quote_id']:d for d in rows(db,'SELECT quote_id,state,sent_at,error FROM qbo_estimate_deliveries')}
            views={v['quote_id']:v for v in rows(db,'SELECT quote_id,count(*) opens,max(viewed_at) last_viewed_at FROM quote_views GROUP BY quote_id')}
            invoices={}
            for i in rows(db,'SELECT j.quote_id,i.id,i.number,i.status,EXISTS(SELECT 1 FROM invoice_revisions r WHERE r.invoice_id=i.id) has_issued FROM job_invoices i JOIN jobs j ON j.id=i.job_id ORDER BY i.created_at'):
                invoices.setdefault(i['quote_id'],[]).append(i)
            result=[]
            for raw in rows(db,'SELECT q.*,l.client_name,l.email FROM quotes q JOIN leads l ON l.id=q.lead_id ORDER BY q.created_at DESC,q.revision DESC'):
                q=payload(raw);m=messages.get('quote-delivery:'+q['id']+':'+str(q['revision']),{});v=views.get(q['id'],{})
                qbo=qbo_deliveries.get(q['id'])
                if qbo:m={'status':'Sent' if qbo['sent_at'] else qbo['state'],'sent_at':qbo['sent_at']}
                linked=invoices.get(q['id'],[])
                issued=any(i['has_issued'] for i in linked)
                state='Invoiced' if issued else 'Accepted' if q['status']=='Accepted' else 'Expired' if q['status']=='Approved' and expired_quote(raw) else 'Sent' if q['status']=='Approved' and m.get('status')=='Sent' else 'Ready' if q['status']=='Approved' else q['status']
                if qbo and qbo['state']=='Rejected' and not issued and q['status']!='Accepted':state='Declined'
                q.update(delivery_provider='QuickBooks' if qbo else 'CrownOps',delivery_error=qbo['error'] if qbo else '')
                q.update(display_state=state, email_status=m.get('status','Not sent'), sent_at=m.get('sent_at'), opens=v.get('opens',0),last_viewed_at=v.get('last_viewed_at'),invoices=linked)
                result.append(q)
        return result

    @app.patch('/api/documents/invoices/{iid}/due-date')
    def due_date(iid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        if set(data)-{'due_date','version'}:raise HTTPException(422,'Only the due date can be changed here.')
        value=data.get('due_date','')
        try:
            if not isinstance(value,str) or (value and date.fromisoformat(value).isoformat()!=value):raise ValueError()
        except ValueError:raise HTTPException(422,'Choose a valid due date or leave it blank.')
        with transaction(path) as db:
            inv,_=invoice_current(db,iid);invoice_job(db,inv['job_id'],u)
            if inv['status']!='Draft' or data.get('version')!=inv['version']:raise HTTPException(409,'Open the current draft to change its due date. Issued invoices require a correction revision.')
            terms=json.loads(inv['terms_json']);old=terms.get('due_date','');terms['due_date']=value
            db.execute('UPDATE job_invoices SET terms_json=?,version=version+1,updated_at=? WHERE id=?',(dumps(terms),now(),iid))
            audit(db,u['id'],'Invoice due date reviewed','job_invoices',iid,{'before':old,'after':value})
        return {'ok':True}

    @app.get('/api/documents/quotes/{qid}/print')
    def print_quote(qid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:q=one(db,'SELECT * FROM quotes WHERE id=?',(qid,))
        if not q:raise HTTPException(404,'Estimate not found.')
        return HTMLResponse(quote_print(q),headers={'Cache-Control':'no-store'})

    @app.get('/api/documents/invoices/{iid}/print')
    def print_invoice(iid:str,u=Depends(auth)):
        from .invoices import invoice_html
        with closing(connect(path)) as db:
            inv,_=invoice_current(db,iid);invoice_job(db,inv['job_id'],u)
            if inv['status']!='Issued':raise HTTPException(409,'Finalize this revision before printing a client invoice.')
            saved=one(db,'SELECT document_json FROM invoice_revisions WHERE invoice_id=? AND revision=?',(iid,inv['revision']))
            if not saved:raise HTTPException(404,'Issued revision not found.')
            return HTMLResponse(print_controls(invoice_html(json.loads(saved['document_json']))),headers={'Cache-Control':'no-store'})

    @app.post('/api/documents/{kind}/{doc_id}/share')
    def share(kind:str,doc_id:str,data:dict,u=Depends(auth)):
        if data.get('confirmed') is not True:raise HTTPException(422,'Confirm this private link is intended only for the client.')
        with transaction(path) as db:
            if kind=='quotes':
                require_role(u,SALES)
                q=one(db,'SELECT * FROM quotes WHERE id=?',(doc_id,))
                if not q:raise HTTPException(404,'Estimate not found.')
                from .quote_options import expired_quote
                if q['status'] not in ('Approved','Accepted') or (q['status']!='Accepted' and expired_quote(q)):raise HTTPException(409,'Approve a current estimate before sharing client access.')
                if one(db,"SELECT id FROM quotes WHERE lead_id=? AND revision>? AND status IN ('Approved','Accepted')",(q['lead_id'],q['revision'])):raise HTTPException(409,'Share the latest approved estimate instead.')
                lead=one(db,'SELECT * FROM leads WHERE id=?',(q['lead_id'],))
                from .client_communications import portal_link, quote_scope_current, pending_scope_changes
                if not quote_scope_current(db,q,lead) or pending_scope_changes(db,lead['id']):raise HTTPException(409,'Review changed client details before sharing this estimate.')
                if not lead['email']:raise HTTPException(422,'Save the client email first.')
                url=portal_link(db,lead,u['id'],public_url,tab='estimates');recipient=lead['email']
            elif kind=='invoices':
                inv,_=invoice_current(db,doc_id);job=invoice_job(db,inv['job_id'],u)
                if inv['status']!='Issued' or (data.get('version'),data.get('revision'))!=(inv['version'],inv['revision']):raise HTTPException(409,'Open the current finalized invoice before sharing.')
                lead=one(db,'SELECT * FROM leads WHERE id=?',(job['lead_id'],));terms=json.loads(inv['terms_json'])
                recipient=terms['customer'].get('email') or lead['email']
                if not recipient:raise HTTPException(422,'Save the billing email first.')
                from .invoice_access import invoice_access_link
                url=invoice_access_link(db,inv,recipient,u['id'],public_url)
            else:raise HTTPException(404,'Document type not found.')
            audit(db,u['id'],'Private document link prepared',kind,doc_id,{'recipient':recipient,'sent':False})
        return {'url':url,'recipient':recipient,'sent':False}
