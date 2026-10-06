"""Launch-ready sales CRM and quote workflow for Crown Movers."""
from contextlib import closing
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal, ROUND_HALF_UP
from math import ceil
from zoneinfo import ZoneInfo
import json, re, secrets

from fastapi import Depends, HTTPException

from .store import connect, transaction, rows, one, insert, audit, uid, now, dumps
from .platform_common import SALES, OFFICE, require_role, settings, put_setting, queue_message, add_task
from .pricing import seasonal_rate, number

MONTREAL=ZoneInfo("America/Toronto")
LOCAL_FUEL=Decimal("35.00")
EXTRA_TRUCK=Decimal("25.00")
LONG_KM=Decimal("0.94")
LONG_DISTANCE_KM_BUFFER=Decimal("1.05")
LONG_TRANSPORT_MULTIPLIER=Decimal("1.15")
HOS_MAX_DRIVING_HOURS=Decimal("13")
HOS_MAX_ON_DUTY_HOURS=Decimal("14")
HOS_MAX_ELAPSED_HOURS=Decimal("16")
HOS_DAILY_OFF_DUTY_HOURS=Decimal("10")
LONG_DISTANCE_CITY_PRESETS=[
 ("Granby","QC",90),("Sherbrooke","QC",155),("Drummondville","QC",110),("Trois-Rivieres","QC",140),
 ("Victoriaville","QC",165),("Quebec City","QC",255),("Gatineau","QC",205),("Saguenay","QC",460),
 ("Rimouski","QC",545),("Rouyn-Noranda","QC",630),("Val-d'Or","QC",525),("Baie-Comeau","QC",680),
 ("Cornwall","ON",115),("Brockville","ON",215),("Kingston","ON",290),("Ottawa","ON",200),
 ("Belleville","ON",370),("Peterborough","ON",460),("Oshawa","ON",480),("Toronto","ON",541),
 ("Mississauga","ON",560),("Brampton","ON",560),("Hamilton","ON",610),("St. Catharines","ON",660),
 ("Niagara Falls","ON",670),("Kitchener","ON",645),("Waterloo","ON",650),("Guelph","ON",620),
 ("London","ON",720),("Windsor","ON",910),("Barrie","ON",620),("North Bay","ON",570),
 ("Sudbury","ON",690),("Sault Ste. Marie","ON",1000),("Thunder Bay","ON",1390),
 ("Moncton","NB",1000),("Fredericton","NB",840),("Saint John","NB",890),("Halifax","NS",1250),
 ("Sydney","NS",1460),("Charlottetown","PE",1140),("Winnipeg","MB",2300),("Regina","SK",2950),
 ("Saskatoon","SK",3050),("Calgary","AB",3600),("Edmonton","AB",3800),("Lethbridge","AB",3500),
 ("Kelowna","BC",4300),("Vancouver","BC",4550),("Prince George","BC",4400),
]
JUNK={
 "single":("Single Item Disposal",Decimal("175.00")),
 "quarter":("1/4 Truck - approx. 5 ft",Decimal("400.00")),
 "half":("1/2 Truck - approx. 10 ft",Decimal("800.00")),
 "three_quarter":("3/4 Truck - approx. 15 ft",Decimal("1200.00")),
 "full":("Full Truck - 20 ft container",Decimal("1600.00")),
}
JUNK_LABOUR=Decimal("80.00")

def money(v):
    return int((Decimal(str(v))*100).quantize(Decimal("1"),rounding=ROUND_HALF_UP))

def minimum_trucks_for_movers(movers):
    """Crown Movers launch rule for minimum truck count by crew size."""
    movers=int(movers)
    if movers>=6:return 3
    if movers>=4:return 2
    return 1

def long_distance_hos_plan(true_drive_hours,service_high_hours):
    """Preliminary Quebec heavy-vehicle HOS planning screen; final dispatch requires driver-specific log/cycle verification."""
    drive=Decimal(str(true_drive_hours or 0));service=Decimal(str(service_high_hours or 0));on_duty=drive+service
    days=max(1,ceil(float(drive/HOS_MAX_DRIVING_HOURS)) if drive else 1,ceil(float(on_duty/HOS_MAX_ON_DUTY_HOURS)) if on_duty else 1)
    one_day=drive<=HOS_MAX_DRIVING_HOURS and on_duty<=HOS_MAX_ON_DUTY_HOURS
    return {
      "heavy_vehicle_threshold_kg":4500,
      "true_driving_hours":float(drive),"estimated_service_hours":float(service),"estimated_on_duty_hours":float(on_duty),
      "max_driving_hours":float(HOS_MAX_DRIVING_HOURS),"max_on_duty_hours":float(HOS_MAX_ON_DUTY_HOURS),
      "max_elapsed_hours":float(HOS_MAX_ELAPSED_HOURS),"daily_off_duty_hours":float(HOS_DAILY_OFF_DUTY_HOURS),
      "minimum_schedule_days":days,"one_day_possible":one_day,
      "status":"Potential 1-day - driver HOS verification required" if one_day else f"{days}-day minimum recommended - exceeds a one-day HOS planning limit",
      "dispatch_verification_required":True
    }

def service_minimum_hours(data,service):
    """Apply Crown's standard minimum, with the reduced minimum reserved for small afternoon local jobs."""
    requested=number(data.get("minimum_hours",3),"Minimum service hours",0,24)
    small=bool(data.get("small_afternoon_job",False))
    start_window=str(data.get("start_window") or "morning").strip().lower()
    if small:
        if service not in ("Moving","Packing"):
            raise HTTPException(422,"The small-afternoon minimum is available only for local moving or packing services.")
        if start_window!="afternoon":
            raise HTTPException(422,"Small afternoon jobs must use the Afternoon arrival window.")
        return Decimal("1.5"),True
    if service in ("Moving","Packing"):
        return max(requested,Decimal("3")),False
    return requested,False

def norm_email(v): return str(v or "").strip().lower()
def norm_phone(v):
    d=re.sub(r"\D","",str(v or ""))
    return d[1:] if len(d)==11 and d.startswith("1") else d

def identity(lead):
    e=norm_email(lead.get("email"));p=norm_phone(lead.get("phone"))
    return "e:"+e if e else ("p:"+p if p else "l:"+lead["id"])

def quote_start_window(inputs):
    """Return the operational start time and client-facing arrival-window label."""
    mode=str(inputs.get("start_window") or "morning").strip().lower()
    if mode=="morning":return "09:00","9:00-9:30 AM"
    if mode=="afternoon":return "13:00","1:00-4:00 PM"
    if mode=="custom":
        value=str(inputs.get("start_time") or "").strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",value):raise HTTPException(422,"Choose a valid custom start time.")
        hour,minute=map(int,value.split(":"));suffix="AM" if hour<12 else "PM";shown=hour%12 or 12
        return value,f"{shown}:{minute:02d} {suffix}"
    raise HTTPException(422,"Choose Morning, Afternoon, or a custom exact start time.")

def quoted_block_hours(inputs):
    service=str(inputs.get("service_type") or "Moving")
    if service=="Junk Removal":
        return max(1.5,Decimal("1.5")+Decimal("0.5")*Decimal(str(inputs.get("junk_extra_half_hours",0) or 0)))
    high=Decimal(str(inputs.get("hours_high",3) or 3));minimum=Decimal(str(inputs.get("minimum_hours",0) or 0))
    transport=Decimal(str(inputs.get("transport_hours",0) or 0))
    work=max(high,minimum)+transport
    if service=="Long Distance":
        plan=long_distance_hos_plan(transport,max(high,minimum))
        return max(Decimal("1"),work+HOS_DAILY_OFF_DUTY_HOURS*Decimal(str(max(0,plan["minimum_schedule_days"]-1))))
    return max(Decimal("1"),work)

def ensure_tentative_job(db,q,lead,actor):
    """Create the accepted-estimate schedule hold using the sold arrival window."""
    existing=one(db,"SELECT * FROM jobs WHERE quote_id=? ORDER BY created_at DESC LIMIT 1",(q["id"],))
    if existing:return existing
    inp=json.loads(q["inputs_json"]);day=inp.get("move_date") or lead.get("move_date")
    if not day:return None
    start_text,label=quote_start_window(inp)
    start=datetime.fromisoformat(day+"T"+start_text+":00").replace(tzinfo=MONTREAL).astimezone(timezone.utc)
    end=start+timedelta(hours=float(quoted_block_hours(inp)))
    jid=uid("JOB")
    insert(db,"jobs",dict(id=jid,lead_id=lead["id"],quote_id=q["id"],title=lead["client_name"],
      phase=inp.get("service_type") or "Moving",start_at=start.isoformat(timespec="seconds"),end_at=end.isoformat(timespec="seconds"),
      status="Tentative",stops_json=dumps([x for x in (lead.get("pickup"),lead.get("dropoff")) if x]),
      notes="Accepted estimate - awaiting deposit / booking confirmation. Arrival window: "+label+".",
      capacity_reviewed=0,exception_reason="",created_by=actor,created_at=now()))
    return one(db,"SELECT * FROM jobs WHERE id=?",(jid,))

def duplicate_candidates(db,email="",phone="",exclude_id=""):
    """Return exact contact matches without ever merging records by fuzzy name/address."""
    found={}
    email=norm_email(email);phone_key=norm_phone(phone)
    if email:
        for a in rows(db,"SELECT * FROM client_accounts WHERE email=? COLLATE NOCASE",(email,)):
            if a["id"]!=exclude_id:found.setdefault(a["id"],{"client":a,"reasons":[]})["reasons"].append("Same email")
    if phone_key:
        for a in rows(db,"SELECT * FROM client_accounts WHERE phone!=''"):
            if a["id"]!=exclude_id and norm_phone(a.get("phone"))==phone_key:
                found.setdefault(a["id"],{"client":a,"reasons":[]})["reasons"].append("Same phone")
    return list(found.values())

def flag_duplicate(db,client_id,candidate_id,reason):
    if not client_id or not candidate_id or client_id==candidate_id:return
    if not one(db,"SELECT id FROM crm_duplicate_flags WHERE client_id=? AND candidate_client_id=? AND reason=?",
               (client_id,candidate_id,reason)):
        insert(db,"crm_duplicate_flags",dict(id=uid("DUP"),client_id=client_id,candidate_client_id=candidate_id,
          reason=reason,status="Open",created_at=now()))

def sync_unlinked_clients(db):
    """Attach new leads safely: exact email wins; conflicting phone matches are flagged, never silently merged."""
    for l in rows(db,"""SELECT l.* FROM leads l LEFT JOIN client_account_leads x ON x.lead_id=l.id
                        WHERE x.lead_id IS NULL ORDER BY l.created_at"""):
        email=norm_email(l.get("email"));phone=norm_phone(l.get("phone"));acct=None
        matches=duplicate_candidates(db,email,l.get("phone"))
        email_matches=[m for m in matches if "Same email" in m["reasons"]]
        phone_matches=[m for m in matches if "Same phone" in m["reasons"]]
        # Exact email is the strongest identity signal. Phone-only matches are reused only when
        # the incoming record does not contradict an existing email address.
        if len(email_matches)==1:
            acct=email_matches[0]["client"]
        elif not email_matches and len(phone_matches)==1:
            candidate=phone_matches[0]["client"];candidate_email=norm_email(candidate.get("email"))
            if not email or not candidate_email or email==candidate_email:acct=candidate
        if not acct:
            key=identity(l);base=key;n=2
            while one(db,"SELECT id FROM client_accounts WHERE identity_key=?",(key,)):
                key=base+":"+str(n);n+=1
            cid=uid("CLIENT")
            insert(db,"client_accounts",dict(id=cid,identity_key=key,client_name=l["client_name"] or "Client",
                email=email,phone=l["phone"] or "",language=l["language"] or "English",
                created_at=l["created_at"],updated_at=now()))
            # Keep conflicting phone/email records separate and surface them for human review.
            for m in matches:
                reason=", ".join(m["reasons"])+"; contact details require review"
                flag_duplicate(db,cid,m["client"]["id"],reason)
                flag_duplicate(db,m["client"]["id"],cid,reason)
        else:
            cid=acct["id"]
            db.execute("""UPDATE client_accounts SET
              client_name=CASE WHEN client_name='' OR client_name='Website inquiry' THEN ? ELSE client_name END,
              email=CASE WHEN email='' THEN ? ELSE email END,
              phone=CASE WHEN phone='' THEN ? ELSE phone END,
              updated_at=? WHERE id=?""",(l["client_name"],email,l["phone"],now(),cid))
            for m in matches:
                if m["client"]["id"]!=cid:
                    reason=", ".join(m["reasons"])+"; exact email matched another client"
                    flag_duplicate(db,cid,m["client"]["id"],reason)
                    flag_duplicate(db,m["client"]["id"],cid,reason)
        insert(db,"client_account_leads",dict(client_id=cid,lead_id=l["id"],created_at=now()))

def schema(path):
    with transaction(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS client_accounts(
          id TEXT PRIMARY KEY, identity_key TEXT UNIQUE NOT NULL, client_name TEXT NOT NULL,
          email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', language TEXT NOT NULL DEFAULT 'English',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS client_account_leads(
          client_id TEXT NOT NULL REFERENCES client_accounts(id), lead_id TEXT NOT NULL UNIQUE REFERENCES leads(id),
          created_at TEXT NOT NULL, PRIMARY KEY(client_id,lead_id));
        CREATE INDEX IF NOT EXISTS client_accounts_email ON client_accounts(email);
        CREATE INDEX IF NOT EXISTS client_accounts_phone ON client_accounts(phone);
        CREATE TABLE IF NOT EXISTS crm_quote_templates(
          id TEXT PRIMARY KEY, template_key TEXT UNIQUE NOT NULL, service_type TEXT NOT NULL,
          language TEXT NOT NULL, name TEXT NOT NULL, body_json TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS crm_duplicate_flags(
          id TEXT PRIMARY KEY,
          client_id TEXT NOT NULL REFERENCES client_accounts(id),
          candidate_client_id TEXT NOT NULL REFERENCES client_accounts(id),
          reason TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'Open',
          created_at TEXT NOT NULL,
          UNIQUE(client_id,candidate_client_id,reason));
        """)
        sync_unlinked_clients(db)
        defaults=[
          ("moving-en","Moving","English","Local Moving - English",{"title":"Moving Service","liability":"Free limited liability coverage: $0.60/lb per article.","fragile":"TVs, large mirrors, glass, marble and other fragile items require appropriate protective packing or boxing. Added protection does not increase carrier liability unless separate replacement-value coverage is purchased and its conditions are met."}),
          ("moving-fr","Moving","French","Déménagement local - Français",{"title":"Service de déménagement","liability":"Responsabilité limitée gratuite : 0,60 $/lb par article.","fragile":"Les téléviseurs, grands miroirs, articles en verre, marbre et autres objets fragiles exigent un emballage ou une boîte de protection appropriée. La protection additionnelle n'augmente pas la responsabilité du transporteur à moins qu'une couverture de valeur de remplacement distincte soit achetée et que ses conditions soient respectées."}),
          ("packing-en","Packing","English","Packing Service - English",{"title":"Packing Service","liability":"Packing labour is billed at the selected moving crew hourly rate. Materials are additional unless specifically included.","fragile":"Fragile items require suitable boxes and protective materials."}),
          ("packing-fr","Packing","French","Service d'emballage - Français",{"title":"Service d'emballage","liability":"La main-d'œuvre d'emballage est facturée au taux horaire de l'équipe sélectionnée. Les matériaux sont en supplément sauf indication contraire.","fragile":"Les objets fragiles exigent des boîtes et matériaux de protection appropriés."}),
          ("junk-en","Junk Removal","English","Junk Removal - English",{"title":"Junk Removal","liability":"Final disposal pricing is based on the actual truck space used on site.","fragile":"Additional on-site labour, difficult access or sorting may be charged separately."}),
          ("junk-fr","Junk Removal","French","Enlèvement de déchets - Français",{"title":"Enlèvement de déchets","liability":"Le prix final est établi selon l'espace réellement utilisé dans le camion sur place.","fragile":"Du temps de main-d'œuvre supplémentaire, un accès difficile ou du tri peuvent être facturés séparément."}),
          ("long-en","Long Distance","English","Long Distance Moving - English (blank)",{"title":"Long-Distance Moving Service","liability":"Free limited liability coverage: $0.60/lb per article.","fragile":"Fragile-item packing requirements and any replacement-value insurance must be reviewed before the move.","blank_notes":"Blank long-distance template: enter agreed service hours, fixed transportation fee or transport hours, distance, stops and special handling."}),
          ("long-fr","Long Distance","French","Déménagement longue distance - Français (vierge)",{"title":"Service de déménagement longue distance","liability":"Responsabilité limitée gratuite : 0,60 $/lb par article.","fragile":"Les exigences d'emballage des objets fragiles et toute assurance de valeur de remplacement doivent être révisées avant le déménagement.","blank_notes":"Modèle longue distance vierge : inscrire les heures convenues, les frais fixes de transport ou les heures de transport, la distance, les arrêts et la manutention spéciale."}),
        ]
        for key,service,lang,name,body in defaults:
            if not one(db,"SELECT id FROM crm_quote_templates WHERE template_key=?",(key,)):
                insert(db,"crm_quote_templates",dict(id=uid("TMPL"),template_key=key,service_type=service,
                  language=lang,name=name,body_json=dumps(body),active=1,updated_at=now()))
        if one(db,"SELECT name FROM sqlite_master WHERE type='table' AND name='material_catalog'"):
            if not one(db,"SELECT id FROM material_catalog WHERE code='TV_PROTECTION_PACKAGE'"):
                insert(db,"material_catalog",dict(id=uid("MAT"),code="TV_PROTECTION_PACKAGE",
                  name="TV protection - bubble wrap + cardboard",category="Protection",sell_cents=3500,cost_cents=0,
                  quantity=0,minimum=0,location="Truck/Warehouse",taxable=1,active=1,updated_by="crm-v6",updated_at=now()))

def account_for_lead(db,lid):
    x=one(db,"SELECT client_id FROM client_account_leads WHERE lead_id=?",(lid,))
    return one(db,"SELECT * FROM client_accounts WHERE id=?",(x["client_id"],)) if x else None

def rate_for(db,day,movers):
    d=one(db,"SELECT * FROM daily_rates WHERE service_date=? AND movers=? ORDER BY version DESC LIMIT 1",(day,movers))
    if d:return Decimal(d["hourly_cents"])/100,"Exact-day rate"
    a=rows(db,"SELECT * FROM annual_rate_periods WHERE active=1 AND start_date<=? AND end_date>=? ORDER BY version DESC",(day,day))
    if len(a)>1:raise HTTPException(409,"This date matches more than one active rate period. Fix Pricing & rates first.")
    if a:return Decimal(a[0][f"rate_{movers}_cents"])/100,a[0]["label"]
    p=one(db,"SELECT * FROM pricing_versions ORDER BY approved DESC,created_at DESC LIMIT 1")
    if not p:raise HTTPException(422,"No pricing calendar is available.")
    try:
        r,label=seasonal_rate(json.loads(p["rates_json"]),day,movers);return Decimal(r),label
    except Exception as e:raise HTTPException(422,str(e))

def materials(db,requested):
    cat={r["code"]:r for r in rows(db,"SELECT * FROM material_catalog WHERE active=1")}
    out=[];total=0
    for raw in requested or []:
        qty=Decimal(str(raw.get("qty",0) or 0))
        if qty<=0:continue
        code=str(raw.get("code","")).strip();item=cat.get(code)
        name=item["name"] if item else str(raw.get("name") or "Other material")[:160]
        unit=int(item["sell_cents"]) if item else money(raw.get("unit_price",0))
        line=int((Decimal(unit)*qty).quantize(Decimal("1"),rounding=ROUND_HALF_UP))
        out.append({"code":code,"name":name,"qty":float(qty),"unit_cents":unit,"total_cents":line});total+=line
    return out,total

def calculate(db,data):
    service=str(data.get("service_type") or "Moving")
    language="French" if str(data.get("language","English")).lower().startswith("fr") else "English"
    if service=="Junk Removal":
        tier=str(data.get("junk_tier") or "quarter")
        if tier not in JUNK:raise HTTPException(422,"Choose a valid junk-removal tier.")
        name,base=JUNK[tier];units=int(number(data.get("junk_extra_half_hours",0),"Extra labour units",0,50))
        extra=JUNK_LABOUR*units;total=base+extra
        junk_names={"single":"Disposition d'un article","quarter":"1/4 camion - env. 5 pi","half":"1/2 camion - env. 10 pi","three_quarter":"3/4 camion - env. 15 pi","full":"Camion complet - conteneur 20 pi"} if language=="French" else {}
        lines=[{"name":junk_names.get(tier,name),"qty":1,"unit_cents":money(base),"total_cents":money(base)}]
        if units:lines.append({"name":"Main-d'oeuvre supplementaire - 30 minutes / 2 demenageurs" if language=="French" else "Additional on-site labour - 30 minutes / 2 crew","qty":units,"unit_cents":money(JUNK_LABOUR),"total_cents":money(extra)})
        return {"currency":"CAD","before_tax":True,"service_type":service,"language":language,"hourly_rate_cents":0,
          "service_low":0,"service_high":0,"minimum_hours":0,"transport_hours":0,"transport_cents":0,
          "fuel_cents":0,"materials_cents":0,"other_cents":0,"discount_cents":0,"service_low_cents":money(base),
          "service_high_cents":money(base),"low_cents":money(total),"high_cents":money(total),"movers":2,"trucks":1,
          "season":"Junk removal pricing","line_items":lines,
          "tax_note":"Before applicable taxes. Final junk price is based on actual truck space and approved extra labour."}
    day=str(data.get("move_date") or "")
    try:date.fromisoformat(day)
    except Exception:raise HTTPException(422,"Choose the service date.")
    movers=int(number(data.get("movers",2),"Movers",2,6));trucks=int(number(data.get("trucks",1),"Trucks",1,8))
    minimum_trucks=minimum_trucks_for_movers(movers)
    if trucks<minimum_trucks:
        raise HTTPException(422,f"Crown Movers requires at least {minimum_trucks} truck(s) for a crew of {movers} movers.")
    suggested,source=rate_for(db,day,movers)
    override=data.get("hourly_rate");rate=number(override,"Hourly rate",1,5000) if override not in (None,"") else suggested
    low=number(data.get("hours_low",3),"Estimated low hours",.25,100);high=number(data.get("hours_high",low),"Estimated high hours",.25,120)
    if high<low:raise HTTPException(422,"Estimated high hours cannot be lower than low hours.")
    minimum,small_afternoon=service_minimum_hours(data,service);low=max(low,minimum);high=max(high,minimum)
    transport_hours=number(data.get("transport_hours",1),"True driving / transportation hours",0,100)
    fixed=data.get("fixed_transport")
    if fixed not in (None,""):
        transport=number(fixed,"Fixed transportation fee",0,100000)
        billable_transport_hours=Decimal("0")
        show_transport_hours=Decimal("0")
    elif service=="Long Distance":
        billable_transport_hours=transport_hours*LONG_TRANSPORT_MULTIPLIER if transport_hours>=1 else transport_hours
        transport=rate*billable_transport_hours
        show_transport_hours=transport_hours
    else:
        billable_transport_hours=transport_hours
        transport=rate*transport_hours+EXTRA_TRUCK*max(0,trucks-1)
        show_transport_hours=transport_hours
    km=number(data.get("distance_km",0),"Actual round-trip distance km",0,10000);km_rate=number(data.get("distance_rate",LONG_KM),"Distance rate",0,20)
    adjusted_km=km*LONG_DISTANCE_KM_BUFFER if service=="Long Distance" else km
    fuel=adjusted_km*km_rate*trucks if service=="Long Distance" else LOCAL_FUEL*trucks
    mats,mat_total=materials(db,data.get("materials") or [])
    hos_plan=long_distance_hos_plan(transport_hours,high) if service=="Long Distance" else None
    other=number(data.get("other_amount",0),"Other agreed charges",0,100000);discount=number(data.get("discount_amount",0),"Discount",0,100000)
    slo=rate*low;shi=rate*high;fixed_total=transport+fuel+Decimal(mat_total)/100+other
    if discount>slo+fixed_total:raise HTTPException(422,"Discount exceeds the low estimate.")
    tlo=slo+fixed_total-discount;thi=shi+fixed_total-discount
    fr=language=="French"
    lines=[
      {"name":f"{movers} demenageurs + {trucks} camion(s)" if fr else f"{movers} movers + {trucks} truck(s)","qty":float(low),"unit_cents":money(rate),"total_cents":money(slo),"note":(f"Estimation : {float(low):g}-{float(high):g} heures de service. Minimum {float(minimum):g} h." if fr else f"Estimated {float(low):g}-{float(high):g} service hours. Minimum {float(minimum):g} h.")},
      {"name":("Frais de transport longue distance" if fr else "Long Distance Transportation Fee") if service=="Long Distance" else ("Frais de transport" if fr else "Transportation Fee"),
       "qty":1,"unit_cents":money(transport),"total_cents":money(transport),
       "note":((f"{float(show_transport_hours):g} h de conduite reelle x 1,15 = {float(billable_transport_hours):g} h facturables au taux horaire de l'equipe." if fr else f"{float(show_transport_hours):g} true driving hours x 1.15 = {float(billable_transport_hours):g} billable transportation hours at the team hourly rate.") if service=="Long Distance" and fixed in (None,"") else ("Montant de transport longue distance fixe convenu." if fr else "Agreed fixed long-distance transportation amount.") if service=="Long Distance" else ("Montant de transport presente au client." if fr else "Client-facing transportation amount."))},
    ]
    if service=="Long Distance":
        lines.append({"name":"Carburant / distance longue distance" if fr else "Long-distance fuel / distance fee","qty":1,"unit_cents":money(fuel),"total_cents":money(fuel),"note":(f"{float(km):g} km aller-retour + 5 % = {float(adjusted_km):g} km ajustes a {float(km_rate):.2f} $ CA/km x {trucks} camion(s)." if fr else f"{float(km):g} actual round-trip km + 5% = {float(adjusted_km):g} adjusted km at CAD {float(km_rate):.2f}/km x {trucks} truck(s).")})
    else:
        lines.append({"name":"Frais de carburant" if fr else "Fuel fee","qty":trucks,"unit_cents":money(LOCAL_FUEL),"total_cents":money(fuel),"note":"Frais de carburant local." if fr else "Local fuel charge."})
    lines.extend(mats)
    if other:lines.append({"name":"Autres frais convenus" if fr else "Other agreed charges","qty":1,"unit_cents":money(other),"total_cents":money(other)})
    if discount:lines.append({"name":"Rabais approuve" if fr else "Approved discount","qty":1,"unit_cents":-money(discount),"total_cents":-money(discount)})
    return {"currency":"CAD","before_tax":True,"service_type":service,"language":language,"hourly_rate_cents":money(rate),
      "suggested_hourly_rate_cents":money(suggested),"rate_source":source,"service_low":float(low),"service_high":float(high),
      "minimum_hours":float(minimum),"transport_hours":float(show_transport_hours),"billable_transport_hours":float(billable_transport_hours),"transport_cents":money(transport),
      "transport_multiplier":float(LONG_TRANSPORT_MULTIPLIER) if service=="Long Distance" and fixed in (None,"") and transport_hours>=1 else 1.0,
      "fuel_cents":money(fuel),"truck_fee_cents":money(EXTRA_TRUCK*max(0,trucks-1)),"distance_km":float(km),
      "adjusted_distance_km":float(adjusted_km),"distance_buffer_percent":5.0 if service=="Long Distance" else 0.0,
      "distance_rate_cents":money(km_rate),"materials_cents":mat_total,"other_cents":money(other),"discount_cents":money(discount),
      "service_low_cents":money(slo),"service_high_cents":money(shi),"low_cents":money(tlo),"high_cents":money(thi),
      "movers":movers,"trucks":trucks,"season":source,"line_items":lines,"small_afternoon_job":small_afternoon,
      "hos_plan":hos_plan,
      "scope":"Service hours are an estimate unless a fixed-price line explicitly says otherwise.",
      "tax_note":"Before applicable taxes. Quebec tax treatment is handled in the reviewed accounting setup."}

def payload(q):
    d=dict(q);d["inputs"]=json.loads(d.pop("inputs_json"));d["result"]=json.loads(d.pop("result_json"));d["lead_snapshot"]=json.loads(d.pop("lead_snapshot_json"));return d

def confirmation(lead,q):
    inp=json.loads(q["inputs_json"]);fr=str(inp.get("language") or lead.get("language","")).lower().startswith("fr")
    _,window=quote_start_window(inp)
    if fr:
        return f"Bonjour {lead['client_name']},\n\nVotre service avec Crown Movers est maintenant reserve et confirme. Fenetre d'arrivee : {window}. Si une urgence vous oblige a demander un changement de date, nous ferons notre possible pour vous accommoder. Un preavis d'au moins 10 jours est requis selon les conditions de reservation. Un changement demande a moins de 10 jours peut entrainer la perte du depot. Une nouvelle date depend des disponibilites et le tarif peut changer selon la date choisie.\n\nMerci,\nCrown Movers"
    return f"Hi {lead['client_name']},\n\nYour Crown Movers service is booked and confirmed. Arrival window: {window}. If an emergency means you need to request a date change, we will do our best to accommodate you. At least 10 days' notice is required under the booking terms. A change requested inside 10 days may result in loss of the deposit. A replacement date is subject to availability and rates may change for the new date.\n\nThank you,\nCrown Movers"

def confirm_job(db,q,lead,actor,reason):
    j=ensure_tentative_job(db,q,lead,actor)
    if not j:raise HTTPException(409,"Add the service date before confirming the booking.")
    db.execute("UPDATE jobs SET status='Confirmed',exception_reason=?,version=version+1 WHERE id=?",(reason,j["id"]))
    j=one(db,"SELECT * FROM jobs WHERE id=?",(j["id"],))
    add_task(db,lead["id"],"Assign crew and truck for confirmed service",lead.get("assigned_to",""),"assign:"+j["id"])
    key="booking-confirmed:"+q["id"]
    if lead.get("email") and not one(db,"SELECT id FROM outbound_messages WHERE event_key=?",(key,)):
        queue_message(db,lead["id"],"Reservation confirmee | Crown Movers" if str(json.loads(q["inputs_json"]).get("language","")).lower().startswith("fr") else "Booking confirmed | Crown Movers",confirmation(lead,q),"Preparation",actor,key,j["id"],j.get("version",1))
    db.execute("UPDATE leads SET stage='Booked',updated_at=? WHERE id=?",(now(),lead["id"]))
    return j

def register_sales_crm_v6(app,path,auth,public_url):
    schema(path)

    @app.get("/api/crm/clients")
    def clients(q:str="",u=Depends(auth)):
        require_role(u,OFFICE);needle=(q or "").strip().lower();like="%"+needle+"%"
        with transaction(path) as db:
            sync_unlinked_clients(db)
            if needle:
                accts=rows(db,"""SELECT a.* FROM client_accounts a
                  WHERE lower(a.client_name||' '||a.email||' '||a.phone) LIKE ?
                     OR EXISTS (
                       SELECT 1 FROM client_account_leads x JOIN leads l ON l.id=x.lead_id
                       WHERE x.client_id=a.id AND lower(COALESCE(l.pickup,'')||' '||COALESCE(l.dropoff,'')) LIKE ?)
                  ORDER BY a.updated_at DESC LIMIT 40""",(like,like))
            else:
                accts=rows(db,"SELECT * FROM client_accounts ORDER BY updated_at DESC LIMIT 40")
            for a in accts:
                a["services"]=rows(db,"""SELECT l.id,l.lead_type,l.move_date,l.pickup,l.dropoff,l.home_size,l.stage,l.created_at,
                  (SELECT status FROM quotes q WHERE q.lead_id=l.id ORDER BY revision DESC LIMIT 1) quote_status
                  FROM client_account_leads x JOIN leads l ON l.id=x.lead_id WHERE x.client_id=?
                  ORDER BY COALESCE(l.move_date,l.created_at) DESC LIMIT 12""",(a["id"],))
        return accts

    @app.get("/api/crm/clients/{cid}")
    def client(cid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            a=one(db,"SELECT * FROM client_accounts WHERE id=?",(cid,))
            if not a:raise HTTPException(404,"Client not found.")
            a["services"]=rows(db,"SELECT l.* FROM client_account_leads x JOIN leads l ON l.id=x.lead_id WHERE x.client_id=? ORDER BY COALESCE(l.move_date,l.created_at) DESC",(cid,))
            a["possible_duplicates"]=rows(db,"""SELECT f.id,f.reason,c.id candidate_id,c.client_name,c.email,c.phone
              FROM crm_duplicate_flags f JOIN client_accounts c ON c.id=f.candidate_client_id
              WHERE f.client_id=? AND f.status='Open' ORDER BY f.created_at DESC""",(cid,))
        return a

    @app.post("/api/crm/clients")
    def new_client(data:dict,u=Depends(auth)):
        require_role(u,SALES);name=str(data.get("client_name","")).strip();email=norm_email(data.get("email"));phone=str(data.get("phone","")).strip()
        if not name:raise HTTPException(422,"Enter the client name.")
        if email and ("@" not in email or "." not in email.rsplit("@",1)[-1]):raise HTTPException(422,"Enter a valid client email.")
        if not email and not norm_phone(phone):raise HTTPException(422,"Enter an email or phone number.")
        key="e:"+email if email else "p:"+norm_phone(phone)
        with transaction(path) as db:
            matches=duplicate_candidates(db,email,phone)
            if matches:
                return {"duplicate":True,"candidates":[{"id":m["client"]["id"],"client_name":m["client"]["client_name"],
                  "email":m["client"]["email"],"phone":m["client"]["phone"],"reasons":m["reasons"]} for m in matches]}
            cid=uid("CLIENT");insert(db,"client_accounts",dict(id=cid,identity_key=key,client_name=name,email=email,phone=phone,
              language=data.get("language") or "English",created_at=now(),updated_at=now()));audit(db,u["id"],"Client account created","client_accounts",cid)
            return one(db,"SELECT * FROM client_accounts WHERE id=?",(cid,))

    @app.patch("/api/crm/clients/{cid}")
    def update_client(cid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES)
        name=str(data.get("client_name","")).strip();email=norm_email(data.get("email"));phone=str(data.get("phone","")).strip()
        if not name:raise HTTPException(422,"Enter the client name.")
        if email and ("@" not in email or "." not in email.rsplit("@",1)[-1]):raise HTTPException(422,"Enter a valid client email.")
        if not email and not norm_phone(phone):raise HTTPException(422,"Enter an email or phone number.")
        language="French" if str(data.get("language","English")).lower().startswith("fr") else "English"
        new_key="e:"+email if email else "p:"+norm_phone(phone)
        with transaction(path) as db:
            a=one(db,"SELECT * FROM client_accounts WHERE id=?",(cid,))
            if not a:raise HTTPException(404,"Client not found.")
            conflict=one(db,"SELECT id FROM client_accounts WHERE identity_key=? AND id!=?",(new_key,cid))
            if conflict:raise HTTPException(409,"Another client already uses that primary email or phone. Open that client instead of creating a duplicate.")
            db.execute("UPDATE client_accounts SET identity_key=?,client_name=?,email=?,phone=?,language=?,updated_at=? WHERE id=?",
                       (new_key,name,email,phone,language,now(),cid))
            audit(db,u["id"],"Client contact profile updated","client_accounts",cid)
        return {"ok":True,"id":cid}

    @app.post("/api/crm/duplicates/{fid}/dismiss")
    def dismiss_duplicate(fid:str,u=Depends(auth)):
        require_role(u,SALES)
        with transaction(path) as db:
            f=one(db,"SELECT * FROM crm_duplicate_flags WHERE id=?",(fid,))
            if not f:raise HTTPException(404,"Duplicate warning not found.")
            db.execute("UPDATE crm_duplicate_flags SET status='Dismissed' WHERE id=?",(fid,))
            audit(db,u["id"],"Possible duplicate warning dismissed","crm_duplicate_flags",fid,{"client_id":f["client_id"],"candidate_client_id":f["candidate_client_id"]})
        return {"ok":True}

    @app.post("/api/crm/clients/{cid}/services")
    def new_service(cid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES)
        with transaction(path) as db:
            a=one(db,"SELECT * FROM client_accounts WHERE id=?",(cid,))
            if not a:raise HTTPException(404,"Client not found.")
            lid=uid("LEAD");service=str(data.get("service_type") or "Moving")
            rec=dict(id=lid,client_name=a["client_name"],email=a["email"],phone=a["phone"],move_date=str(data.get("move_date") or ""),
              pickup=str(data.get("pickup") or ""),dropoff=str(data.get("dropoff") or ""),home_size=str(data.get("home_size") or ""),
              boxes=str(data.get("boxes") or ""),pickup_access=str(data.get("pickup_access") or ""),dropoff_access=str(data.get("dropoff_access") or ""),
              pickup_walk=str(data.get("pickup_walk") or ""),dropoff_walk=str(data.get("dropoff_walk") or ""),inventory=str(data.get("inventory") or ""),
              heavy_items=str(data.get("heavy_items") or ""),photo_link="",language=a["language"],source="CrownOps CRM",lead_type=service,
              stage="New Lead",assigned_to=u["id"],priority="Normal",notes=str(data.get("notes") or ""),archived_import=0,notifications_suppressed=0,
              raw_json=dumps({"service_type":service}),version=1,created_at=now(),updated_at=now())
            insert(db,"leads",rec);insert(db,"client_account_leads",dict(client_id=cid,lead_id=lid,created_at=now()))
            db.execute("UPDATE client_accounts SET updated_at=? WHERE id=?",(now(),cid));audit(db,u["id"],"New service added to client","leads",lid,{"client_id":cid})
        return {"lead_id":lid,"client_id":cid}

    @app.get("/api/crm/templates")
    def templates(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:out=rows(db,"SELECT * FROM crm_quote_templates WHERE active=1 ORDER BY service_type,language")
        for r in out:r["body"]=json.loads(r.pop("body_json"))
        return out

    @app.get("/api/crm/quote-setup")
    def setup(lead_id:str,move_date:str="",movers:int=2,u=Depends(auth)):
        require_role(u,SALES)
        with closing(connect(path)) as db:
            l=one(db,"SELECT * FROM leads WHERE id=?",(lead_id,))
            if not l:raise HTTPException(404,"Service record not found.")
            day=move_date or l["move_date"];rate=source=None
            if day:rate,source=rate_for(db,day,int(movers))
            mats=rows(db,"SELECT code,name,category,sell_cents FROM material_catalog WHERE active=1 ORDER BY category,name")
            a=account_for_lead(db,lead_id)
        presets=[]
        for city,province,one_way in LONG_DISTANCE_CITY_PRESETS:
            round_trip=Decimal(str(one_way))*2;adjusted=round_trip*LONG_DISTANCE_KM_BUFFER
            presets.append({"key":(city+"-"+province).lower().replace(" ","-").replace(".",""),"city":city,"province":province,
              "one_way_km":float(one_way),"round_trip_km":float(round_trip),"adjusted_km":float(adjusted),
              "gas_one_truck_cents":money(adjusted*LONG_KM)})
        return {"client":a,"lead":l,"suggested_hourly_rate":float(rate) if rate is not None else None,"rate_source":source or "",
          "materials":mats,"long_distance_presets":presets,
          "defaults":{"local_fuel_per_truck":35.0,"extra_truck_transport":25.0,"long_distance_per_km":0.94,
            "long_distance_km_buffer_percent":5.0,"deposit":300.0,"limited_liability":"$0.60/lb per article"}}

    @app.post("/api/crm/quotes/calculate")
    def calc(data:dict,u=Depends(auth)):
        require_role(u,SALES)
        with closing(connect(path)) as db:return calculate(db,data)

    @app.post("/api/crm/quotes")
    def create_quote(data:dict,u=Depends(auth)):
        require_role(u,SALES);lid=str(data.get("lead_id") or "")
        with transaction(path) as db:
            l=one(db,"SELECT * FROM leads WHERE id=?",(lid,))
            if not l:raise HTTPException(404,"Service record not found.")
            result=calculate(db,data);day=str(data.get("move_date") or l["move_date"])
            if day and day!=l["move_date"]:
                db.execute("UPDATE leads SET move_date=?,version=version+1,updated_at=? WHERE id=?",(day,now(),lid));l=one(db,"SELECT * FROM leads WHERE id=?",(lid,))
            p=one(db,"SELECT * FROM pricing_versions ORDER BY approved DESC,created_at DESC LIMIT 1")
            if not p:raise HTTPException(422,"A pricing snapshot is required for audit history.")
            rev=db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM quotes WHERE lead_id=?",(lid,)).fetchone()[0]
            inp=dict(data);inp["deposit_required"]=float(data.get("deposit_required",300) or 0);inp["limited_liability"]="$0.60/lb per article"
            service=result["service_type"];language=result["language"];template_key=str(inp.get("template_key") or "").strip()
            tmpl=one(db,"SELECT * FROM crm_quote_templates WHERE template_key=? AND active=1",(template_key,))
            if not tmpl or tmpl["service_type"]!=service or tmpl["language"]!=language:
                raise HTTPException(422,"Choose the active quote template that matches this service and language.")
            start_time,start_label=quote_start_window(inp);inp["start_time"]=start_time;inp["start_window_label"]=start_label
            inp["template_snapshot"]={"template_key":tmpl["template_key"],"name":tmpl["name"],**json.loads(tmpl["body_json"])}
            qid=uid("QUOTE");snapshot={k:l[k] for k in l.keys() if k!="raw_json"}
            insert(db,"quotes",dict(id=qid,lead_id=lid,revision=rev,status="Draft",inputs_json=dumps(inp),result_json=dumps(result),
              pricing_id=p["id"],lead_snapshot_json=dumps(snapshot),lead_version=l["version"],created_by=u["id"],created_at=now()))
            db.execute("UPDATE leads SET stage='Quote',updated_at=? WHERE id=?",(now(),lid));audit(db,u["id"],"CRM quote revision created","quotes",qid,{"revision":rev})
            return payload(one(db,"SELECT * FROM quotes WHERE id=?",(qid,)))

    @app.get("/api/crm/quotes/{qid}")
    def get_quote(qid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            q=one(db,"SELECT * FROM quotes WHERE id=?",(qid,))
            if not q:raise HTTPException(404,"Estimate not found.")
            l=one(db,"SELECT * FROM leads WHERE id=?",(q["lead_id"],));a=account_for_lead(db,q["lead_id"])
            j=one(db,"SELECT * FROM jobs WHERE quote_id=? ORDER BY created_at DESC LIMIT 1",(qid,))
            paid=db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE lead_id=? AND purpose='Deposit' AND verified=1",(q["lead_id"],)).fetchone()[0]
        return {"quote":payload(q),"lead":l,"client":a,"job":j,"verified_deposit_cents":paid}

    @app.post("/api/crm/quotes/{qid}/finalize")
    def finalize(qid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES);invite_url="";mid=None
        with transaction(path) as db:
            q=one(db,"SELECT * FROM quotes WHERE id=?",(qid,))
            if not q or q["status"] not in ("Draft","Approved"):raise HTTPException(409,"Only a draft or approved estimate can be finalized.")
            l=one(db,"SELECT * FROM leads WHERE id=?",(q["lead_id"],))
            if l["version"]!=q["lead_version"]:raise HTTPException(409,"Client/service details changed. Save a new estimate revision first.")
            missing=[label for key,label in [("client_name","client name"),("email","email"),("phone","phone"),("move_date","service date")] if not str(l.get(key,"")).strip()]
            if l.get("email") and ("@" not in l["email"] or "." not in l["email"].rsplit("@",1)[-1]):missing.append("valid email")
            service=json.loads(q["inputs_json"]).get("service_type","Moving")
            if service in ("Moving","Long Distance") and (not l["pickup"] or not l["dropoff"]):missing.append("pickup and destination")
            if missing:raise HTTPException(422,"Complete before sending: "+", ".join(missing)+".")
            if q["status"]=="Draft":db.execute("UPDATE quotes SET status='Approved',approved_by=?,approved_at=? WHERE id=?",(u["id"],now(),qid))
            from .platform_common import stamp_hash
            raw=secrets.token_urlsafe(32);db.execute("UPDATE portal_invites SET revoked=1 WHERE lead_id=? AND used_at IS NULL",(l["id"],))
            insert(db,"portal_invites",dict(token_hash=stamp_hash(raw),lead_id=l["id"],email=l["email"],
              expires_at=(datetime.now(timezone.utc)+timedelta(days=14)).isoformat(timespec="seconds"),created_by=u["id"],created_at=now()))
            invite_url=public_url+"/portal#access="+raw;r=json.loads(q["result_json"]);inp=json.loads(q["inputs_json"]);fr=str(inp.get("language","English")).lower().startswith("fr")
            template=inp.get("template_snapshot") or {};_,arrival=quote_start_window(inp);service_title=template.get("title") or inp.get("service_type") or "Moving"
            subject=("Votre devis Crown Movers - "+service_title) if fr else ("Your Crown Movers estimate - "+service_title)
            body=(f"Bonjour {l['client_name']},\n\nVotre devis Crown Movers est pret pour le {inp.get('move_date') or l.get('move_date')} ({arrival}). Estimation avant taxes : {r['low_cents']/100:.2f} $ a {r['high_cents']/100:.2f} $ CA.\n\nConsultez les details, les protections applicables et les conditions, puis acceptez votre devis ici :\n{invite_url}\n\nApres l'acceptation, les options de depot applicables seront affichees.\n\nMerci,\nCrown Movers" if fr else
              f"Hi {l['client_name']},\n\nYour Crown Movers estimate is ready for {inp.get('move_date') or l.get('move_date')} ({arrival}). Estimated range before tax: CAD {r['low_cents']/100:.2f} to CAD {r['high_cents']/100:.2f}.\n\nReview the service details, applicable protection and booking terms, then accept your estimate here:\n{invite_url}\n\nAfter acceptance, the available deposit options will appear.\n\nThank you,\nCrown Movers")
            msg=queue_message(db,l["id"],subject,body,"Reply",u["id"],"quote-delivery:"+qid+":"+str(q["revision"]));mid=msg["id"]
            db.execute("UPDATE leads SET stage='Quote Sent',notifications_suppressed=0,updated_at=? WHERE id=?",(now(),l["id"]));audit(db,u["id"],"Estimate finalized for client delivery","quotes",qid,{"mail_id":mid})
        sent=False;err=""
        if data.get("send_now") is True:
            try:
                with transaction(path) as db:db.execute("UPDATE outbound_messages SET status='Approved',approved_by=? WHERE id=?",(u["id"],mid))
                from .providers import process_mail
                sent=bool(process_mail(path,mid).get("sent"))
            except Exception as ex:err=str(getattr(ex,"detail",ex))[:250]
        qbo_result=None;qbo_error=""
        if data.get("sync_quickbooks") is True:
            try:
                from .extensions import qbo_work
                from .providers import qbo_mapping,qbo_customer,qbo_call
                with closing(connect(path)) as db:
                    qnow=one(db,"SELECT * FROM quotes WHERE id=?",(qid,))
                    lnow=one(db,"SELECT * FROM leads WHERE id=?",(qnow["lead_id"],))
                    rr=json.loads(qnow["result_json"])
                item=qbo_mapping(path,"service_item_id");tax=qbo_mapping(path,"tax_code_id")
                if not item or not tax:raise HTTPException(503,"QuickBooks moving-service/tax mapping is incomplete.")
                amount=rr["low_cents"]
                def send_qbo(key):
                    customer=qbo_customer(path,lnow)
                    body={"CustomerRef":{"value":customer},"CurrencyRef":{"value":"CAD"},"GlobalTaxCalculation":"TaxExcluded",
                      "TxnDate":now()[:10],"Line":[{"DetailType":"SalesItemLineDetail","Amount":amount/100,
                      "Description":f"Crown Movers estimate range CAD {rr['low_cents']/100:.2f} - CAD {rr['high_cents']/100:.2f}. {rr.get('movers',0)} movers / {rr.get('trucks',0)} truck(s).",
                      "SalesItemLineDetail":{"ItemRef":{"value":item},"TaxCodeRef":{"value":tax},"Qty":1,"UnitPrice":amount/100}}],
                      "PrivateNote":"CrownOps "+qid+" client estimate; client delivery controlled by CrownOps."}
                    remote=qbo_call(path,"POST","estimate",body,key)["Estimate"]
                    with transaction(path) as db:
                        insert(db,"external_records",dict(provider="quickbooks",kind="Estimate",object_id=qid,revision=qnow["revision"],external_id=remote["Id"],sync_token=remote.get("SyncToken","0"),amount_cents=amount,updated_at=now()))
                    return {"external_id":remote["Id"],"range_endpoint":"low","sent":False}
                qbo_result=qbo_work(path,"Estimate",qid,qnow["revision"],u["id"],send_qbo)
            except Exception as ex:qbo_error=str(getattr(ex,"detail",ex))[:250]
        return {"ok":True,"quote_status":"Approved","mail_id":mid,"sent":sent,"send_error":err,"portal_url":invite_url,
                "quickbooks":qbo_result,"quickbooks_error":qbo_error}

    @app.post("/api/crm/quotes/{qid}/manual-deposit")
    def manual_deposit(qid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES);method=str(data.get("method") or "")
        if method not in ("Interac e-Transfer","Cash","Cheque"):raise HTTPException(422,"Choose e-transfer, cash or cheque.")
        mode=str(data.get("mode") or "received")
        with transaction(path) as db:
            q=one(db,"SELECT * FROM quotes WHERE id=? AND status='Accepted'",(qid,))
            if not q:raise HTTPException(409,"The client must accept the estimate first.")
            l=one(db,"SELECT * FROM leads WHERE id=?",(q["lead_id"],));required=money(json.loads(q["inputs_json"]).get("deposit_required",300))
            if mode=="received":
                amount=money(data.get("amount",required/100));ref=str(data.get("reference") or "").strip()
                if amount<=0 or not ref:raise HTTPException(422,"Enter the amount actually received and a unique reference.")
                if one(db,"SELECT id FROM payments WHERE lead_id=? AND reference=?",(l["id"],ref)):raise HTTPException(409,"That payment reference is already recorded.")
                pid=uid("PAY");insert(db,"payments",dict(id=pid,lead_id=l["id"],amount_cents=amount,method=method,purpose="Deposit",reference=ref,verified=1,recorded_by=u["id"],created_at=now()))
                total=db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE lead_id=? AND purpose='Deposit' AND verified=1",(l["id"],)).fetchone()[0]
                if total<required:return {"ok":True,"confirmed":False,"remaining_cents":required-total}
                j=confirm_job(db,q,l,u["id"],method+" deposit verified");audit(db,u["id"],"Deposit verified and booking confirmed","payments",pid,{"job_id":j["id"]});return {"ok":True,"confirmed":True,"job_id":j["id"]}
            if mode!="due_on_service":raise HTTPException(422,"Choose received or due on service day.")
            if method not in ("Cash","Cheque"):raise HTTPException(422,"Only cash or cheque can be approved as due on the service day. Record an e-transfer only after it is actually received.")
            j=confirm_job(db,q,l,u["id"],"Manager-approved deposit exception: "+method+" due on service day");audit(db,u["id"],"Booking confirmed with deposit exception","jobs",j["id"],{"method":method})
            return {"ok":True,"confirmed":True,"job_id":j["id"],"exception":True}

    @app.post("/api/crm/quotes/{qid}/deposit-reminder")
    def reminder(qid:str,u=Depends(auth)):
        require_role(u,SALES)
        with transaction(path) as db:
            q=one(db,"SELECT * FROM quotes WHERE id=? AND status='Accepted'",(qid,))
            if not q:raise HTTPException(409,"Only an accepted estimate can receive a deposit reminder.")
            l=one(db,"SELECT * FROM leads WHERE id=?",(q["lead_id"],));qin=json.loads(q["inputs_json"]);amount=float(qin.get("deposit_required",300));fr=str(qin.get("language") or l["language"]).lower().startswith("fr")
            dep=settings(db,"deposit_policy",{}) or {};interac=""
            if dep.get("approved") and dep.get("enabled") and dep.get("recipient_email"):
                if fr:
                    interac=f"\n\nVirement Interac : {dep.get('recipient_email')} ({dep.get('recipient_name') or 'Crown Movers'})."
                    if dep.get("auto_deposit",True):interac+=" Le depot automatique est active; aucun mot de passe n'est requis."
                    if dep.get("reference_note"):interac+=" "+str(dep.get("reference_note"))
                else:
                    interac=f"\n\nInterac e-Transfer: {dep.get('recipient_email')} ({dep.get('recipient_name') or 'Crown Movers'})."
                    if dep.get("auto_deposit",True):interac+=" Auto-deposit is enabled; no password is required."
                    if dep.get("reference_note"):interac+=" "+str(dep.get("reference_note"))
            subject="Rappel amical - dépôt Crown Movers" if fr else "Friendly reminder - Crown Movers deposit"
            body=(f"Bonjour {l['client_name']},\n\nPetit rappel amical : le dépôt de {amount:.2f} $ CA lié à votre réservation est toujours en attente. Si vous l'avez déjà envoyé, vous pouvez ignorer ce message.{interac}\n\nMerci,\nCrown Movers" if fr else
              f"Hi {l['client_name']},\n\nA friendly reminder that the CAD {amount:.2f} deposit for your booking is still pending. If you already sent it, please disregard this message.{interac}\n\nThank you,\nCrown Movers")
            m=queue_message(db,l["id"],subject,body,"Follow-up",u["id"],"deposit-reminder:"+qid+":"+now()[:10])
        return {"id":m["id"],"status":m["status"]}

    @app.get("/api/crm/review-settings")
    def get_review_settings(u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:return settings(db,"automation_policy",{}) or {}

    @app.post("/api/crm/review-settings")
    def set_review_settings(data:dict,u=Depends(auth)):
        require_role(u,{"owner"});url=str(data.get("review_url") or "").strip()
        if url and not url.startswith("https://"):raise HTTPException(422,"Use the full https:// public review URL.")
        with transaction(path) as db:
            cfg=settings(db,"automation_policy",{}) or {};cfg.update({"enabled":bool(data.get("enabled",True)),"review_request":bool(data.get("review_request",True)),"review_url":url});put_setting(db,"automation_policy",cfg);audit(db,u["id"],"Review-request settings updated","settings","automation_policy",{"has_url":bool(url)})
        return {"ok":True}

    @app.post("/api/crm/jobs/{jid}/review-request")
    def review_request(jid:str,u=Depends(auth)):
        require_role(u,SALES)
        with transaction(path) as db:
            j=one(db,"SELECT * FROM jobs WHERE id=?",(jid,))
            if not j or j["status"]!="Completed":raise HTTPException(409,"Complete the service before requesting a public review.")
            l=one(db,"SELECT * FROM leads WHERE id=?",(j["lead_id"],));cfg=settings(db,"automation_policy",{}) or {};url=str(cfg.get("review_url") or "")
            if not url:raise HTTPException(422,"Add the company's public review URL in review settings first.")
            fr=str(l["language"]).lower().startswith("fr");subject="Merci de nous avoir choisis" if fr else "Thank you for choosing Crown Movers"
            body=(f"Bonjour {l['client_name']},\n\nMerci d'avoir choisi Crown Movers. Si vous le souhaitez, vous pouvez partager un avis honnête sur votre expérience ici : {url}\n\nVotre avis est entièrement facultatif et aucun rabais ou avantage ne dépend d'un avis public.\n\nMerci,\nCrown Movers" if fr else
              f"Hi {l['client_name']},\n\nThank you for choosing Crown Movers. If you'd like, you're welcome to share an honest review of your experience here: {url}\n\nLeaving a review is completely optional, and no discount or benefit depends on a public review.\n\nThank you,\nCrown Movers")
            m=queue_message(db,l["id"],subject,body,"Review request",u["id"],"neutral-review:"+jid,jid,j["version"])
        return {"id":m["id"],"status":m["status"]}
