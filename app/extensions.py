"""Completion workflows and externally configured accounting / messaging actions."""
from contextlib import closing
from datetime import datetime, timezone, timedelta, date
from urllib.parse import quote as urlquote
from email.message import EmailMessage
import base64, hashlib, json, os, secrets, time
import requests
from fastapi import Depends, HTTPException, Request
from .client_communications import pending_scope_changes,quote_scope_current
from .store import *
from .platform_common import *
from .pricing import number, cents
from .providers import qbo_call,qbo_customer,qbo_mapping,provider_token,process_mail,token_save
from .invoice_payments import checked_quickbooks_link


def qbo_work(path,kind,obj,rev,actor,action):
    """Reserve one write; failures of uncertain completion require human reconciliation."""
    key='qbo:'+kind+':'+obj+':'+str(rev)
    with transaction(path) as db:
        old=one(db,'SELECT * FROM provider_operations WHERE id=?',(key,))
        if old:
            if old['status']=='Completed':return {**json.loads(old['result_json']),'duplicate':True}
            raise HTTPException(409,'Existing accounting attempt requires reconciliation before retrying.')
        insert(db,'provider_operations',dict(id=key,provider='quickbooks',object_id=obj,kind=kind,status='Running',created_at=now(),updated_at=now()))
    try:
        out=action(key)
        with transaction(path) as db:
            db.execute("UPDATE provider_operations SET status='Completed',result_json=?,updated_at=? WHERE id=?",(dumps(out),now(),key));audit(db,actor,'Accounting operation completed',kind,obj,out)
        return out
    except Exception as e:
        failed=isinstance(e,ValueError)
        with transaction(path) as db:db.execute('UPDATE provider_operations SET status=?,error=?,updated_at=? WHERE id=?',('Failed' if failed else 'Unknown',str(e)[:250] if failed else audit_safe_error(e),now(),key))
        raise HTTPException(502,str(e)[:250] if failed else 'Provider completion is unknown; reconcile before retrying.')


def register_extensions(app,path,auth,public_url):
    @app.get('/api/invoices/{iid}/quickbooks-payment-link')
    def quickbooks_payment_link(iid:str,u=Depends(auth)):
        from fastapi.responses import JSONResponse
        return JSONResponse(checked_quickbooks_link(path,iid,u),headers={'Cache-Control':'no-store'})

    @app.post('/api/quotes/{qid}/quickbooks')
    def export_estimate(qid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES);quickbooks_gate()
        if data.get('confirm') is not True:raise HTTPException(422,'Confirm draft-only export.')
        with closing(connect(path)) as db:
            q=one(db,'SELECT * FROM quotes WHERE id=?',(qid,))
            if not q or q['status'] not in ('Approved','Accepted'):raise HTTPException(409,'Approve this estimate before export.')
            l=one(db,'SELECT * FROM leads WHERE id=?',(q['lead_id'],))
            if l['notifications_suppressed'] or not quote_scope_current(db,q,l):raise HTTPException(409,'Activate and review the current client project first.')
            r=json.loads(q['result_json'])
        # Range is not a fixed quote: staff must choose which endpoint QBO displays.
        if data.get('basis') not in ('low','high'):raise HTTPException(422,'Choose the reviewed low or high range endpoint for the QuickBooks draft.')
        item=qbo_mapping(path,'service_item_id');tax=qbo_mapping(path,'tax_code_id')
        if not item or not tax:raise HTTPException(503,'Configure reviewed service item and tax code IDs.')
        amt=r[data['basis']+'_cents']
        def go(key):
            cid=qbo_customer(path,l)
            body={'CustomerRef':{'value':cid},'CurrencyRef':{'value':'CAD'},'GlobalTaxCalculation':'TaxExcluded','TxnDate':now()[:10],
               'Line':[{'DetailType':'SalesItemLineDetail','Amount':amt/100,'Description':f"CrownOps estimate range CA${r['low_cents']/100:.2f} - CA${r['high_cents']/100:.2f} before applicable taxes. This draft uses the {data['basis']} endpoint. Actual service under accepted terms. {r['movers']} movers / {r['trucks']} trucks.",
                 'SalesItemLineDetail':{'ItemRef':{'value':item},'TaxCodeRef':{'value':tax},'Qty':1,'UnitPrice':amt/100}}],
               'PrivateNote':'CrownOps '+qid+'; draft only; client delivery not authorized by this export.'}
            remote=qbo_call(path,'POST','estimate',body,key)['Estimate']
            with transaction(path) as db:insert(db,'external_records',dict(provider='quickbooks',kind='Estimate',object_id=qid,revision=1,external_id=remote['Id'],sync_token=remote.get('SyncToken','0'),amount_cents=amt,updated_at=now()))
            return {'external_id':remote['Id'],'sent':False,'range_endpoint':data['basis']}
        return qbo_work(path,'Estimate',qid,1,u['id'],go)

    @app.post('/api/payments/{pid}/quickbooks')
    def export_payment(pid:str,data:dict,u=Depends(auth)):
        require_role(u,{'owner','manager','accounting'});quickbooks_gate()
        if data.get('confirm') is not True:raise HTTPException(422,'Confirm this accounting payment posting; it does not charge a card.')
        with closing(connect(path)) as db:
            p=one(db,'SELECT * FROM payments WHERE id=?',(pid,))
            if not p or not p['verified'] or p['amount_cents']<=0 or p['reverses_id'] or one(db,'SELECT id FROM payments WHERE reverses_id=?',(pid,)):raise HTTPException(409,'Only verified unreversed receipts can be posted.')
            l=one(db,'SELECT * FROM leads WHERE id=?',(p['lead_id'],))
            aa=rows(db,'SELECT a.*,i.revision,i.status FROM invoice_payment_allocations a JOIN job_invoices i ON i.id=a.invoice_id WHERE a.payment_id=?',(pid,))
            if not aa or sum(x['amount_cents'] for x in aa)!=p['amount_cents']:raise HTTPException(409,'Allocate the full verified receipt to issued invoices first. Unapplied deposits require accounting review.')
            links=[]
            for a in aa:
                ext=one(db,"SELECT * FROM external_records WHERE provider='quickbooks' AND kind='Invoice' AND object_id=? AND revision=?",(a['invoice_id'],a['revision']))
                op=one(db,'SELECT status FROM provider_operations WHERE id=?',('qbo:invoice:'+a['invoice_id']+':'+str(a['revision']),))
                if not ext or a['status']!='Issued' or not op or op['status']!='Completed':raise HTTPException(409,'Synchronize and reconcile each current invoice before posting its receipt.')
                links.append({'Amount':a['amount_cents']/100,'LinkedTxn':[{'TxnId':ext['external_id'],'TxnType':'Invoice'}]})
        def go(key):
            body={'CustomerRef':{'value':qbo_customer(path,l)},'TotalAmt':p['amount_cents']/100,'CurrencyRef':{'value':'CAD'},'TxnDate':p['created_at'][:10],'PaymentRefNum':p['reference'][:21],'PrivateNote':'CrownOps receipt '+pid,'Line':links}
            dep=qbo_mapping(path,'deposit_account_id')
            if dep:body['DepositToAccountRef']={'value':dep}
            remote=qbo_call(path,'POST','payment',body,key)['Payment']
            with transaction(path) as db:insert(db,'external_records',dict(provider='quickbooks',kind='Payment',object_id=pid,revision=1,external_id=remote['Id'],sync_token=remote.get('SyncToken','0'),amount_cents=p['amount_cents'],updated_at=now()))
            return {'external_id':remote['Id'],'money_moved':False}
        return qbo_work(path,'Payment',pid,1,u['id'],go)

    @app.post('/api/invoices/{iid}/cancel-checkout')
    def cancel_checkout(iid:str,data:dict,u=Depends(auth)):
        external_gate()
        with transaction(path) as db:
            inv,_=invoice_current(db,iid);invoice_job(db,inv['job_id'],u)
            link=one(db,"SELECT * FROM payment_links WHERE invoice_id=? AND status IN ('Active','Unknown','Creating') ORDER BY created_at DESC LIMIT 1",(iid,))
            if not link:return {'ok':True,'already_closed':True}
            if link['status']!='Active' or not link['link_id']:raise HTTPException(409,'An unresolved checkout creation must be checked in Square before it can be closed here.')
            if data.get('confirm') is not True:raise HTTPException(422,'Confirm checkout cancellation.')
            db.execute("UPDATE payment_links SET status='Cancelling' WHERE id=?",(link['id'],))
        try:
            host='https://connect.squareupsandbox.com' if os.getenv('SQUARE_ENV','sandbox')=='sandbox' else 'https://connect.squareup.com'
            resp=requests.delete(host+'/v2/online-checkout/payment-links/'+urlquote(link['link_id'],safe=''),headers={'Authorization':'Bearer '+os.getenv('SQUARE_ACCESS_TOKEN',''),'Square-Version':os.getenv('SQUARE_API_VERSION','2026-09-16')},timeout=25)
            resp.raise_for_status()
            with transaction(path) as db:
                # A completed payment callback wins over cancellation.
                db.execute("UPDATE payment_links SET status='Cancelled' WHERE id=? AND status='Cancelling'",(link['id'],));audit(db,u['id'],'Checkout closed at provider','payment_links',link['id'])
            return {'ok':True}
        except Exception:
            with transaction(path) as db:db.execute("UPDATE payment_links SET status='Unknown' WHERE id=? AND status='Cancelling'",(link['id'],))
            raise HTTPException(502,'Checkout cancellation was not confirmed. Check Square before collecting or correcting.')

    @app.get('/api/invoices/{iid}/delivery-state')
    def invoice_delivery_state(iid:str,u=Depends(auth)):
        with closing(connect(path)) as db:
            inv,_=invoice_current(db,iid);invoice_job(db,inv['job_id'],u)
            record=one(db,"""SELECT id,status,sent_at,created_at FROM outbound_messages
                WHERE event_key IN (?,?) ORDER BY CASE WHEN status='Sent' THEN 0 WHEN status='Draft' THEN 2 ELSE 1 END,created_at DESC LIMIT 1""",
                ('invoice-delivery:'+iid+':'+str(inv['revision']),'invoice-mail:'+iid+':'+str(inv['revision'])))
            lead=one(db,'SELECT email FROM leads WHERE id=(SELECT lead_id FROM jobs WHERE id=?)',(inv['job_id'],))
            terms=json.loads(inv['terms_json']);billing=str(terms.get('customer',{}).get('email') or lead['email']).strip().lower()
            recipients=list(dict.fromkeys([billing,lead['email'].lower()]))
        return {'invoice_id':iid,'revision':inv['revision'],'version':inv['version'],'recipient':billing,'recipients':recipients,'message_id':record['id'] if record else None,'status':record['status'] if record else 'Not sent',
                'sent_at':record['sent_at'] if record else None,'can_send':inv['status']=='Issued' and (not record or record['status'] in ('Draft','Cancelled'))}

    @app.post('/api/invoices/{iid}/deliver')
    def deliver(iid:str,data:dict,u=Depends(auth)):
        if data.get('confirm') is not True:raise HTTPException(422,'Confirm sending this finalized invoice to its reviewed billing recipient.')
        external_gate()
        from .invoice_payments import quickbooks_payment_state
        payment_state=quickbooks_payment_state(path,iid,user=u,reconcile=True,actor=u['id'])
        if payment_state['mapped'] and (payment_state['status'] in ('Review required','Unavailable') or payment_state['local_balance_cents']!=payment_state['remote_balance_cents']):raise HTTPException(409,payment_state['reason'] or 'Reconcile the current payment status before emailing this invoice.')
        payment=checked_quickbooks_link(path,iid,u) if data.get('include_quickbooks_payment') is True else None
        with transaction(path) as db:
            inv,calc=invoice_current(db,iid);j=invoice_job(db,inv['job_id'],u);l=one(db,'SELECT * FROM leads WHERE id=?',(j['lead_id'],))
            if inv['version']!=payment_state['version'] or inv['revision']!=payment_state['revision']:raise HTTPException(409,'Invoice or payments changed. Reload before emailing it.')
            if inv['status']!='Issued' or l['notifications_suppressed']:raise HTTPException(409,'A finalized invoice and an activated client email are required.')
            terms=json.loads(inv['terms_json']);billing=str(terms.get('customer',{}).get('email') or l['email']).strip().lower()
            recipient=str(data.get('recipient') or billing).strip().lower()
            if recipient not in {billing,l['email'].lower()} or not __import__('re').fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',recipient):raise HTTPException(422,'Choose the saved billing or client email. Management must correct any other billing recipient first.')
            if 'version' in data and (data['version']!=inv['version'] or data.get('revision')!=inv['revision']):raise HTTPException(409,'Invoice changed. Reload before confirming its recipient.')
            if os.getenv('CROWNOPS_ENV','test')!='production' and not recipient.endswith('.invalid'):
                allow={x.strip().lower() for x in os.getenv('CROWNOPS_TEST_EMAIL_ALLOWLIST','').split(',') if x.strip()}
                if recipient not in allow:raise HTTPException(409,'Test installation can send only to explicitly allowlisted test recipients.')
            existing=one(db,"""SELECT * FROM outbound_messages WHERE event_key IN (?,?)
                ORDER BY CASE WHEN status='Sent' THEN 0 WHEN status='Draft' THEN 2 ELSE 1 END,created_at DESC LIMIT 1""",
                ('invoice-delivery:'+iid+':'+str(inv['revision']),'invoice-mail:'+iid+':'+str(inv['revision'])))
            if existing and existing['status']=='Sent':return {'sent':True,'duplicate':True}
            if existing and existing['status'] not in ('Draft','Cancelled'):raise HTTPException(409,'This delivery is already approved, in progress or unresolved. Review delivery status before retrying.')
            if payment and (payment['version']!=inv['version'] or payment['revision']!=inv['revision'] or payment['amount_cents']!=calc['balance_cents']):
                raise HTTPException(409,'Invoice changed after checking its payment link. Reload before sending.')
            is_fr=l['language'].lower().startswith('fr')
            subject='Votre facture Crown Movers' if is_fr else 'Your Crown Movers invoice'
            from .client_communications import invoice_body
            from .invoice_access import invoice_access_link
            link=invoice_access_link(db,inv,recipient,u['id'],public_url)
            body=invoice_body(l,inv,calc,link,payment)
            m=existing or queue_message(db,l['id'],subject,body,'Invoice',u['id'],'invoice-delivery:'+iid+':'+str(inv['revision']),jid=j['id'])
            old=one(db,'SELECT * FROM outbound_messages WHERE id=?',(m['id'],))
            if old['status']=='Sent':return {'sent':True,'duplicate':True}
            if old['status'] not in ('Draft','Cancelled'):raise HTTPException(409,'This delivery is already approved, in progress or unresolved.')
            # Direct delivery always regenerates the current financial summary.
            db.execute('UPDATE outbound_messages SET subject=?,body=?,recipient=?,source_version=? WHERE id=?',(subject,body,recipient,inv['version'],m['id']))
            db.execute("UPDATE outbound_messages SET status='Approved',approved_by=? WHERE id=?",(u['id'],m['id']))
        return process_mail(path,m['id'])

    @app.get('/api/client-projects')
    def clients(u=Depends(auth)):
        with closing(connect(path)) as db:
            if u['role']=='crew':
                r=rows(db,"SELECT DISTINCT l.id,l.client_name,l.email,l.phone,l.pickup,l.dropoff,l.inventory,l.boxes,l.pickup_access,l.dropoff_access,l.pickup_walk,l.dropoff_walk,l.heavy_items,l.move_date,l.language,l.version FROM leads l JOIN jobs j ON j.lead_id=l.id JOIN assignments a ON a.job_id=j.id WHERE a.resource_type='employee' AND a.resource_id=? ORDER BY l.move_date DESC",(u.get('employee_id') or '',))
            else:r=rows(db,'SELECT id,client_name,email,phone,pickup,dropoff,inventory,boxes,pickup_access,dropoff_access,move_date,language,version,photo_link FROM leads ORDER BY move_date DESC LIMIT 1000')
            return r

    @app.put('/api/employees/{eid}/qualification')
    def qualification(eid:str,data:dict,u=Depends(auth)):
        require_role(u,MANAGERS)
        expiry=text(data.get('license_expiry',''),'License expiry',10);note=text(data.get('note',''),'Evidence',3000)
        if expiry:
            try:date.fromisoformat(expiry)
            except ValueError:raise HTTPException(422,'Use YYYY-MM-DD.')
        if not note:raise HTTPException(422,'Record reviewed qualification evidence.')
        with transaction(path) as db:
            if not one(db,'SELECT id FROM employees WHERE id=?',(eid,)):raise HTTPException(404,'Employee not found.')
            db.execute("INSERT INTO staff_profiles(employee_id,license_expiry,updated_at) VALUES(?,?,?) ON CONFLICT(employee_id) DO UPDATE SET license_expiry=excluded.license_expiry,updated_at=excluded.updated_at",(eid,expiry,now()));audit(db,u['id'],'Driver document expiry reviewed','employees',eid,{'note':note})
        return {'ok':True}

    @app.post('/api/automation/run')
    def run_automation(u=Depends(auth)):
        require_role(u,MANAGERS)
        from .worker import tick
        return tick(path)

    @app.get('/api/readiness')
    def readiness(u=Depends(auth)):
        require_role(u,OFFICE)
        env=quickbooks_environment()
        mappings={k:qbo_mapping(path,k) for k in ('service_item_id','tax_code_id','discount_account_id','deposit_account_id')}
        with closing(connect(path)) as db:
            groups={k:bool(settings(db,k,{}).get('approved')) for k in ('billing_policy','client_terms','deposit_policy','media_program','payroll_policy')}
            deposit=settings(db,'deposit_policy',{}) or {}
            connected=[r['provider'] for r in rows(db,'SELECT provider FROM oauth_credentials')]
            reviewed=settings(db,'quickbooks_mappings_reviewed_'+env,{}) or {}
            reviewed_matches=bool(reviewed and reviewed.get('policy')=='quebec-normal-move-v1' and
              all(str(reviewed.get(k,'')).strip()==str(mappings.get(k,'')).strip() for k in ('service_item_id','tax_code_id','deposit_account_id')))
            qbo_mappings_complete=bool(reviewed_matches and mappings['service_item_id'] and mappings['tax_code_id'] and mappings['deposit_account_id'])
            counts={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('leads','jobs','users','client_changes','attachments')}
            failures=db.execute("SELECT count(*) FROM provider_operations WHERE status IN ('Unknown','Mismatch','Failed','Running')").fetchone()[0]
            active_fleet=rows(db,"""SELECT v.id,v.name,p.gvwr_kg FROM vehicles v
              LEFT JOIN fleet_profiles p ON p.vehicle_id=v.id WHERE v.active=1 ORDER BY v.name""")
            missing_gvwr=[{'id':v['id'],'name':v['name']} for v in active_fleet if v.get('gvwr_kg') is None]
            verified_drivers=db.execute("SELECT count(*) FROM employees WHERE active=1 AND driver_verified=1").fetchone()[0]
        gmail_authorized='gmail' in connected
        qbo_authorized='quickbooks' in connected
        square_configured=bool(os.getenv('SQUARE_ACCESS_TOKEN') and os.getenv('SQUARE_LOCATION_ID'))
        interac_ready=bool(groups.get('deposit_policy') and deposit.get('enabled') and deposit.get('recipient_email'))
        items=[
          {'key':'billing','label':'Billing / tax policy approved','ready':groups.get('billing_policy',False),'critical':True},
          {'key':'terms','label':'Client booking terms approved','ready':groups.get('client_terms',False),'critical':True},
          {'key':'deposit','label':'Interac deposit instructions approved','ready':interac_ready,'critical':True},
          {'key':'gmail','label':'Gmail account authorized','ready':gmail_authorized,'critical':True},
          {'key':'fleet_gvwr','label':'Fleet GVWR completed before applicable long-distance dispatch','ready':not missing_gvwr,'critical':False,
            'detail':(', '.join(v['name'] for v in missing_gvwr)+' still needs GVWR') if missing_gvwr else 'All active trucks have GVWR on file'},
          {'key':'drivers','label':'Verified driver recorded before heavy-truck dispatch','ready':verified_drivers>0,'critical':False,
            'detail':str(verified_drivers)+' verified driver(s)'},
          {'key':'qbo_auth','label':'QuickBooks account authorized','ready':qbo_authorized,'critical':False},
          {'key':'qbo_mapping','label':'QuickBooks Quebec mappings reviewed','ready':qbo_mappings_complete,'critical':False},
          {'key':'square','label':'Square card deposits configured','ready':square_configured,'critical':False},
          {'key':'external','label':'External actions enabled for live use','ready':live_enabled(),'critical':True},
          {'key':'exceptions','label':'No unresolved integration operations','ready':failures==0,'critical':True,
            'detail':str(failures)+' unresolved operation(s)' if failures else ''},
        ]
        critical_ready=all(x['ready'] for x in items if x['critical'])
        return {'mode':os.getenv('CROWNOPS_ENV','test'),'public_url':public_url,'configuration_approved':groups,
          'authorized_connections':connected,'external_actions_enabled':live_enabled(),'counts':counts,
          'integration_exceptions':failures,'launch':{'critical_ready':critical_ready,'items':items,
          'missing_gvwr':missing_gvwr,'verified_driver_count':verified_drivers,'interac_ready':interac_ready,
          'qbo_environment':env,'qbo_mappings_complete':qbo_mappings_complete,'square_configured':square_configured},
          'notice':'Passing code tests and authorized accounts do not replace final production credential, accounting, tax, fleet or security review.'}
