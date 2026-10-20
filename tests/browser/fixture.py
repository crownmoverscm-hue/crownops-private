"""Read-only workspace QA with deliberately fictional service and client records."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
os.environ['CROWNOPS_PUBLIC_URL'] = 'http://127.0.0.1:8095'
from test_job_handoff import JobHandoffTests
from app.store import dumps, insert, now, transaction
from app.platform_common import add_task
from app.invoices import validate_terms
import uvicorn

fixture = JobHandoffTests('test_assigned_brief_has_actual_people_leads_truck_route_and_materials_without_pay')
fixture.setUp()
with transaction(fixture.path) as db:
    for index, name in enumerate(['Fictional Samira Lévesque', 'Fictional Alexander Long Client Name', 'Fictional Chloé Test']):
        lid = 'FINISH-L' + str(index)
        insert(db, 'leads', dict(id=lid, client_name=name, phone='514555015' + str(index), email='workspace-' + str(index) + '@example.invalid',
            pickup='100 Fictional Avenue, apartment 4 — parking and elevator to confirm', dropoff='200 Fictional Destination, Montréal',
            move_date='2026-11-15', home_size='2 bedroom apartment', stage='Waiting on client', source='Quote Form', lead_type='Moving',
            assigned_to='sales', created_at=now(), updated_at=now()))
        add_task(db, lid, 'Confirm elevator reservation and client photos', 'sales', 'finish-task-' + str(index))
    terms = validate_terms({'issuer': {'name': 'Fictional Crown Movers', 'address': 'Test company address'},
        'customer': {'name': 'Fictional service client', 'email': 'handoff-client@example.invalid'},
        'hourly_rate': 150, 'minimum_hours': 3, 'transport_amount': 35, 'fuel_amount': 0, 'discount_amount': 0,
        'taxes': [], 'catalogue': [], 'approval_reference': 'Fictional workspace visual test'})
    insert(db, 'job_invoices', dict(id='FINISH-INV', job_id='J1', number='TEST-WORKSPACE', status='Draft', terms_json=dumps(terms), actual_json='{}',
        terms_approved_by='manager', terms_approved_at=now(), created_by='manager', created_at=now(), updated_at=now()))
with transaction(fixture.path) as db:
    insert(db,'calls',dict(id='INBOX-UNMATCHED',lead_id=None,payload_json=dumps({'phone':'5145550198','disposition':'Missed','note':'Fictional unmatched call for Inbox QA'}),created_at=now()))
    insert(db,'integration_events',dict(id='LAYOUT-BAD-QUOTE',event_key='gmail:fictional-invalid',event_type='gmail_quote',object_id='fictional-invalid',payload_json=dumps({'parsed':{'client_name':'Fictional invalid form','email':'invalid-layout@example.invalid'},'origin_label':'Quote Form','body':'Fictional original information retained for office review.','validation_review':{'message':'Review inventory.','original_preserved':True}}),status='Needs review',created_at=now()))
if os.getenv('CROWNOPS_BROWSER_HOURS_COSTS')=='1':
    from app.cost_tracking import stage_records
    with transaction(fixture.path) as db:
        db.execute("UPDATE jobs SET status='Completed' WHERE id='J1'")
        stage_records(db,'fictional-company','production',[('Purchase',dict(Id='fictional-1',TxnDate='2026-11-15',CurrencyRef={'value':'CAD'},TotalAmt=114.98,TxnTaxDetail={'TotalTax':14.98},EntityRef={'name':'Fictional fuel supplier'},Line=[{'Description':'Fuel for the fictional service'}]))],'owner')
if os.getenv('CROWNOPS_BROWSER_INBOX')=='1':
    with transaction(fixture.path) as db:
        insert(db,'calls',dict(id='INBOX-ANSWERED',lead_id='FINISH-L2',payload_json=dumps({'phone':'5145550152','disposition':'Answered','note':'Fictional answered call with no callback promised.'}),created_at='2020-10-20T13:30:00+00:00'))
        insert(db,'tasks',dict(id='INBOX-ANSWERED-TASK',lead_id='FINISH-L2',title='Return / review client call',due_date='2020-10-20',owner='',raw_json=dumps({'call_id':'INBOX-ANSWERED'}),created_at=now()))
        insert(db,'calls',dict(id='INBOX-VOICEMAIL',lead_id='FINISH-L1',payload_json=dumps({'phone':'5145550151','disposition':'Voicemail','note':'Fictional voicemail: moving date already on service.'}),created_at='2020-10-21T13:30:00+00:00'))
        insert(db,'messages',dict(id='INBOX-EMAIL',lead_id='FINISH-L0',kind='Incoming email',subject='Packing supplies question',body='Fictional packing question',author='test',created_at=now()))
if os.getenv('CROWNOPS_BROWSER_WIX')=='1':
    import hashlib
    os.environ['CROWNOPS_WIX_KEY_SHA256']=hashlib.sha256(b'fictional-browser-wix-key-only-123456').hexdigest()
uvicorn.run(fixture.app, host='127.0.0.1', port=8095, log_level='warning')
