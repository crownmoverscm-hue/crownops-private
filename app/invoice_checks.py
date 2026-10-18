"""Invoice review and verified deposit allocation inside the caller's transaction."""
import hashlib
import json
from .store import one, rows, insert, uid, now, dumps, audit


def allocated_credits(db, iid):
    return db.execute('''SELECT coalesce(sum(a.amount_cents),0) FROM invoice_payment_allocations a
        JOIN payments p ON p.id=a.payment_id WHERE a.invoice_id=? AND p.verified=1
        AND NOT EXISTS(SELECT 1 FROM payments rev WHERE rev.reverses_id=p.id)''', (iid,)).fetchone()[0]


def actuals_need_review(db, inv):
    actual=json.loads(inv['actual_json'])
    if actual.get('service_hours') is None:return True
    prepared=one(db,"SELECT id FROM audit WHERE object_type='job_invoices' AND object_id=? AND action='Invoice prepared from accepted quote' ORDER BY id DESC LIMIT 1",(inv['id'],))
    # Earlier releases prefilled the estimate as actual hours. An explicit actuals
    # save must exist after preparation before those invoices can be finalized.
    return bool(prepared and not one(db,"SELECT id FROM audit WHERE object_type='job_invoices' AND object_id=? AND action='Invoice actuals updated' AND id>?",(inv['id'],prepared['id'])))


def deposit_review_state(db, inv, job):
    deposits=rows(db,'''SELECT p.id,p.amount_cents,
        coalesce((SELECT sum(a.amount_cents) FROM invoice_payment_allocations a WHERE a.payment_id=p.id),0) AS used_cents
        FROM payments p WHERE p.lead_id=? AND p.purpose='Deposit' AND p.verified=1
        AND p.amount_cents>0 AND p.reverses_id IS NULL
        AND NOT EXISTS(SELECT 1 FROM payments rev WHERE rev.reverses_id=p.id) ORDER BY p.id''',(job['lead_id'],))
    other_jobs=rows(db,"SELECT id,status FROM jobs WHERE lead_id=? AND id<>? AND status<>'Cancelled' ORDER BY id",(job['lead_id'],job['id']))
    allocations=rows(db,'''SELECT a.invoice_id,a.payment_id,a.amount_cents FROM invoice_payment_allocations a
        JOIN payments p ON p.id=a.payment_id WHERE p.lead_id=? AND p.purpose='Deposit' ORDER BY a.id''',(job['lead_id'],))
    fingerprint=hashlib.sha256(dumps({'deposits':deposits,'jobs':other_jobs,'allocations':allocations,'invoice_revision':inv['revision']}).encode()).hexdigest()
    remaining=sum(max(0,p['amount_cents']-p['used_cents']) for p in deposits)
    reviewed=one(db,'SELECT * FROM invoice_deposit_reviews WHERE invoice_id=? AND fingerprint=?',(inv['id'],fingerprint))
    return {'unallocated_cents':remaining,'ambiguous':bool(other_jobs),'review_required':bool(remaining and not reviewed),
        'reviewed':bool(reviewed),'review_note':reviewed['reason'] if reviewed else '',
        'fingerprint':fingerprint,'deposits':deposits}


def apply_unambiguous_deposits(db, inv, job, actor):
    """Never guess between service phases or spend the same verified deposit twice."""
    from .invoices import calculate_invoice
    state=deposit_review_state(db,inv,job)
    if state['ambiguous'] or not state['unallocated_cents']:return 0
    total=calculate_invoice(json.loads(inv['terms_json']),json.loads(inv['actual_json']),allocated_credits(db,inv['id']))
    room=max(0,total['balance_cents']);applied=0
    for p in state['deposits']:
        amount=min(room,max(0,p['amount_cents']-p['used_cents']))
        if not amount:continue
        insert(db,'invoice_payment_allocations',dict(id=uid('ALLOC'),invoice_id=inv['id'],payment_id=p['id'],amount_cents=amount,allocated_by=actor,created_at=now()))
        audit(db,actor,'Verified booking deposit applied to final invoice','job_invoices',inv['id'],{'payment_id':p['id'],'amount_cents':amount})
        applied+=amount;room-=amount
    return applied
