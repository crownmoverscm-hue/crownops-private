"""Completion workflows and externally configured accounting / messaging actions."""
from contextlib import closing
from datetime import datetime, timezone, timedelta, date
from urllib.parse import quote as urlquote
from email.message import EmailMessage
import base64, hashlib, json, os, secrets, time
import requests
from fastapi import Depends, HTTPException, Request
from .store import *
from .platform_common import *
from .pricing import number, cents
from .providers import qbo_call,qbo_customer,qbo_mapping,provider_token,process_mail,token_save


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
    @app.post('/api/quotes/{qid}/quickbooks')
    def export_estimate(qid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES);quickbooks_gate()
        if data.get('confirm') is not True:raise HTTPException(422,'Confirm draft-only export.')
        with closing(connect(path)) as db:
            q=one(db,'SELECT * FROM quotes WHERE id=?',(qid,))
            if not q or q['status'] not in ('Approved','Accepted'):raise HTTPException(409,'Approve this estimate before export.')
            l=one(db,'SELECT * FROM leads WHERE id=?',(q['lead_id'],))
            if l['notifications_suppressed'] or l['version']!=q['lead_version']:raise HTTPException(409,'Activate and review the current client project first.')
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
        require_role(u,{'owner','accounting'});quickbooks_gate()
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
            inv,_=invoice_current(db,iid);assigned(db,inv['job_id'],u)
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

    @app.post('/api/invoices/{iid}/deliver')
    def deliver(iid:str,data:dict,u=Depends(auth)):
        if data.get('confirm') is not True:raise HTTPException(422,'Confirm sending this finalized invoice to the saved client email.')
        external_gate()
        with transaction(path) as db:
            inv,calc=invoice_current(db,iid);j=assigned(db,inv['job_id'],u);l=one(db,'SELECT * FROM leads WHERE id=?',(j['lead_id'],))
            if inv['status']!='Issued' or l['notifications_suppressed']:raise HTTPException(409,'A finalized invoice and an activated client email are required.')
            if os.getenv('CROWNOPS_ENV','test')!='production' and not l['email'].endswith('.invalid'):
                allow={x.strip().lower() for x in os.getenv('CROWNOPS_TEST_EMAIL_ALLOWLIST','').split(',') if x.strip()}
                if l['email'].lower() not in allow:raise HTTPException(409,'Test installation can send only to explicitly allowlisted test recipients.')
            m=queue_message(db,l['id'],'Votre facture Crown Movers' if l['language']=='French' else 'Your Crown Movers invoice',
              f"{l['client_name']},\n\nInvoice {inv['number']} (revision {inv['revision']}) is attached.\nTotal: CA${calc['total_cents']/100:.2f}\nVerified payments: CA${calc['deposit_cents']/100:.2f}\nConfirmed balance: CA${calc['balance_cents']/100:.2f}\nRecently collected payments may still be awaiting verification.\n\nThank you,\nCrown Movers",'Invoice',u['id'],'invoice-delivery:'+iid+':'+str(inv['revision']),jid=j['id'])
            old=one(db,'SELECT * FROM outbound_messages WHERE id=?',(m['id'],))
            if old['status']=='Sent':return {'sent':True,'duplicate':True}
            if old['status']!='Draft':raise HTTPException(409,'This delivery is already approved, in progress or unresolved.')
            db.execute("UPDATE outbound_messages SET status='Approved',approved_by=? WHERE id=?",(u['id'],m['id']))
        return process_mail(path,m['id'])

    @app.get('/api/client-projects')
    def clients(u=Depends(auth)):
        with closing(connect(path)) as db:
            if u['role']=='crew':
                r=rows(db,"SELECT DISTINCT l.id,l.client_name,l.email,l.phone,l.pickup,l.dropoff,l.inventory,l.boxes,l.pickup_access,l.dropoff_access,l.pickup_walk,l.dropoff_walk,l.heavy_items,l.move_date,l.language,l.version FROM leads l JOIN jobs j ON j.lead_id=l.id ORDER BY l.move_date DESC")
            else:r=rows(db,'SELECT id,client_name,email,phone,pickup,dropoff,inventory,boxes,pickup_access,dropoff_access,move_date,language,version FROM leads ORDER BY move_date DESC LIMIT 1000')
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
        with closing(connect(path)) as db:
            groups={k:bool(settings(db,k,{}).get('approved')) for k in ('billing_policy','client_terms','media_program','payroll_policy')}
            connected=[r['provider'] for r in rows(db,'SELECT provider FROM oauth_credentials')]
            counts={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('leads','jobs','users','client_changes','attachments')}
            failures=db.execute("SELECT count(*) FROM provider_operations WHERE status IN ('Unknown','Mismatch','Failed','Running')").fetchone()[0]
        return {'mode':os.getenv('CROWNOPS_ENV','test'),'public_url':public_url,'configuration_approved':groups,'authorized_connections':connected,'external_actions_enabled':live_enabled(),'counts':counts,'integration_exceptions':failures,'notice':'Authorized accounts and passed local tests are not a production security or accounting certification.'}
