"""Final sales/CRM workflow helpers for CrownOps.

This module keeps the existing lead/quote/job tables intact while presenting a
client-first CRM: one client identity may have multiple service records. It also
adds reviewed quote-delivery and post-service feedback preparation without
silently sending email or moving money.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import json
import re
import secrets

from fastapi import Depends, HTTPException

from .store import connect, transaction, rows, one, insert, audit, uid, now, dumps
from .platform_common import require_role, SALES, OFFICE, settings, put_setting, queue_message, add_task, stamp_hash, text
from .pricing import calculate_with_overrides


DEFAULT_TEMPLATES = [
    {
        "id": "moving-en", "name": "Local moving - English", "service_type": "Moving",
        "language": "English", "long_distance": False, "default_deposit": 300,
        "liability_text": "Free limited liability coverage: $0.60/lb per article. Optional additional coverage is handled separately.",
        "fragile_text": "TVs, large mirrors, glass and other fragile items require appropriate protection/boxing. Additional protection does not automatically increase liability coverage."
    },
    {
        "id": "moving-fr", "name": "Déménagement local - Français", "service_type": "Moving",
        "language": "French", "long_distance": False, "default_deposit": 300,
        "liability_text": "Responsabilité limitée incluse sans frais : 0,60 $/lb par article. Toute protection additionnelle est traitée séparément.",
        "fragile_text": "Les téléviseurs, grands miroirs, vitres et autres articles fragiles nécessitent une protection/boîte appropriée. Une protection additionnelle n'augmente pas automatiquement la limite de responsabilité."
    },
    {
        "id": "long-en", "name": "Long-distance moving - English", "service_type": "Moving",
        "language": "English", "long_distance": True, "default_deposit": 1000,
        "liability_text": "Free limited liability coverage: $0.60/lb per article. Optional additional coverage is handled separately.",
        "fragile_text": "TVs, large mirrors, glass and other fragile items require appropriate protection/boxing. Additional protection does not automatically increase liability coverage."
    },
    {
        "id": "long-fr", "name": "Déménagement longue distance - Français", "service_type": "Moving",
        "language": "French", "long_distance": True, "default_deposit": 1000,
        "liability_text": "Responsabilité limitée incluse sans frais : 0,60 $/lb par article. Toute protection additionnelle est traitée séparément.",
        "fragile_text": "Les téléviseurs, grands miroirs, vitres et autres articles fragiles nécessitent une protection/boîte appropriée. Une protection additionnelle n'augmente pas automatiquement la limite de responsabilité."
    },
    {
        "id": "packing-en", "name": "Packing service - English", "service_type": "Packing",
        "language": "English", "long_distance": False, "default_deposit": 300,
        "liability_text": "Packing labour is billed at the selected moving-team hourly rate. Materials used are additional.",
        "fragile_text": "Fragile items requiring specialty boxes or protection are added as reviewed material line items."
    },
    {
        "id": "packing-fr", "name": "Service d'emballage - Français", "service_type": "Packing",
        "language": "French", "long_distance": False, "default_deposit": 300,
        "liability_text": "La main-d'œuvre d'emballage est facturée au tarif horaire de l'équipe sélectionnée. Les matériaux utilisés sont en supplément.",
        "fragile_text": "Les articles fragiles nécessitant des boîtes ou une protection spécialisée sont ajoutés comme matériaux révisés."
    },
    {
        "id": "junk-en", "name": "Junk removal - English", "service_type": "Junk Removal",
        "language": "English", "long_distance": False, "default_deposit": 300,
        "junk_prices": {"single": 175, "quarter": 400, "half": 800, "three_quarter": 1200, "full": 1600, "extra_half_hour": 80}
    },
    {
        "id": "junk-fr", "name": "Ramassage de déchets - Français", "service_type": "Junk Removal",
        "language": "French", "long_distance": False, "default_deposit": 300,
        "junk_prices": {"single": 175, "quarter": 400, "half": 800, "three_quarter": 1200, "full": 1600, "extra_half_hour": 80}
    },
]


def seed_sales_final_defaults(path):
    with transaction(path) as db:
        if settings(db, "sales_quote_templates_v1") is None:
            put_setting(db, "sales_quote_templates_v1", DEFAULT_TEMPLATES)
        if settings(db, "sales_pricing_defaults_v1") is None:
            put_setting(db, "sales_pricing_defaults_v1", {
                "local_gas_per_truck": 35.00,
                "extra_truck_transport_fee": 25.00,
                "long_distance_gas_per_km_per_truck": 0.94,
                "tv_protection_package": 35.00,
                "limited_liability_per_lb": 0.60,
            })
        if settings(db, "deposit_policy") is None:
            put_setting(db, "deposit_policy", {
                "etransfer_email": "",
                "autodeposit": True,
                "card_enabled": True,
                "cash_cheque_exception": True,
                "note": "Add the verified Crown Movers e-transfer address before production client use."
            })
        if settings(db, "client_terms") is None:
            put_setting(db, "client_terms", {
                "approved": False,
                "version": "CROWN-BOOKING-DRAFT-2026-10",
                "text": (
                    "DRAFT - MANAGEMENT REVIEW REQUIRED BEFORE ACTIVATION. "
                    "Your accepted estimate reserves the quoted scope and rates, subject to the stated minimums, transportation, fuel, materials and applicable taxes. "
                    "A booking is locked when the required deposit is verified or Crown Movers approves a documented cash/cheque arrangement. "
                    "If an emergency requires a date change, please provide at least 10 days' notice. A change with less than 10 days' notice may result in loss of the deposit. "
                    "Replacement dates are subject to availability, and pricing may change according to the rates applicable to the new date. "
                    "Crown Movers will make reasonable efforts to accommodate requested changes. "
                    "Standard limited liability coverage is $0.60 per pound per article unless separate additional coverage is accepted in writing. "
                    "Fragile items such as televisions, large mirrors and glass require suitable packing/protection; any special handling or coverage terms shown on the estimate also apply."
                ),
                "review_note": "Draft created from Crown Movers requested workflow; management/legal review required before activation."
            })
        if settings(db, "review_request_v1") is None:
            put_setting(db, "review_request_v1", {
                "google_review_url": "",
                "english": (
                    "Hi {name},\n\nThank you again for choosing Crown Movers. "
                    "We hope everything went smoothly. If you have a minute, we would really appreciate "
                    "your honest feedback about your experience. Your feedback helps our team improve and "
                    "helps future clients know what to expect.\n\n{review_link}\n\nThank you,\nCrown Movers"
                ),
                "french": (
                    "Bonjour {name},\n\nMerci encore d'avoir choisi Crown Movers. "
                    "Nous espérons que tout s'est bien déroulé. Si vous avez une minute, nous apprécierions "
                    "beaucoup vos commentaires honnêtes sur votre expérience. Vos commentaires nous aident "
                    "à nous améliorer et aident les futurs clients à savoir à quoi s'attendre.\n\n{review_link}\n\nMerci,\nCrown Movers"
                ),
                "note": "Send the same neutral request regardless of expected sentiment. No reward is tied to a public review."
            })


def _digits(v):
    return re.sub(r"\D", "", str(v or ""))


def _identity_key(row):
    email = str(row.get("email") or "").strip().lower()
    if "@" in email:
        return "e:" + email
    phone = _digits(row.get("phone"))
    if len(phone) >= 7:
        return "p:" + phone[-10:]
    return "l:" + row["id"]


def _same_identity_sql(lead):
    email = str(lead.get("email") or "").strip()
    phone = _digits(lead.get("phone"))
    clauses, params = [], []
    if email and "@" in email:
        clauses.append("email=? COLLATE NOCASE")
        params.append(email)
    if len(phone) >= 7:
        clauses.append("replace(replace(replace(replace(replace(phone,' ',''),'-',''),'(',''),')',''),'+','') LIKE ?")
        params.append("%" + phone[-10:])
    if not clauses:
        return "id=?", [lead["id"]]
    return "(" + " OR ".join(clauses) + ")", params


def _client_cards(db, source_rows):
    grouped = {}
    for r in source_rows:
        key = _identity_key(r)
        current = grouped.get(key)
        if current is None or (r.get("updated_at") or "") > (current.get("updated_at") or ""):
            grouped[key] = dict(r)
    out = []
    for r in grouped.values():
        clause, params = _same_identity_sql(r)
        svc = db.execute("SELECT count(*) FROM leads WHERE " + clause, params).fetchone()[0]
        latest = one(db, "SELECT id,move_date,pickup,dropoff,home_size,stage,updated_at FROM leads WHERE " + clause + " ORDER BY CASE WHEN move_date='' THEN 1 ELSE 0 END, move_date DESC, updated_at DESC LIMIT 1", params) or {}
        out.append({
            "id": r["id"], "client_name": r["client_name"], "email": r["email"], "phone": r["phone"],
            "language": r["language"], "service_count": svc, "latest": latest,
        })
    out.sort(key=lambda x: (x["latest"].get("updated_at") or "", x["client_name"]), reverse=True)
    return out


def _service_summary(db, lead):
    quotes = rows(db, "SELECT id,revision,status,result_json,created_at,accepted_at FROM quotes WHERE lead_id=? ORDER BY revision DESC", (lead["id"],))
    for q in quotes:
        q["result"] = json.loads(q.pop("result_json"))
    jobs = rows(db, "SELECT id,title,phase,start_at,end_at,status,quote_id FROM jobs WHERE lead_id=? ORDER BY start_at DESC", (lead["id"],))
    return {
        "id": lead["id"], "move_date": lead["move_date"], "pickup": lead["pickup"], "dropoff": lead["dropoff"],
        "home_size": lead["home_size"], "boxes": lead["boxes"], "inventory": lead["inventory"],
        "pickup_access": lead["pickup_access"], "dropoff_access": lead["dropoff_access"],
        "stage": lead["stage"], "source": lead["source"], "notes": lead["notes"],
        "quotes": quotes, "jobs": jobs, "updated_at": lead["updated_at"],
    }


def ensure_booking_hold(db, lead_id, quote_id, actor):
    existing = one(db, "SELECT * FROM jobs WHERE quote_id=? AND phase='Moving'", (quote_id,))
    if existing:
        return existing
    lead = one(db, "SELECT * FROM leads WHERE id=?", (lead_id,))
    q = one(db, "SELECT * FROM quotes WHERE id=? AND lead_id=?", (quote_id, lead_id))
    if not lead or not q:
        raise HTTPException(404, "Client or estimate not found.")
    move_date = str(lead.get("move_date") or "").strip()
    if not move_date:
        add_task(db, lead_id, "Accepted estimate: choose service date before scheduling", lead.get("assigned_to") or "", "accepted-date:" + quote_id)
        return None
    try:
        day = datetime.fromisoformat(move_date).date()
    except ValueError:
        raise HTTPException(422, "The accepted estimate has an invalid move date.")
    result = json.loads(q["result_json"])
    # A booking hold is deliberately generous. Crew/trucks are assigned later.
    hours = max(3.0, float(result.get("service_high") or 0) + float(result.get("transport_hours") or 0))
    start_local = datetime(day.year, day.month, day.day, 9, 0, tzinfo=ZoneInfo("America/Toronto"))
    end_local = start_local + timedelta(hours=hours)
    rec = dict(
        id=uid("JOB"), lead_id=lead_id, quote_id=quote_id, title=lead["client_name"],
        phase="Moving", start_at=start_local.astimezone(timezone.utc).isoformat(timespec="seconds"),
        end_at=end_local.astimezone(timezone.utc).isoformat(timespec="seconds"), status="Tentative",
        stops_json=dumps([x for x in (lead.get("pickup"), lead.get("dropoff")) if x]),
        notes="Accepted estimate booking hold. Assign crew/truck and verify dispatch readiness.",
        capacity_reviewed=0, exception_reason="", created_by=actor, created_at=now()
    )
    insert(db, "jobs", rec)
    add_task(db, lead_id, "Assign crew and truck for accepted estimate", lead.get("assigned_to") or "", "assign:" + quote_id)
    audit(db, actor, "Accepted estimate added to schedule as booking hold", "jobs", rec["id"], {"quote_id": quote_id})
    return rec


def mark_booking_locked(db, lead_id, actor, reason):
    db.execute("UPDATE leads SET stage='Booked',updated_at=? WHERE id=?", (now(), lead_id))
    j = one(db, "SELECT * FROM jobs WHERE lead_id=? AND status='Tentative' ORDER BY created_at DESC LIMIT 1", (lead_id,))
    if j:
        note = (j.get("notes") or "").strip()
        marker = "BOOKING LOCKED: " + reason
        if marker not in note:
            db.execute("UPDATE jobs SET notes=?,version=version+1 WHERE id=?", ((note + "\n" + marker).strip(), j["id"]))
    audit(db, actor, "Booking locked pending dispatch assignment", "leads", lead_id, {"reason": reason})


def queue_booking_confirmation(db, lead_id, quote_id, actor, reason):
    lead = one(db, "SELECT * FROM leads WHERE id=?", (lead_id,))
    if not lead or not lead.get("email") or lead.get("notifications_suppressed"):
        return None
    fr = str(lead.get("language") or "").lower().startswith("fr")
    if fr:
        subject = "Votre déménagement est réservé | Crown Movers"
        body = (
            f"Bonjour {lead['client_name']},\n\nC'est confirmé : votre service Crown Movers est maintenant réservé pour le "
            f"{lead.get('move_date') or 'date convenue'}.\n\nSi une urgence vous oblige à changer la date, prévenez-nous dès que possible. "
            "Conformément aux conditions de réservation acceptées, nous demandons au moins 10 jours de préavis. "
            "Un changement avec moins de 10 jours de préavis peut entraîner la perte du dépôt. Une nouvelle date demeure "
            "sujette aux disponibilités et le tarif peut changer selon la nouvelle date. Nous ferons toujours notre possible "
            "pour trouver la meilleure option disponible.\n\nMerci de votre confiance!\nCrown Movers"
        )
    else:
        subject = "You're booked and ready to go! | Crown Movers"
        body = (
            f"Hi {lead['client_name']},\n\nYou're confirmed: your Crown Movers service is now booked for "
            f"{lead.get('move_date') or 'the agreed date'}.\n\nIf an emergency means you need to change the date, please tell us as early as possible. "
            "Under the booking terms you accepted, we require at least 10 days' notice. A change with less than 10 days' notice "
            "may result in loss of the deposit. A replacement date is subject to availability, and the rate may change based on "
            "the new date. We will always work with you to find the best available option.\n\nThank you for choosing Crown Movers!\nCrown Movers"
        )
    key = "booking-confirmation:" + quote_id
    mail = queue_message(db, lead_id, subject, body, "Preparation", actor, key)
    db.execute("UPDATE outbound_messages SET status='Approved',approved_by=? WHERE id=? AND status='Draft'", (actor, mail["id"]))
    audit(db, actor, "Booking confirmation prepared for automatic delivery", "outbound_messages", mail["id"], {"reason": reason})
    return mail["id"]


def register_sales_final(app, path, auth, public_url):
    @app.get("/api/ux/clients")
    def clients(q: str = "", u=Depends(auth)):
        require_role(u, OFFICE)
        term = str(q or "").strip()
        with closing(connect(path)) as db:
            if term:
                like = "%" + term + "%"
                found = rows(db, """SELECT * FROM leads
                    WHERE client_name LIKE ? COLLATE NOCASE OR email LIKE ? COLLATE NOCASE OR phone LIKE ?
                       OR pickup LIKE ? COLLATE NOCASE OR dropoff LIKE ? COLLATE NOCASE
                    ORDER BY updated_at DESC LIMIT 200""", (like, like, like, like, like))
            else:
                found = rows(db, "SELECT * FROM leads ORDER BY updated_at DESC LIMIT 250")
            return _client_cards(db, found)

    @app.get("/api/ux/clients/{lid}")
    def client_profile(lid: str, u=Depends(auth)):
        require_role(u, OFFICE)
        with closing(connect(path)) as db:
            lead = one(db, "SELECT * FROM leads WHERE id=?", (lid,))
            if not lead:
                raise HTTPException(404, "Client not found.")
            clause, params = _same_identity_sql(lead)
            services = rows(db, "SELECT * FROM leads WHERE " + clause + " ORDER BY CASE WHEN move_date='' THEN 1 ELSE 0 END, move_date DESC, updated_at DESC", params)
            return {
                "client": {k: lead.get(k, "") for k in ("id","client_name","email","phone","language","assigned_to","stage")},
                "services": [_service_summary(db, s) for s in services],
            }

    @app.post("/api/ux/clients/{lid}/new-service")
    def new_service(lid: str, data: dict = None, u=Depends(auth)):
        require_role(u, SALES)
        data = data or {}
        with transaction(path) as db:
            lead = one(db, "SELECT * FROM leads WHERE id=?", (lid,))
            if not lead:
                raise HTTPException(404, "Client not found.")
            rec = dict(
                id=uid("LEAD"), external_id=None, client_name=lead["client_name"], email=lead["email"], phone=lead["phone"],
                move_date="", pickup="", dropoff="", home_size="", boxes="", pickup_access="", dropoff_access="",
                pickup_walk="", dropoff_walk="", inventory="", heavy_items="", photo_link="", language=lead["language"],
                source="Returning client", lead_type="Manual", stage="New Lead", assigned_to=lead.get("assigned_to") or u["id"],
                priority="Normal", notes=text(data.get("note", ""), "Note", 1500), archived_import=0,
                notifications_suppressed=0, raw_json="{}", version=1, created_at=now(), updated_at=now()
            )
            insert(db, "leads", rec)
            add_task(db, rec["id"], "Confirm new service details", rec["assigned_to"], "new-service:" + rec["id"])
            audit(db, u["id"], "New service created for existing client", "leads", rec["id"], {"copied_from": lid})
        return {"id": rec["id"], "client_name": rec["client_name"]}

    @app.get("/api/ux/quote-templates")
    def quote_templates(u=Depends(auth)):
        require_role(u, OFFICE)
        with closing(connect(path)) as db:
            return {
                "templates": settings(db, "sales_quote_templates_v1", DEFAULT_TEMPLATES),
                "defaults": settings(db, "sales_pricing_defaults_v1", {}),
            }

    @app.get("/api/ux/rate-preview")
    def rate_preview(move_date: str, movers: int, u=Depends(auth)):
        require_role(u, SALES)
        with closing(connect(path)) as db:
            p = one(db, "SELECT * FROM pricing_versions ORDER BY created_at DESC LIMIT 1")
            if not p:
                raise HTTPException(422, "Import your rate table first.")
            settings_json = json.loads(p["settings_json"])
            payload = {
                "pricing_id": p["id"], "move_date": move_date, "movers": movers, "trucks": 2 if movers >= 4 else 1,
                "hours_low": 1, "hours_high": 1, "allowance_percent": 0, "transport_hours": 1,
                "minimum_key": "morning_service_minimum" if "morning_service_minimum" in settings_json else "minimum_billable_hours",
                "fuel_key": "normal_local_fuel" if "normal_local_fuel" in settings_json else ("gas_per_truck" if "gas_per_truck" in settings_json else "extended_local_fuel"),
                "materials_amount": 0, "other_amount": 0, "discount_amount": 0,
            }
            try:
                result = calculate_with_overrides(payload, p, db)
            except (ValueError, TypeError, KeyError) as ex:
                raise HTTPException(422, str(ex))
            return {
                "pricing_id": p["id"], "pricing_label": p["label"], "hourly_rate_cents": result["hourly_rate_cents"],
                "minimum_key": payload["minimum_key"], "minimum_hours": result["minimum_hours"],
                "fuel_key": payload["fuel_key"], "fuel_per_truck_cents": result["fuel_per_truck_cents"],
                "default_trucks": payload["trucks"], "season": result["season"],
            }

    @app.post("/api/ux/quotes/{qid}/prepare-delivery")
    def prepare_delivery(qid: str, data: dict = None, u=Depends(auth)):
        require_role(u, SALES)
        data = data or {}
        with transaction(path) as db:
            q = one(db, "SELECT * FROM quotes WHERE id=?", (qid,))
            if not q or q["status"] not in ("Approved", "Accepted"):
                raise HTTPException(409, "Finalize the estimate before preparing client delivery.")
            lead = one(db, "SELECT * FROM leads WHERE id=?", (q["lead_id"],))
            if not lead or "@" not in (lead.get("email") or ""):
                raise HTTPException(422, "Confirm the client email first.")
            communication_suppressed = bool(lead["notifications_suppressed"])
            raw = secrets.token_urlsafe(32)
            expires = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(timespec="seconds")
            insert(db, "portal_invites", dict(
                token_hash=stamp_hash(raw), lead_id=lead["id"], email=lead["email"], expires_at=expires,
                created_by=u["id"], created_at=now()
            ))
            link = public_url + "/portal#access=" + raw
            quote_inputs = json.loads(q["inputs_json"])
            fr = str(quote_inputs.get("template_language") or lead.get("language") or "").lower().startswith("fr")
            if fr:
                subject = "Votre devis Crown Movers est prêt"
                body = (
                    f"Bonjour {lead['client_name']},\n\nVotre devis Crown Movers est prêt. "
                    "Cliquez sur le lien ci-dessous pour vérifier les détails, accepter le devis et, une fois accepté, "
                    "voir les options de dépôt.\n\n" + link +
                    "\n\nSi vous avez des questions ou souhaitez modifier un détail, répondez simplement à ce courriel.\n\nMerci,\nCrown Movers"
                )
            else:
                subject = "Your Crown Movers estimate is ready"
                body = (
                    f"Hi {lead['client_name']},\n\nYour Crown Movers estimate is ready. "
                    "Click the link below to review the details, accept the estimate and, once accepted, "
                    "see the deposit options.\n\n" + link +
                    "\n\nIf you have any questions or need to change a detail, simply reply to this email.\n\nThank you,\nCrown Movers"
                )
            mail = queue_message(db, lead["id"], subject, body, "Preparation", u["id"], "quote-delivery:" + qid + ":" + uid("D"), None, None)
            audit(db, u["id"], "Estimate delivery prepared", "quotes", qid, {"mail_id": mail["id"], "portal_expires": expires})
            return {"mail_id": mail["id"], "subject": subject, "body": body, "portal_url": link, "sent": False,
                    "communication_suppressed": communication_suppressed}

    @app.post("/api/ux/outbound/{mid}/send-reviewed")
    def send_reviewed(mid: str, data: dict, u=Depends(auth)):
        require_role(u, SALES)
        if data.get("reviewed") is not True:
            raise HTTPException(422, "Review the recipient and message before sending.")
        with transaction(path) as db:
            m = one(db, "SELECT * FROM outbound_messages WHERE id=?", (mid,))
            if not m or m["status"] != "Draft":
                raise HTTPException(409, "Only a current draft can be sent.")
            lead = one(db, "SELECT * FROM leads WHERE id=?", (m["lead_id"],))
            if lead and lead["notifications_suppressed"]:
                if data.get("contact_confirmed") is not True:
                    raise HTTPException(422, "Confirm that the client requested or authorized this operational email.")
                db.execute("UPDATE leads SET notifications_suppressed=0,updated_at=? WHERE id=?", (now(), lead["id"]))
                audit(db, u["id"], "Operational quote email permission confirmed", "leads", lead["id"], {"mail_id": mid})
            db.execute("UPDATE outbound_messages SET status='Approved',approved_by=? WHERE id=?", (u["id"], mid))
            audit(db, u["id"], "Client email reviewed and approved for sending", "outbound_messages", mid)
        from .providers import process_mail
        return process_mail(path, mid)

    @app.post("/api/ux/quotes/{qid}/booking-exception")
    def booking_exception(qid: str, data: dict, u=Depends(auth)):
        require_role(u, SALES)
        method = str(data.get("method") or "")
        if method not in ("Cash on move day", "Cheque on move day"):
            raise HTTPException(422, "Choose Cash on move day or Cheque on move day.")
        note = text(data.get("note", ""), "Booking exception note", 1500)
        if len(note) < 8:
            raise HTTPException(422, "Add a short note explaining the payment arrangement.")
        with transaction(path) as db:
            q = one(db, "SELECT * FROM quotes WHERE id=?", (qid,))
            if not q or q["status"] != "Accepted":
                raise HTTPException(409, "The client must accept the estimate first.")
            job = ensure_booking_hold(db, q["lead_id"], qid, u["id"])
            if job:
                db.execute("UPDATE jobs SET exception_reason=?,version=version+1 WHERE id=?", (method + ": " + note, job["id"]))
            mark_booking_locked(db, q["lead_id"], u["id"], method)
            confirmation_mail_id = queue_booking_confirmation(db, q["lead_id"], qid, u["id"], method)
            add_task(db, q["lead_id"], "Collect " + method.lower() + " and verify receipt", u["id"], "cash-arrangement:" + qid)
        sent = False
        if confirmation_mail_id:
            try:
                from .providers import process_mail
                sent = bool(process_mail(path, confirmation_mail_id).get("sent"))
            except Exception:
                with transaction(path) as db:
                    add_task(db, q["lead_id"], "Send booking confirmation email", u["id"], "booking-confirmation-send:" + qid)
        return {"ok": True, "booking_locked": True, "payment_received": False, "job_id": job["id"] if job else None,
                "confirmation_mail_id": confirmation_mail_id, "confirmation_sent": sent}

    @app.post("/api/ux/jobs/{jid}/prepare-review-request")
    def prepare_review_request(jid: str, u=Depends(auth)):
        require_role(u, SALES)
        with transaction(path) as db:
            job = one(db, "SELECT * FROM jobs WHERE id=?", (jid,))
            if not job or job["status"] != "Completed":
                raise HTTPException(409, "Complete the service before asking for feedback.")
            lead = one(db, "SELECT * FROM leads WHERE id=?", (job["lead_id"],))
            if not lead or not lead.get("email"):
                raise HTTPException(422, "Client email is required.")
            cfg = settings(db, "review_request_v1", {})
            automation = settings(db, "automation_policy", {}) or {}
            url = str(cfg.get("google_review_url") or automation.get("review_url") or "").strip()
            link_text = url if url else "Reply to this email with your feedback."
            fr = str(lead.get("language") or "").lower().startswith("fr")
            template = cfg.get("french" if fr else "english") or DEFAULT_TEMPLATES[0]["liability_text"]
            body = template.format(name=lead["client_name"], review_link=link_text)
            subject = "Votre avis nous aide | Crown Movers" if fr else "How did we do? | Crown Movers"
            mail = queue_message(db, lead["id"], subject, body, "Review request", u["id"], "review-request:" + jid, jid, job["version"])
            audit(db, u["id"], "Neutral post-service review request prepared", "jobs", jid, {"mail_id": mail["id"], "sent": False})
        return {"mail_id": mail["id"], "subject": subject, "body": body, "sent": False}
