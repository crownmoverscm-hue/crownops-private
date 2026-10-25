# Core launch workflow — development acceptance record

This branch leaves the independent Staff AI staging build unchanged. Do not merge or
deploy to production. No real provider request, email, invitation, import or payment is
authorized by these instructions.

## Combined sandbox path

New `app/quote_booking_workflow.py` reuses `estimate_delivery.py`, the CRM finalize
handler, existing Gmail outbox and scoped client portal. No second booking/CRM ledger.

Rollout is default off and requires ALL: `CROWNOPS_ENV=staging`, `QBO_ENV=sandbox`,
`CROWNOPS_GMAIL_QBO_SANDBOX_WORKFLOW=1`, plus existing estimate preparation/sandbox-write
gates. Email additionally requires the external-action gate, approved recipient allowlist
and proven company sender. Do not enable these flags until the owner approves the test.
The flag cannot enable this bridge in production.

Routes under `/api/quotes/{quote_id}/booking-workflow`:

1. GET base: local state only; no provider call. Email, estimate ID/reviewed totals and
   acceptance synchronization are separate outcomes.
2. POST `/prepare` `{confirm:true,basis:"high"|"low"}`: creates the existing itemized
   unsent sandbox estimate. Stable provider request IDs and existing local preparation
   reservation prevent blind duplicates. Unresolved preparation requires reconciliation.
3. GET `/preview`: read provider estimate, compare source contents and non-compounded
   Québec TPS/TVQ, expose actual external ID, lines, taxes, cent totals and review hash.
4. POST `/review` `{confirm:true,review_hash:...}`: stores reviewed amounts and prepares
   the existing Draft CrownOps email/private portal link. Sends nothing.
5. POST `/send-email` `{confirm:true,review_hash:...}`: explicit approved test send,
   verified sender and allowlist required. Existing outbox tracks Sent vs Unknown.
   QuickBooks email is blocked for this path. No automatic send retry.
6. Existing authenticated portal accepts the quote/terms. Exactly one unassigned
   Tentative job; duplicate acceptance creates no second job. Deposit request preparation
   retains existing guards; Draft is never reported as Sent. No payment verification.
7. POST `/sync-acceptance` `{confirm:true}`: explicit staff-authorized sandbox sparse
   update of the SAME estimate, using real portal acceptance evidence. Read before retry;
   Pending/Unresolved/Synced are separate from local client acceptance. Never marks synced
   on API failure. No additional job is created here.

Intuit schema checked against official Estimate API, sparse update: Id, SyncToken,
CustomerRef, TxnStatus Accepted, AcceptedBy, AcceptedDate. Reference:
https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/estimate

## Sender proof

`app/sender_proof.py`: Not verified → Pending → Verified → Sending enabled.
Owner explicitly sends one allowlisted verification email, then the app reads that
sent message's From/Reply-To headers through Gmail readonly scope. Mismatches cannot
be approved. Editing sender identity invalidates proof. Stored-header evidence does NOT
prove recipient delivery or SPF/DKIM/DMARC; owner must inspect the received message.
Timeouts remain Pending; no resend loop. A transport-ambiguous send with no message ID
needs manual Sent-mail reconciliation, not an invented success.

Connections contains the walkthrough and read-only setup previews. Quote QuickBooks
action uses combined flow only when its staging gate is enabled; legacy flow otherwise.

## Intake and recovery

Parser now uses HTMLParser for visible text, including quoted `>` attributes, entity
decoding and table cells. Exact label boundaries prevent Email from consuming Email
Address and Phone from consuming Phone Number. EN/FR aliases, inventory and optional
arrival/driving facts reuse existing fields. Manual exception review reparses stored body.
New leads immediately link to client accounts using existing identity rules.

`POST /api/intake/recovery/preview` accepts `{event_ids:[...]}` for 1–100 exceptions;
no mutation/provider calls. Explicit `/apply` additionally requires confirm and current
preview_hash; it rechecks identity between items, uses per-item rollback, preserves source
body and returns client/service/quote IDs or exact blocking details. No production apply
has been run. Original four tester bodies remain unavailable; synthetic tests do not
certify those original templates or the live four-form acceptance test.

## Bulk preparation only

Owner-only POST `/api/launch/staff-invitations/preview` and `/materials/preview` take
`{rows:[...]}` (1–200). They return normalized values, errors, intended action and hash.
NO apply endpoint is exposed: no invitations, employee accounts or catalog changes occur.
UI accepts JSON; CSV upload/import and bilingual SKU/QBO mapping need a later reviewed
extension before claiming a full bulk import is launch-ready.

Staff fields: name,email,role,employee_id. Existing login, current invitation, duplicate
email, invalid role, inactive/nonexistent employee and missing crew mapping are flagged.
Roles are actual CrownOps role names; secretary must be mapped to an approved existing role.

Material fields: code,name,category,sell_price,cost_price,quantity,minimum,location,
taxable,active,version (version required for existing SKU). Explicit prices/booleans,
duplicate SKU checks, exact cent conversion, current version. No blank-price default to zero.
No future-year material prices are invented; catalog has no effective-date pricing schema yet.

## Still required for actual milestone

- Merge/review branch and run browser CI; arrange staging release AFTER Muse finishes
  the current build retest. Record the actual deployed version/build at that time.
- Owner-authorized Gmail/Intuit sandbox credentials, exact sandbox company, reviewed
  service item/tax/discount account; secret values entered only in protected dashboards.
- Sender proof email + received-message confirmation; separate explicit quote-email approval.
- Real provider scenario: saved quote → actual QB ID and exact cents → approved inbox
  delivery → portal acceptance → one Tentative job → real acceptance sync evidence.
- New original form fixtures and an authorized stored-exception recovery report.
- Canonical rate/fee approvals, final confirmation human-call gate, full role matrix,
  invoices/deposits, security/backups and production approval remain launch gates.

Local contract tests use simulated providers and disposable SQLite. They are not real
Gmail or QuickBooks integration acceptance, and have not sent mail or money.
