"""Launch-ready sales CRM and quote workflow for Crown Movers."""
from contextlib import closing
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal, ROUND_HALF_UP
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

def norm_email(v): return str(v or "").strip().lower()
def norm_phone(v):
    d=re.sub(r"\D","",str(v or ""))
    return d[1:] if len(d)==11 and d.startswith("1") else d

def identity(lead):
    e=norm_email(lead.get("email"));p=norm_phone(lead.get("phone"))
    return "e:"+e if e else ("p:"+p if p else "l:"+lead["id"])

def sync_unlinked_clients(db):
    """Attach newly arrived form/Gmail/manual leads to the persistent client directory."""
    for l in rows(db,"""SELECT l.* FROM leads l LEFT JOIN client_account_leads x ON x.lead_id=l.id
                        WHERE x.lead_id IS NULL ORDER BY l.created_at"""):
        email=norm_email(l.get("email"));phone=norm_phone(l.get("phone"));acct=None
        # Prefer an exact email match. If email is unavailable, reuse a normalized phone match.
        if email:acct=one(db,"SELECT * FROM client_accounts WHERE email=? COLLATE NOCASE",(email,))
        if not acct and phone:
            for candidate in rows(db,"SELECT * FROM client_accounts WHERE phone!=''"):
                if norm_phone(candidate.get("phone"))==phone:
                    acct=candidate;break
        if not acct:
            key=identity(l);base=key;n=2
            while one(db,"SELECT id FROM client_accounts WHERE identity_key=?",(key,)):
                key=base+":"+str(n);n+=1
            cid=uid("CLIENT")
            insert(db,"client_accounts",dict(id=cid,identity_key=key,client_name=l["client_name"] or "Client",
                email=email,phone=l["phone"] or "",language=l["language"] or "English",
                created_at=l["created_at"],updated_at=now()))
        else:
            cid=acct["id"]
            db.execute("""UPDATE client_accounts SET
              client_name=CASE WHEN client_name='' OR client_name='Website inquiry' THEN ? ELSE client_name END,
              email=CASE WHEN email='' THEN ? ELSE email END,
              phone=CASE WHEN phone='' THEN ? ELSE phone END,
              updated_at=? WHERE id=?""",(l["client_name"],email,l["phone"],now(),cid))
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
        """)
        sync_unlinked_clients(db)
        defaults=[
          ("moving-en","Moving","English","Local Moving - English",{
            "title":"Moving Service",
            "included":"Furniture blanket wrapping; ordinary disassembly/reassembly as needed; free limited liability coverage of $0.60/lb per article.",
            "liability":"Free limited liability coverage: $0.60/lb per article.",
            "fragile":"TVs, large mirrors, glass, marble and other fragile items require appropriate protective packing or boxing. Added protection does not increase carrier liability unless separate replacement-value coverage is purchased and its conditions are met.",
            "deposit_terms":"The booking is secured when the required deposit is verified, or when Crown approves an alternate cash/cheque arrangement.",
            "date_change_terms":"We will always try to accommodate an emergency date change. At least 10 days' notice is required under the booking terms. Changes inside 10 days may result in loss of the deposit, are subject to availability, and may use the rates applicable to the new date."
          }),
          ("moving-fr","Moving","French","Déménagement local - Français",{
            "title":"Service de déménagement",
            "included":"Protection des meubles avec couvertures; montage/démontage ordinaire au besoin; responsabilité limitée gratuite de 0,60 $/lb par article.",
            "liability":"Responsabilité limitée gratuite : 0,60 $/lb par article.",
            "fragile":"Les téléviseurs, grands miroirs, articles en verre, marbre et autres objets fragiles exigent un emballage ou une boîte de protection appropriée. La protection additionnelle n'augmente pas la responsabilité du transporteur à moins qu'une couverture de valeur de remplacement distincte soit achetée et que ses conditions soient respectées.",
            "deposit_terms":"La réservation est confirmée lorsque le dépôt requis est vérifié ou lorsqu'une entente de paiement comptant/chèque est approuvée par Crown.",
            "date_change_terms":"Nous ferons toujours notre possible pour accommoder un changement de date en cas d'urgence. Un préavis d'au moins 10 jours est requis selon les conditions de réservation. Un changement à moins de 10 jours peut entraîner la perte du dépôt, dépend des disponibilités et peut être facturé selon le tarif applicable à la nouvelle date."
          }),
          ("packing-en","Packing","English","Packing Service - English",{
            "title":"Packing Service",
            "included":"Packing labour is billed at the selected crew hourly rate. Furniture blankets used for moving protection are included; consumable packing materials are additional unless specifically included.",
            "liability":"Free limited liability coverage for the move remains $0.60/lb per article unless separate replacement-value coverage is purchased and its conditions are met.",
            "fragile":"Fragile items require suitable boxes and protective materials. TVs, large mirrors and fragile glass require proper boxing/protection.",
            "deposit_terms":"The booking is secured when the required deposit is verified, or when Crown approves an alternate cash/cheque arrangement.",
            "date_change_terms":"Date changes are subject to availability and may affect the applicable rate."
          }),
          ("packing-fr","Packing","French","Service d'emballage - Français",{
            "title":"Service d'emballage",
            "included":"La main-d'œuvre d'emballage est facturée au taux horaire de l'équipe sélectionnée. Les couvertures utilisées pour la protection lors du déménagement sont incluses; les matériaux d'emballage consommables sont en supplément sauf indication contraire.",
            "liability":"La responsabilité limitée gratuite pour le déménagement demeure de 0,60 $/lb par article, sauf si une couverture de valeur de remplacement distincte est achetée et que ses conditions sont respectées.",
            "fragile":"Les objets fragiles exigent des boîtes et matériaux de protection appropriés. Les téléviseurs, grands miroirs et articles en verre fragile exigent une protection/boîte adéquate.",
            "deposit_terms":"La réservation est confirmée lorsque le dépôt requis est vérifié ou lorsqu'une entente de paiement comptant/chèque est approuvée par Crown.",
            "date_change_terms":"Les changements de date dépendent des disponibilités et peuvent modifier le tarif applicable."
          }),
          ("junk-en","Junk Removal","English","Junk Removal - English",{
            "title":"Junk Removal",
            "included":"Pricing is based on the actual truck space used on site.",
            "liability":"Final disposal pricing is based on the actual truck space used on site.",
            "fragile":"Additional on-site labour, difficult access, sorting or unusual disposal requirements may be charged separately.",
            "deposit_terms":"Any deposit or payment arrangement shown on the estimate must be completed before the booking is considered fully secured.",
            "date_change_terms":"Changes are subject to crew and truck availability."
          }),
          ("junk-fr","Junk Removal","French","Enlèvement de déchets - Français",{
            "title":"Enlèvement de déchets",
            "included":"Le prix est établi selon l'espace réellement utilisé dans le camion sur place.",
            "liability":"Le prix final est établi selon l'espace réellement utilisé dans le camion sur place.",
            "fragile":"Du temps de main-d'œuvre supplémentaire, un accès difficile, du tri ou des exigences d'élimination particulières peuvent être facturés séparément.",
            "deposit_terms":"Tout dépôt ou arrangement de paiement indiqué au devis doit être complété avant que la réservation soit considérée comme entièrement confirmée.",
            "date_change_terms":"Les changements dépendent des disponibilités de l'équipe et du camion."
          }),
          ("long-en","Long Distance","English","Long Distance Moving - English (blank)",{
            "title":"Long-Distance Moving Service",
            "included":"Blank long-distance template for a reviewed fixed transportation fee or internally calculated transport hours, plus route-distance fuel.",
            "liability":"Free limited liability coverage: $0.60/lb per article.",
            "fragile":"Fragile-item packing requirements and any replacement-value insurance must be reviewed before the move.",
            "deposit_terms":"The booking is secured when the required deposit is verified, or when Crown approves an alternate cash/cheque arrangement.",
            "date_change_terms":"Long-distance date changes are subject to route, crew and truck availability and may change the applicable rate or transportation fee.",
            "blank_notes":"Enter the agreed service-hour range, fixed transportation fee (or internal transport hours), route kilometres, stops, truck count and special handling. The client sees only the final transportation amount, not the internal hour multiplication."
          }),
          ("long-fr","Long Distance","French","Déménagement longue distance - Français (vierge)",{
            "title":"Service de déménagement longue distance",
            "included":"Modèle longue distance vierge pour un frais de transport forfaitaire révisé ou des heures de transport calculées à l'interne, plus les frais de carburant selon la distance.",
            "liability":"Responsabilité limitée gratuite : 0,60 $/lb par article.",
            "fragile":"Les exigences d'emballage des objets fragiles et toute assurance de valeur de remplacement doivent être révisées avant le déménagement.",
            "deposit_terms":"La réservation est confirmée lorsque le dépôt requis est vérifié ou lorsqu'une entente de paiement comptant/chèque est approuvée par Crown.",
            "date_change_terms":"Les changements de date longue distance dépendent de la route, de l'équipe et des camions disponibles et peuvent modifier le tarif ou les frais de transport applicables.",
            "blank_notes":"Inscrire la plage d'heures de service convenue, le frais de transport forfaitaire (ou les heures de transport internes), les kilomètres de route, les arrêts, le nombre de camions et la manutention spéciale. Le client voit uniquement le montant final du transport, pas le calcul interne des heures."
          }),
        ]
        for key,service,lang,name,body in defaults:
            old=one(db,"SELECT id FROM crm_quote_templates WHERE template_key=?",(key,))
            if old:
                db.execute("UPDATE crm_quote_templates SET service_type=?,language=?,name=?,body_json=?,active=1,updated_at=? WHERE id=?",
                           (service,lang,name,dumps(body),now(),old["id"]))
            else:
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

def suggested_hours(lead):
    """Conservative planning suggestion only; staff can always edit before sending."""
    text=(" ".join(str(lead.get(k) or "") for k in ("home_size","inventory","heavy_items","pickup_access","dropoff_access"))).lower()
    size=str(lead.get("home_size") or "").lower()
    low,high=Decimal("3.0"),Decimal("4.5")
    if any(x in size for x in ("studio","bachelor","1 1/2","2 1/2")):low,high=Decimal("2.5"),Decimal("4.0")
    elif any(x in size for x in ("1 bedroom","one bedroom","3 1/2","3½")):low,high=Decimal("3.0"),Decimal("4.5")
    elif any(x in size for x in ("2 bedroom","two bedroom","4 1/2","4½")):low,high=Decimal("4.0"),Decimal("6.0")
    elif any(x in size for x in ("3 bedroom","three bedroom","5 1/2","5½")):low,high=Decimal("5.0"),Decimal("7.5")
    elif any(x in size for x in ("4 bedroom","four bedroom","5 bedroom","house","home")):low,high=Decimal("6.0"),Decimal("9.0")
    boxes=0
    m=re.search(r"\d+",str(lead.get("boxes") or ""))
    if m:boxes=int(m.group())
    if boxes>=60:low+=Decimal("1.0");high+=Decimal("1.5")
    elif boxes>=40:low+=Decimal("0.5");high+=Decimal("1.0")
    elif boxes>=20:high+=Decimal("0.5")
    if any(x in text for x in ("stairs","stair","flight","walk-up","walkup")):high+=Decimal("0.75")
    if any(x in text for x in ("piano","treadmill","gym","safe","marble","oversized","heavy")):high+=Decimal("0.75")
    return {"low":float(low),"high":float(high),"basis":"Planning suggestion from saved move size, boxes and access. Review before sending."}

def calculate(db,data):
    service=str(data.get("service_type") or "Moving")
    language="French" if str(data.get("language","English")).lower().startswith("fr") else "English"
    if service=="Junk Removal":
        tier=str(data.get("junk_tier") or "quarter")
        if tier not in JUNK:raise HTTPException(422,"Choose a valid junk-removal tier.")
        name,base=JUNK[tier];units=int(number(data.get("junk_extra_half_hours",0),"Extra labour units",0,50))
        extra=JUNK_LABOUR*units;total=base+extra
        lines=[{"name":name,"qty":1,"unit_cents":money(base),"total_cents":money(base)}]
        if units:lines.append({"name":"Additional on-site labour - 30 minutes / 2 crew","qty":units,"unit_cents":money(JUNK_LABOUR),"total_cents":money(extra)})
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
    suggested,source=rate_for(db,day,movers)
    override=data.get("hourly_rate");rate=number(override,"Hourly rate",1,5000) if override not in (None,"") else suggested
    low=number(data.get("hours_low",3),"Estimated low hours",.25,100);high=number(data.get("hours_high",low),"Estimated high hours",.25,120)
    if high<low:raise HTTPException(422,"Estimated high hours cannot be lower than low hours.")
    minimum=number(data.get("minimum_hours",3),"Minimum service hours",0,24);low=max(low,minimum);high=max(high,minimum)
    transport_hours=number(data.get("transport_hours",1),"Transportation hours",0,100)
    fixed=data.get("fixed_transport")
    transport=number(fixed,"Fixed transportation fee",0,100000) if fixed not in (None,"") else rate*transport_hours+EXTRA_TRUCK*max(0,trucks-1)
    show_transport_hours=Decimal("0") if fixed not in (None,"") else transport_hours
    km=number(data.get("distance_km",0),"Distance km",0,10000);km_rate=number(data.get("distance_rate",LONG_KM),"Distance rate",0,20)
    fuel=km*km_rate*trucks if service=="Long Distance" else LOCAL_FUEL*trucks
    mats,mat_total=materials(db,data.get("materials") or [])
    other=number(data.get("other_amount",0),"Other agreed charges",0,100000);discount=number(data.get("discount_amount",0),"Discount",0,100000)
    slo=rate*low;shi=rate*high;fixed_total=transport+fuel+Decimal(mat_total)/100+other
    if discount>slo+fixed_total:raise HTTPException(422,"Discount exceeds the low estimate.")
    tlo=slo+fixed_total-discount;thi=shi+fixed_total-discount
    lines=[
      {"name":f"{movers} movers + {trucks} truck(s)","qty":float(low),"unit_cents":money(rate),"total_cents":money(slo),"note":f"Estimated {float(low):g}-{float(high):g} service hours. Minimum {float(minimum):g} h."},
      {"name":"Transportation fee","qty":1,"unit_cents":money(transport),"total_cents":money(transport),"note":"Fixed client-facing transportation amount."},
    ]
    if service=="Long Distance":
        lines.append({"name":"Long-distance fuel / distance fee","qty":1,"unit_cents":money(fuel),"total_cents":money(fuel),"note":f"{float(km):g} km at CAD {float(km_rate):.2f}/km x {trucks} truck(s)."})
    else:
        lines.append({"name":"Fuel fee","qty":trucks,"unit_cents":money(LOCAL_FUEL),"total_cents":money(fuel),"note":"Local fuel charge."})
    lines.extend(mats)
    if other:lines.append({"name":"Other agreed charges","qty":1,"unit_cents":money(other),"total_cents":money(other)})
    if discount:lines.append({"name":"Approved discount","qty":1,"unit_cents":-money(discount),"total_cents":-money(discount)})
    return {"currency":"CAD","before_tax":True,"service_type":service,"language":language,"hourly_rate_cents":money(rate),
      "suggested_hourly_rate_cents":money(suggested),"rate_source":source,"service_low":float(low),"service_high":float(high),
      "minimum_hours":float(minimum),"transport_hours":float(show_transport_hours),"transport_cents":money(transport),
      "fuel_cents":money(fuel),"truck_fee_cents":money(EXTRA_TRUCK*max(0,trucks-1)),"distance_km":float(km),
      "distance_rate_cents":money(km_rate),"materials_cents":mat_total,"other_cents":money(other),"discount_cents":money(discount),
      "service_low_cents":money(slo),"service_high_cents":money(shi),"low_cents":money(tlo),"high_cents":money(thi),
      "movers":movers,"trucks":trucks,"season":source,"line_items":lines,
      "scope":"Service hours are an estimate unless a fixed-price line explicitly says otherwise.",
      "tax_note":"Before applicable taxes. Quebec tax treatment is handled in the reviewed accounting setup."}

def payload(q):
    d=dict(q);d["inputs"]=json.loads(d.pop("inputs_json"));d["result"]=json.loads(d.pop("result_json"));d["lead_snapshot"]=json.loads(d.pop("lead_snapshot_json"));return d

def confirmation(lead):
    if str(lead.get("language","")).lower().startswith("fr"):
        return f"Bonjour {lead['client_name']},\n\nVotre service avec Crown Movers est maintenant réservé et confirmé. Si une urgence vous oblige à demander un changement de date, nous ferons notre possible pour vous accommoder. Un préavis d'au moins 10 jours est requis selon les conditions de réservation. Un changement demandé à moins de 10 jours peut entraîner la perte du dépôt. Une nouvelle date dépend des disponibilités et le tarif peut changer selon la date choisie.\n\nMerci,\nCrown Movers"
    return f"Hi {lead['client_name']},\n\nYour Crown Movers service is booked and confirmed. If an emergency means you need to request a date change, we will do our best to accommodate you. At least 10 days' notice is required under the booking terms. A change requested inside 10 days may result in loss of the deposit. A replacement date is subject to availability and rates may change for the new date.\n\nThank you,\nCrown Movers"

def confirm_job(db,q,lead,actor,reason):
    j=one(db,"SELECT * FROM jobs WHERE quote_id=? ORDER BY created_at DESC LIMIT 1",(q["id"],))
    if not j:
        inp=json.loads(q["inputs_json"]);day=inp.get("move_date") or lead.get("move_date")
        if not day:raise HTTPException(409,"Add the service date before confirming the booking.")
        start=datetime.fromisoformat(day+"T09:00:00").replace(tzinfo=MONTREAL).astimezone(timezone.utc);end=start+timedelta(hours=8)
        jid=uid("JOB");insert(db,"jobs",dict(id=jid,lead_id=lead["id"],quote_id=q["id"],title=lead["client_name"],
          phase=inp.get("service_type") or "Moving",start_at=start.isoformat(timespec="seconds"),end_at=end.isoformat(timespec="seconds"),
          status="Confirmed",stops_json=dumps([x for x in [lead.get("pickup"),lead.get("dropoff")] if x]),notes="",
          capacity_reviewed=0,exception_reason=reason,created_by=actor,created_at=now()));j=one(db,"SELECT * FROM jobs WHERE id=?",(jid,))
    else:
        db.execute("UPDATE jobs SET status='Confirmed',exception_reason=?,version=version+1 WHERE id=?",(reason,j["id"]))
    add_task(db,lead["id"],"Assign crew and truck for confirmed service",lead.get("assigned_to",""),"assign:"+j["id"])
    key="booking-confirmed:"+q["id"]
    if lead.get("email") and not one(db,"SELECT id FROM outbound_messages WHERE event_key=?",(key,)):
        queue_message(db,lead["id"],"Booking confirmed | Réservation confirmée",confirmation(lead),"Preparation",actor,key,j["id"],j.get("version",1))
    db.execute("UPDATE leads SET stage='Booked',updated_at=? WHERE id=?",(now(),lead["id"]))
    return j

def register_sales_crm_v6(app,path,auth,public_url):
    schema(path)

    @app.get("/api/crm/clients")
    def clients(q:str="",u=Depends(auth)):
        require_role(u,OFFICE);needle=(q or "").strip().lower();like="%"+needle+"%";digits=norm_phone(needle)
        with transaction(path) as db:
            sync_unlinked_clients(db)
            if needle:
                phone_like="%"+digits+"%" if digits else "%"
                accts=rows(db,"""SELECT DISTINCT a.* FROM client_accounts a
                  WHERE lower(a.client_name||' '||a.email||' '||a.phone) LIKE ?
                     OR replace(replace(replace(replace(replace(a.phone,'(',''),')',''),'-',''),' ',''),'+','') LIKE ?
                     OR EXISTS(
                       SELECT 1 FROM client_account_leads x JOIN leads l ON l.id=x.lead_id
                       WHERE x.client_id=a.id AND lower(COALESCE(l.pickup,'')||' '||COALESCE(l.dropoff,'')||' '||
                         COALESCE(l.home_size,'')||' '||COALESCE(l.notes,'')) LIKE ?)
                  ORDER BY a.updated_at DESC LIMIT 60""",(like,phone_like,like))
            else:
                accts=rows(db,"SELECT * FROM client_accounts ORDER BY updated_at DESC LIMIT 60")
            for a in accts:
                a["services"]=rows(db,"""SELECT l.id,l.move_date,l.pickup,l.dropoff,l.home_size,l.stage,l.lead_type,l.created_at,
                  (SELECT id FROM quotes q WHERE q.lead_id=l.id ORDER BY revision DESC LIMIT 1) quote_id,
                  (SELECT status FROM quotes q WHERE q.lead_id=l.id ORDER BY revision DESC LIMIT 1) quote_status,
                  (SELECT status FROM jobs j WHERE j.lead_id=l.id ORDER BY created_at DESC LIMIT 1) job_status
                  FROM client_account_leads x JOIN leads l ON l.id=x.lead_id WHERE x.client_id=?
                  ORDER BY COALESCE(l.move_date,l.created_at) DESC LIMIT 20""",(a["id"],))
        return accts

    @app.get("/api/crm/clients/{cid}")
    def client(cid:str,u=Depends(auth)):
        require_role(u,OFFICE)
        with closing(connect(path)) as db:
            a=one(db,"SELECT * FROM client_accounts WHERE id=?",(cid,))
            if not a:raise HTTPException(404,"Client not found.")
            a["services"]=rows(db,"""SELECT l.*,
              (SELECT id FROM quotes q WHERE q.lead_id=l.id ORDER BY revision DESC LIMIT 1) quote_id,
              (SELECT status FROM quotes q WHERE q.lead_id=l.id ORDER BY revision DESC LIMIT 1) quote_status,
              (SELECT status FROM jobs j WHERE j.lead_id=l.id ORDER BY created_at DESC LIMIT 1) job_status,
              (SELECT COALESCE(SUM(amount_cents),0) FROM payments p WHERE p.lead_id=l.id AND p.purpose='Deposit' AND p.verified=1) verified_deposit_cents
              FROM client_account_leads x JOIN leads l ON l.id=x.lead_id WHERE x.client_id=?
              ORDER BY COALESCE(l.move_date,l.created_at) DESC""",(cid,))
        return a

    @app.post("/api/crm/clients")
    def new_client(data:dict,u=Depends(auth)):
        require_role(u,SALES);name=str(data.get("client_name","")).strip();email=norm_email(data.get("email"));phone=str(data.get("phone","")).strip()
        if not name:raise HTTPException(422,"Enter the client name.")
        if email and ("@" not in email or "." not in email.rsplit("@",1)[-1]):raise HTTPException(422,"Enter a valid client email.")
        if not email and not norm_phone(phone):raise HTTPException(422,"Enter an email or phone number.")
        key="e:"+email if email else "p:"+norm_phone(phone)
        with transaction(path) as db:
            old=one(db,"SELECT * FROM client_accounts WHERE identity_key=?",(key,))
            if old:return old
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
        return {"client":a,"lead":l,"suggested_hourly_rate":float(rate) if rate is not None else None,"rate_source":source or "",
          "suggested_hours":suggested_hours(l),"materials":mats,
          "defaults":{"local_fuel_per_truck":35.0,"extra_truck_transport":25.0,"long_distance_per_km":0.94,"deposit":300.0,"limited_liability":"$0.60/lb per article"}}

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
            template_key=str(data.get("template_key") or "").strip()
            tmpl=one(db,"SELECT body_json,name FROM crm_quote_templates WHERE template_key=? AND active=1",(template_key,)) if template_key else None
            inp["template_name"]=tmpl["name"] if tmpl else ""
            inp["template_body"]=json.loads(tmpl["body_json"]) if tmpl else {}
            qid=uid("QUOTE");snapshot={k:l[k] for k in l.keys() if k!="raw_json"}
            insert(db,"quotes",dict(id=qid,lead_id=lid,revision=rev,status="Draft",inputs_json=dumps(inp),result_json=dumps(result),
              pricing_id=p["id"],lead_snapshot_json=dumps(snapshot),lead_version=l["version"],created_by=u["id"],created_at=now()))
            db.execute("UPDATE leads SET stage='Quote',updated_at=? WHERE id=?",(now(),lid));audit(db,u["id"],"CRM quote revision created","quotes",qid,{"revision":rev})
            return payload(one(db,"SELECT * FROM quotes WHERE id=?",(qid,)))

    @app.patch("/api/crm/quotes/{qid}")
    def edit_quote(qid:str,data:dict,u=Depends(auth)):
        require_role(u,SALES)
        with transaction(path) as db:
            q=one(db,"SELECT * FROM quotes WHERE id=?",(qid,))
            if not q:raise HTTPException(404,"Estimate not found.")
            if q["status"]!="Draft":raise HTTPException(409,"Only a draft can be edited in place. Create a new revision after an estimate is finalized.")
            if str(data.get("lead_id") or q["lead_id"])!=q["lead_id"]:raise HTTPException(422,"The client/service cannot be changed on an existing draft.")
            l=one(db,"SELECT * FROM leads WHERE id=?",(q["lead_id"],))
            result=calculate(db,data);day=str(data.get("move_date") or l["move_date"])
            if day and day!=l["move_date"]:
                db.execute("UPDATE leads SET move_date=?,version=version+1,updated_at=? WHERE id=?",(day,now(),l["id"]));l=one(db,"SELECT * FROM leads WHERE id=?",(l["id"],))
            inp=dict(data);inp["deposit_required"]=float(data.get("deposit_required",300) or 0);inp["limited_liability"]="$0.60/lb per article"
            template_key=str(data.get("template_key") or "").strip()
            tmpl=one(db,"SELECT body_json,name FROM crm_quote_templates WHERE template_key=? AND active=1",(template_key,)) if template_key else None
            inp["template_name"]=tmpl["name"] if tmpl else ""
            inp["template_body"]=json.loads(tmpl["body_json"]) if tmpl else {}
            snapshot={k:l[k] for k in l.keys() if k!="raw_json"}
            db.execute("UPDATE quotes SET inputs_json=?,result_json=?,lead_snapshot_json=?,lead_version=? WHERE id=?",
                       (dumps(inp),dumps(result),dumps(snapshot),l["version"],qid))
            db.execute("UPDATE leads SET stage='Quote',updated_at=? WHERE id=?",(now(),l["id"]))
            audit(db,u["id"],"CRM draft estimate edited","quotes",qid,{"revision":q["revision"]})
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
            invite_url=public_url+"/portal#access="+raw;r=json.loads(q["result_json"]);fr=str(l["language"]).lower().startswith("fr")
            subject="Votre devis Crown Movers est prêt" if fr else "Your Crown Movers estimate is ready"
            body=(f"Bonjour {l['client_name']},\n\nVotre devis Crown Movers est prêt. Estimation avant taxes : {r['low_cents']/100:.2f} $ à {r['high_cents']/100:.2f} $ CA.\n\nConsultez et acceptez votre devis ici :\n{invite_url}\n\nAprès l'acceptation, vous verrez les options de dépôt applicables.\n\nMerci,\nCrown Movers" if fr else
              f"Hi {l['client_name']},\n\nYour Crown Movers estimate is ready. Estimated range before tax: CAD {r['low_cents']/100:.2f} to CAD {r['high_cents']/100:.2f}.\n\nView and accept your estimate here:\n{invite_url}\n\nAfter acceptance, the available deposit options will appear.\n\nThank you,\nCrown Movers")
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
            l=one(db,"SELECT * FROM leads WHERE id=?",(q["lead_id"],));amount=float(json.loads(q["inputs_json"]).get("deposit_required",300));fr=str(l["language"]).lower().startswith("fr")
            subject="Rappel amical - dépôt Crown Movers" if fr else "Friendly reminder - Crown Movers deposit"
            body=(f"Bonjour {l['client_name']},\n\nPetit rappel amical : le dépôt de {amount:.2f} $ CA lié à votre réservation est toujours en attente. Si vous l'avez déjà envoyé, vous pouvez ignorer ce message. Sinon, répondez-nous si vous avez besoin des instructions de paiement.\n\nMerci,\nCrown Movers" if fr else
              f"Hi {l['client_name']},\n\nA friendly reminder that the CAD {amount:.2f} deposit for your booking is still pending. If you already sent it, please disregard this message. Otherwise, reply if you need the payment instructions.\n\nThank you,\nCrown Movers")
            m=queue_message(db,l["id"],subject,body,"Follow-up",u["id"],"deposit-reminder:"+qid+":"+now()[:10])
        return {"id":m["id"],"status":m["status"]}

    @app.get("/api/crm/payment-settings")
    def get_payment_settings(u=Depends(auth)):
        require_role(u,OFFICE)
        import os
        with closing(connect(path)) as db:cfg=settings(db,"crm_payment_settings",{}) or {}
        return {"etransfer_email":str(cfg.get("etransfer_email") or ""),"etransfer_note":str(cfg.get("etransfer_note") or ""),
                "card_configured":bool(os.getenv("SQUARE_TOKEN") and os.getenv("SQUARE_LOCATION_ID"))}

    @app.post("/api/crm/payment-settings")
    def set_payment_settings(data:dict,u=Depends(auth)):
        require_role(u,{"owner"})
        email=norm_email(data.get("etransfer_email"));note=str(data.get("etransfer_note") or "").strip()[:500]
        if email and ("@" not in email or "." not in email.rsplit("@",1)[-1]):raise HTTPException(422,"Enter a valid Interac e-Transfer recipient email.")
        with transaction(path) as db:
            put_setting(db,"crm_payment_settings",{"etransfer_email":email,"etransfer_note":note})
            audit(db,u["id"],"Client deposit instructions updated","settings","crm_payment_settings",{"has_etransfer_email":bool(email)})
        return {"ok":True}

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
