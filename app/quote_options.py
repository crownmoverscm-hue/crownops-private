"""Editable quote metadata; saved with each revision rather than changing past documents."""
from datetime import date,datetime
from zoneinfo import ZoneInfo
import re
import json
from fastapi import HTTPException
from .platform_common import text

def expired_quote(quote):
    expiry=json.loads(quote['inputs_json']).get('expiry_date','')
    if not expiry:return False
    try:return date.fromisoformat(expiry)<datetime.now(ZoneInfo('America/Toronto')).date()
    except (ValueError,TypeError):return True

def document_options(data,lead):
    billing=data.get('billing') or {}
    if not isinstance(billing,dict) or set(billing)-{'name','company','email','phone','address'}:raise HTTPException(422,'Review the billing information.')
    defaults={'name':lead.get('client_name',''),'company':'','email':lead.get('email',''),'phone':lead.get('phone',''),'address':''}
    billing={k:text(billing.get(k,v),'Billing '+k,1500 if k=='address' else 254) for k,v in defaults.items()}
    if billing['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',billing['email']):raise HTTPException(422,'Enter a valid billing email.')
    expiry=text(data.get('expiry_date',''),'Expiration date',10)
    if expiry:
        try:
            if date.fromisoformat(expiry).isoformat()!=expiry:raise ValueError()
        except ValueError:raise HTTPException(422,'Choose a valid quote expiration date.')
    return {'billing':billing,'expiry_date':expiry,'payment_terms':text(data.get('payment_terms',''),'Payment terms',1000),
            'service_line_name':text(data.get('service_line_name',''),'Service description',160),
            'service_line_note':text(data.get('service_line_note',''),'Service note',2000)}
