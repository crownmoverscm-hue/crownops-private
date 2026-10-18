# CrownOps Sales release 0.10.0-rc1

This source release improves the Sales-to-client information loop. Deployment status must be checked separately; a source package is not evidence that production has updated.

## What changed

- Routine Waiting on client, Quote Sent, ownership and internal-note updates preserve the quote's scope version. Actual scope changes and closing a sale as Lost still require quote review.
- Sales can record a deposit actually received. Only Owners and Managers can approve cash/cheque due on the service day. Approval does not create a fictitious payment.
- Request missing details is available from an inquiry, quote or client file. Choose the information to check and prepare a friendly English/French email in Communications. The request starts with the existing private moving file. Staff review and send the draft manually.
- Client progress saves privately while typing, survives reopening and supports French/English. The client can upload photos/videos, paste a shared folder link and request a callback using the existing portal. Submit my details sends the completed information to staff for review. Dates, quotes and accepted terms are not silently changed.
- Submitted changes are compared against the current service. If the service changed after submission, approval requires explicit reconciliation and a note. No-change confirmations preserve valid estimates.
- Sales displays an owner and the next open task with its due date. Suggested next actions prioritize callback requests, submitted information, overdue work, accepted estimates awaiting confirmation, quote opens, due tasks and unsent estimates. These are explained rules, not a claim that an AI model has evaluated the client. Nothing is sent automatically.
- Estimates show the latest revision for each service by default; revision history remains available. Draft, Ready to send, Sent and Accepted are separate states. Booking confirmation remains separate from acceptance.
- A visible estimate open is recorded only by an authenticated private client session, after the estimate heading is visible for one second. Repeated events are grouped per browser session and 30-minute window. Staff see the count and last open; the first open of a sent estimate creates a follow-up task. This is an observed portal open, not proof that the client read the estimate.
- Intake failures now show an actionable connection/processing message and record a safe failure category in runtime logs. A source change cannot reconnect a Gmail account by itself.
- Existing production data is backed up and restore-verified before adding the new Sales tables. Startup stops if recovery verification fails.

## Team process

1. Open Sales & clients, review the suggested actions and open the client/service.
2. Check existing details. Use Request missing details if information is missing. Review and send the prepared email from Communications.
3. The client checks the prefilled form, saves progress, adds photos or a folder link, and clicks Submit my details.
4. Open Client portal, compare and review submitted changes, then mark the information request reviewed from the client file.
5. Prepare or revise the quote using the reviewed service details. Check rates, materials, transport, billing and arrival window.
6. Review and send the quote. Ready means the quote is prepared; Sent requires confirmed email delivery. Use the observed opens and next action as context for a personal follow-up.
7. Acceptance creates a tentative service. Confirm the deposit or ask management to approve a payment arrangement. Confirm availability, then assign the actual crew and truck before dispatch.

## Validation and remaining launch checks

Server suite: 292 tests passed. JavaScript suite: 13 tests passed. New Sales browser flow passed at desktop, tablet and phone sizes for Sales, Manager and Owner roles. Existing client, crew invoice, expense-permission, upload, quote-editor, callback and schedule checks passed. All email/accounting browser providers were mocked and test records were disposable; no real client was contacted or charged.

Production still needs a controlled real Gmail delivery/intake and QuickBooks invoice/payment walkthrough using an authorized company test client. Review the real overdue queue with its owners; this release does not delete or auto-complete customer tasks. Confirm permanent Railway hosting and a recovery copy outside the service. Synchronize the reviewed source to the company GitHub repository so an older repository deployment cannot replace these changes.
