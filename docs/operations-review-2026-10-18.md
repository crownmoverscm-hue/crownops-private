# CrownOps operations review — October 18, 2026

## This release

- Schedule runs Monday–Sunday, including Today, date jumps, saved views, month/year boundaries and daylight-saving transitions. The payroll period is still chosen independently.
- Crew can submit their own actual hours. Assigned team leads and managers can enter team hours once and adjust individual exceptions. Existing recorded hours take precedence over invoice suggestions. Include paid travel and subtract unpaid breaks.
- Payroll shows recorded hours, approved hours and missing/submitted entries. Inactive staff with worked time remain visible. Minute totals are aggregated before rounding.
- Only completed-service hours can be approved. Approval captures the current pay rate. Changed hours lose approval; stale form submissions and changed review snapshots are rejected. Changing a future pay rate does not silently recalculate an approved entry.
- Owners/managers can export approved hours independently of invoice issuance. Missing/submitted time or missing approved rates blocks export. The CSV includes exact minutes, job/time IDs and versions for reconciliation. It is a payroll handoff, not a pay run: overtime, statutory adjustments, vacation pay, deductions and payments remain with the payroll provider. Do not combine this export with the older invoice-based payroll export for the same services. Multi-day work should use separate job phases so each day's hours has its correct service date.
- Expenses now contains three views: Expense ledger, Job margins, QuickBooks review. Cost and wage details remain private to Owners/Managers, with each employee retaining access to their own pay information.
- QuickBooks fetch reads posted Purchase and Bill records for a selected date range. It does not write to QuickBooks or make payments. Records are staged; the manager matches existing expenses or adds a separate cost and assigns a job. Remote IDs are scoped by company/environment/type. Changed records need fresh review; a bill's payment is not counted as another expense.
- Sandbox records, refunds/credits, foreign/unknown currencies, missing tax amounts and invalid amounts cannot silently enter the CAD expense ledger. Company expenses, payroll payments, asset purchases and tax remittances need accounting review. A partially paid bill remains Unpaid with its actual balance visible in the review queue.
- Job margin uses the latest preserved issued invoice, approved straight-time labour and linked expenses before tax. Missing hours/rates or changed linked QuickBooks costs withhold the margin. Company overhead is shown separately. Unrecorded costs, credits, employer costs and tax recoverability mean this is not accounting net profit.
- Phone date controls and payroll totals are more compact and readable. Import instructions can be expanded when needed.

## Section-by-section findings

The navigation review covered 26 owner sections and the pages available to Manager, Sales and Crew, across six viewport widths. It checked page rendering, navigation, clipping and browser errors. It is not a claim that every real-provider action was executed. The changed hours/cost workflows were also exercised end-to-end on fictional records.

| Section | Working path / finding | Remaining useful next step |
| --- | --- | --- |
| Today | Role-specific action queue and links to current work. | Keep this as the daily starting point; reduce operational exceptions rather than add more metrics. |
| Sales & clients | Shared client record, assignment, intake review and next action. | Verify operational Wix site access and inspect existing automations before enabling a second intake route. |
| Estimates | Draft editing, materials, share/print controls, status and client-view tracking. | One real staff-to-client quote walkthrough, including delivery, acceptance and follow-up. |
| Follow-ups | Ownership, due dates and outcomes are centralized. | Use one next action per client; do not duplicate reminders in Wix and CrownOps. |
| Communications | Inbox, outgoing mail and call context. | Confirm the intended company sender and real delivery/replies. |
| Schedule | My Jobs/All Jobs, crew/trucks and availability. Now Monday–Sunday. | Review staged imported services that still need confirmed assignments or missing dates. |
| Invoices & payments | Versioned issued documents and payment/delivery actions. | Complete a real production QuickBooks invoice/payment reconciliation. |
| Client portal / documents | Client detail collection and document access remain available. | Run the pilot with a real client on their own phone and verify photos and private links. |
| Time off | Accessible from operations and crew navigation. | Have the pilot team submit a genuine request and verify the manager procedure. |
| Incident reports | Available to staff with role-scoped records. | Confirm the company escalation contact and response procedure. |
| Employee directory / contacts | Assigned services and private wage access. | Verify every active staff account is linked to the correct employee, including managers who work on crews. |
| Payroll & hours | Actual hours are independent of invoices; completeness, approval and CSV handoff added. | Confirm hourly rates and the company's payroll period; reconcile one exported period with the payroll provider. |
| Expenses | Ledger, QuickBooks review and recorded job margins added. | Connect/review the real company file; reconcile a paid expense, unpaid bill and existing duplicate before routine use. |
| Team access | Owner/manager access and individual accounts. | Verify active roles and remove obsolete accounts using the existing controls. |
| Pricing & rates / materials | Existing catalogue and agreed-price workflows retained. | Confirm current rates and material selling/cost prices. |
| Fleet | Vehicle status and service records retained. | Complete required vehicle information and review current restrictions. |
| Connections | Saved credentials and automatic token renewal already exist. | Wix authorization is still blocked; QuickBooks live accounting and email delivery need real-account validation. |
| Operating rules / company setup | Central business rules and company information. | Owners must verify legal company details, tax registrations, payment terms and crew protocols. |
| My account / Help & install | Account, installation and app-update help available. | Installed users should use the in-app update/relaunch control to load the release. |
| Launch checklist | Remaining production checks are collected in one place. | Run a small staff pilot after the real email and accounting walkthrough. |

## External access and accounting scope

Wix was retried for site `8911c013-d113-41ff-bca5-8484b699083b` with both exposed connections. The operational site context was unavailable; the new connection's site-properties call returned reauthentication required. No operational quotes/invoices were imported during this review. Existing accepted-quote and paid-invoice automations should be inspected before adding another sync.

QuickBooks imports are an explicit fetch/review workflow, not an automatic live bank feed. Bank transactions awaiting review must first be handled in QuickBooks. Deletions outside the fetched results and supplier credits are not automatically reconciled; use the accounting file as the source of truth. The current release provides tested adapter behavior but has not certified the user's production company connection.

Technical sources checked:

- [Intuit query pagination and syntax](https://developer.intuit.com/app/developer/qbo/docs/learn/explore-the-quickbooks-online-api/data-queries)
- [Intuit Purchase entity: totals, tax-inclusive amount, credits, currency and date](https://static.developer.intuit.com/sdkdocs/qbv3doc/ippphpdevkitv3/entities/files/IPPPurchase.html)
- [Intuit Bill entity: amount, currency and outstanding balance](https://static.developer.intuit.com/sdkdocs/qbv3doc/ippphpdevkitv3/entities/files/IPPBill.html)
- [Intuit support on pending bank-feed API access](https://help.developer.intuit.com/s/question/0D5TR00001VhrZq0AJ/is-there-a-supported-api-scope-or-partner-program-to-read-or-accept-pending-bankfeed-for-review-transactions-in-qbo-if-not-are-bank-rules-with-autoadd-the-recommended-mechanism-for-automating-recurring-merchant-categorization-on-our-own-file)

## Verification

- Existing full Python suite plus added financial/hour regression cases; final CI must pass before merge.
- 15 Node tests, including Monday–Sunday date boundaries and saved-view normalization.
- 214 navigation/page/role/viewport combinations, no detected overflow, clipped content or browser errors.
- Real Chromium interactions on fictional data: Crew self-entry, Manager team entry/approval/export, Owner cost review, Sales access restrictions, QuickBooks staged expense import, margins and phone/tablet/desktop layouts.
- Additive database migration preserves time records and runs the production backup/restore check before adding columns/tables.
