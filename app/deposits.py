"""Accepted-estimate deposits. A paid deposit never bypasses dispatch approval."""
from contextlib import closing
from decimal import Decimal
import json,os,requests
from fastapi import HTTPException
from .store import connect,transaction,rows,one,insert,audit,now,uid
from .platform_common import external_gate,add_task
from .pricing import cents


def deposit_state(db,lid,q):
    required=cents(json.loads(q['inputs_json']).get('deposit_required',0))
    verified=db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE lead_id=? AND purpose='Deposit' AND verified=1",(lid,)).fetchone()[0]
    pending=db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE lead_id=? AND purpose='Deposit' AND verified=0",(lid,)).fetchone()[0]
    return {'required_cents':required,'verified_cents':verified,'pending_cents':max(0,pending),'due_cents':max(0,required-verified)}


def create_deposit_checkout(path,qid,lid,actor):
    external_gate();token=os.getenv('SQUARE_ACCESS_TOKEN');loc=os.getenv('SQUARE_LOCATION_ID')
    if not token or not loc:raise HTTPException(503,'Card deposits are not configured. Ask Crown for its approved payment instructions.')
    with transaction(path) as db:
        q=one(db,"SELECT * FROM quotes WHERE id=? AND lead_id=? AND status='Accepted'",(qid,lid))
        if not q:raise HTTPException(409,'Accept the reviewed estimate before paying its deposit.')
        l=one(db,'SELECT * FROM leads WHERE id=?',(lid,))
        if os.getenv('SQUARE_ENV','sandbox')!='sandbox' and (lid.startswith('DEMO-') or l['email'].endswith('.invalid')):raise HTTPException(409,'Fictional clients cannot create production card deposits.')
        if l['version']!=q['lead_version'] or one(db,"SELECT id FROM client_changes WHERE lead_id=? AND status='Pending'",(lid,)):raise HTTPException(409,'Ask Crown to review the latest changes before paying.')
        balance=deposit_state(db,lid,q)
        if balance['pending_cents']:raise HTTPException(409,'A deposit has already been recorded and is awaiting verification. Do not pay again.')
        if not balance['due_cents']:raise HTTPException(409,'No deposit is outstanding.')
        old=one(db,"SELECT * FROM deposit_links WHERE lead_id=? AND status IN ('Creating','Active','Unknown','Cancelling')",(lid,))
        if old:
            if old['status']=='Active' and old['quote_id']==qid and old['amount_cents']==balance['due_cents']:return {'url':old['url'],'already_created':True,'booking_confirmed':False}
            raise HTTPException(409,'An existing deposit checkout needs reconciliation before another payment method is used.')
        key=uid('DEPOSIT');insert(db,'deposit_links',dict(id=key,lead_id=lid,quote_id=qid,amount_cents=balance['due_cents'],created_by=actor,created_at=now()))
    host='https://connect.squareupsandbox.com' if os.getenv('SQUARE_ENV','sandbox')=='sandbox' else 'https://connect.squareup.com'
    try:
        r=requests.post(host+'/v2/online-checkout/payment-links',headers={'Authorization':'Bearer '+token,'Square-Version':os.getenv('SQUARE_API_VERSION','2026-09-16')},json={'idempotency_key':key,'quick_pay':{'name':'Crown Movers deposit','price_money':{'amount':balance['due_cents'],'currency':'CAD'},'location_id':loc},'checkout_options':{'allow_tipping':False}},timeout=30)
        r.raise_for_status();v=r.json()['payment_link']
        with transaction(path) as db:
            db.execute("UPDATE deposit_links SET status='Active',link_id=?,order_id=?,url=? WHERE id=?",(v['id'],v['order_id'],v['url'],key));audit(db,actor,'Deposit checkout prepared; not a booking','deposit_links',key)
        return {'url':v['url'],'booking_confirmed':False}
    except Exception:
        with transaction(path) as db:db.execute("UPDATE deposit_links SET status='Unknown' WHERE id=?",(key,))
        raise HTTPException(502,'Deposit checkout not confirmed. Ask Crown to check before retrying or paying another way.')


def record_deposit_callback(db,p,event_id):
    link=one(db,'SELECT * FROM deposit_links WHERE order_id=?',(p.get('order_id'),))
    if not link:return None
    if p.get('status')!='COMPLETED':return {'applied':False}
    money=p.get('amount_money',{})
    if money.get('currency')!='CAD' or money.get('amount')!=link['amount_cents'] or p.get('location_id')!=os.getenv('SQUARE_LOCATION_ID'):
        add_task(db,link['lead_id'],'URGENT: deposit provider mismatch',key=event_id);return {'applied':False,'reason':'Deposit mismatch'}
    old=one(db,'SELECT id FROM payments WHERE reference=?',('Square:'+p['id'],))
    if old:return {'applied':False,'already_recorded':True}
    pid=uid('PAY');insert(db,'payments',dict(id=pid,lead_id=link['lead_id'],amount_cents=money['amount'],method='Credit card',purpose='Deposit',reference='Square:'+p['id'],verified=1,recorded_by='square-webhook',created_at=now()))
    db.execute("UPDATE deposit_links SET status='Paid' WHERE id=?",(link['id'],))
    q=one(db,"SELECT * FROM quotes WHERE id=? AND lead_id=? AND status='Accepted'",(link['quote_id'],link['lead_id']))
    lead=one(db,'SELECT * FROM leads WHERE id=?',(link['lead_id'],))
    state=deposit_state(db,link['lead_id'],q) if q else {'due_cents':1}
    confirmed=False;job_id=None;confirmation_message_id=None
    if q and lead and state['due_cents']<=0:
        from .sales_crm_v6 import confirm_job
        job=confirm_job(db,q,lead,'square-webhook','Verified card deposit')
        confirmed=True;job_id=job['id'];confirmation_message_id=job.get('confirmation_message_id')
    else:
        add_task(db,link['lead_id'],'Deposit received: verify remaining balance / booking status',key='deposit:'+pid)
    audit(db,'square-webhook','Verified deposit recorded','payments',pid,{'amount_cents':money['amount'],'booking_confirmed':confirmed,'job_id':job_id})
    return {'applied':True,'payment_id':pid,'purpose':'Deposit','booking_confirmed':confirmed,'job_id':job_id,'confirmation_message_id':confirmation_message_id}
