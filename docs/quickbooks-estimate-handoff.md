# QuickBooks quote, invoice and connection handoff — 0.12.7-rc2

## Intended staff and client journey

1. Sales creates/selects the client and prepares the quote in CrownOps. Include service date, contact details, addresses, labour, transport, materials, expiry and payment terms.
2. Open **QuickBooks delivery**, choose which end of the estimated range is displayed as the accounting amount, then **Prepare for review**. The full range and actual-hours wording remain on the document. This creates an unsent, itemized estimate.
3. Review the saved QuickBooks content, recipient, tax breakdown, total, expiry and booking terms. **Send through QuickBooks** is a separate explicit action. QuickBooks controls the email layout, logo and sender presentation; its company template must be configured and tested.
4. The client receives QuickBooks' estimate email and accepts there. CrownOps' private client portal remains available for information, photos and callbacks; it directs this estimate's acceptance to QuickBooks rather than offering a second acceptance form.
5. CrownOps checks linked estimates every two minutes when enabled in production, processing at most 25 each pass. Staff can also click **Check acceptance**. This is polling, not an instant webhook guarantee.
6. Matching provider acceptance records the acceptance and creates one unassigned **Tentative** job on the sold date/window, plus an office task. Assign the crew/truck and review deposit/capacity before final confirmation. Existing booking-confirmation controls remain in place.
7. Fully booked dates, another existing job for the same service, altered provider contents, changed company, expired estimates or stale client scope require office review. They do not silently confirm or duplicate a booking. A declined estimate raises a follow-up task; existing jobs are never automatically cancelled.

## Activation and test requirements

The new write flow defaults OFF. No production credentials were changed and no customer email was sent while implementing it.

Use the isolated staging setup in `tester-handoff-2026-10-21.md`. Configure a dedicated QuickBooks sandbox with reviewed item/tax/discount mappings and the test mailbox allowlist. In staging only, enable `CROWNOPS_ALLOW_QBO_SANDBOX_WRITES=1` and `CROWNOPS_ALLOW_QBO_ESTIMATE_DELIVERY=1`. Keep unrelated external actions disabled. Only `jonathanprobonas15@gmail.com` has owner approval for real test email receipt; fictional `.invalid` addresses in automated tests use mocked providers only.

Before production activation, verify:

- Actual Canadian QuickBooks account/plan supports accepting this API-created estimate from the email. The documentation describes acceptance, but this implementation is not live-account certified.
- Company legal name, logo, reply/contact identity, EN/FR presentation, customer and billing information, all item quantities/rates, local transport amount (never silently assume the $35 placeholder), materials, discount and TPS/TVQ are correct in the actual emailed estimate/PDF.
- Full terms, included/excluded scope and expiry survive QuickBooks' rendering, including long description-only lines. Reject truncation; do not enable if critical terms disappear.
- Owner confirms receipt in the approved inbox, opens and accepts, then checks one tentative service in CrownOps with no automatic crew assignment, payment or final confirmation. Provider `EmailSent` is not inbox-delivery proof; `TxnStatus=Accepted` can also result from a staff update in QuickBooks and is recorded as provider-reported acceptance, not a claimed verified client click.
- Test refusal, duplicate send clicks, delayed/failed send response, a closed schedule date, existing job/revised quote, changed provider total/taxes, wrong company and role restrictions.
- Client information collection and final booking confirmation still work; no automatic staff notification or other recipient is triggered by the test.

Enable production delivery only after that walkthrough and credential/security review, using the production QuickBooks write gate plus the feature flag. Railway's October 21 deployment/security incident prevents claiming this release is live; recheck the provider before deployment. Never copy production grants or data into staging.

## Existing drafts and unresolved operations

The old **QuickBooks draft** and legacy finalize/sync paths created summary-only estimates with no environment/company/content snapshot. They are not silently adopted or emailed. If a revision already has such an export, review/close that draft in QuickBooks and create a revised CrownOps quote for the new delivery flow. It does not send or create a second remote estimate on that revision.

Prepared estimates retain source and provider snapshots. A timed-out create/send remains unresolved and is never blindly retried. **Check acceptance** can reconcile a completed send. If the provider still cannot confirm it, inspect QuickBooks before any recovery. There is deliberately no automatic resend button. New delivery and old CrownOps email/export paths are mutually exclusive for the same revision.

## Final invoice and payment journey

1. The assigned crew prepares the invoice from the accepted service, records actual hours and approved materials/extras, checks the billing recipient and finalizes it. Management owns the approved rates/taxes and any corrections. Set an agreed due date before issuing when applicable.
2. Open **Review QuickBooks invoice**. If it is not synchronized, an owner/manager can synchronize the issued invoice directly from this dialog. Ordinary crew do not receive accounting-creation or configuration authority.
3. New exports link the accepted estimate through QuickBooks `LinkedTxn` when the estimate was prepared with the new flow. Company, customer, status and contents must match. Revisions preserve existing provider transaction links.
4. Review the actual provider invoice, due date, recipient, total and current balance, then **Send through QuickBooks**. Assigned crew may send their own job's issued/reconciled invoice. Customer messages are friendly EN/FR text; internal billing-approval references are no longer exported as the customer memo. Payment terms are not silently truncated.
5. QuickBooks controls the actual email/PDF layout and which online payment methods are offered. Its merchant/payment setup, branded company template and email delivery require the real sandbox/account walkthrough. A connected accounting account alone does not prove payment collection works.
6. Native invoice email defaults OFF behind `CROWNOPS_ALLOW_QBO_INVOICE_DELIVERY=1`, in addition to the existing environment-specific QuickBooks write gate. The approved staging recipient allowlist still applies. There is no automatic email worker.
7. A separate opt-in `CROWNOPS_ALLOW_QBO_RECEIPT_SYNC=1` enables periodic read/reconciliation batches. Every five minutes after the prior batch completes, the production worker checks up to 25 issued invoices, rotating through them. Only exports with saved matching environment/company provenance are included. This imports only proven receipt allocations through the existing reconciliation rules. It never creates provider payments, charges cards, refunds or changes QuickBooks. Large accounts take multiple batches; this is not an instant webhook or bank-settlement confirmation. Legacy unscoped exports remain manual-review items.
8. Imported deposits/receipts are applied once. Ambiguous deposits, credits, refunds, changed totals and unmatched local receipts still require finance review. Native and CrownOps invoice email paths block a second sender once delivery has started. Unknown native sends cannot be retried blindly; refreshing provider status can reconcile a confirmed send without emailing again.

## Clear company connection status

The primary Connections card separates **authorization saved**, **account verified**, **permission needed**, temporary retry problems and failed account checks. It displays the company name, sandbox/live mode, last account-read check, tax/item review state and each feature's enablement. Advanced controls are collapsed to avoid repeating the main card.

**Check QuickBooks connection** reads CompanyInfo only. A CompanyInfo entity ID is not assumed to equal the OAuth realm ID. Verification is associated with the authenticated environment/realm, so switching companies does not reuse the old verification. A failed check retains the saved grant and shows a generic recovery message without exposing provider details. Only an owner can connect/disconnect; office staff may perform this nonfinancial read check. The card explicitly states that an account read does not certify quote/invoice email, acceptance or payment.

## Remaining activation checks

- Restore authenticated hosting access and resolve the Railway security/credential review before a launch deployment.
- Provision isolated staging, dedicated QuickBooks sandbox and fictional role accounts. Do not copy production data or provider grants.
- Complete one approved-inbox quote → acceptance → tentative schedule → final invoice → payment/reconciliation walkthrough; inspect actual EN/FR email/PDF fields, tax breakdown and due dates.
- Verify the company/legal billing settings, review ambiguous deposits and test a small staff pilot on actual phones. Emulated widths do not replace device testing.

No real mailbox, QuickBooks write or payment was used during the implementation tests. The shipping/activation status must be reported separately from source and CI success.

## Evidence and boundaries

Automated tests use disposable data and provider fixtures. Browser checks at 320/390/768/1440 pixels are viewport emulation, not real-device or actual-email certification. These tests do not establish inbox delivery, QuickBooks sandbox functionality, real tax configuration, OAuth health or production deployment.

Official API reference: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/estimate — create/read and explicit `POST /v3/company/<realmID>/estimate/<estimateId>/send?sendTo=<emailAddr>` with `application/octet-stream`.

Canadian customer workflow: https://quickbooks.intuit.com/learn-support/en-ca/help-article/job-estimates/create-send-estimates-quickbooks-online/L0kOXRjoP_CA_en_CA — QuickBooks estimates can be accepted/declined from the email and their status updates there.
