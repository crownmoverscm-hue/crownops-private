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

## Master-owner approval and separate SEND

Location: existing quote r2 → **QuickBooks delivery** → **Approve one sandbox email**.

The old build had only a backend approval record with no writer route and an always-disabled button. The new `POST /api/quotes/{qid}/sandbox-send-approval` is restricted to the active Master owner, independently checked in the database on every approval/send. Session authentication and CSRF apply. Sales, crew, managers and other owners cannot approve or send; no role, actor or timestamp supplied by the browser is trusted.

The form displays the existing Canadian sandbox, realm, Customer **70**, Estimate **185**, r2, fixed **$558.77**, immutable snapshot fingerprint, existing BillEmail, and only intended recipient **jonathanprobonas15@gmail.com**. The owner records where they checked this sandbox's defaults/estimate and explicitly confirms **no CC, BCC or send-me-a-copy**, acknowledges the BillEmail change, and acknowledges unknown actual delivery headers/link support. These are owner attestations, not fabricated API/header verification.

Approval stores its version, authenticated owner ID, server timestamp, estimate/quote/realm, exact recipient, current BillEmail, total, snapshot/review hashes and confirmations. A detailed audit event preserves that record. It neither claims the send slot nor calls a provider POST. Repeating the same approval returns its original timestamp. A changed provider revision/content, saved snapshot, source, recipient or company contact requires fresh review; a newer quote, expiry, known CC/BCC or any send attempt blocks approval. No route resets a consumed send slot.

The dedicated `CROWNOPS_QBO_TEST_SEND_ESTIMATE=185` capability may be enabled **only on isolated quote staging** for this owner-controlled workflow. It does not approve or send by itself. Every broad external/write/email/acceptance/worker flag stays OFF. The visible SEND button requires both this narrow capability and the recorded, current Master-owner approval. No other estimate, recipient or Customer ID is accepted. The user will complete actual approval and sending after Muse independently tests the controls. Developers must not approve or send on the real staging database.

After approval, a distinct **Send ONE QuickBooks test email** button requires a separate confirmation. Its owner-only `POST /api/quotes/{qid}/sandbox-send` reads the provider again, checks current approval and source, then claims a durable slot in an immediate transaction before any POST. Concurrent or repeated requests cannot claim it again. Sending, Unknown and Sent consume the slot. A timeout or unconfirmed readback remains Unknown; no automatic retry or reset endpoint exists. The UI does not retry transport requests. Reconciliation is read-only; an unknown outcome is not permission to resend.

Intended external action: `POST https://sandbox-quickbooks.api.intuit.com/v3/company/9341458310238189/estimate/185/send?sendTo=jonathanprobonas15@gmail.com`. This reuses the estimate and customer; it does not CREATE either. `sendTo` can update Estimate.BillEmail.Address, while the fictional Customer.PrimaryEmailAddr remains unchanged. Original saved payload, remote snapshot and source hash remain immutable.

## What is known before delivery

- Sender: QuickBooks controls it; CrownOps Gmail is not used. Exact actual From/header is **unverified** before this controlled test.
- Reply-To: CompanyInfo customer-facing contact email is displayed if available, explicitly **not** proof of the outgoing Reply-To header.
- CC/BCC: CrownOps requests none. Known estimate CC/BCC fields block approval/send. Absent API fields do not establish absent company defaults. The Master owner must check the sandbox defaults, Estimate 185 and any self-copy option and record that review before approval. The UI labels this as owner-confirmed, with actual headers still unverified.
- Recipient: current BillEmail and proposed sendTo are separate fields; approval acknowledges the disclosed billing-email change.
- Rendering/approval: API does not supply an exact email/header preview here. Canadian QuickBooks documents approval from estimate email, but an API-sent email from **this** sandbox must still be tested. `EmailSent` proves neither receipt nor an approval link. `Accepted` can be manually set and is not independent customer-click evidence.

No additional approval of bookings, acceptance sync, crew/truck assignment, invoices, deposits or payments is conveyed by email approval or SEND.

## Acceptance preparation only

`POST /api/webhooks/quickbooks/quote-sandbox` is OFF unless `CROWNOPS_QBO_TEST_WEBHOOK_CAPTURE=1`. Do not enable it yet. It requires the dedicated sandbox verifier secret and HMAC-SHA256 `intuit-signature`, the exact realm, and Estimate 185. It accepts current CloudEvents and legacy notification shapes, stores only event identity/type/time, and deduplicates durably. It never applies acceptance.

Owner `POST /api/quote-staging/events/reconcile` reads the provider through the same strict preflight, records successful readback or a recoverable Failed event, and permits later retry of failed reads. Delayed events read current provider state rather than replaying old status. No event path calls the normal acceptance synchronizer. `Accepted`, `AcceptedBy` and `AcceptedDate` are provider observations, not independently verified customer-click evidence. Genuine acceptance proof and exactly one tentative unassigned booking need a later separately approved implementation/test gate. Production webhook subscriptions are untouched.

## Muse sequence — no writes to providers

1. Verify the deployed build matches the corrective commit and CI. Confirm production and Staff AI deployments are unchanged.
2. As owner, read data-review counts; if any Gmail correspondence is not proven synthetic, report it privately. Do not delete it. Test the Gmail connection/read and both import routes plus forced intake: all must be blocked before company-mailbox access.
3. Open existing r2; clear service date → Calculate → visible error. Restore November 15, 2026 → Calculate successfully → error disappears and dialog remains usable. A later invalid attempt must show its error again. Exercise Cancel/X/Escape and Keep editing/Discard. Do not save an unnecessary new revision.
4. Open QuickBooks delivery. Verify r1/r2 parent, persisted Customer ID, Estimate 185, immutable snapshot and both email addresses. Confirm unknown delivery details are explicit. As a non-Master owner or office user, approval/SEND must be unavailable. As Master owner, inspect the approval fields without completing real approval. This reads existing provider records only.
5. Muse must not complete real Master-owner approval or POST CREATE, SEND, acceptance, job, invoice, deposit or payment operations. Confirm the approval-only and single-use tests pass against disposable databases/mocked providers. Then hand back to the owner to check sandbox CC/BCC/self-copy settings, complete approval in CrownOps and separately choose the controlled SEND. Automated duplicate/timeout/webhook cases use synthetic local records and simulated providers; they are not live acceptance evidence.

## Approval required later

After reconciliation, recipient/default/header review and capability limitations are resolved, the owner must explicitly authorize **one official QuickBooks sandbox email of existing Estimate 185, CAD $558.77, to jonathanprobonas15@gmail.com**, acknowledging the Estimate BillEmail change, owner-checked no-CC/BCC/self-copy settings and explicitly unverified delivery headers/approval link. Approval excludes customer/estimate creation, acceptance, booking, invoices, deposits and payments. Do not interpret this document as that approval.

Official references: [Estimate API](https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/estimate), [Canadian estimate workflow](https://quickbooks.intuit.com/learn-support/en-ca/help-article/job-estimates/create-send-estimates-quickbooks-online/L0kOXRjoP_CA_en_CA), [webhook validation](https://static.developer.intuit.com/output_html/qbo/docs/develop/webhooks/configure-webhooks.html), [webhook recovery](https://static.developer.intuit.com/output_html/qbo/docs/develop/webhooks/best-practices.html). The customer approval experience of an API-sent estimate in this exact Canadian sandbox remains unproven until a later authorized delivery test.
