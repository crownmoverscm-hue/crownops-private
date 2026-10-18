"""Private company expense ledger. Recording an expense never makes a payment."""
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
import csv
import hashlib
import re
import io

from fastapi import Depends, HTTPException
from fastapi.responses import Response
from .store import connect, transaction, rows, one, insert, uid, now, audit, dumps
from .platform_common import require_role, text

ADMINS = {'owner', 'manager'}
CATEGORIES = ('Fuel', 'Truck maintenance', 'Equipment', 'Materials', 'Rent', 'Insurance',
              'Software', 'Marketing', 'Travel', 'Subcontractor', 'Other')


def amount(value, label):
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > 10000000 or number != number.quantize(Decimal('.01')):
            raise ValueError()
        return int(number * 100)
    except (ValueError, InvalidOperation, TypeError):
        raise HTTPException(422, label + ' must be a non-negative CAD amount with at most two decimals.')


def expense_values(db, data):
    allowed = {'date', 'vendor', 'category', 'description', 'subtotal', 'tax', 'status',
               'receipt_url', 'reference', 'job_id', 'vehicle_id', 'notes', 'version', 'request_key'}
    if set(data) - allowed:
        raise HTTPException(422, 'Unknown expense field.')
    try:
        day = date.fromisoformat(str(data.get('date', ''))).isoformat()
    except ValueError:
        raise HTTPException(422, 'Choose the expense date.')
    vendor = text(data.get('vendor', ''), 'Vendor', 200)
    if not vendor:
        raise HTTPException(422, 'Enter the vendor.')
    category = data.get('category', 'Other')
    if category not in CATEGORIES:
        raise HTTPException(422, 'Choose an expense category.')
    status = data.get('status', 'Paid')
    if status not in ('Paid', 'Unpaid'):
        raise HTTPException(422, 'Choose Paid or Unpaid.')
    subtotal, tax = amount(data.get('subtotal', 0), 'Subtotal'), amount(data.get('tax', 0), 'Tax')
    if not subtotal + tax:
        raise HTTPException(422, 'Enter an expense greater than zero.')
    receipt = text(data.get('receipt_url', ''), 'Receipt link', 3000)
    if receipt:
        try:
            parsed = urlsplit(receipt)
            valid = parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
        except ValueError:
            valid = False
        if not valid:
            raise HTTPException(422, 'Use a complete HTTPS receipt link.')
    links = {}
    for field, table in (('job_id', 'jobs'), ('vehicle_id', 'vehicles')):
        value = text(data.get(field) or '', field, 150) or None
        if value and not one(db, 'SELECT id FROM ' + table + ' WHERE id=?', (value,)):
            raise HTTPException(422, 'Choose an existing ' + ('move' if field == 'job_id' else 'truck') + '.')
        links[field] = value
    return dict(expense_date=day, vendor=vendor, category=category, status=status,
                description=text(data.get('description', ''), 'Description', 1000),
                notes=text(data.get('notes', ''), 'Notes', 6000), reference=text(data.get('reference', ''), 'Reference', 200),
                subtotal_cents=subtotal, tax_cents=tax, total_cents=subtotal + tax, receipt_url=receipt, **links)


def expense_filter(start, end, category, status):
    clauses, params = [], []
    for value, field, op in ((start, 'expense_date', '>='), (end, 'expense_date', '<=')):
        if value:
            try:
                value = date.fromisoformat(value).isoformat()
            except ValueError:
                raise HTTPException(422, 'Use valid expense filter dates.')
            clauses.append('e.' + field + op + '?'); params.append(value)
    if start and end and start > end:
        raise HTTPException(422, 'End date must follow the start date.')
    if category:
        if category not in CATEGORIES:
            raise HTTPException(422, 'Choose an expense category.')
        clauses.append('e.category=?'); params.append(category)
    if status:
        if status not in ('Paid', 'Unpaid', 'Voided'):
            raise HTTPException(422, 'Choose an expense status.')
        clauses.append('e.status=?'); params.append(status)
    return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), params


def register_expenses(app, path, auth):
    @app.get('/api/expenses')
    def listing(start: str = '', end: str = '', category: str = '', status: str = '', offset: int = 0, limit: int = 50, u=Depends(auth)):
        require_role(u, ADMINS)
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(422, 'Choose a valid expense page.')
        clause, params = expense_filter(start, end, category, status)
        with closing(connect(path)) as db:
            summary = one(db, """SELECT count(*) total,
                coalesce(sum(CASE WHEN e.status!='Voided' THEN e.total_cents ELSE 0 END),0) total_cents,
                coalesce(sum(CASE WHEN e.status='Unpaid' THEN e.total_cents ELSE 0 END),0) unpaid_cents
                FROM expenses e""" + clause, params)
            items = rows(db, """SELECT e.*,u.name entered_by_name,j.title job_title,v.name vehicle_name
                FROM expenses e LEFT JOIN users u ON u.id=e.created_by
                LEFT JOIN jobs j ON j.id=e.job_id LEFT JOIN vehicles v ON v.id=e.vehicle_id""" + clause +
                ' ORDER BY e.expense_date DESC,e.created_at DESC,e.id LIMIT ? OFFSET ?', params + [limit, offset])
        return {**summary, 'items': items, 'categories': CATEGORIES, 'offset': offset, 'limit': limit, 'currency': 'CAD'}

    @app.get('/api/expenses/export.csv')
    def export(start: str = '', end: str = '', category: str = '', status: str = '', u=Depends(auth)):
        require_role(u, ADMINS)
        clause, params = expense_filter(start, end, category, status)
        with closing(connect(path)) as db:
            records = rows(db, 'SELECT e.* FROM expenses e' + clause + ' ORDER BY e.expense_date,e.id', params)
        def safe(value):
            value = str(value or '')
            return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
        output = io.StringIO(); writer = csv.writer(output)
        writer.writerow(['ID', 'Date', 'Vendor', 'Category', 'Description', 'Subtotal CAD', 'Tax CAD', 'Total CAD', 'Status', 'Reference', 'Move', 'Truck', 'Receipt link'])
        for e in records:
            writer.writerow([safe(e[k]) for k in ('id', 'expense_date', 'vendor', 'category', 'description')] +
                [f'{e[k]/100:.2f}' for k in ('subtotal_cents', 'tax_cents', 'total_cents')] +
                [safe(e[k]) for k in ('status', 'reference', 'job_id', 'vehicle_id', 'receipt_url')])
        return Response(output.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="crownops-expenses.csv"', 'Cache-Control': 'no-store'})

    @app.get('/api/expenses/{eid}')
    def detail(eid: str, u=Depends(auth)):
        require_role(u, ADMINS)
        with closing(connect(path)) as db:
            record = one(db, 'SELECT * FROM expenses WHERE id=?', (eid,))
            if not record:
                raise HTTPException(404, 'Expense not found.')
        return record

    @app.post('/api/expenses')
    def create(data: dict, u=Depends(auth)):
        require_role(u, ADMINS)
        with transaction(path) as db:
            values = expense_values(db, data)
            key = data.get('request_key')
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9-]{16,100}', key):
                raise HTTPException(422, 'Refresh the expense form before saving.')
            digest = hashlib.sha256(dumps(values).encode()).hexdigest()
            previous = one(db, 'SELECT * FROM expenses WHERE created_by=? AND request_key=?', (u['id'], key))
            if previous:
                if previous['request_hash'] != digest:
                    raise HTTPException(409, 'This save request already belongs to a different expense.')
                return previous
            record = dict(request_key=key, request_hash=digest, id=uid('EXP'), **values, version=1, created_by=u['id'], updated_by=u['id'], created_at=now(), updated_at=now())
            insert(db, 'expenses', record)
            audit(db, u['id'], 'Expense recorded', 'expenses', record['id'], {'after': values})
        return record

    @app.put('/api/expenses/{eid}')
    def update(eid: str, data: dict, u=Depends(auth)):
        require_role(u, ADMINS)
        with transaction(path) as db:
            record = one(db, 'SELECT * FROM expenses WHERE id=?', (eid,))
            if not record:
                raise HTTPException(404, 'Expense not found.')
            if type(data.get('version')) is not int or data['version'] != record['version']:
                raise HTTPException(409, 'This expense changed. Reload it before saving.')
            if record['status'] == 'Voided':
                raise HTTPException(409, 'Voided expenses retain their history. Create a new expense instead.')
            values = expense_values(db, data)
            db.execute('UPDATE expenses SET ' + ','.join(k + '=?' for k in values) + ',version=version+1,updated_by=?,updated_at=? WHERE id=?', list(values.values()) + [u['id'], now(), eid])
            audit(db, u['id'], 'Expense updated', 'expenses', eid, {'before': record, 'after': values})
        return {'ok': True, 'id': eid, 'version': record['version'] + 1}

    @app.post('/api/expenses/{eid}/void')
    def void(eid: str, data: dict, u=Depends(auth)):
        require_role(u, ADMINS)
        reason = text(data.get('reason', ''), 'Reason', 2000)
        if not reason:
            raise HTTPException(422, 'Enter a reason for voiding this expense.')
        with transaction(path) as db:
            record = one(db, 'SELECT * FROM expenses WHERE id=?', (eid,))
            if not record:
                raise HTTPException(404, 'Expense not found.')
            if type(data.get('version')) is not int or data['version'] != record['version']:
                raise HTTPException(409, 'This expense changed. Reload it before voiding.')
            if record['status'] == 'Voided':
                raise HTTPException(409, 'This expense is already voided.')
            db.execute("UPDATE expenses SET status='Voided',void_reason=?,version=version+1,updated_by=?,updated_at=? WHERE id=?", (reason, u['id'], now(), eid))
            audit(db, u['id'], 'Expense voided', 'expenses', eid, {'reason': reason, 'total_cents': record['total_cents']})
        return {'ok': True}
