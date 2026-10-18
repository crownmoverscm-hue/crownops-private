"""Owner/manager cost review. QuickBooks is read-only; no payments are created."""
from contextlib import closing
from datetime import date
import hashlib
import json
from fastapi import Depends, HTTPException
from .store import connect, transaction, rows, one, insert, uid, now, audit, dumps
from .platform_common import require_role, business_day, quickbooks_environment
from .expenses import ADMINS, CATEGORIES, amount, expense_values
from .worked_hours import date_range, pay_cents
from . import providers


def normalized(kind, raw):
    """Intuit Purchase/Bill totals include tax. Credits are never treated as costs."""
    problems = []
    currency = (raw.get('CurrencyRef') or {}).get('value', '')
    if currency != 'CAD':
        problems.append('Only explicit CAD transactions can be imported.')
    if raw.get('Credit'):
        problems.append('Refund or credit: reconcile this in QuickBooks.')
    try:
        day = date.fromisoformat(raw.get('TxnDate', '')).isoformat()
    except (ValueError, TypeError):
        day = ''; problems.append('The transaction date is missing.')
    total = tax = balance = None
    try:
        total = amount(raw.get('TotalAmt'), 'QuickBooks total')
        tax_value = (raw.get('TxnTaxDetail') or {}).get('TotalTax')
        if tax_value is None and raw.get('GlobalTaxCalculation') == 'NotApplicable':
            tax_value = 0
        tax = amount(tax_value, 'QuickBooks tax')
        balance = amount(raw.get('Balance'), 'Bill balance') if kind == 'Bill' else 0
        if not total or tax > total or balance > total:
            raise ValueError()
    except (HTTPException, ValueError):
        problems.append('Review the amount, tax, balance or voided transaction in QuickBooks.')
    ref = raw.get('VendorRef' if kind == 'Bill' else 'EntityRef') or {}
    vendor = str(ref.get('name') or '').strip()
    if not vendor:
        problems.append('The supplier name is missing in QuickBooks.')
    return dict(date=day, vendor=vendor[:200], currency=currency, total_cents=total, tax_cents=tax,
                subtotal_cents=total-tax if total is not None and tax is not None else None,
                balance_cents=balance, status='Unpaid' if balance else 'Paid',
                reference=str(raw.get('DocNumber') or raw.get('PaymentRefNum') or raw.get('Id') or '')[:200],
                description='; '.join(str(line.get('Description') or '') for line in raw.get('Line', []) if isinstance(line,dict))[:1000],
                problems=problems)


def source_hash(raw):
    return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def stage_records(db, realm, environment, records, actor):
    for kind, raw in records:
        remote_id = str(raw.get('Id') or '')
        if not remote_id or len(remote_id) > 150:
            raise HTTPException(502, 'QuickBooks returned an invalid transaction ID. No records were imported.')
        digest = source_hash(raw)
        data = normalized(kind, raw)
        previous = one(db, 'SELECT * FROM expense_sources WHERE realm_id=? AND environment=? AND kind=? AND external_id=?',
                       (realm, environment, kind, remote_id))
        if previous and previous['source_hash'] == digest:
            db.execute('UPDATE expense_sources SET checked_at=? WHERE id=?', (now(), previous['id']))
            continue
        state = 'Changed' if previous and previous['expense_id'] else 'Unsupported' if data['problems'] else 'Review'
        if previous:
            db.execute('UPDATE expense_sources SET source_hash=?,data_json=?,state=?,checked_at=?,version=version+1 WHERE id=?',
                       (digest, dumps(data), state, now(), previous['id']))
        else:
            insert(db, 'expense_sources', dict(id=uid('QBCOST'), realm_id=realm, environment=environment,
                   kind=kind, external_id=remote_id, source_hash=digest, data_json=dumps(data), state=state,
                   checked_at=now(), created_at=now()))
    audit(db, actor, 'QuickBooks expenses fetched for review; ledger unchanged', 'expense_sources', realm,
          {'environment':environment, 'records':len(records)})


def margin_report(db, start, end):
    start, end = date_range(start, end)
    jobs = rows(db, """SELECT j.id,j.title,j.start_at,j.status FROM jobs j WHERE j.status!='Cancelled' AND
        (j.status IN ('In progress','Completed') OR EXISTS(SELECT 1 FROM expenses e WHERE e.job_id=j.id AND e.status!='Voided')
         OR EXISTS(SELECT 1 FROM job_invoices i JOIN invoice_revisions r ON r.invoice_id=i.id WHERE i.job_id=j.id))
        ORDER BY j.start_at DESC""")
    items = []
    for job in jobs:
        day = business_day(job['start_at'])
        if not start <= day <= end:
            continue
        invoice = one(db, '''SELECT i.id,i.number,i.status,r.document_json,r.revision FROM job_invoices i
            JOIN invoice_revisions r ON r.invoice_id=i.id
            WHERE i.job_id=? ORDER BY r.revision DESC LIMIT 1''', (job['id'],))
        warnings = []
        revenue = None
        if invoice:
            calc = json.loads(invoice['document_json'])['calculation']
            revenue = calc['total_cents'] - sum(t['amount_cents'] for t in calc['taxes'])
            if invoice['status'] != 'Issued':
                warnings.append('Invoice correction is still a draft; using the last issued revision.')
        else:
            warnings.append('Invoice has not been issued.')
        team = rows(db, "SELECT resource_id FROM assignments WHERE job_id=? AND resource_type='employee'", (job['id'],))
        times = rows(db, 'SELECT * FROM times WHERE job_id=?', (job['id'],))
        missing = len({e['resource_id'] for e in team} - {t['employee_id'] for t in times})
        unapproved = sum(t['status'] != 'Approved' or t['approved_hourly_cents'] is None for t in times)
        if not team: warnings.append('No crew is assigned.')
        if missing: warnings.append(f'{missing} crew member(s) have no recorded hours.')
        if unapproved: warnings.append(f'{unapproved} time entry/entries need approval and a pay rate.')
        labour = sum(pay_cents(t['service_minutes']+t['transport_minutes'],t['approved_hourly_cents']) for t in times
                     if t['status']=='Approved' and t['approved_hourly_cents'] is not None)
        costs = one(db, "SELECT count(*) count,coalesce(sum(subtotal_cents),0) subtotal FROM expenses WHERE job_id=? AND status!='Voided'", (job['id'],))
        changed = one(db, "SELECT count(*) count FROM expense_sources s JOIN expenses e ON e.id=s.expense_id WHERE e.job_id=? AND s.state='Changed'", (job['id'],))['count']
        if changed: warnings.append('A linked QuickBooks expense changed; review it.')
        if not costs['count']: warnings.append('No extra expenses recorded; check fuel, materials and subcontractors.')
        ready = revenue is not None and bool(team) and not missing and not unapproved and not changed
        items.append(dict(job_id=job['id'], title=job['title'], date=day, status=job['status'],
            invoice_id=invoice['id'] if invoice else None, invoice_number=invoice['number'] if invoice else '',
            revenue_cents=revenue, approved_labour_cents=labour, expense_cents=costs['subtotal'],
            recorded_margin_cents=revenue-labour-costs['subtotal'] if ready else None, warnings=warnings))
    overhead = one(db, "SELECT count(*) count,coalesce(sum(subtotal_cents),0) cents FROM expenses WHERE job_id IS NULL AND status!='Voided' AND expense_date>=? AND expense_date<=?", (start,end))
    review = one(db, "SELECT count(*) count FROM expense_sources WHERE state IN ('Review','Changed','Unsupported') AND environment='production'")['count']
    return dict(start=start,end=end,items=items,company_costs=overhead,quickbooks_review_count=review,
                note='Recorded job margin before company overhead, employer costs and tax adjustments. Uses issued invoices, approved straight-time labour and linked expenses before tax. This is not net profit.')


def register_cost_tracking(app, path, auth):
    @app.get('/api/costs/margins')
    def margins(start:str='',end:str='',u=Depends(auth)):
        require_role(u,ADMINS)
        with closing(connect(path)) as db:return margin_report(db,start,end)

    @app.get('/api/costs/quickbooks')
    def source_list(offset:int=0,u=Depends(auth)):
        require_role(u,ADMINS)
        if offset<0:raise HTTPException(422,'Choose a valid page.')
        with closing(connect(path)) as db:
            records=rows(db,"SELECT * FROM expense_sources ORDER BY CASE WHEN state IN ('Changed','Review','Unsupported') THEN 0 ELSE 1 END,checked_at DESC,id LIMIT 50 OFFSET ?",(offset,))
            for r in records:
                r['data']=json.loads(r.pop('data_json'))
                r['expense']=one(db,'SELECT id,version,vendor,expense_date,total_cents,status,job_id,category FROM expenses WHERE id=?',(r['expense_id'],)) if r['expense_id'] else None
                r['matches']=rows(db,"""SELECT e.id,e.version,e.vendor,e.expense_date,e.total_cents,e.job_id
                    FROM expenses e WHERE e.total_cents=? AND e.expense_date=? AND e.status!='Voided'
                    AND NOT EXISTS(SELECT 1 FROM expense_sources s WHERE s.expense_id=e.id) ORDER BY e.id LIMIT 20""",
                    (r['data']['total_cents'],r['data']['date'])) if not r['expense_id'] else []
            connected=bool(one(db,"SELECT 1 FROM oauth_credentials WHERE provider='quickbooks'"))
            total=one(db,'SELECT count(*) n FROM expense_sources')['n']
        return dict(items=records,offset=offset,total=total,connected=connected,environment=quickbooks_environment(),categories=CATEGORIES)

    @app.post('/api/costs/quickbooks/fetch')
    def fetch_sources(data:dict,u=Depends(auth)):
        require_role(u,ADMINS)
        start,end=date_range(data.get('start'),data.get('end'))
        _,realm,_=providers._qbo_connection(path)
        environment=quickbooks_environment()
        records=[]
        try:
            # Pull both complete result sets before changing the local review queue.
            for kind in ('Purchase','Bill'):
                for position in range(1,2002,200):
                    result=providers.qbo_query(path,f"SELECT * FROM {kind} WHERE TxnDate >= '{start}' AND TxnDate <= '{end}' STARTPOSITION {position} MAXRESULTS 200")
                    batch=result.get(kind,[])
                    if not isinstance(batch,list) or any(not isinstance(r,dict) for r in batch):raise ValueError()
                    if position>2000 and batch:raise HTTPException(422,'Choose a smaller date range (up to 2,000 expenses and bills each).')
                    records.extend((kind,r) for r in batch)
                    if len(batch)<200:break
        except HTTPException:raise
        except Exception:raise HTTPException(502,'QuickBooks could not return the expense list. Existing records are unchanged; try again shortly.')
        with transaction(path) as db:stage_records(db,realm,environment,records,u['id'])
        return dict(ok=True,records=len(records),environment=environment)

    @app.post('/api/costs/quickbooks/{sid}/review')
    def review_source(sid:str,data:dict,u=Depends(auth)):
        require_role(u,ADMINS)
        action=data.get('action')
        if action not in ('create','match','update','ignore'):raise HTTPException(422,'Choose a review action.')
        with transaction(path) as db:
            source=one(db,'SELECT * FROM expense_sources WHERE id=?',(sid,))
            if not source:raise HTTPException(404,'Expense source not found.')
            if type(data.get('version')) is not int or data['version']!=source['version']:raise HTTPException(409,'The source changed. Refresh the review list.')
            if source['environment']!='production':raise HTTPException(409,'Sandbox records cannot enter your company expense ledger. Connect production QuickBooks first.')
            item=json.loads(source['data_json'])
            if action=='ignore':
                if source['expense_id']:raise HTTPException(409,'This record is already linked. Review the existing expense instead.')
                state='Ignored';expense_id=None
            else:
                if item['problems']:raise HTTPException(409,'Resolve the flagged transaction in QuickBooks before importing.')
                if source['expense_id'] and action!='update':raise HTTPException(409,'This transaction is already linked to an expense.')
                if action=='update' and not source['expense_id']:raise HTTPException(409,'There is no linked expense to update.')
                expense_id=source['expense_id'] or data.get('expense_id') if action in ('match','update') else None
                existing=one(db,'SELECT * FROM expenses WHERE id=?',(expense_id,)) if expense_id else None
                if action in ('match','update'):
                    if not existing or existing['status']=='Voided':raise HTTPException(409,'Choose a non-voided expense.')
                    if type(data.get('expense_version')) is not int or existing['version']!=data['expense_version']:raise HTTPException(409,'The expense changed. Refresh before matching.')
                    if one(db,'SELECT 1 FROM expense_sources WHERE expense_id=? AND id!=?',(expense_id,sid)):raise HTTPException(409,'That expense is already matched to another transaction.')
                    if action=='match' and (existing['total_cents']!=item['total_cents'] or existing['tax_cents']!=item['tax_cents'] or existing['expense_date']!=item['date']):raise HTTPException(409,'The date, amount and tax must match the existing expense.')
                if action in ('create','update'):
                    values=expense_values(db,dict(date=item['date'],vendor=item['vendor'],category=data.get('category','Other'),
                        subtotal=f"{item['subtotal_cents']/100:.2f}",tax=f"{item['tax_cents']/100:.2f}",status=item['status'],
                        reference=item['reference'],description=item['description'],job_id=data.get('job_id') or None,
                        notes=existing['notes'] if existing else '',receipt_url=existing['receipt_url'] if existing else '',
                        vehicle_id=existing['vehicle_id'] if existing else None))
                    if existing:
                        db.execute('UPDATE expenses SET '+','.join(k+'=?' for k in values)+',version=version+1,updated_by=?,updated_at=? WHERE id=?',list(values.values())+[u['id'],now(),expense_id])
                    else:
                        # A likely duplicate must be explicitly cleared or matched first.
                        possible=one(db,"SELECT 1 FROM expenses WHERE expense_date=? AND total_cents=? AND status!='Voided'",(item['date'],item['total_cents']))
                        if possible and data.get('confirmed_distinct') is not True:raise HTTPException(409,'A same-date, same-amount expense already exists. Match it, or confirm this is a different purchase.')
                        expense_id=uid('EXP')
                        insert(db,'expenses',dict(id=expense_id,**values,request_key=sid,request_hash=source['source_hash'],version=1,created_by=u['id'],updated_by=u['id'],created_at=now(),updated_at=now()))
                state='Linked' if action=='match' else 'Imported'
            db.execute('UPDATE expense_sources SET expense_id=?,state=?,version=version+1 WHERE id=?',(expense_id,state,sid))
            audit(db,u['id'],'QuickBooks expense reviewed','expense_sources',sid,{'action':action,'expense_id':expense_id,'source_hash':source['source_hash'],'before':existing if action!='ignore' else None})
        return dict(ok=True,expense_id=expense_id)
