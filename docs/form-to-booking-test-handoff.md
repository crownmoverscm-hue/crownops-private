# Form to booking readiness and Muse test handoff

## Verified release and implementation boundaries

Production was verified at 0.12.7-rc4, deployment c8566dec-8f71-4d08-8aef-6a0773af9acd, commit 195cf2587f435fe43a670ecba2f4f9e80ada2c25. Health returned ok. The release's isolated automated checks passed 559 server tests, 20 JavaScript tests and ten browser suites. This is not certification of real Gmail delivery, QuickBooks acceptance, payment processing or customer automations.

The next patch prepares a reviewable deposit-request email when a newly accepted estimate has a valid tentative job. English/French copy includes the sold date/window, outstanding local deposit, duplicate-payment caution and a prefilled portal link. It uses only approved Interac instructions, otherwise asks the client to contact Crown. It creates no checkout and sends nothing. Repeated checks reuse the same draft. Send-time validation cancels requests made stale by payment, job, policy or client changes. It does not automatically migrate past accepted quotes.

Website intake currently polls Gmail every two minutes through configured feeds. Client matching can require office review; Inbox must retain the unmatched event. Local calculator drafting requires sufficient details and approved pricing, and never auto-sends. Imported client communications are suppressed until office review/activation.

QuickBooks sends official estimates/invoices only through explicit reviewed actions and enabled gates. Acceptance polling is every two minutes in batches up to 25. Accepted estimates become tentative jobs when scheduling checks pass; fully booked/missing-date/changed-scope cases remain office review. A provider status is not proof of a client click. No email-open count should be invented if the provider does not expose it.

Deposits currently use approved Interac verification or the existing Square checkout, not a new native QuickBooks deposit workflow. A verified deposit still goes through availability/capacity confirmation checks. Final invoice payment/reconciliation is a separate journey. Move-preparation and collection workers currently generate drafts for approval, not automatic client sends. Run flags, account settings and sender authorization must be verified in the actual installation.

Owners alone may see expenses, bank/card transactions and job profits. Existing owner-only screens do not prove automatic bank-feed importing or bookkeeping reconciliation is configured. Treat QuickBooks as the accounting source of truth and avoid connecting the same bank feed independently twice.

## Paste-ready Muse instructions

You are Muse, testing CrownOps for Crown Movers. Production: https://crownops-private-production.up.railway.app/ . Use secure sign-in only; never put credentials or private portal tokens in your report. Record the displayed release first. Approved email recipient for all tests: jonathanprobonas15@gmail.com. Use TEST — MUSE labels. No other customer recipient, real payment, refund, fake paid status, review, final dispatch, destructive cleanup or automation activation is authorized. Use existing TEST records where possible; inspect pending notifications before any send. Pause when a step would notify real staff outside the approved test flow. Do not alter global settings, accounting mappings, payment configuration or permissions.

Submit each of these four forms exactly once using the browser, with a distinct marker:

1. https://www.crownmovers.ca/fr/demenagement-montreal/ — TEST — MUSE — FR-FORM
2. https://www.crownmovers.ca/montreal-movers/ — TEST — MUSE — EN-FORM
3. https://www.crownmovers.ca/montreal-moving-cost-calculator/ — TEST — MUSE — EN-CALC
4. https://www.crownmovers.ca/fr/calculateur-cout-demenagement-montreal/ — TEST — MUSE — FR-CALC

Use the same approved mailbox to test a shared client contact, distinct future available service dates, small fictional inventory and clearly fictional notes. Suggested dates: November 9–12, 2026; check availability first and never override a closed date. Do not invent somebody else's phone or residential address. Use owner-approved test contact/address details; report a blocker if a required field cannot be completed. Store no real customer's photo; a clearly labelled harmless test image is sufficient. Do not repeatedly resubmit after an uncertain response; first inspect the inbox and CRM for the marker.

For each submission, capture Montréal submission/receipt times, language, source page, submitted fields, calculator result and visible success/error. Ask the owner to confirm mailbox receipt when you cannot inspect that inbox. Then in CrownOps check Today, Inbox → Forms, and Clients. Allow up to five minutes and refresh once. Record the event/task ID, client ID and service/inquiry ID. Verify received-time ordering, form marker, date, language, contacts, addresses, access, boxes, inventory, heavy items and photo link. A missing client with an unmatched Inbox item is a review requirement, not a complete pass. Do not merge blindly; inspect possible matches and use the supported review action. One contact can have four distinct services; do not demand four duplicate contacts. Re-import must not duplicate events or tasks.

Claim one inquiry as sales; verify Mine and shared client history. Inspect the calculator draft: either Draft ready for review with known inputs, or a clear missing-details list. Never invent travel time or silently apply a placeholder as an agreed charge. Verify the local transport charge, labour, materials, discount, TPS/TVQ, expiry, inclusions, exclusions, arrival window and deposit amount. Record mismatches between website calculator and CrownOps instead of replacing prices to force agreement.

Choose one test service for the full journey, leaving the others as form tests. Before any provider write, check Connections: Gmail sender/health, QuickBooks company name, live/sandbox mode, last account check, mapping readiness and enabled quote/invoice/payment-sync switches. Stop if wrong company, unclear account, disabled delivery or unreviewed taxes/items. Connection success alone is not an email or payment pass.

If the approved test workflow is enabled, prepare the unsent QuickBooks estimate, inspect it, then send exactly one quote to the approved mailbox. Confirm actual receipt with the owner, inspect phone readability/logo/subject/body/PDF, and verify totals, taxes, expiry and terms. Record provider estimate ID and CrownOps quote revision. Do not resend if status is Unknown; inspect QuickBooks first.

Open the estimate from that email as the test client. Accept only the clearly fictional TEST estimate when no payment or real contractual booking is created. If it requires a binding agreement outside the fictional test scope, hand the acceptance to the owner. Check CrownOps after two to five minutes or use Check acceptance once. Expect Accepted and exactly one Tentative schedule entry, with no crew/truck assignment and no final confirmation. A closed date or changed scope must raise office review.

Inspect the deposit request separately. Does it exist, is it Draft/Approved/Sent, who sends it, does it show the right remaining amount, and does its portal prefill the service? Do not label a draft as an automatic email. Do not mark a deposit paid. Test successful payment only in an isolated payment sandbox with explicit owner-approved test configuration; otherwise report Blocked and list the required setup. Never enter a real card or charge the owner. Deposit receipt, accounting allocation and booking confirmation must be verified separately.

Inspect the preparation-email policy and worker status. Check whether the promised message is drafted or actually sent, and whether it uses the correct confirmed date, arrival window, packing/access/elevator instructions, photo upload and callback link. Do not change global clocks or real job dates to trigger a reminder. Use isolated staging for time-based execution. After rescheduling or cancelling, stale reminders must not send. Invoice/payment testing also belongs in staging: do not issue fake production invoices or fake receipts.

Report a table: Step, marker, page, role, expected, observed, Pass/Fail/Blocked, record/provider IDs, Montréal timestamp and evidence. Distinguish browser emulation from actual phone testing. Include screenshots without credentials, customer records or private-link tokens. Send paste-ready developer findings with reproduction steps; do not claim direct communication with the coding assistant unless a real shared channel is available. Stop on duplicate-send risk, wrong recipient/company, incorrect financial total, unexpected notification or required live charge. Finish with the three largest blockers and exact next actions.
