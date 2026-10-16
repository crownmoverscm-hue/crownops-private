"""Ordered service locations, including compatibility with earlier address lists."""
import json
from fastapi import HTTPException

KINDS={'Pickup','Stop','Delivery','Storage','Return'}

def validate_route(value):
    if not isinstance(value,list) or not 0<=len(value)<=30:raise HTTPException(422,'Use up to 30 route locations.')
    out=[]
    for item in value:
        if not isinstance(item,dict):raise HTTPException(422,'Each route location needs an address.')
        kind=item.get('kind','Stop')
        if kind not in KINDS:raise HTTPException(422,'Choose a valid stop type.')
        row={'kind':kind}
        for key,limit in [('address',1000),('access',1000),('notes',2000)]:
            v=item.get(key,'')
            if not isinstance(v,str) or len(v)>limit:raise HTTPException(422,'Review the route '+key+'.')
            row[key]=v.strip()
        if not row['address']:raise HTTPException(422,'Enter an address for every route location.')
        out.append(row)
    return out

def job_route(job,lead):
    explicit=json.loads(job.get('route_stops_json') or '[]')
    if explicit:return explicit
    stops=json.loads(job.get('stops_json') or '[]')
    pickup=(lead.get('pickup') or '').strip();dropoff=(lead.get('dropoff') or '').strip()
    addresses=[str(x).strip() for x in stops if str(x).strip()]
    if pickup and (not addresses or addresses[0].casefold()!=pickup.casefold()):addresses.insert(0,pickup)
    if dropoff and (not addresses or addresses[-1].casefold()!=dropoff.casefold()):addresses.append(dropoff)
    out=[]
    for i,address in enumerate(addresses):
        first=i==0 and pickup and address.casefold()==pickup.casefold()
        last=i==len(addresses)-1 and dropoff and address.casefold()==dropoff.casefold()
        out.append({'kind':'Pickup' if first else 'Delivery' if last else 'Stop','address':address,
            'access':'; '.join(filter(None,[lead.get('pickup_access'),lead.get('pickup_walk')])) if first else '; '.join(filter(None,[lead.get('dropoff_access'),lead.get('dropoff_walk')])) if last else '', 'notes':''})
    return out
