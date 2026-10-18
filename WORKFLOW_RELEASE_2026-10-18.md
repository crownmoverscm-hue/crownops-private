# CrownOps workflow release 0.11.0-rc1

Prepared October 18, 2026 UTC / October 17 in Montréal. This candidate is saved and tested; it is not yet deployed. GitHub repository access is still required to publish it to the existing Railway service. No production client record, message, invoice or payment was changed by this review.

## What improved

| Area | Result |
| --- | --- |
| Client preparation | Prefilled private form, missing details first, saved progress, photos/videos and shared folder links, clear submission status and callback request. Commercial changes require staff review. |
| CRM intake | Quote forms and calls retain their source. Ambiguous messages show candidate services for an explicit match, preventing accidental attachment to the newest job. Evening tasks use Montréal's business date. |
| Sales and estimates | Clear ownership and next tasks, itemized/manual quote editing, materials, billing details and the local transportation fee. Contact-only corrections preserve a valid quote; commercial changes require review. |
| Booking and dispatch | Acceptance stays separate from confirmation. Date closures and existing assignment protections remain. Booking confirmations track real date/arrival changes rather than internal crew edits. |
| Crew and leads | Actual assigned names/truck, Manager My Jobs when assigned, crew brief, reviewed protocol guide, truck-check action and before/after checklists. |
| Invoice preparation | Actual hours start blank instead of inheriting estimated time. Verified unambiguous deposits apply once. Ambiguous credits need management review; they are never fabricated or silently moved. |
| Extra charges | Crew requests an item; management records client agreement and approves the price before it enters the draft. Pending requests prevent premature invoice issue. |
| Invoice access | Approved billing recipient, private one-invoice link, printable issued document and recovery link sent only to the still-approved address. Existing same-browser access can reopen safely. |
| Payment status | Current invoice/customer/CAD checks plus exact linked receipt evidence. Owner/Manager reconciliation avoids duplicate imported payments. Discrepancies, refunds and uncertain evidence require review. |
| Email delivery | Confirmed Gmail identity, reviewed Send-as/Reply-To defaults, clear English/French messages and current invoice checks before sending. Unknown deliveries have a management recovery process instead of automatic resends. |
| Phone/tablet/desktop | Improved contrast, readable cards, wrapped filters, compact day agenda, keyboard-aware navigation drawer and one visible invoice save action. |

## Validation of this source

- **399 Python/server tests passed**, including permissions, matching, client preparation, quote versions, date closure, media, crew preparation, invoices, deposits, payment reconciliation, email identity and access recovery.
- **13 JavaScript tests passed** for calculator guidance and schedule navigation. All **37** application JavaScript files passed syntax checks.
- The complete browser journey passed across Client, Sales, Manager, Crew and Owner: inquiry → information email/form → photo/folder/callback → itemized estimate → acceptance → verified deposit → Manager/crew/truck assignment → actual invoice → invoice email → private payer and moving-file balances. No page errors were recorded.
- Twelve role/device workspace combinations passed across phone, tablet and desktop. Separate invoice footer/save, management extra/deposit review and ambiguous Gmail matching journeys passed. Additional Sales/client detail journeys passed at all three widths.
- Browser records were disposable. Email MIME was generated and captured locally, not delivered through Gmail. QuickBooks responses were simulated; no real money moved. This validates application behavior, not live provider authorization, deliverability or settlement.
- The release targets the existing single-instance Railway volume. Final production startup/migration evidence and public health checks remain pending deployment.

## Publishing status

The connected Railway service still uses the existing production source. This release has not been pushed to `crownmoverscm-hue/crownops-private` and no fresh Railway deployment is claimed. Repository synchronization must preserve any newer remote work. The package contains no company database, uploads, credentials or access tokens.

A direct Railway upload route was blocked by the environment's automatic approval review because the Railway network connection was rejected. Reusing the old image would not publish this source. The available route is to connect GitHub with access to the company repository, then reconcile/push the tested source and verify the resulting deployment.

## Remaining launch steps

1. Publish this exact candidate after reconciling the current repository. Confirm successful Railway deployment, `/api/health` version `0.11.0-rc1`, restore-verified pre-migration backup and matching static assets.
2. Verify company configuration: actual staff/employee links, materials and billing rates, reviewed crew protocols, company Gmail/alias and Reply-To, QuickBooks company/CAD mappings, uploads/scanner and a recovery copy outside the service. Keep separate provider controls deliberate.
3. With an authorized company test client, verify one real inquiry/intake, received email and reply, prefilled details/media, sent/accepted estimate, booking confirmation, actual invoice, approved billing recipient and QuickBooks payment reconciliation. Inspect both systems and prove repeated clicks do not duplicate records. Application mocks cannot replace this check.
4. Run a small team pilot on actual phones and computers. Confirm permanent hosting, staff access/MFA and a named person responsible for support/recovery before general customer rollout.

## Recommended procedure and next improvements

Use START_HERE.md for each role. Keep a named owner and dated next task on every live inquiry. Review unresolved client changes before quoting, show actual assignments to the team and record actual billing work once.

After the pilot, prioritize changes using observed quote-preparation time, clients needing assistance, overdue/unclaimed inquiries, upload failures and payment exceptions. Review collecting/campaigns and model-generated follow-up writing remain later work. Current next-action suggestions are explained rules; this release does not claim that an AI evaluated every client or automatically contact anyone.
