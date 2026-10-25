"""Owner-only, read-only bulk setup previews. Never create links or send mail."""
from contextlib import closing
import hashlib
import re
import csv
import io
from fastapi import Depends, HTTPException
from .store import connect, one, rows, dumps, now
from .platform_common import require_role
from .pricing import number, cents


def batch(data,allowed):
    if set(data)=={'csv'}:
        source=data['csv']
        if not isinstance(source,str) or len(source.encode())>100000:raise HTTPException(422,'Use a CSV under 100 KB.')
        try:
            reader=csv.DictReader(io.StringIO(source.lstrip('\ufeff')),strict=True)
            headers=reader.fieldnames or []
            if not headers or len(headers)!=len(set(headers)) or set(headers)-allowed:
                raise HTTPException(422,'CSV headers must be unique documented field names.')
            records=[]
            for row in reader:
                if None in row or any(value is None for value in row.values()):raise HTTPException(422,'Each CSV row must match its headers.')
                row={key:value.strip() for key,value in row.items()}
                if not any(row.values()):continue
                for key in ('taxable','active'):
                    if key in row and row[key].casefold() in ('true','false'):row[key]=row[key].casefold()=='true'
                if row.get('version','').isdigit():row['version']=int(row['version'])
                records.append(row)
                if len(records)>200:raise HTTPException(422,'Use at most 200 CSV rows per preview.')
            data={'rows':records}
        except csv.Error:raise HTTPException(422,'CSV quoting or formatting is invalid.')
    if set(data)!={'rows'} or not isinstance(data['rows'],list) or not 1<=len(data['rows'])<=200:
        raise HTTPException(422,'Supply 1–200 rows or CSV records for a dry-run preview.')
    if any(not isinstance(row,dict) or set(row)-allowed for row in data['rows']):
        raise HTTPException(422,'Unknown import fields. Use the documented preview format.')
    return data['rows']


def required_text(row,key,limit=160):
    value=row.get(key,'')
    if not isinstance(value,str) or not value.strip() or len(value)>limit:raise ValueError('Enter '+key+' within '+str(limit)+' characters.')
    return value.strip()


def staff_preview(db,data):
    from .main import ROLES
    incoming=batch(data,{'name','email','role','employee_id'})
    items=[];seen=set();seen_employees=set()
    for index,row in enumerate(incoming,1):
        errors=[];normalized={};action='Create invitation link after approval'
        try:
            normalized['name']=required_text(row,'name',150)
            email=required_text(row,'email',254).lower()
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise ValueError('Invalid email.')
            normalized['email']=email
            if email in seen:errors.append('Duplicate email in this batch.')
            seen.add(email)
            role=row.get('role');normalized['role']=role
            if not isinstance(role,str) or role not in ROLES:errors.append('Unknown role; use an existing CrownOps role.')
            account=one(db,'SELECT id,role,active FROM users WHERE email=? COLLATE NOCASE',(email,))
            if account:errors.append('An account already exists; use Team access instead of another invitation.')
            if one(db,'SELECT token_hash FROM invitations WHERE email=? COLLATE NOCASE AND used_at IS NULL AND expires_at>?',(email,now())):
                errors.append('A current invitation already exists; review it instead of duplicating it.')
            eid=row.get('employee_id') or None;normalized['employee_id']=eid
            if eid is not None and (not isinstance(eid,str) or len(eid)>150):errors.append('Invalid employee ID.')
            elif eid:
                if eid in seen_employees:errors.append('Employee ID is repeated in this batch.')
                seen_employees.add(eid)
                employee=one(db,'SELECT id,active FROM employees WHERE id=?',(eid,))
                if not employee or not employee['active']:errors.append('Employee must exist and be active.')
                if one(db,'SELECT id FROM users WHERE employee_id=?',(eid,)):errors.append('This employee is already linked to an account.')
            elif role=='crew':errors.append('Crew requires an explicit active employee mapping.')
            if role=='owner':action='Master-owner approval required before invitation'
        except ValueError as ex:errors.append(str(ex))
        items.append({'row':index,'values':normalized,'action':action,'errors':errors,'valid':not errors})
    return preview_result(items)


def materials_preview(db,data):
    incoming=batch(data,{'code','name','category','sell_price','cost_price','quantity','minimum','location','taxable','active','version'})
    items=[];seen=set()
    for index,row in enumerate(incoming,1):
        errors=[];values={};existing=None
        try:
            code=required_text(row,'code',60).upper().replace(' ','_');values['code']=code
            if not re.fullmatch(r'[A-Z0-9_-]+',code):raise ValueError('Use an ASCII material code with letters, numbers, _ or -.')
            if code in seen:errors.append('Duplicate material code in this batch.')
            seen.add(code)
            existing=one(db,'SELECT * FROM material_catalog WHERE code=?',(code,))
            values.update(name=required_text(row,'name'),category=required_text(row,'category',120))
            for key in ('sell_price','cost_price'):
                if key not in row:raise ValueError('Enter '+key+' explicitly; blank prices are never treated as zero.')
                values[key.replace('_price','_cents')]=cents(number(row[key],key,0,10000))
            for key in ('quantity','minimum'):
                values[key]=float(number(row.get(key,(existing or {}).get(key,0)),key,0,100000))
            values['location']=row.get('location',(existing or {}).get('location','Warehouse'))
            if not isinstance(values['location'],str) or len(values['location'])>160:raise ValueError('Invalid location.')
            for key in ('taxable','active'):
                value=row.get(key)
                if type(value) is not bool:raise ValueError('Choose '+key+' explicitly as true or false.')
                values[key]=value
            if existing and (type(row.get('version')) is not int or row['version']!=existing['version']):
                errors.append('Existing material version differs or is missing; refresh before approving an update.')
        except ValueError as ex:errors.append(str(ex))
        items.append({'row':index,'values':values,'action':'Update existing' if existing else 'Create new',
                      'existing_id':(existing or {}).get('id'),'existing_version':(existing or {}).get('version'),
                      'errors':errors,'valid':not errors})
    return preview_result(items)


def preview_result(items):
    return {'dry_run':True,'items':items,'valid_rows':sum(x['valid'] for x in items),
            'invalid_rows':sum(not x['valid'] for x in items),'writes':0,'messages_sent':0,
            'preview_hash':hashlib.sha256(dumps(items).encode()).hexdigest(),
            'apply_available':False}


def register_launch_previews(app,path,auth):
    @app.post('/api/launch/staff-invitations/preview')
    def staff(data:dict,u=Depends(auth)):
        require_role(u,{'owner'})
        with closing(connect(path)) as db:return staff_preview(db,data)

    @app.post('/api/launch/materials/preview')
    def materials(data:dict,u=Depends(auth)):
        require_role(u,{'owner'})
        with closing(connect(path)) as db:return materials_preview(db,data)
