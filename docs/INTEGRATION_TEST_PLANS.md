# CrownOps — External Integration Test Plans (Gmail, QuickBooks Online, Square)

**Status 2026-11-05: every live or sandbox test below is BLOCKED.** No sandbox credentials, OAuth apps or test accounts are available to this session. Activating any of them needs Jonathan's approval.

Nothing here has been run against Google, Intuit or Square. "Local" means mocked tests in this repository, which prove CrownOps' side of the contract only.

## Ground rules for every sandbox session

- **One disposable installation** (`HOSTED_PILOT_PREFLIGHT.md`) with fictional data. Never Crown's installation.
- **Sandbox only:** `QBO_ENV=sandbox` and `SQUARE_ENV=sandbox`.
- **Gmail recipients:** only mailboxes Jonathan controls, listed in `CROWNOPS_TEST_EMAIL_ALLOWLIST`.
- **Enable one switch at a time**, and record the start and end time of each switch:
  - `CROWNOPS_ALLOW_EXTERNAL=1`
  - `CROWNOPS_ALLOW_QBO_SANDBOX_WRITES=1`
  - `CROWNOPS_ALLOW_QBO_ESTIMATE_DELIVERY` / `_INVOICE_DELIVERY` / `_RECEIPT_SYNC`
  - `CROWNOPS_ALLOW_SQUARE_SANDBOX_DEPOSITS=1`
- **Evidence per test:** the request time, the CrownOps record id and status, the provider-side record id, a screenshot of the provider dashboard, and the `ops-check` result. Never record tokens or secret values.
- **Unknown outcomes are never retried.** A test passes only if CrownOps leaves the record Unknown and the documented reconciliation produces the right final state.

## Prerequisites (all missing today)

| Provider | Needs | Who |
|---|---|---|
| Gmail | A Google Cloud OAuth client (web) with redirect `https://<pilot>/api/oauth/gmail/callback`, scopes `gmail.readonly` and `gmail.send`, a test Google Workspace or Gmail mailbox, and test-user consent screen status | Jonathan creates the account and app |
| QuickBooks | An Intuit developer app with sandbox company (Canada, CAD home currency, GST/QST set up), redirect `https://<pilot>/api/oauth/quickbooks/callback`, scope `com.intuit.quickbooks.accounting` | Jonathan |
| Square | A Square developer sandbox application: sandbox access token, a location id, and a webhook subscription to `https://<pilot>/api/webhooks/square` with its signature key | Jonathan |

## Gmail (sending and reading)

**How CrownOps talks to it** (`providers.py`, `gmail_sync.py`, `account_access.py`):
- **Sending:** OAuth with offline refresh, then `users/me/profile` and `users/me/messages/send` with a deterministic `Message-ID <id@crownops.local>`.
- **No provider idempotency key.** Duplicates are prevented by `outbound_messages.event_key` and the Approved → Sending status change.
- **Reads** retry 3 times with backoff; **sends never retry.**

| ID | Test | Expected | Status |
|---|---|---|---|
| GM-1 | Connect: manager starts OAuth, consents, callback stores the encrypted token | Connections shows Gmail connected. `oauth_states` row consumed. A callback from a different user's session is refused. | BLOCKED |
| GM-2 | Token refresh after expiry, then the 15-minute `maintain_connections` run | New access token, no user action needed | BLOCKED |
| GM-3 | Revoked grant (revoke in Google account settings) | 409 "reconnect"; no send attempted; Connections shows the problem | BLOCKED |
| GM-4 | Send an approved estimate email to an allowlisted mailbox | Arrives once. Status Sent. Raw headers show the expected Message-ID, From (sender or alias) and subject. | BLOCKED |
| GM-5 | Recipient not on the allowlist (non-production) | Refused before any Google call | Local: covered (`test_email_identity.py`) |
| GM-6 | **Unknown outcome:** drop the network after the request leaves (proxy kill) | Status Unknown, 502, no retry. `ops-check` shows `messages_unconfirmed`. | BLOCKED |
| GM-7 | Reconcile an Unknown **invoice** email: `POST /api/invoices/{id}/resolve-delivery` | Found in Sent with the exact recipient, subject and body → Sent. Not found → `confirmed_not_sent`, a 30-minute wait, then Draft. | Local: covered (`test_invoice_finish.py`); live BLOCKED |
| GM-8 | Unknown **non-invoice** client email | **Gap:** no in-app reconcile route (SR-11). Expected: owner-only resolve with Sent-folder evidence. | OPEN DEFECT |
| GM-9 | Inbound intake poll (`automatic_intake`) with a labelled test message | One lead created; a repeated poll creates none | BLOCKED |
| GM-10 | Google 429 or 5xx on a read | Backoff, then error; no data loss | Local: covered (`test_gmail_access.py`) |
| GM-11 | Labels for a non-Crown company | **Gap:** label names are hard-coded (`New Quote Request`, `Missed Calls`, `Voice Mail`). The pilot company must create those labels or the labels must become configurable. | OPEN (scope) |

## QuickBooks Online (sandbox)

**How CrownOps talks to it** (`providers.py`, `estimate_delivery.py`, `invoice_quickbooks_delivery.py`, `invoice_payments.py`, `extensions.py`):
- Writes go through `qbo_call` with `requestid=sha256(key)[:40]`. Keys: `customer:<lead>`, `qbo:invoice:<iid>:<rev>`, `estimate-delivery:<qid>`, `qbo:Payment:<pid>:1`.
- `/send` calls have no requestid. A local `provider_operations` row guards them.
- Writes need `CROWNOPS_ALLOW_QBO_SANDBOX_WRITES=1`.

| ID | Test | Expected | Status |
|---|---|---|---|
| QB-1 | Connect and capture `realmId` | Connected. `companyinfo` read shows the sandbox company. Home currency CAD. | BLOCKED |
| QB-2 | Tax and item mapping read (Item, TaxCode, Account) | Readiness lists mapped codes. Missing mapping → refusal, no write. | BLOCKED |
| QB-3 | Create customer and estimate from an approved quote | One customer and one estimate. Totals equal CrownOps to the cent, including the rounding-adjustment line. | BLOCKED |
| QB-4 | **Repeated request:** replay QB-3 with the same key | Intuit returns the original entity (same requestid); no duplicate | BLOCKED |
| QB-5 | Send the estimate (`/estimate/{id}/send`) to an allowlisted address | `qbo_estimate_deliveries.state` Sent. A second click is refused locally. | BLOCKED |
| QB-6 | **Unknown on send:** timeout after the request leaves | State Unknown, no retry. The poller or `quickbooks-delivery/sync` reconciles from `EmailStatus`. | Local: covered (`test_estimate_delivery.py`); live BLOCKED |
| QB-7 | Invoice export for revision 1, then a correction to revision 2 | Separate requestids. Revision 2 updates with the right SyncToken. A total mismatch → Mismatch. | BLOCKED |
| QB-8 | Unknown or Mismatch invoice export | **Gap:** no in-app route clears it (SR-11). Expected: owner-only reconcile showing CrownOps and QuickBooks totals side by side. | OPEN DEFECT |
| QB-9 | Payment recorded in QuickBooks; receipt sync poll | Matched to the invoice once. A second poll makes no change. Partial payment leaves the right balance. | BLOCKED |
| QB-10 | Intuit outage (5xx or connection refused) on a write | Failed or Unknown as classified. `background_job_failing` after 3 runs. No partial local state. | BLOCKED |
| QB-11 | Non-Quebec Canadian company (e.g. Ontario HST) | **Gap:** tax picker is GST/QST (`quebec-normal-move-v1`). Expected: refusal until mapping is configured. | OPEN (scope) |
| QB-12 | Non-CAD home currency | Refused by `_cad_currency`, so US companies are unsupported | Local behaviour; scope decision |

## Square (sandbox)

**How CrownOps talks to it:**
- A static bearer token for `POST /v2/online-checkout/payment-links` (invoice balance and deposits) and `DELETE` to cancel.
- Webhook `POST /api/webhooks/square`: HMAC over URL and body, dedupe by `event_id` and payment reference.
- A **fresh idempotency key per attempt**, plus a local lock: an existing Creating, Active, Unknown or Cancelling link blocks a new one.

| ID | Test | Expected | Status |
|---|---|---|---|
| SQ-1 | Create an invoice checkout for an issued sandbox invoice | Link Active, amount equals the balance, CAD, correct location | BLOCKED |
| SQ-2 | Pay with a Square sandbox test card; webhook arrives | Payment recorded once, allocated, link Paid, collection drafts cancelled | Local (signed synthetic event): **covered** (`test_square_webhook.py`); live BLOCKED |
| SQ-3 | **Repeated webhook:** use "resend" in the Square dashboard | `duplicate: true`; no second payment | Local: covered; live BLOCKED |
| SQ-4 | Same payment under a new event id | Not applied twice (payment-reference dedupe) | Local: covered; live BLOCKED |
| SQ-5 | Amount, currency or location mismatch | Not applied; URGENT reconcile task | Local: covered; live BLOCKED |
| SQ-6 | Signature: wrong key, altered body, missing header | 401; nothing recorded | Local: covered; live BLOCKED |
| SQ-7 | Refund in the sandbox dashboard | URGENT finance task; **nothing reversed automatically** | Local: covered; live BLOCKED |
| SQ-8 | Dispute (sandbox dispute test card) | Same as SQ-7 | Local: covered; live BLOCKED |
| SQ-9 | **Unknown on link creation:** timeout after the request leaves | Link Unknown, 502, no retry. **Gap:** no in-app reconcile (SR-11); the link blocks new checkouts until someone resolves it in Square. | OPEN DEFECT |
| SQ-10 | Deposit checkout from the client portal (sandbox flag and test-email list) | Link created only for allowlisted test emails. The payment webhook confirms the booking once. | Local: covered (`test_square_deposit_permissions.py`, mocked HTTP); live BLOCKED |
| SQ-11 | Cancel a checkout | DELETE succeeds and the link is Cancelled. Failure → Unknown. | BLOCKED |
| SQ-12 | Square outage | 502; link Unknown; staff told to check Square before retrying | BLOCKED |

## Ready now vs blocked

| Category | Count |
|---|---|
| Local tests covering CrownOps' side (this repository) | Gmail 5 suites, QuickBooks 4 suites plus readiness tests, Square 2 suites (14 tests, including 6 new webhook tests) |
| Live or sandbox tests specified above | 35 |
| Live or sandbox tests runnable today | **0** (no sandbox accounts or credentials) |
| Open defects these plans expose | GM-8, QB-8, SQ-9 (no in-app Unknown resolution) |
| Open scope limits these plans expose | GM-11 (labels), QB-11 and QB-12 (tax and currency) |
