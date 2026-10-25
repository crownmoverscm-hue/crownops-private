# CrownOps website form contract — implementation v1

Scope: existing `POST /api/webhooks/intake`. Use staging first. This is server-to-server;
never expose `CROWNOPS_WEBHOOK_SECRET` in browser JavaScript, Wix page code, forms or logs.
No email, payment, accounting write, assignment or booking is triggered by intake.

## Authentication and retry contract

- Send HTTPS `Content-Type: application/json`.
- Maximum raw body: 100,000 bytes.
- `X-Crown-Timestamp`: current Unix epoch seconds; server permits ±300 seconds.
- `X-Crown-Signature`: lowercase hex HMAC-SHA256 of `timestamp + '.' + exact_raw_body`,
  using the backend-only shared secret (at least 32 characters).
- Store a stable `event_id` at the website backend before sending. Retries of that
  submission use the SAME ID and EXACT same JSON bytes, with a fresh timestamp/signature.
- A successful duplicate returns the original service/client IDs; it does not create another service.
- Reusing an ID with changed content returns 409. A genuinely new submission needs a new ID.
- Retry network/429/5xx failures with bounded exponential backoff. Do not endlessly retry
  401/409/413/422; retain the submission in the website's secure delivery queue for staff review.
- Redact contact details and signatures from routine transport logs. Do not log the shared secret.

## Request

```json
{
  "event_id": "website-en-quote-unique-submission-id",
  "lead": {
    "client_name": "TEST — MUSE — Alex",
    "email": "jonathanprobonas15@gmail.com",
    "phone": "",
    "language": "English",
    "move_date": "2026-11-15",
    "pickup": "TEST pickup, Verdun",
    "dropoff": "TEST destination, Longueuil",
    "home_size": "2 bedroom apartment",
    "boxes": "25",
    "inventory": "Sofa, queen bed, boxes",
    "pickup_access": "Elevator",
    "dropoff_access": "One flight of stairs",
    "pickup_walk": "10 m",
    "dropoff_walk": "20 m",
    "heavy_items": "55-inch TV",
    "photo_link": "https://drive.google.com/drive/folders/fictional",
    "notes": "TEST only. Customer supplied information, not approved pricing.",
    "intake": {
      "local_area": "Yes",
      "start_window": "morning",
      "service_details": "Customer supplied details",
      "between_stops_minutes": "",
      "pickup_base_minutes": "",
      "return_base_minutes": "",
      "additional_services": [],
      "heavy_items": []
    }
  }
}
```

Required: nonempty `client_name`; a usable email or North American phone.
Email is required later for quote email delivery. Unknown facts remain empty strings.
Use exactly `English` or `French`; the interface language is independent.
`move_date`, when supplied, must be a real exact `YYYY-MM-DD` date.
`photo_link` must be complete HTTPS without embedded credentials. Upload binary files
through the existing authenticated portal; this endpoint does not accept file bytes.
`local_area`: `Yes`, `No`, `Not sure` or empty. `start_window`: `morning`, `afternoon`,
`unknown` or empty. Driving-minute fields are numeric strings from 0 to 1440 or empty.
The two intake arrays contain up to 20 strings of 150 characters each.

Other accepted lead fields: `notes`, `photo_link` and all moving-detail fields shown above.
Do not submit `stage`, `assigned_to`, `priority`, `source`, `lead_type`, prices, discounts,
tax amounts, quote status, payment status, IDs or arbitrary extra properties.
The server sets source Website and type Form. It never trusts browser calculator totals
as approved quote amounts. Preserve a website calculator summary in clearly labelled notes.

## Response and meaning

HTTP 200:

```json
{
  "lead_id": "LEAD-actual-id",
  "client_id": "CLIENT-actual-id",
  "duplicate": false,
  "messages_sent": 0,
  "quote_id": null,
  "draft_status": "Needs details",
  "missing": ["approved pricing setup", "preferred arrival window"]
}
```

`lead_id` is the service inquiry. Several distinct submissions may share a client.
Client matching uses existing exact email/phone rules; conflicts are flagged, not fuzzy-merged.
Do not tell the customer a quote exists unless `quote_id` is non-null. A missing pricing
approval or necessary detail produces a preparation record with explicit missing fields,
not invented rates. All generated quotes remain Draft, requiring human review.

Errors: 401 invalid/stale signature; 409 ID/content conflict; 413 oversize; 422 invalid
fields; 503 missing server configuration. Always display a friendly website message and
retain the submission for recovery. Do not imply booking confirmation on HTTP 200.

## Developer acceptance

Test EN form, FR form, EN calculator, FR calculator separately. Capture submission ID,
timestamp, response IDs, and every populated moving field. Repeat each request unchanged,
then with conflicting content. Verify one shared client, distinct service inquiries,
zero duplicate events, zero sends/payments/jobs, and truthful draft/missing-detail state.
Coordinate any actual website submission with the owner because it may trigger existing
website notification emails outside this endpoint. This document does not authorize sends.
