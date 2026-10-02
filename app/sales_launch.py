"""CrownOps v5.7 sales-launch workflow: client CRM, simple quotes and booking handoff."""
from contextlib import closing
from datetime import datetime,timedelta,timezone
from decimal import Decimal,ROUND_HALF_UP
from zoneinfo import ZoneInfo
import json,re,secrets
from fastapi import Depends,HTTPException
from .store import connect,transaction,rows,one,insert,audit,now,uid,dumps
from .platform_common import queue_message,stamp_hash,settings,put_setting
from .pricing import seasonal_rate,cents

SALES={"owner","sales"}
D=Decimal
LOCAL_GAS=D("35")
EXTRA_TRUCK=D("25")
KM_RATE=D("0.94")
PROTECTION=D("35")
JUNK={
 "single":("Single item disposal",D("175")),
 "quarter":("1/4 truck - approx. 5 ft",D("400")),
 "half":("1/2 truck - approx. 10 ft",D("800")),
 "three_quarter":("3/4 truck - approx. 15 ft",D("1200")),
 "full":("Full truck - 20 ft container",D("1600")),
}
TEMPLATES=[
 {"key":"local-en","name":"Local moving - English","language":"English","service_kind":"Moving - Local"},
 {"key":"local-fr","name":"Demenagement local - Francais","language":"French","service_kind":"Moving - Local"},
 {"key":"long-en","name":"Long-distance moving - English","language":"English","service_kind":"Moving - Long Distance","blank":True},
 {"key":"long-fr","name":"Demenagement longue distance - Francais","language":"French","service_kind":"Moving - Long Distance","blank":True},
 {"key":"packing-en","name":"Packing - English","language":"English","service_kind":"Packing"},
 {"key":"packing-fr","name":"Emballage - Francais","language":"French","service_kind":"Packing"},
 {"key":"junk-en","name":"Junk removal - English","language":"English","service_kind":"Junk Removal"},
 {"key":"junk-fr","name":"Enlevement - Francais","language":"French","service_kind":"Junk Removal"},
]

def req(u):
 if u["role"] not in SALES: raise HTTPException(403,"Your role does not allow sales quoting.")

def dec(v,default="0"):
 try:d=D(str(v if v not in (None,"") else default))
 except Exception:raise HTTPException(422,"Enter a valid numeric amount.")
 if not d.is_finite():raise HTTPException(422,"Enter a finite numeric amount.")
 return d

def money(v):return int((D(str(v or 0))*100).quantize(D("1"),rounding=ROUND_HALF_UP))
def norm_email(v):return str(v or "").strip().lower()
def norm_phone(v):
 s=re.sub(r"\D","",str(v or ""))
 return s[-10:] if len(s)>=10 else s

def contact_for_lead(db,l):
 c=one(db,"SELECT c.* FROM crm_contacts c JOIN crm_contact_leads m ON m.contact_id=c.id WHERE m.lead_id=?",(l["id"],))
 if c:return c
 e,p=norm_email(l["email"]),norm_phone(l["phone"])
 c=one(db,"SELECT * FROM crm_contacts WHERE email_norm=? ORDER BY updated_at DESC LIMIT 1",(e,)) if e else None
 if not c and p:c=one(db,"SELECT * FROM crm_contacts WHERE phone_norm=? ORDER BY updated_at DESC LIMIT 1",(p,))
 if not c:
  c={"id":uid("CLIENT"),"client_name":l["client_name"],"email":l["email"],"email_norm":e,"phone":l["phone"],"phone_norm":p,"language":l["language"],"notes":"","created_at":l["created_at"],"updated_at":l["updated_at"]}
  insert(db,"crm_contacts",c)
 insert(db,"crm_contact_leads",{"contact_id":c["id"],"lead_id":l["id"],"created_at":now()})
 return c

def sync_contacts(db):
 for l in rows(db,"SELECT * FROM leads ORDER BY created_at"):contact_for_lead(db,l)

def service_summary(db,l):
 q=one(db,"SELECT * FROM quotes WHERE lead_id=? ORDER BY revision DESC LIMIT 1",(l["id"],))
 j=one(db,"SELECT * FROM jobs WHERE lead_id=? ORDER BY created_at DESC LIMIT 1",(l["id"],))
 i=json.loads(q["inputs_json"]) if q else {};r=json.loads(q["result_json"]) if q else {}
 return {"id":l["id"],"move_date":l["move_date"],"pickup":l["pickup"],"dropoff":l["dropoff"],"home_size":l["home_size"],"stage":l["stage"],
  "service_kind":i.get("service_kind") or (l["lead_type"] if l["lead_type"] not in ("Form","Email","Manual") else "Moving - Local"),
  "quote":None if not q else {"id":q["id"],"revision":q["revision"],"status":q["status"],"low_cents":r.get("low_cents",0),"high_cents":r.get("high_cents",0)},
  "job":None if not j else {"id":j["id"],"status":j["status"],"start_at":j["start_at"]},"updated_at":l["updated_at"]}

def rate_for(db,p,move_date,movers):
 rate,label=seasonal_rate(json.loads(p["rates_json"]),move_date,movers)
 a=rows(db,"SELECT * FROM annual_rate_periods WHERE active=1 AND start_date<=? AND end_date>=? ORDER BY version DESC",(move_date,move_date))
 if len(a)>1:raise HTTPException(422,"This date matches more than one managed rate period.")
 if a:rate=D(a[0]["rate_"+str(movers)+"_cents"])/100;label=a[0]["label"]
 daily=one(db,"SELECT * FROM daily_rates WHERE service_date=? AND movers=? ORDER BY version DESC LIMIT 1",(move_date,movers))
 if daily:rate=D(daily["hourly_cents"])/100;label="Daily override "+move_date
 return rate,label

def suggested_hours(l,movers):
 s=str(l["home_size"] or "").lower();lo,hi=D("4"),D("7")
 for keys,rng in [
  (("studio","bachelor"),(D("2.5"),D("4"))),
  (("1 bedroom","1-bedroom","1 bed"),(D("3"),D("5"))),
  (("2 bedroom","2-bedroom","2 bed"),(D("4"),D("6"))),
  (("3 bedroom","3-bedroom","3 bed"),(D("5"),D("8"))),
  (("4 bedroom","4-bedroom","4 bed"),(D("6"),D("9")))]:
  if any(k in s for k in keys):lo,hi=rng;break
 nums=[int(x) for x in re.findall(r"\d+",str(l["boxes"] or ""))]
 if nums and max(nums)>=50:lo+=D(".5");hi+=D("1")
 access=" ".join(str(l[k] or "").lower() for k in ("pickup_access","dropoff_access","pickup_walk","dropoff_walk"))
 if any(x in access for x in ("stairs","flight","long walk")):lo+=D(".5");hi+=D("1")
 if movers>=4:lo=max(D("2.5"),lo-D(".5"));hi=max(lo,hi-D(".5"))
 return lo,hi

def material_default(db,term):
 r=one(db,"SELECT sell_cents FROM inventory WHERE lower(name) LIKE ? AND sell_cents>0 ORDER BY sell_cents LIMIT 1",("%"+term+"%",))
 return D(r["sell_cents"])/100 if r else D("0")

def calculate(db,l,d):
 kind=str(d.get("service_kind") or "Moving - Local")
 date=str(d.get("move_date") or l["move_date"] or "")
 if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",date):raise HTTPException(422,"Choose the service date first.")
 language=str(d.get("language") or l["language"] or "English")
 fr=language.lower().startswith("fr")
 key=str(d.get("template_key") or (("long-fr" if fr else "long-en") if kind=="Moving - Long Distance" else ("packing-fr" if fr else "packing-en") if kind=="Packing" else ("junk-fr" if fr else "junk-en") if kind=="Junk Removal" else ("local-fr" if fr else "local-en")))
 deposit=float(dec(d.get("deposit_required"),"300"))
 if kind=="Junk Removal":
  code=str(d.get("junk_package") or "quarter")
  if code not in JUNK:raise HTTPException(422,"Choose a junk-removal package.")
  name,base=JUNK[code];blocks=dec(d.get("junk_labor_blocks"));other=dec(d.get("other_amount"));discount=dec(d.get("discount_amount"))
  total=base+blocks*80+other-discount
  if total<0:raise HTTPException(422,"Discount exceeds this quote.")
  result={"currency":"CAD","before_tax":True,"hourly_rate_cents":0,"service_low":0,"service_high":0,"minimum_hours":0,"transport_hours":0,"transport_cents":0,"fuel_cents":0,"materials_cents":0,"other_cents":money(other),"discount_cents":money(discount),"service_low_cents":money(base+blocks*80),"service_high_cents":money(base+blocks*80),"low_cents":money(total),"high_cents":money(total),"movers":2,"trucks":1,"season":"Junk removal guide","service_kind":kind,"template_key":key,"line_items":[{"code":"junk","name":name,"qty":1,"unit_cents":money(base),"total_cents":money(base)},{"code":"labor","name":"Extra on-site labor - 2 crew / 30 minutes","qty":float(blocks),"unit_cents":8000,"total_cents":money(blocks*80)}],"tax_note":"Before applicable taxes.","liability_note":"Junk removal pricing is separate from moving-service limited liability coverage."}
  inputs=dict(d);inputs.update({"service_kind":kind,"move_date":date,"language":language,"template_key":key,"movers":2,"trucks":1,"start_time":str(d.get("start_time") or "09:00")[:5],"deposit_required":deposit})
  return inputs,result,{"hours_low":0,"hours_high":0,"standard_rate_cents":0}
 movers=int(dec(d.get("movers"),"2"));trucks=int(dec(d.get("trucks"),"1"))
 if movers<2 or movers>6 or trucks<0 or trucks>4:raise HTTPException(422,"Review the mover and truck count.")
 if kind.startswith("Moving") and trucks<1:raise HTTPException(422,"A moving quote needs at least one truck.")
 p=one(db,"SELECT * FROM pricing_versions WHERE id=?",(d.get("pricing_id"),)) if d.get("pricing_id") else one(db,"SELECT * FROM pricing_versions ORDER BY created_at DESC LIMIT 1")
 if not p:raise HTTPException(422,"Load the rate calendar before quoting.")
 standard,label=rate_for(db,p,date,movers);override=dec(d.get("hourly_rate_override"));rate=override if override>0 else standard
 slo,shi=suggested_hours(l,movers);lo=dec(d.get("hours_low"),str(slo));hi=dec(d.get("hours_high"),str(shi));minimum=dec(d.get("minimum_hours"),"3")
 if lo<=0 or hi<lo:raise HTTPException(422,"Review the estimated service hours.")
 lo=max(lo,minimum);hi=max(hi,minimum)
 mode=str(d.get("transport_mode") or "hours");th=dec(d.get("transport_hours"),"1")
 transport=dec(d.get("transport_amount")) if mode=="fixed" else th*rate+EXTRA_TRUCK*max(0,trucks-1)
 km=dec(d.get("distance_km"));kmrate=dec(d.get("km_rate"),str(KM_RATE));fuel_override=dec(d.get("fuel_amount"))
 if fuel_override>0:fuel=fuel_override
 elif kind=="Moving - Long Distance":
  if km<=0:raise HTTPException(422,"Enter round-trip route kilometres or a fixed fuel amount.")
  fuel=km*kmrate*max(1,trucks)
 else:fuel=LOCAL_GAS*max(1,trucks)
 plastic=dec(d.get("plastic_qty"));bubble=dec(d.get("bubble_qty"));tv=dec(d.get("tv_qty"));mirror=dec(d.get("mirror_qty"));fragile=dec(d.get("fragile_qty"))
 pu=dec(d.get("plastic_unit"),str(material_default(db,"plastic") or material_default(db,"mattress")));bu=dec(d.get("bubble_unit"),str(material_default(db,"bubble")));tvu=dec(d.get("tv_unit"),"35");mu=dec(d.get("mirror_unit"),"35");fu=dec(d.get("fragile_unit"),"35")
 materials=plastic*pu+bubble*bu+tv*tvu+mirror*mu+fragile*fu+dec(d.get("materials_other"));other=dec(d.get("other_amount"));discount=dec(d.get("discount_amount"))
 low=lo*rate+transport+fuel+materials+other-discount;high=hi*rate+transport+fuel+materials+other-discount
 if low<0:raise HTTPException(422,"Discount exceeds the low estimate.")
 lines=[{"code":"service","name":str(movers)+" Movers"+((" + 20 Foot Truck" if trucks==1 else " + "+str(trucks)+" Trucks") if kind.startswith("Moving") else ""),"qty":float(lo),"unit_cents":cents(rate),"total_cents":money(lo*rate)},{"code":"transport","name":"Transportation Fee","qty":1,"unit_cents":money(transport),"total_cents":money(transport)},{"code":"fuel","name":"Long-distance route / fuel" if kind=="Moving - Long Distance" else "Gas Fee","qty":1,"unit_cents":money(fuel),"total_cents":money(fuel)}]
 for code,name,qty,unit in [("plastic","Plastic protection",plastic,pu),("bubble","Bubble wrap",bubble,bu),("tv","TV protection package",tv,tvu),("mirror","Large mirror protection package",mirror,mu),("fragile","Fragile glass / table protection package",fragile,fu)]:
  if qty:lines.append({"code":code,"name":name,"qty":float(qty),"unit_cents":money(unit),"total_cents":money(qty*unit)})
 result={"currency":"CAD","before_tax":True,"hourly_rate_cents":cents(rate),"standard_hourly_rate_cents":cents(standard),"manual_rate_override":bool(override>0 and override!=standard),"service_low":float(lo),"service_high":float(hi),"minimum_hours":float(minimum),"transport_hours":float(th if mode!="fixed" else 0),"transport_mode":mode,"transport_cents":money(transport),"fuel_cents":money(fuel),"distance_km":float(km),"km_rate_cents":money(kmrate),"materials_cents":money(materials),"other_cents":money(other),"discount_cents":money(discount),"service_low_cents":money(lo*rate),"service_high_cents":money(hi*rate),"low_cents":money(low),"high_cents":money(high),"movers":movers,"trucks":trucks,"season":label,"pricing_id":p["id"],"service_kind":kind,"template_key":key,"line_items":lines,"tax_note":"Before applicable taxes. Quebec GST/QST is applied through the reviewed accounting mapping.","liability_note":"Free limited liability coverage: $0.60/lb per article. TVs, mirrors and fragile glass require appropriate protection/boxing. Higher-value protection requires separate insurance terms."}
 inputs=dict(d);inputs.update({"service_kind":kind,"move_date":date,"language":language,"template_key":key,"movers":movers,"trucks":trucks,"start_time":str(d.get("start_time") or "09:00")[:5],"pricing_id":p["id"],"deposit_required":deposit})
 return inputs,result,{"hours_low":float(slo),"hours_high":float(shi),"standard_rate_cents":cents(standard),"rate_label":label}

def essential_missing(l):
 return [label for k,label in {"client_name":"client name","email":"email","phone":"phone","move_date":"move date","pickup":"pickup address","dropoff":"destination address","home_size":"move size"}.items() if not str(l[k] or "").strip()]

def portal_link(db,l,actor,public_url):
 db.execute("UPDATE portal_invites SET revoked=1 WHERE lead_id=? AND used_at IS NULL",(l["id"],))
 raw=secrets.token_urlsafe(32);insert(db,"portal_invites",{"token_hash":stamp_hash(raw),"lead_id":l["id"],"email":l["email"],"expires_at":(datetime.now(timezone.utc)+timedelta(hours=72)).isoformat(timespec="seconds"),"created_by":actor,"created_at":now()})
 return public_url+"/portal#access="+raw

def estimate_mail(l,q,url):
 r=json.loads(q["result_json"]);i=json.loads(q["inputs_json"]);fr=str(i.get("language") or l["language"]).lower().startswith("fr")
 low="CA$"+f"{r['low_cents']/100:,.2f}";high="CA$"+f"{r['high_cents']/100:,.2f}";amount=low if low==high else low+" - "+high
 if fr:return "Votre devis Crown Movers est pret",f"Bonjour {l['client_name']},\n\nVotre devis {amount} avant taxes pour le {l['move_date']} est pret.\n\nConsultez le devis complet et acceptez-le ici :\n{url}\n\nUne fois accepte, les options de depot seront affichees. La reservation devient confirmee lorsque le depot est verifie ou qu'un gestionnaire approuve une autre entente de paiement.\n\nRepondez simplement a ce courriel si quelque chose doit etre ajuste.\n\nMerci,\nCrown Movers\n514-606-4030"
 return "Your Crown Movers estimate is ready",f"Hi {l['client_name']},\n\nYour estimate of {amount} before tax for {l['move_date']} is ready.\n\nView the full estimate and accept it here:\n{url}\n\nAfter acceptance, the deposit options will appear. The booking becomes confirmed once the deposit is verified or a manager approves another payment arrangement.\n\nJust reply to this email if anything needs to be adjusted.\n\nThank you,\nCrown Movers\n514-606-4030"

def ensure_tentative_job(db,qid,actor="system"):
 j=one(db,"SELECT * FROM jobs WHERE quote_id=? ORDER BY created_at LIMIT 1",(qid,))
 if j:return j
 q=one(db,"SELECT * FROM quotes WHERE id=?",(qid,))
 if not q or q["status"]!="Accepted":return None
 l=one(db,"SELECT * FROM leads WHERE id=?",(q["lead_id"],));i=json.loads(q["inputs_json"]);r=json.loads(q["result_json"]);z=ZoneInfo("America/Toronto")
 try:start=datetime.fromisoformat((i.get("move_date") or l["move_date"])+"T"+str(i.get("start_time") or "09:00")).replace(tzinfo=z)
 except Exception:start=datetime.fromisoformat(l["move_date"]+"T09:00").replace(tzinfo=z)
 end=start+timedelta(hours=max(2,float(r.get("service_high") or 3)));phase=i.get("service_kind") or "Moving - Local"
 j={"id":uid("JOB"),"lead_id":l["id"],"quote_id":qid,"title":phase+" | "+l["client_name"],"phase":phase,"start_at":start.astimezone(timezone.utc).isoformat(timespec="seconds"),"end_at":end.astimezone(timezone.utc).isoformat(timespec="seconds"),"status":"Tentative","stops_json":dumps([x for x in (l["pickup"],l["dropoff"]) if x]),"notes":"Accepted estimate. Deposit / payment arrangement pending. Crew and truck assignment still required.","capacity_reviewed":0,"exception_reason":"","created_by":actor,"created_at":now()}
 insert(db,"jobs",j);audit(db,actor,"Accepted estimate added to schedule as tentative","jobs",j["id"],{"quote_id":qid});return j

def queue_booking(db,q,l,actor):
 key="booking-confirmation:"+q["id"]
 if one(db,"SELECT id FROM outbound_messages WHERE event_key=?",(key,)):return
 fr=str(json.loads(q["inputs_json"]).get("language") or l["language"]).lower().startswith("fr")
 if fr:subject="Votre service est reserve | Crown Movers";body=f"Bonjour {l['client_name']},\n\nVotre service du {l['move_date']} est maintenant reserve.\n\nSi une urgence exige un changement de date, avisez-nous le plus tot possible. Nous demandons au moins 10 jours de preavis. Un changement a moins de 10 jours peut entrainer la perte du depot; la nouvelle date reste selon les disponibilites et le tarif peut changer. Nous ferons notre possible pour trouver la meilleure option disponible.\n\nMerci,\nCrown Movers"
 else:subject="You're booked with Crown Movers";body=f"Hi {l['client_name']},\n\nYour service for {l['move_date']} is now booked and ready to go.\n\nIf an emergency requires a date change, please tell us as early as possible. We require at least 10 days' notice. A change inside 10 days may result in loss of the deposit; a replacement date is subject to availability and the rate may change. We'll do our best to find the best available option.\n\nThank you,\nCrown Movers"
 queue_message(db,l["id"],subject,body,"Booking confirmation",actor,key)

def confirm_booking_after_deposit(db,qid,actor="system",exception_reason=""):
 q=one(db,"SELECT * FROM quotes WHERE id=?",(qid,))
 if not q or q["status"]!="Accepted":return None
 l=one(db,"SELECT * FROM leads WHERE id=?",(q["lead_id"],));j=ensure_tentative_job(db,qid,actor)
 if not j:return None
 if exception_reason:db.execute("UPDATE jobs SET status='Confirmed',exception_reason=?,version=version+1 WHERE id=?",(exception_reason,j["id"]))
 else:db.execute("UPDATE jobs SET status='Confirmed',version=version+1 WHERE id=?",(j["id"],))
 db.execute("UPDATE leads SET stage='Booked',updated_at=?,version=version+1 WHERE id=?",(now(),l["id"]))
 if not one(db,"SELECT id FROM tasks WHERE lead_id=? AND title='Assign crew and truck' AND status!='Done'",(l["id"],)):
  insert(db,"tasks",{"id":uid("TASK"),"lead_id":l["id"],"title":"Assign crew and truck","due_date":l["move_date"] or now()[:10],"owner":"","status":"Open","historical":0,"raw_json":"{}","created_at":now()})
 queue_booking(db,q,l,actor);audit(db,actor,"Booking confirmed","jobs",j["id"],{"quote_id":qid,"payment_exception":bool(exception_reason)});return j

def seed_sales_launch_defaults(path):
 with transaction(path) as db:
  if settings(db,"client_terms") is None:
   put_setting(db,"client_terms",{
    "approved":True,
    "version":"Crown booking terms 2026-10",
    "text":"CROWN MOVERS BOOKING TERMS / CONDITIONS DE RESERVATION\n\nENGLISH\nYour estimate is based on the service details currently provided. The service is confirmed after the estimate is accepted and the required deposit is verified, unless a Crown Movers manager approves another payment arrangement. Free limited liability coverage is $0.60/lb per article. TVs, large mirrors, glass tables, mantels and other fragile items require appropriate protection/boxing; any higher-value coverage is subject to separate accepted insurance terms. If an emergency requires a date change, please provide at least 10 days notice. A change inside 10 days may result in loss of the deposit. Replacement dates are subject to availability and rates may change based on the new date. Crown Movers will make reasonable efforts to find the best available alternative.\n\nFRANCAIS\nVotre estimation est basee sur les renseignements actuellement fournis. Le service est confirme apres l'acceptation du devis et la verification du depot requis, sauf si un gestionnaire de Crown Movers approuve une autre entente de paiement. La responsabilite limitee incluse est de 0,60 $/lb par article. Les televiseurs, grands miroirs, tables en verre, manteaux de foyer et autres articles fragiles exigent une protection/boite appropriee; toute protection de valeur superieure est assujettie a des conditions d'assurance acceptees separement. Si une urgence exige un changement de date, veuillez donner au moins 10 jours de preavis. Un changement a moins de 10 jours peut entrainer la perte du depot. Les nouvelles dates sont selon les disponibilites et les tarifs peuvent changer selon la nouvelle date. Crown Movers fera des efforts raisonnables pour trouver la meilleure option disponible.",
    "review_note":"Operational terms supplied by Crown Movers for v5.7 launch review."
   })
  if settings(db,"automation_policy") is None:
   put_setting(db,"automation_policy",{"enabled":True,"preparation":True,"review_request":True,"invoice_followup":True,"fleet_tasks":False,"review_url":"","review_note":"Review requests are neutral and sent to all eligible completed jobs once a public review URL is configured."})


def register_sales_launch(app,path,auth,new_lead,public_url):
 @app.get("/api/launch/templates")
 def templates(u=Depends(auth)):
  req(u)
  with closing(connect(path)) as db:plastic=material_default(db,"plastic") or material_default(db,"mattress");bubble=material_default(db,"bubble")
  return {"templates":TEMPLATES,"junk_pricing":{k:{"name":v[0],"price_cents":money(v[1])} for k,v in JUNK.items()},"defaults":{"local_gas_per_truck_cents":3500,"extra_truck_transport_cents":2500,"long_distance_per_km_cents":94,"tv_protection_cents":3500,"fragile_protection_cents":3500,"plastic_unit_cents":money(plastic),"bubble_unit_cents":money(bubble),"liability":"Free limited liability coverage: $0.60/lb per article."}}

 @app.get("/api/launch/rate")
 def quote_rate(move_date:str,movers:int,lead_id:str="",service_kind:str="Moving - Local",u=Depends(auth)):
  req(u)
  if not re.fullmatch(r"\\d{4}-\\d{2}-\\d{2}",move_date):raise HTTPException(422,"Choose a valid service date.")
  if movers<2 or movers>6:raise HTTPException(422,"Choose 2 to 6 movers.")
  with closing(connect(path)) as db:
   p=one(db,"SELECT * FROM pricing_versions ORDER BY created_at DESC LIMIT 1")
   if not p:raise HTTPException(422,"Load the rate calendar before quoting.")
   rate,label=rate_for(db,p,move_date,movers)
   lead=one(db,"SELECT * FROM leads WHERE id=?",(lead_id,)) if lead_id else None
   lo,hi=suggested_hours(lead,movers) if lead else (D("0"),D("0"))
  return {"hourly_rate_cents":cents(rate),"label":label,"suggested_hours_low":float(lo),"suggested_hours_high":float(hi)}

 @app.get("/api/launch/clients")
 def clients(q:str="",limit:int=60,u=Depends(auth)):
  req(u);limit=min(max(limit,1),100)
  with transaction(path) as db:
   sync_contacts(db);term="%"+q[:200]+"%"
   cs=rows(db,"""SELECT DISTINCT c.* FROM crm_contacts c LEFT JOIN crm_contact_leads m ON m.contact_id=c.id LEFT JOIN leads l ON l.id=m.lead_id
    WHERE ?='' OR c.client_name LIKE ? OR c.email LIKE ? OR c.phone LIKE ? OR l.pickup LIKE ? OR l.dropoff LIKE ? ORDER BY c.updated_at DESC LIMIT ?""",(q,term,term,term,term,term,limit))
   out=[]
   for c in cs:
    ls=rows(db,"SELECT l.* FROM leads l JOIN crm_contact_leads m ON m.lead_id=l.id WHERE m.contact_id=? ORDER BY l.updated_at DESC",(c["id"],));sv=[service_summary(db,l) for l in ls]
    out.append({"id":c["id"],"client_name":c["client_name"],"email":c["email"],"phone":c["phone"],"language":c["language"],"service_count":len(sv),"latest_service":sv[0] if sv else None,"services":sv[:3]})
  return out

 @app.post("/api/launch/clients")
 def create_client(d:dict,u=Depends(auth)):
  req(u);name=str(d.get("client_name") or "").strip();email=norm_email(d.get("email"));phone=str(d.get("phone") or "").strip()
  if not name:raise HTTPException(422,"Enter the client's name.")
  with transaction(path) as db:
   old=one(db,"SELECT * FROM crm_contacts WHERE email_norm=? AND email_norm!='' LIMIT 1",(email,)) if email else None
   if not old and norm_phone(phone):old=one(db,"SELECT * FROM crm_contacts WHERE phone_norm=? AND phone_norm!='' LIMIT 1",(norm_phone(phone),))
   if old:return {"id":old["id"],"existing":True}
   c={"id":uid("CLIENT"),"client_name":name,"email":email,"email_norm":email,"phone":phone,"phone_norm":norm_phone(phone),"language":str(d.get("language") or "English"),"notes":"","created_at":now(),"updated_at":now()}
   insert(db,"crm_contacts",c);audit(db,u["id"],"CRM client created","crm_contacts",c["id"])
  return {"id":c["id"],"existing":False}

 @app.get("/api/launch/clients/{cid}")
 def client_detail(cid:str,u=Depends(auth)):
  req(u)
  with transaction(path) as db:
   sync_contacts(db);c=one(db,"SELECT * FROM crm_contacts WHERE id=?",(cid,))
   if not c:raise HTTPException(404,"Client not found.")
   ls=rows(db,"SELECT l.* FROM leads l JOIN crm_contact_leads m ON m.lead_id=l.id WHERE m.contact_id=? ORDER BY l.updated_at DESC",(cid,))
   return {"client":{k:c[k] for k in ("id","client_name","email","phone","language","notes")},"services":[service_summary(db,l) for l in ls]}

 @app.get("/api/launch/client-for-lead/{lid}")
 def lead_client(lid:str,u=Depends(auth)):
  req(u)
  with transaction(path) as db:
   l=one(db,"SELECT * FROM leads WHERE id=?",(lid,))
   if not l:raise HTTPException(404,"Client service not found.")
   return {"client_id":contact_for_lead(db,l)["id"]}

 @app.post("/api/launch/clients/{cid}/services")
 def add_service(cid:str,d:dict,u=Depends(auth)):
  req(u)
  with transaction(path) as db:
   c=one(db,"SELECT * FROM crm_contacts WHERE id=?",(cid,))
   if not c:raise HTTPException(404,"Client not found.")
   p={"client_name":c["client_name"],"email":c["email"],"phone":c["phone"],"language":c["language"],"source":"Manual - returning client","lead_type":str(d.get("service_kind") or "Moving - Local"),"stage":"New Lead","move_date":str(d.get("move_date") or ""),"pickup":str(d.get("pickup") or ""),"dropoff":str(d.get("dropoff") or ""),"home_size":str(d.get("home_size") or ""),"boxes":str(d.get("boxes") or ""),"inventory":str(d.get("inventory") or ""),"notes":str(d.get("notes") or "")}
   l=new_lead(db,p,u["id"],suppressed=0);insert(db,"crm_contact_leads",{"contact_id":cid,"lead_id":l["id"],"created_at":now()});db.execute("UPDATE crm_contacts SET updated_at=? WHERE id=?",(now(),cid))
  return {"lead_id":l["id"]}

 @app.post("/api/launch/quotes/calculate")
 def calc_quote(d:dict,u=Depends(auth)):
  req(u)
  with closing(connect(path)) as db:
   l=one(db,"SELECT * FROM leads WHERE id=?",(d.get("lead_id"),))
   if not l:raise HTTPException(404,"Client service not found.")
   i,r,s=calculate(db,l,d)
  return {"inputs":i,"result":r,"suggestions":s}

 @app.post("/api/launch/quotes")
 def save_quote(d:dict,u=Depends(auth)):
  req(u)
  with transaction(path) as db:
   l=one(db,"SELECT * FROM leads WHERE id=?",(d.get("lead_id"),))
   if not l:raise HTTPException(404,"Client service not found.")
   if int(d.get("lead_version") or l["version"])!=l["version"]:raise HTTPException(409,"Client details changed. Reload before saving.")
   i,r,_=calculate(db,l,d);rev=db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM quotes WHERE lead_id=?",(l["id"],)).fetchone()[0]
   snap={k:l.get(k,"") for k in ("client_name","email","phone","move_date","pickup","dropoff","home_size","boxes","pickup_access","dropoff_access","pickup_walk","dropoff_walk","inventory","heavy_items","photo_link","language","source","lead_type","stage","assigned_to","priority","notes")}
   rec={"id":uid("QUOTE"),"lead_id":l["id"],"revision":rev,"status":"Draft","inputs_json":dumps(i),"result_json":dumps(r),"pricing_id":r.get("pricing_id") or one(db,"SELECT id FROM pricing_versions ORDER BY created_at DESC LIMIT 1")["id"],"lead_snapshot_json":dumps(snap),"lead_version":l["version"],"created_by":u["id"],"created_at":now()}
   insert(db,"quotes",rec);audit(db,u["id"],"Sales estimate revision saved","quotes",rec["id"],{"revision":rev})
  return {"id":rec["id"],"revision":rev,"status":"Draft","result":r,"inputs":i}

 @app.post("/api/launch/quotes/{qid}/finalize")
 def finalize(qid:str,d:dict,u=Depends(auth)):
  req(u)
  with transaction(path) as db:
   q=one(db,"SELECT * FROM quotes WHERE id=?",(qid,))
   if not q or q["status"] not in ("Draft","Approved"):raise HTTPException(409,"This estimate cannot be finalized.")
   l=one(db,"SELECT * FROM leads WHERE id=?",(q["lead_id"],));missing=essential_missing(l)
   if missing:raise HTTPException(422,"Complete these client details before sending: "+", ".join(missing))
   if l["version"]!=q["lead_version"]:raise HTTPException(409,"Client details changed. Save a new revision.")
   if q["status"]=="Draft":db.execute("UPDATE quotes SET status='Approved',approved_by=?,approved_at=? WHERE id=?",(u["id"],now(),qid));q["status"]="Approved"
   db.execute("UPDATE leads SET notifications_suppressed=0,stage='Quote Sent',updated_at=? WHERE id=?",(now(),l["id"]))
   url=portal_link(db,l,u["id"],public_url);subject,body=estimate_mail(l,q,url);key="estimate-ready:"+qid
   old=one(db,"SELECT id,status FROM outbound_messages WHERE event_key=?",(key,))
   mid=old["id"] if old and old["status"] in ("Draft","Approved") else queue_message(db,l["id"],subject,body,"Estimate",u["id"],key)["id"]
   audit(db,u["id"],"Estimate finalized for client delivery","quotes",qid,{"outbound_message":mid})
  return {"ok":True,"status":"Approved","message_id":mid,"portal_url":url,"sent":False}

 @app.post("/api/launch/quotes/{qid}/deposit-action")
 def deposit_action(qid:str,d:dict,u=Depends(auth)):
  req(u);action=str(d.get("action") or "")
  with transaction(path) as db:
   q=one(db,"SELECT * FROM quotes WHERE id=?",(qid,))
   if not q or q["status"]!="Accepted":raise HTTPException(409,"The client must accept the estimate first.")
   l=one(db,"SELECT * FROM leads WHERE id=?",(q["lead_id"],))
   if action=="reminder":
    url=portal_link(db,l,u["id"],public_url);fr=l["language"].lower().startswith("fr");subject="Rappel de depot | Crown Movers" if fr else "Deposit reminder | Crown Movers";body=(f"Bonjour {l['client_name']},\n\nPetit rappel amical: le depot est toujours requis pour confirmer la reservation.\n\n{url}\n\nMerci,\nCrown Movers" if fr else f"Hi {l['client_name']},\n\nA friendly reminder: the deposit is still required to confirm the booking.\n\n{url}\n\nThank you,\nCrown Movers");m=queue_message(db,l["id"],subject,body,"Deposit reminder",u["id"],"deposit-reminder:"+qid+":"+uid("R"));return {"message_id":m["id"],"sent":False}
   if action in ("cash_on_day","cheque_on_day"):
    reason="Deposit exception approved: "+("Cash due on service day" if action=="cash_on_day" else "Cheque due on service day");j=confirm_booking_after_deposit(db,qid,u["id"],reason);return {"job_id":j["id"],"status":"Confirmed"}
   if action=="etransfer_received":
    amount=money(d.get("amount") or json.loads(q["inputs_json"]).get("deposit_required",300));ref=str(d.get("reference") or "").strip()
    if amount<=0 or not ref:raise HTTPException(422,"Enter the received amount and transfer reference.")
    if one(db,"SELECT id FROM payments WHERE lead_id=? AND reference=?",(l["id"],ref)):raise HTTPException(409,"That payment reference is already recorded.")
    pid=uid("PAY");insert(db,"payments",{"id":pid,"lead_id":l["id"],"amount_cents":amount,"method":"Interac","purpose":"Deposit","reference":ref,"verified":1,"recorded_by":u["id"],"created_at":now()});j=confirm_booking_after_deposit(db,qid,u["id"]);return {"payment_id":pid,"job_id":j["id"],"status":"Confirmed"}
   raise HTTPException(422,"Choose a supported deposit action.")
