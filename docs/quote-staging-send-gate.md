# Existing Estimate 185: staging safeguards and independent test gate

Scope: existing PR #88, `fix/core-launch-workflow`. Production and Staff AI are excluded. No real pricing changes, accounting entity creation, send, acceptance, booking, invoice, deposit or payment is authorized by this release.

## What changed

- Quote staging is identified independently by its service ID, hostname or database path. Its Gmail policy is **synthetic data only, no mailbox access**. Token access, Gmail HTTP reads, manual sync and forced/background intake all enforce this boundary before network access or import writes. No production Gmail labels, credentials or processing are changed.
- Acceptance/booking/billing mutations are blocked on this service. Internal acceptance/job/deposit helpers and general accounting writes are also guarded. Changing a broad write flag does not lift these restrictions.
- Corrected successful Calculate clears the current editor's old error. A closed/replaced editor ignores a late successful result. Cancel, X, Escape, Keep editing, Discard and draft-save semantics are retained.
- The existing QuickBooks delivery view shows the pinned estimate's saved export, Customer ID, logical service parent and revision history. It compares the saved original with fresh provider readback; it does not reconstruct the sold amount from the quote's editable range. A newer revision remains visible and blocks sending. The original `payload_json`, `remote_json` and `source_hash` are never overwritten by the send-only path.
- The old create route cannot recreate the pinned quote's customer or estimate if attempt evidence is missing. Existing Ready preparation can only return its existing ID.

## Read-only reconciliation

Owner: `GET /api/quote-staging/data-review` returns record counts and flags Gmail records without fictional test markers. It returns no correspondence content and deletes nothing. Test markers are a screening aid, not proof a record is fictional. The deployment emits one `QUOTE_STAGING_READONLY_REVIEW` log with counts and whitelisted stored linkage, for reconciliation when browser authentication is unavailable. It does not call Gmail or QuickBooks. Stored evidence is not fresh provider evidence.

Authorized sales/management: open r2 → **QuickBooks delivery**, or `GET /api/quotes/QUOTE-08dd48a364a34f66/sandbox-send-preflight`.

Required linkage: quote `QUOTE-08dd48a364a34f66` r2; r1 `QUOTE-8a7b31fbc00e4302` under the same service; realm `9341458310238189`; company `Sandbox Company CA 5634`, CA; existing Estimate `185`; existing Customer ID must agree between saved payload and external mapping. Missing or conflicting evidence blocks action; never recreate.

Fixed snapshot: labour 3 × fictional $100; fictional transportation $150; fuel $35.99; subtotal $485.99; GST $24.30; QST $48.48; total CAD $558.77. These are not approved business rates.

## SEND remains disabled

`CROWNOPS_QBO_TEST_SEND_ESTIMATE` is absent/empty by default. No route grants approval. The specialized owner-only `POST /api/quotes/{qid}/sandbox-send` rejects before provider access while disabled. General `/send` and `/sync` cannot bypass it.

A later separately authorized test additionally requires an exact reviewed approval record (`sandbox_estimate_185_send_approval_v1`) and evidence of sender, Reply-To, no provider-default CC/BCC, and an evidence reference for this sandbox. Approval binds the current review hash, exact recipient, Estimate 185 and owner identity. A flag alone is insufficient. Do not set this record or flag under this development authorization.

Only proposed recipient: `jonathanprobonas15@gmail.com`. Existing fictional Customer email: `testonly.fictional@example.com`. Existing Estimate BillEmail is displayed separately. Intuit's `sendTo` can update Estimate.BillEmail.Address; the Customer email is not intentionally updated. No CC/BCC is requested; absent fields alone do not prove provider defaults are absent. Unknown sender/reply-to/default behavior is labelled and blocks sending. The saved document is not an exact rendered email/header preview. API `EmailSent` is not proof of inbox receipt or an online approval link.

Intended operation, only after separate approval: `POST https://sandbox-quickbooks.api.intuit.com/v3/company/9341458310238189/estimate/185/send?sendTo=jonathanprobonas15@gmail.com`.

Before provider POST, a SQLite immediate transaction claims `sandbox_estimate_185_send_attempt_v1`. There is no reset/retry endpoint. Sending, Unknown and Sent consume the slot. Timeouts, failed response checks or failed readback stay Unknown. The send handler never calls CREATE, acceptance synchronization, job/deposit helpers, invoice or payment APIs. Even a successful send does not authorize acceptance or booking.

All broad external, QBO write, delivery, receipt, legacy bridge and worker gates must remain OFF. Do not enable them to make preflight pass. The preflight shows their runtime values and effective side-effect gates separately.

## Acceptance preparation only

`POST /api/webhooks/quickbooks/quote-sandbox` is OFF unless `CROWNOPS_QBO_TEST_WEBHOOK_CAPTURE=1`. Do not enable it yet. It requires the dedicated sandbox verifier secret and HMAC-SHA256 `intuit-signature`, the exact realm, and Estimate 185. It accepts current CloudEvents and legacy notification shapes, stores only event identity/type/time, and deduplicates durably. It never applies acceptance.

Owner `POST /api/quote-staging/events/reconcile` reads the provider through the same strict preflight, records successful readback or a recoverable Failed event, and permits later retry of failed reads. Delayed events read current provider state rather than replaying old status. No event path calls the normal acceptance synchronizer. `Accepted`, `AcceptedBy` and `AcceptedDate` are provider observations, not independently verified customer-click evidence. Genuine acceptance proof and exactly one tentative unassigned booking need a later separately approved implementation/test gate. Production webhook subscriptions are untouched.

## Muse sequence — no writes to providers

1. Verify the deployed build matches the corrective commit and CI. Confirm production and Staff AI deployments are unchanged.
2. As owner, read data-review counts; if any Gmail correspondence is not proven synthetic, report it privately. Do not delete it. Test the Gmail connection/read and both import routes plus forced intake: all must be blocked before company-mailbox access.
3. Open existing r2; clear service date → Calculate → visible error. Restore November 15, 2026 → Calculate successfully → error disappears and dialog remains usable. A later invalid attempt must show its error again. Exercise Cancel/X/Escape and Keep editing/Discard. Do not save an unnecessary new revision.
4. Open QuickBooks delivery. Verify r1/r2 parent, persisted Customer ID, Estimate 185, immutable snapshot and both email addresses. Confirm unknown delivery details are explicit and no send action is enabled. This reads the existing provider records only.
5. Do not POST CREATE, SEND, acceptance, job, invoice, deposit or payment operations. Automated duplicate/timeout/webhook cases use synthetic local records and simulated providers; they are not live acceptance evidence.

## Approval required later

After reconciliation, recipient/default/header review and capability limitations are resolved, the owner must explicitly authorize **one official QuickBooks sandbox email of existing Estimate 185, CAD $558.77, to jonathanprobonas15@gmail.com**, acknowledging the Estimate BillEmail change and verified no-CC/BCC behavior. Approval excludes customer/estimate creation, acceptance, booking, invoices, deposits and payments. Do not interpret this document as that approval.

Official references: [Estimate API](https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/estimate), [Canadian estimate workflow](https://quickbooks.intuit.com/learn-support/en-ca/help-article/job-estimates/create-send-estimates-quickbooks-online/L0kOXRjoP_CA_en_CA), [webhook validation](https://static.developer.intuit.com/output_html/qbo/docs/develop/webhooks/configure-webhooks.html), [webhook recovery](https://static.developer.intuit.com/output_html/qbo/docs/develop/webhooks/best-practices.html). The customer approval experience of an API-sent estimate in this exact Canadian sandbox remains unproven until a later authorized delivery test.
