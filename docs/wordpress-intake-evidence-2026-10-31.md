# WordPress intake evidence — 2026-10-31

## Scope and result

Local source inspection and disposable in-process tests only. No form submitted
to WordPress, no request to quote-testing staging, no Gmail read, no provider
write, no deployment, and no change to business records or credentials.

The reported **eight submissions producing zero clients remains unexplained**.
The original eight request bodies, WS Form exports/field IDs, transport logs,
response codes and delivery queue were not provided. The source's
`tests/test_launch_workflow.py:synthetic_form` explicitly labels its four-format
fixtures synthetic, not the missing original tester bodies. They cannot establish
which route the eight submissions used or why that run failed.

A separate, reproducible defect was found and corrected: a blank French answer
could consume the following accented field label as its value. For example,
`Taille du logement:` followed by `Accès au départ: Ascenseur` became a home size
of `Accès au départ: Ascenseur`. `_value_after` previously recognized only ASCII
letters in the next-field boundary. The minimal patch makes that boundary
Unicode-aware. It leaves blank answers blank so normal missing-information
handling remains explicit. It does not infer missing values or approve pricing.
This defect has **not** been established as the cause of the original eight-item
incident.

## Existing transport and expected records

Two existing ingestion paths must be kept distinct:

1. Website notification email → authorized Gmail read-only importer. It extracts
   known visible EN/FR labels, retains an integration event and imported message,
   and matches or creates a service inquiry and client contact. Actual WordPress
   notification templates and current recipient/delivery setup are unverified.
2. WordPress server-side adapter → signed JSON `POST /api/webhooks/intake`.
   This endpoint expects the canonical JSON contract below, **not** raw WS Form
   field IDs or arbitrary notification HTML. A deployed WordPress adapter is
   not evidenced. See `docs/website-form-contract.md` for the full signing contract.

An accepted new event creates one `leads` service/inquiry row, links a
`client_accounts` contact, and creates a staff review task. Draft preparation
records missing information in `intake_drafts`; it creates a quote only if all
required information, pricing and template gates pass. No message, booking,
invoice, deposit or payment is part of intake.

Eight submissions do not necessarily mean eight clients. Exact event retries
must not create more services; separate services from the same known contact
should share one client. Identical email and phone with distinct requested dates
are used in the local harness to prove this distinction.

The existing isolated quote-testing environment deliberately blocks
`/api/webhooks/intake` and Gmail imports server-side. **Do not use that service as
an intake test endpoint or weaken its isolation.**

## Implemented mapping and remaining WordPress mapping gap

Canonical keys are verified from code. Listed email-body labels are parser
examples, not verified WS Form field names/IDs. Raw WS Form IDs and actual
calculator rules remain UNKNOWN until sanitized exports are supplied.

| Canonical key | English visible labels | French visible labels | Handling |
| --- | --- | --- | --- |
| `client_name` | Client Name; Name | Nom du client; Nom complet; Nom | Required name |
| `email` | Email; Email Address; E-mail | Courriel; Adresse courriel; Adresse e-mail | Extract/validate email; contact matching |
| `phone` | Phone; Phone Number; Telephone Number | Téléphone; Numéro de téléphone | North American number normalization |
| `language` | Language; Preferred Language | Langue; Langue préférée | Explicit choice first; known form markers otherwise |
| `move_date` | Moving Date; Move Date; Preferred Moving Date | Date du déménagement; Date de déménagement; Date souhaitée | Real YYYY-MM-DD for signed JSON; subject fallback in email parser |
| `pickup` | Pickup Address; Pickup Address or Postal Code | Adresse de départ; Code postal de départ | Service address, not contact identity |
| `dropoff` | Destination Address; Destination Address or Postal Code | Adresse de destination; Adresse d’arrivée | Service address |
| `home_size` | Home Size; Move Size; Number of Bedrooms | Taille du logement; Nombre de chambres | Blank remains missing |
| `boxes` | Approximate Boxes; Approximate Number of Boxes; Boxes | Nombre approximatif de boîtes; Boîtes | Information only |
| `inventory` | Inventory; Furniture / Inventory; Furniture | Inventaire; Meubles | Details fallback in existing email parser |
| `pickup_access` | Pickup Access | Accès au départ | No invented access assumption |
| `dropoff_access` | Drop-off Access | Accès à destination | No invented access assumption |
| `pickup_walk` | Pickup Walking Distance; Walk at Pickup | Distance de marche au départ | Blank remains missing |
| `dropoff_walk` | Drop-off Walking Distance; Walk at Drop-off | Distance de marche à destination | Blank remains missing |
| `heavy_items` | Heavy Items; Heavy / Specialty Items | Objets lourds; Articles lourds ou spéciaux | Review information |
| `photo_link` | Photo / Video Link; Photo or Video Link | Lien photos / vidéos | HTTPS, no embedded credentials; bad optional email value retained for review, not clickable |
| `notes` | Additional Details; Details / Notes; Notes; Message | Détails supplémentaires; Commentaires | Browser-calculated totals remain notes, never approved rates |
| `intake.local_area` | Greater Montreal Move; Local Montreal | Déménagement dans le Grand Montréal | Yes / No / Not sure |
| `intake.start_window` | Preferred Arrival Window; Arrival Window | Plage d’arrivée | morning / afternoon / unknown |
| `intake.between_stops_minutes` | Driving Minutes Between Stops | Minutes de conduite entre les adresses | Validated numeric minutes, 0–1440 |
| `intake.pickup_base_minutes` | Base to Pickup Minutes | Minutes du dépôt au départ | Validated numeric minutes, 0–1440 |
| `intake.return_base_minutes` | Return to Base Minutes | Minutes de retour au dépôt | Validated numeric minutes, 0–1440 |
| `intake.service_details`, `additional_services`, `heavy_items` | Canonical JSON fields | Canonical JSON fields | Adapter must map actual source fields; original source IDs unknown |

Signed intake fixes source=`Website`, type=`Form`; client-controlled stage,
assignment, priority, pricing and financial workflow fields are rejected. The
email importer fixes source=`Gmail`, with Form/Instant estimate classified from
the subject. No website estimate is an approved quote snapshot.

## Duplicate rules, errors and source preservation

- Signed intake authenticates HMAC-SHA256 over timestamp plus exact raw JSON
  bytes, with a five-minute window and maximum 100,000-byte body. The event ID is
  required and the stored raw-body SHA-256 is bound to it. Exact retry returns
  the original IDs; same event ID with changed bytes returns 409. New event IDs
  are distinct submissions: the WordPress sender must persist and reuse its
  stable event ID across retries, rather than generating a new one.
- `new_lead` and `sync_unlinked_clients` perform contact linking separately from
  event idempotency. Exact normalized email is strongest; a single phone match
  is reused only without contradictory email. Conflicts are flagged for review,
  not silently merged. Gmail additionally resolves multiple service matches only
  when a unique exact service date is available; conflicts/closed services need
  staff review.
- Signed JSON validation failures roll back CRM writes and return an error. The
  CRM stores the accepted event's hash, not the original rejected JSON. **Durable
  preservation of rejected/uncertain direct submissions depends on a WordPress
  delivery queue, which is not evidenced.** The contract requires retaining the
  exact original JSON in that queue, bounded retries for network/429/5xx, and staff
  review of 401/409/413/422.
- Gmail uses per-message savepoints and a stable Gmail-message event key. A
  validation failure retains a Needs review event and task while the next message
  can import. Invalid optional photo URLs preserve the source text for review
  instead of losing the inquiry. Existing scope is not overwritten merely by a
  repeated incoming form.
- Gmail preservation is bounded: event body up to 20,000 characters, message body
  up to 12,000, and HTML is normalized to visible text. It is **not** proof of a
  byte-for-byte original MIME/HTML archive. WordPress source preservation must be
  independently verified before operational acceptance.

## Tests actually executed

Command (disposable SQLite databases, in-process FastAPI client):

```sh
PYTHONPATH=.:tests /tmp/crownops-intake-venv/bin/python -m unittest test_form_language test_wordpress_intake_contract test_intake_resilience test_gmail_ambiguity test_launch_workflow -v
```

**Result: 44 tests passed, 12.927 seconds, October 31, 2026.** No backup/restore
tests were run. This is a local result, not GitHub CI, provider qualification or
deployed behavior.

New tests cover plain/HTML French blank fields and four clearly synthetic
EN/FR form/calculator formats submitted once plus exact retries. Eight local
requests produced **one contact, four services and four accepted events**;
missing arrival/driving information prevented quote creation. Counts remained
zero for quotes, outbound messages, jobs, payment links, deposit links, payments
and job invoices. The new signed-intake harness also rejects any attempted
`requests`-based external call. Existing tests cover failed-message preservation,
continuation after validation errors, partial-write rollback, duplicate review,
contact conflicts, safe recovery preview/apply, changed-event 409 and forbidden
workflow/date fields. No live endpoint was invoked.

## Next independent Muse test and dependencies

1. Inspect the minimal parser diff and these tests; execute the exact local
   command if supported. Mark code execution NOT TESTED if unavailable. The
   four-format fixture is synthetic and must not be described as the original
   eight submissions.
2. Obtain sanitized WordPress WS Form exports and the original eight fictional
   submissions, including actual field IDs, form language, calculator output,
   notification template/body or exact JSON, stable event IDs and sanitized
   endpoint responses. Inspect source queue and retry settings read-only.
3. Replay those **verbatim fictional** fixtures only through a disposable local
   harness; compare every field, language, preserved source, service/contact ID,
   duplicate/conflict result and missing-information reason. Original fixture
   acceptance is BLOCKED until those artifacts exist.
4. If browser-level WordPress delivery testing is then needed, prepare a separate
   synthetic intake environment with no real customers/provider credentials,
   no email/automation, no acceptance/booking writes, and a securely configured
   server-side sender. Owner approval of that exact environment and test writes
   is required first. Do not use the live website or quote-testing staging.

## Customer-journey stop gate

`docs/form-to-booking-test-handoff.md` contains obsolete execution instructions
to use live forms and an acceptance path that creates a tentative job and deposit
request. Those instructions are **superseded and must not be followed**.
Source inspection confirms `app/portal.py` still calls `ensure_tentative_job` and
`prepare_deposit_request` from the legacy acceptance handler. It was not invoked
or modified in this intake patch. This is a separate release blocker: acceptance
must record acceptance only; a separately authorized staff operation must create
exactly one tentative booking after checks. No automatic deposit/payment,
crew/truck assignment or final confirmation is authorized.

## Owner approvals still required

No deployment is authorized by this local evidence. Any staging deployment must
identify the exact reviewed commit, CI and target. Original fictional exports may
be supplied without credentials or real customer records. New browser submissions,
separate intake test resources and any customer-journey mutation require their
own exact approval. Existing pricing and business approval gates remain intact.
