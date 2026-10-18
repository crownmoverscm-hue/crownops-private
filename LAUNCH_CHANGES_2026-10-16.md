# CrownOps launch changes — October 16, 2026

## Release status

**Deployed successfully to the existing CrownOps production service.** Version `0.7.0-rc1`, deployment `0ace549c-ee1e-4f1b-9a70-4421ecad5fbf`, healthy on October 16 at 07:01 UTC / 03:01 Montreal time. This was a direct source upload; the changes have not been pushed to GitHub.
Source base: the supplied main-branch ZIP, commit `247844fc1308abf19eb5cc0e0c1efb2be621a581`.
Railway's previous live deployment matched that source base exactly. Runtime logs verified a restored recovery copy before migration and confirmed all four existing accounts were preserved. See DEPLOYMENT_2026-10-16.md for live evidence and remaining launch work.

## Team access

| Account | Access |
| --- | --- |
| Owner | Full company administration; existing master-owner protections remain. |
| Manager | Same company, billing, payroll, staff-management, expense and setup access as an ordinary Owner. |
| Sales | Existing CRM, estimates, clients, scheduling and follow-up permissions. |
| Crew | Assigned-job invoice preparation, actual hours/materials, finalization and direct email delivery. |

Only the master owner can assign Owner access or manage another Owner. Managers cannot disable the master owner, grant themselves Owner access or modify their own role.

Managers can keep a linked employee record and be assigned to a service without changing their account role. My Jobs uses that employee link; All Jobs remains available. Crew planning authority alone does not grant access to another team's invoices, collection records or payment links. Current assignments are checked on each invoice request.

Existing users, passwords, employee links, sessions and authenticator records are preserved by the Manager-role schema migration. Before changing an existing production role schema, startup creates a portable database/upload recovery archive and verifies a separate restore. A failed recovery check stops startup before migration. The migration also compares every account row and checks database references before committing. Existing Dispatch accounts are not silently promoted to Manager. An Owner can change a staff account to Manager in Team access after release.

## Private expenses

Owners and Managers get an Expenses section with date, vendor, category, before-tax amount, tax, status, description, reference, optional move/truck, notes and a private receipt-folder link. The page supports filters, pagination, CSV export, edits and voiding with a reason. Records and audit history are retained. Retries do not create duplicate expenses; stale edits are rejected.

Sales, Accounting, Dispatch and Crew cannot read or change this ledger through the interface or direct API routes. Inventory/material purchase-cost fields are also limited to Owners and Managers.

Receipt links retain their external sharing permissions. This release stores links rather than uploading expense receipts. Expense entries and CSV exports do not automatically create QuickBooks bills or expenses, and marking an expense Paid does not transfer money.

## Crew invoices and QuickBooks payments

1. Open an assigned move and prepare its invoice from the accepted estimate and approved billing policy.
2. Enter actual hours and approved materials; complete the job and finalize the invoice.
3. Review the total, credited payments, remaining balance and saved recipient.
4. Choose **Email final invoice**. Delivery status appears on the invoice. Repeated delivery requests do not resend the same revision. An uncertain send remains blocked for management review.
5. For QuickBooks payment, an Owner/Manager first synchronizes and reconciles the current issued invoice in QuickBooks.
6. Choose **Check QuickBooks payment link**. When a link is available, staff can open it or choose **Email invoice with QuickBooks link**.

The payment-link check reads QuickBooks' existing `InvoiceLink`; it does not create a charge or enable merchant settings. It verifies the current revision, customer, CAD currency, total and outstanding balance, rejects pending receipts or an unresolved existing checkout, and accepts only HTTPS Intuit-hosted links. QuickBooks online payments must be enabled for the company/invoice. A test invoice cannot request a production payment; sandbox links are clearly marked.

Direct invoice delivery regenerates the email's current financial summary before sending. Prepared Communications drafts remain subject to the existing review/send workflow. Old `invoice-mail` and current `invoice-delivery` event keys are both recognized to prevent duplicate delivery. Correcting an invoice cancels unsent drafts under either key.

The payment screen now directs staff to QuickBooks rather than offering a new Square final-balance link. Existing Square checkout records and their reconciliation/cancellation controls remain available. Existing deposit-provider behavior is unchanged. QuickBooks receipt posting records a verified, allocated receipt; it does not collect money. This release does not automatically import QuickBooks settlements or refunds into CrownOps.

Reviews and testimonial changes were excluded as requested.

## Verification

- 241 Python server tests pass, including Manager migration, preserved authenticators/sessions, immediate access revocation, private expense permissions, duplicate prevention, stale edits, backup restoration and assigned-crew invoice delivery.
- The end-to-end test covers Sales creating the client/estimate, portal acceptance, verified deposit, Manager dispatch with employee link, assigned crew completion/final invoice, email delivery, receipt reconciliation, and idempotent QuickBooks invoice/payment posting.
- 13 Node calculator/navigation tests pass; every application JavaScript file passes syntax checking and Python compilation passes.
- Chromium checks at 1440×1000, 820×1180 and 390×844 cover expense creation, edits and voiding. The phone check covers crew invoice actuals, finalization, checked QuickBooks link and email delivery. No JavaScript page errors were reported. Mobile expense rows use cards.
- Email and QuickBooks transports in these tests are mocked. No real customer email, charge or accounting write was made. This is browser viewport coverage, not certification on physical phones or Safari.

## Remaining launch work

1. Put this source in the company GitHub repository before another repository deployment. GitHub still contains the prior source; a new push/redeploy from it could replace these changes.
2. Verify real Owner/Manager/Crew logins, Manager employee links, My Jobs/All Jobs, saved truck/crew labels and expense privacy on the deployed app. No existing Dispatch accounts were silently promoted.
3. Download the verified portable recovery copy from Launch checklist and keep it privately outside Railway. Keep the original encryption key in the company vault. Privileged-account MFA still needs account-level verification.
4. Configure recurring backup protection. Railway rejected manual volume snapshots and daily schedules because this workspace is not on Pro; no subscription was changed. The verified app recovery copy exists on the persistent volume, which is not offsite protection.
5. Finish one controlled live email and QuickBooks payment/reconciliation test using an explicitly designated company test client. Do not mark launch complete based on mocked provider results.

Deployment and public health/source checks are complete. Live signed-in role workflows and provider tests remain outstanding. This release does not establish that production is ready for unrestricted launch.

## QuickBooks reference

Intuit's Invoice API documents `InvoiceLink`, the `include=invoiceLink` query parameter and the online-payment prerequisites:
https://developer.intuit.com/docs/api/accounting/invoice
