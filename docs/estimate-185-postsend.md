# Existing Estimate 185: post-send observation, never resend

The dedicated send slot is terminal. No existing quote, reconciliation event27,
approval/event28, claim/result audit, saved provider snapshot, Customer70 or
Estimate185 is rewritten by this release. New diagnostic schema is additive;
there is no backfill of missing original HTTP status, body or trace ID.

The server already holds a sandbox OAuth grant. Authenticated Master owners can
GET `/api/quotes/QUOTE-08dd48a364a34f66/sandbox-provider-readback` without signing
into the QuickBooks website. This performs one fixed sandbox GET, no automatic
retry/redirect, returns allowlisted fields and new GET HTTP/trace metadata, and
does not write diagnostic/audit rows. The trace is explicitly a new GET trace,
never the missing original SEND trace. Wrong host/realm/service/environment,
quote/mapping or actor fails before any provider request. Credentials stay on
the server. QuickBooks website login is only needed to inspect its activity UI.

Preflight now exposes `postsend`: preserved historical authorization,
current protected business integrity, terminal slot and the label
**Provider reports sent — inbox receipt unconfirmed.** Legacy185 evidence is
reconstructed from the matching approval/claim audits, not retroactively
attested. Future claims capture exact authorization/baseline fingerprints.
Historical identity survives later owner deactivation; current actions still
require the active Master owner.

The post-send comparison restores only the expressly disclosed BillEmail,
EmailStatus, supported TxnStatus, DeliveryInfo, SyncToken and LastUpdatedTime
fields to the original baseline, then compares the FULL provider fingerprint.
Unexpected business fields, customer/company identity, terms/lines/taxes/total,
metadata other than update time, recipient or local family/context changes fail
integrity. Protected local history does not become editable. For the legacy
case, an unavailable/reconstructed baseline that cannot reproduce event27's
fingerprint requests review rather than granting eligibility.

Later provider Accepted is only an observation. No acceptance write, booking,
invoice, deposit or payment helper is invoked. Generic poller enrollment is
not needed and no workflow flags are enabled. Email error metadata is evidence
to inspect, never inbox-delivery proof.

Future separately authorized SEND requests preserve encrypted append-only
request/response evidence with HTTP status, effective version, timestamps and
allowlisted intuit_tid/Date/Content-Type before raising/parsing the response.
Fault text, arbitrary headers, secrets and raw free-form payloads are excluded.
Pre-request evidence failure stops before POST; post-request uncertainty keeps
the existing claim Unknown and permanently consumed. No automatic POST retry
or redirect. No approval or reconciliation is reset to fix telemetry.

## Independent Muse acceptance

1. Confirm deployed commit/build and existing r1/r2/r3, Customer70, Estimate185,
   exact CAD558.77 snapshot, event27 and approval28 unchanged.
2. Open r2 QuickBooks delivery: preserved reconciliation/approval identities and
   times; reconstructed legacy evidence; terminal slot; exact sent/unconfirmed
   label; no reapprove, resend or acceptance action. Inspect four widths.
3. Master owner may open the raw-readback GET once to capture available
   DeliveryInfo/DeliveryErrorType, BillEmail, SyncToken, LastUpdatedTime,
   CustomerRef/totals, HTTP status and the NEW GET trace. Do not press SEND.
4. Check non-Master/staff GET denial and wrong quote/env denial using disposable
   fixtures. Never tamper with real records to test rejection.
5. Run `test_sandbox_postsend.py`, existing revision/send suites and the
   quote_calendar browser suite against disposable DBs/mocked providers.
   Material business/customer/recipient/new revision changes fail; expected
   send metadata and later status reads never write acceptance/jobs/payments;
   duplicate/timeout/failure cases retain a consumed slot and sanitized trace.
6. Provider EmailSent/DeliveryInfo or staff-set Accepted is not recipient receipt
   or customer-click evidence. Delivery cause may still need Intuit support.
