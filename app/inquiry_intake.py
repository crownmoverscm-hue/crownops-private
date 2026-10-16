"""Optional structured call intake. Unknown answers stay unknown."""
from urllib.parse import urlsplit
from fastapi import HTTPException

TEXT_FIELDS={'local_area':100,'service_details':6000,'start_window':30,
             'between_stops_minutes':20,'pickup_base_minutes':20,'return_base_minutes':20}
LIST_FIELDS={'additional_services','heavy_items'}

def photo_link(value):
    if not isinstance(value,str) or len(value)>3000:raise HTTPException(422,'Photo link must be text under 3,000 characters.')
    value=value.strip()
    if value:
        try:
            parsed=urlsplit(value)
            valid=parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password
        except ValueError:valid=False
        if not valid:raise HTTPException(422,'Use a complete HTTPS photo or Drive link.')
    return value

def intake_values(value):
    if not isinstance(value,dict) or set(value)-set(TEXT_FIELDS)-LIST_FIELDS:
        raise HTTPException(422,'Invalid inquiry details.')
    result={}
    for key,limit in TEXT_FIELDS.items():
        text=value.get(key,'')
        if not isinstance(text,str) or len(text)>limit:raise HTTPException(422,'Review '+key.replace('_',' ')+'.')
        text=text.strip()
        if key.endswith('_minutes') and text:
            try:minutes=float(text)
            except ValueError:raise HTTPException(422,'Driving minutes must be a number.')
            if not 0<=minutes<=1440:raise HTTPException(422,'Driving minutes must be between 0 and 1,440.')
        result[key]=text
    if result['local_area'] not in ('','Yes','No','Not sure'):raise HTTPException(422,'Choose whether this is a local Montreal move.')
    if result['start_window'] not in ('','morning','afternoon','unknown'):raise HTTPException(422,'Choose a preferred arrival window.')
    for key in LIST_FIELDS:
        items=value.get(key,[])
        if not isinstance(items,list) or len(items)>20 or any(not isinstance(x,str) or len(x)>150 for x in items):
            raise HTTPException(422,'Review the selected '+key.replace('_',' ')+'.')
        result[key]=list(dict.fromkeys(x.strip() for x in items if x.strip()))
    return result
