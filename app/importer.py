"""Read CrownOps XLSX into a separate pilot database; never modify the workbook.

Derived tabs (My Leads, Payroll, Dashboard, Queue views) are NOT imported as
transactions. All meaningful source rows remain available in the private DB.
Existing records are not overwritten on repeated imports.
"""
from datetime import date, datetime, time
from decimal import Decimal
from io import BytesIO
import hashlib
import json
import re
import zipfile
from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel
from .store import transaction, insert, one, uid, now, dumps, audit
from .pricing import cents

ERRORS = {'#REF!', '#VALUE!', '#DIV/0!', '#N/A', '#NAME?', '#ERROR!', '#NUM!'}
DATE_FIELDS = {'Date Received','Move Date','Due Date','Created Date','Call Date','Job Date','Imported At'}


def cleaned(v, field='', epoch=None):
    if v is None:
        return ''
    if isinstance(v, str) and (v in ERRORS or v.startswith('=')):
        return ''
    if isinstance(v, (datetime,date,time)):
        return v.isoformat()
    if field in DATE_FIELDS and isinstance(v,(float,int)) and not isinstance(v,bool) and 20000 < v < 90000:
        return from_excel(v,epoch=epoch).isoformat()
    if isinstance(v,float) and v.is_integer():
        return int(v)
    return v


def records(sheet, header_row, epoch):
    headers = [str(c.value or 'Extra column '+str(c.column)) for c in sheet[header_row]]
    for i,row in enumerate(sheet.iter_rows(min_row=header_row+1, values_only=True),header_row+1):
        if not any(v not in (None,'',False,0) for v in row):
            continue
        raw = {k:(v.isoformat() if isinstance(v,(date,datetime,time)) else v) for k,v in zip(headers,row)}
        data = {k:cleaned(v,k,epoch) for k,v in zip(headers,row)}
        yield i, data, raw


def text(value):
    if value is None:
        return ''
    return str(value).strip()


def extract_pricing(w):
    rates=[]; settings={}
    for _,r,_ in records(w['Quote Setup'],4,w.epoch):
        if r.get('Season / Date Range') and r.get('Start Month'):
            rates.append({k:r[k] for k in ['Season / Date Range','Start Month','Start Day','End Month','End Day','2 movers','3 movers','4 movers','5 movers','6 movers','Notes']})
        if r.get('n8n Field'):
            settings[r['n8n Field']]=r.get('Value')
    return rates, settings


def import_workbook(path, content, filename, actor='importer'):
    if len(content)>12_000_000:
        raise ValueError('Workbook exceeds the 12 MB pilot limit.')
    try:
        with zipfile.ZipFile(BytesIO(content)) as z:
            if sum(i.file_size for i in z.infolist())>70_000_000 or len(z.infolist())>1500:
                raise ValueError('Workbook expands beyond the safe pilot import limit.')
    except zipfile.BadZipFile:
        raise ValueError('Please upload a valid .xlsx workbook.')
    digest=hashlib.sha256(content).hexdigest()
    w=load_workbook(BytesIO(content),data_only=True)
    required={'Leads','Calls','Follow-Ups','Employees','Inventory','Quote Setup','Schedule'}
    if not required.issubset(set(w.sheetnames)):
        raise ValueError('This importer requires the CrownOps v8 workbook tabs.')
    rates,settings=extract_pricing(w)
    stamp=now(); iid=uid('IMPORT')
    report={'filename':filename,'sha256':digest,'sheets':len(w.sheetnames),'counts':{},'quarantined':[],
            'warnings':['Historical inquiries and follow-ups are imported with notifications suppressed.',
             'Source pricing is preserved but requires approval before formal quotes.',
             'No current live schedule or external account has been synchronized.',
             'Payroll is not imported from formula output. Completed rows missing Job ID are quarantined.',
             'Route matrix and exact postal overrides must be supplied separately.'],
            'duplicate_records_skipped':0,'source_error_cells':0}
    header_map={'Leads':1,'Calls':1,'Follow-Ups':1,'Employees':4,'Inventory':4,'Schedule':4,
                'Automation Control':4,'Form Registry':4,'Local Routing Zones':4,'Cash Payments':5,
                'Quick Add Client':4,'Import Audit':4,'Schedule Queue':4}
    with transaction(path) as db:
        prior=one(db,'SELECT report_json FROM imports WHERE sha256=?',(digest,))
        if prior:
            out=json.loads(prior['report_json']);out['already_imported']=True
            return out
        insert(db,'imports',dict(id=iid,sha256=digest,filename=filename,created_at=stamp,report_json='{}'))
        for sn,hr in header_map.items():
            if sn not in w.sheetnames:
                continue
            count=0
            for rownum,r,raw in records(w[sn],hr,w.epoch):
                report['source_error_cells']+=sum(1 for v in raw.values() if isinstance(v,str) and v in ERRORS)
                disposition='Reference only'
                target=None;record=None
                if sn=='Leads' and text(r.get('Lead ID')).startswith('CM-'):
                    legacy=text(r['Lead ID']);lid=legacy
                    names={'Client Name':'client_name','Email':'email','Phone':'phone','Move Date':'move_date',
                      'Pickup Address':'pickup','Drop-off Address':'dropoff','Home Size':'home_size','Boxes':'boxes',
                      'Pickup Access':'pickup_access','Drop-off Access':'dropoff_access','Pickup Walk':'pickup_walk',
                      'Drop-off Walk':'dropoff_walk','Additional Details':'inventory','Heavy Items':'heavy_items',
                      'Photo / Video Link':'photo_link','Language':'language','Source':'source','Lead Type':'lead_type',
                      'Sales Stage':'stage','Assigned To':'assigned_to','Priority':'priority','Notes':'notes'}
                    record={v:text(r.get(k,'')) for k,v in names.items()}
                    record['client_name']=record['client_name'] or 'Unknown Caller'
                    record['stage']=record['stage'] or 'New Lead'
                    record['move_date']=record['move_date'][:10]
                    record.update(id=lid,external_id=legacy,archived_import=1,notifications_suppressed=1,
                                  raw_json=dumps(raw),created_at=text(r.get('Date Received')) or stamp,updated_at=stamp)
                    target='leads'
                elif sn=='Calls' and text(r.get('Call ID')).startswith('CALL-'):
                    legacy=text(r['Call ID']);link=text(r.get('Matched Lead ID'))
                    record=dict(id=legacy,legacy_id=legacy,lead_id=link if one(db,'SELECT id FROM leads WHERE id=?',(link,)) else None,
                                payload_json=dumps(raw),created_at=text(r.get('Call Date')) or stamp)
                    target='calls'
                elif sn=='Follow-Ups' and text(r.get('Follow-Up ID')).startswith('FU-'):
                    legacy=text(r['Follow-Up ID']);link=text(r.get('Lead ID'))
                    status='Done' if r.get('Done') is True else text(r.get('Status')) or 'Open'
                    record=dict(id=legacy,legacy_id=legacy,lead_id=link if one(db,'SELECT id FROM leads WHERE id=?',(link,)) else None,
                                title=text(r.get('Follow-Up Type')) or 'Review inquiry',due_date=text(r.get('Due Date'))[:10],
                                owner=text(r.get('Assigned To')),status=status,historical=1,raw_json=dumps(raw),
                                created_at=text(r.get('Created Date')) or stamp)
                    target='tasks'
                elif sn=='Employees' and text(r.get('Employee ID')).startswith('EMP-'):
                    legacy=text(r['Employee ID'])
                    record=dict(id=legacy,external_id=legacy,name=text(r['Employee Name']),role=text(r.get('Role')),
                                active=int(str(r.get('Active?')).lower()=='yes'),driver_verified=0,
                                hourly_cents=cents(r.get('Hourly Rate') or 0),raw_json=dumps(raw))
                    target='employees'
                elif sn=='Inventory' and text(r.get('Item ID')).startswith(('INV-','EQ-')):
                    legacy=text(r['Item ID'])
                    if r.get('Category')=='Truck / Vehicle':
                        target='vehicles'
                        record=dict(id=legacy,name=text(r['Item Name']),active=0,
                                    notes='Imported reference. Verify actual truck and availability before dispatch. '+text(r.get('Notes')))
                    else:
                        target='inventory'
                        record=dict(id=legacy,external_id=legacy,name=text(r['Item Name']),category=text(r.get('Category')),
                           quantity=float(r.get('Current Qty') or 0),minimum=float(r.get('Minimum Qty') or 0),
                           sell_cents=cents(r.get('Selling Price') or 0),cost_cents=cents(r.get('Unit Cost') or 0),
                           location=text(r.get('Location')),raw_json=dumps(raw))
                elif sn=='Schedule':
                    substantive=r.get('Job ID') or r.get('Job Status') or r.get('Client Name') or r.get('Actual Hours Entry')
                    if substantive:
                        disposition='Quarantined'
                        report['quarantined'].append({'sheet':sn,'row':rownum,
                            'reason':'Requires live schedule reconciliation; no automatic production job or payroll import.'})
                    else:
                        continue
                if target and record:
                    if one(db,f'SELECT id FROM {target} WHERE id=?',(record['id'],)):
                        report['duplicate_records_skipped']+=1;disposition='Existing record preserved'
                    else:
                        insert(db,target,record);count+=1;disposition='Imported into '+target
                insert(db,'source_rows',dict(import_id=iid,sheet=sn,row_number=rownum,payload_json=dumps(raw),disposition=disposition))
            report['counts'][sn]=count
        pid=uid('RATE')
        insert(db,'pricing_versions',dict(id=pid,label='Workbook snapshot '+stamp[:10],rates_json=dumps(rates),
                settings_json=dumps(settings),approved=0,created_at=stamp))
        report['pricing_id']=pid
        report['counts']['Confirmed jobs imported']=0
        report['counts']['Payroll transactions imported']=0
        report['counts']['Seasonal rate rows']=len(rates)
        db.execute('UPDATE imports SET report_json=? WHERE id=?',(dumps(report),iid))
        audit(db,actor,'Workbook imported','imports',iid,report)
    w.close()
    return report
