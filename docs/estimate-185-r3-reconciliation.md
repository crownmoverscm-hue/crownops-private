# Estimate 185 — proposed r3 delivery reconciliation

This release implements the mechanism only. It does not record the real withdrawal, grant email approval, send, create a customer/estimate, relink an export, or enable acceptance/booking/billing. The owner must review the exact proposal and separately authorize the real operation after independent verification.

## Exact proposed owner action

Existing r2 → **QuickBooks delivery** → **Resolve r3 billing email change**. Review the original email, r3 email, separate recipient, fixed estimate, retained/withdrawn revision IDs and preservation statement. Enter a reason and tick the explicit preservation/withdrawal confirmation. Only then select **Record r3 withdrawal — no email approval**.

- Preserve r1 `QUOTE-8a7b31fbc00e4302`, r2 `QUOTE-08dd48a364a34f66`, r3 `QUOTE-af7136453bf64544` without changing any row contents, statuses, creation events or original save metadata.
- Retain the existing r2 → Estimate `185` mapping, Customer `70`, sandbox realm `9341458310238189`, company Sandbox Company CA 5634, total CAD **558.77** (485.99 + GST 24.30 + QST 48.48).
- Withdraw r3's intentional Bill-to change **from this existing estimate's delivery use only**. The chronological latest revision remains r3. No r4 is created and no document is rewritten.
- Original billing identity: `testonly.fictional@example.com`. Separate test recipient: `jonathanprobonas15@gmail.com`. No QuickBooks UPDATE or CREATE is performed.

Proposed reason (owner may revise): “I intentionally changed r3's Bill-to email to receive the sandbox test. Withdraw that billing change from Estimate 185 delivery use; retain the fictional billing identity and use Jonathan's address only as the separate test recipient.”

## Recorded evidence and protected audit

An immediate SQLite transaction appends exactly one immutable `sandbox_revision_reconciliations` row and one matching audit event (`Sandbox r3 delivery reconciliation recorded`). Both commit or neither does. Repeated/concurrent equivalent submissions return the existing decision without replacing the reason or timestamp or adding a second audit event.

The record contains server-authenticated Master-owner ID/name, server timestamp, owner reason, exact three quote IDs, retained r2 and withdrawn r3 IDs, realm/Estimate/Customer IDs, fixed total, original/withdrawn billing emails, separate recipient, complete family fingerprints, complete local-context fingerprint, provider fingerprint, saved financial snapshot hash and audit event ID. It explicitly records `email_approved=false`, `email_sent=false`, `downstream_actions_enabled=false`.

Database triggers reject UPDATE/DELETE of the decision and its audit event. There is no overwrite, reset, delete or generic settings route for it. If a recorded reconciliation becomes stale, this mechanism cannot replace it: stop for a new review.

## Eligibility and invalidation

Before: r3 is the highest revision, so r2 approval and SEND remain blocked.

After a valid owner-recorded resolution: only the specialized Estimate 185 workflow recognizes r2 as eligible for **separate email approval**. Reconciliation alone cannot send. The existing Master-owner approval, exact recipient/review binding and durable single-send slot remain required. The general estimate/acceptance/booking paths gain no exception.

The exact r1/r2/r3 Draft IDs and shared service must match. The complete normalized r2/r3 business contents must differ only at `inputs.billing.email`, from the original fictional address to the pinned test inbox. The only excluded comparison metadata is quote ID/revision/creation timestamp, draft key/request hash and lead-snapshot `updated_at`, matching the observed read-only comparison. Even this save metadata is included in the recorded complete-row fingerprints.

Prices, hours, taxes, scope, addresses, service date, terms, other contacts, statuses or creator changes cannot use the email-only exception. The saved r2 source must also match the immutable provider export: changing both r2 and r3 to the same different business data does not make that change eligible.

Every preview, approval transaction and send-claim transaction validates the same decision. New revisions (including r4), changed quote rows, service/client profiles, export mappings, saved delivery snapshot, create-attempt evidence or provider operations invalidate the local-context match. Full CompanyInfo/Customer/Estimate readback plus realm and recipient is fingerprinted; provider changes (including SyncToken, email, amounts, status and customer identity) invalidate the provider match. Scope/env/expiry/unresolved-write/CC-BCC/owner/send-slot guards still apply. A changed or inactive Master owner also invalidates authority. Any concurrent local change between preflight and transaction fails closed.

## Interfaces

- `GET /api/quotes/QUOTE-08dd48a364a34f66/sandbox-revision-reconciliation`: read-only proposal/current validity. Office-authorized roles may inspect.
- `POST` to that same path: active Master owner + session + CSRF, exact reviewed fingerprint/IDs/recipient, reason, and preservation confirmation required. No arbitrary quote/revision/recipient is accepted.
- Preflight, email approval and SEND share `current_for_delivery`; no path simply ignores a newer revision or checks a writable status flag.

No real endpoint POST is authorized by this document. Source implementation and simulated test evidence are distinct from applying a real decision.

## Independent Muse verification

1. Verify commit/build and that r3 still blocks real r2 approval/SEND before any real reconciliation. Read the GET proposal only; confirm the IDs, difference, fixed snapshot and recipient.
2. Run `python -m unittest discover -s tests -p test_sandbox_revision_reconciliation.py -v` against disposable fixtures. Confirm preservation, exact-only difference, role/CSRF denials, idempotent/concurrent recording, immutable audit, financial/scope/date/terms rejection, provider/customer/new-revision invalidation and races.
3. Run `python -m unittest discover -s tests -p test_quote_staging_release.py -v` and the `quote_calendar` browser suite. These use synthetic records and mocked providers, not the real r3. The browser suite covers four widths, explicit reconciliation, no automatic email approval/send, role visibility, existing calculation recovery and dismissal controls.
4. Confirm actual approval/sending/booking/invoice/deposit/payment state remains untouched. Do not create revisions or POST the real reconciliation, approval or SEND.
5. Return evidence to the owner. Only after the owner separately authorizes the real reconciliation should the owner execute the explicit control. Muse then independently reads the durable decision and unchanged snapshots/mappings. Email approval and actual SEND remain later, separately authorized actions.
