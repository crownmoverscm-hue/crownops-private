# Wix to CrownOps

The two existing Crown Schedule automations use `POST /api/webhooks/wix` on
the CrownOps production domain. Keep **Nest request body under data** selected.
The established fields are `connectionKey`, `event`, `documentId`, `number`,
`title`, `customer`, `email`, `phone`, `address`, `city`, `contactId`, `total`,
`currency`, and `occurredAt`. Events are `quote_accepted` and `invoice_paid`.

The deployment variable `CROWNOPS_WIX_KEY_SHA256` holds only the SHA-256 digest
of the existing automation secret. Never put the secret in a URL, source file,
log or review note. The receiver fails closed if no digest is configured.
This is an independent server connection; ChatGPT login expiry does not stop it.

## Office workflow

Open **Connections → Wix incoming**. It shows incoming documents, delivery
state, duplicate counts and review reasons. Only the owner can pause intake;
paused deliveries return 503 so Wix sees a failure rather than a false success.
Check Wix automation history for failed deliveries and retry after resuming.
No receiver can guarantee delivery while its sender is disabled or unavailable.

An accepted quote creates a CRM inquiry without a speculative second estimate.
The billing address remains identified as billing information, never pickup.
An unambiguous service date from October 19, 2026 appears in **Schedule → All
Jobs** as a source entry requiring planning. Dates follow the observed Crown
Wix title convention **MM/DD/YYYY**; **YYYY-MM-DD** is also supported. Missing
years, invalid dates, multiple service dates and TBD alternatives need review.
Unknown times stay unknown. No duration, crew, truck, payment or confirmation is
invented. **Plan service** opens the existing reviewed job-creation workflow and
reuses the linked CRM inquiry.

Fully booked days and potential duplicates do not gain another calendar entry.
Resolve capacity or compare the existing service, then use the notice's date
form when appropriate. Changed versions of the same Wix document remain in the
review queue and never overwrite an existing schedule entry. Date changes to
already-created services must be made in the normal service planning screen.

A paid invoice event is a source notice, not a verified bank receipt. Owners,
managers and accounting can record a reconciliation note after checking it in
QuickBooks or against bank evidence. No money or accounting entry is created
by receiving or reviewing the notice.

## Delivery test and rollout

Deploy through the protected GitHub CI process. Database startup creates and
restore-tests a recovery archive before adding the Wix table in production.
Point one automation temporarily at `/api/webhooks/wix?probe=1` and use Wix's
isolated test. A successful probe records a timestamp and creates zero business
records. Verify the successful action and CrownOps log, then remove `?probe=1`
and publish the production URL for both automations. Do not create or accept a
real client document simply to test delivery.

The automations handle **new events**. Publishing them does not replay earlier
accepted quotes or paid invoices. Historical imports need a separate reviewed
backfill using original document identities and the same duplicate checks.

Tests: `python -m unittest discover -s tests -p test_wix_sync.py -v`.
UI: `CROWNOPS_BROWSER_WIX=1 python tests/browser/run_mobile.py wix_sync` with
Playwright on `NODE_PATH`. Fixtures contain fictional clients only.
