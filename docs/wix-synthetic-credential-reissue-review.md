# Synthetic credential reissue - preparation only

Date: 2026-10-29 (Toronto). Base: 75ba357ef627d365cd7ae5c6e9b4c94719f9a383.
This preparation performs no deployment, enablement, real reset or account changes. Owner authorization is still required before executing the reset.
Current deployed staging build remains 1c522693c3fb32bb95d8c01c.

## Six answers to the original DEV Note

1. **How/when/by whom?** The preparation control shipped in commit 75ba357, but account creation is not a deployment/startup hook. It requires an explicit authenticated Master-owner POST with CSRF and exact staging checks. Retained request logs showed HTTP 200 at 2026-10-28T09:29:18Z (05:29:18 Toronto) and 2026-10-28T21:08:42Z (17:08:42 Toronto). The note's approximately 17:08 observation matches the latter. Both creation and existing-account results return 200; these logs do not establish creation time, actor or credential receipt. Origin remains UNKNOWN until metadata/audit correlation supports it.
2. **Intended accounts or discard?** The five emails match the intended synthetic test names, but `existing: true` proves only an email match. The deployed preparation path does not validate the existing ID, role or active flag. Preserve them; do not discard/recreate. Muse subsequently verified all five exact IDs, roles and active flags through authenticated /api/users on build 1c522693c3fb32bb95d8c01c. Creation attribution remains UNKNOWN.
3. **Original passwords?** New accounts receive random passwords in the first authenticated response only. The database stores one-way credential representations. Setup does not return existing credentials. Their private handover/retention is unproven. Do not retrieve original response bodies, password hashes, browser password stores or credentials.
4. **Secure handover?** If the owner retained them privately, use the owner's private password-manager sharing or owner-controlled authenticated browser sessions per role. No credentials in chat, tickets, GitHub, screenshots, logs or reports. No original receipt is inferred from an HTTP 200.
5. **Reset/reissue?** A prepared, not deployed, Master-owner-only mechanism is described below. It accepts five owner-entered fresh passwords privately rather than displaying or generating new credentials for handover. It revokes the five accounts' sessions and reset grants, preserves metadata and MFA, and cannot automatically retry a completed operation. No reset is authorized yet.
6. **Local only/no side effects?** The reviewed helper inserts local users and appends local evidence; it has no invitation/email/provider call. Its deployed guard requires the exact isolated quote service, environment, host, database and sandbox realm. This establishes the implementation's behavior; it does not independently prove historical origin of the existing rows. CI users are created in disposable temporary databases; the browser fixture uses localhost. No live creation was performed by this preparation.

## Live inventory gate

Muse-verified identities supplied and reviewed by the owner; this is not reset authorization:

| Email | Intended role | Intended active flag | Verified live ID |
|---|---|---|---|
| wix-pilot-nonmaster-owner@example.invalid | owner, non-Master | 1 | WIX-PILOT-nonmaster-owner |
| wix-pilot-manager@example.invalid | manager | 1 | WIX-PILOT-manager |
| wix-pilot-sales@example.invalid | sales | 1 | WIX-PILOT-sales |
| wix-pilot-crew@example.invalid | crew | 1 | WIX-PILOT-crew |
| wix-pilot-inactive@example.invalid | sales | 0 | WIX-PILOT-inactive |

Muse completed the authenticated read-only /api/users inspection on the expected build. All five IDs, roles and active flags match the intended accounts. Creation timestamps and original creation/login attribution remain UNKNOWN; do not repeat the completed inspection or rerun preparation. No new Master-owner DEV session is requested.

## Prepared implementation

- `app/wix_test_credential_reissue.py`: actual CrownOps authenticated routes, dedicated metadata preflight, plan and execute core; no provider or notification hooks.
- `app/static/wix_test_credential_reissue.html` and `.js`: separate Master-owner preflight and five private password/confirmation pairs. No local/session storage, console output or automatic retry. Inputs clear after submission, including uncertain results.
- `config/wix_synthetic_accounts_verified.json`: contains exactly the five Muse-verified IDs above, supplied and reviewed by the owner. The execution flag remains OFF. Filling the manifest does not plan or execute a reset.
- `CROWNOPS_WIX_TEST_REISSUE_ENABLED` defaults off. The preparation does not set it or call Railway.
- Production registration adds no routes/tables. Staging enforces exact service, environment, host and database path, in addition to active Master identity and valid session/CSRF. Test exception is limited to temporary databases off Railway.
- Only a server-side owner-reviewed manifest containing exactly five distinct verified IDs with the exact intended emails/roles/status permits a plan. Browser payloads cannot supply or override that allowlist.
- Master owner and five accounts are checked in the transaction. The Master account can never be targeted. A frozen inventory includes ID/email/name/role/active/employee link/creation timestamp, non-secret MFA metadata and account-audit sequence.
- One durable operation per reviewed manifest. Preparing it records identity, reason, targets and inventory hash, and changes no credentials. It expires after 10 minutes. A consumed or expired operation cannot silently become a new one; fresh manifest review is required for a new operation.
- Execute requires exactly the five target IDs, explicit confirmation, five distinct 16-200 character replacement passwords and matching confirmations. Owner saves fresh passwords privately first. Existing credential material is not selected by this mechanism.
- The atomic transaction updates only password credentials, revokes sessions and outstanding password-reset grants for those five IDs, writes sanitized per-account/operation audits and permanently marks Completed. It does not alter users' IDs, emails, roles, active flags, employee links or MFA. The inactive account remains inactive.
- Malformed credential bodies produce generic errors without reflecting inputs. Responses/audits contain no credential values or credential representations. Existing normal authentication still performs its own internal password verification.
- Completion replay returns Completed and performs no new mutation, even if new sessions were created after the first completion. SQLite write serialization protects concurrent execution. A completed scope cannot be reused for another rotation without fresh reviewed manifest authorization.
- No recovery-email or invitation function is invoked. There are no calls to Wix, QuickBooks, Gmail, n8n, invoices, bookings, deposits or payments.

## Disposable test coverage

New tests use real CrownOps auth/CSRF with disposable SQLite users, runtime-generated fixture passwords and mock provider prohibitions. They cover exact inventory, all roles, anonymous/inactive denial, flags/manifest gates, wrong IDs/roles/status, Master exclusion, unique passwords/confirmations, terminal replay, expiry, changed MFA, concurrency, rollback after a write, old-vs-new login for a disposable account, inactive denial, preservation of business data, response/audit secrecy, malformed-body non-reflection and production registration.

The new CI browser fixture uses a temporary approved manifest and local app only. It covers four widths, real owner preflight/plan/execute, cleared password inputs, sales denial and zero external requests. It does not prove the live accounts' identities or authorize a staging reset.

## Exact five-account owner-approval preflight

The future approval screen must show:

1. Exact deployed candidate commit/build/CI after separate staging deployment approval. No candidate is deployed by this preparation.
2. Host crownops-quote-acceptance-staging.up.railway.app; environment 53900324-c678-40b0-91f0-b6560316a5d2; service 5de22c3a-ea7e-4fa6-bc96-2f9472431c7c; database /data/crownops-quote-acceptance.sqlite3.
3. All five actual account IDs and emails matched to Muse's report and owner-reviewed manifest. Intended role/status table above is not a substitute.
4. Original creation actor/time evidence, or explicitly UNKNOWN; original credential handover UNKNOWN unless independently established.
5. Current role/active/MFA flags, inventory hash, reviewed manifest reference, reason, exact operation ID and expiry.
6. Five private new-password/confirmation inputs; no existing passwords requested, no credentials echoed or exported.
7. Exact consequences: five credentials replaced; their existing sessions/recovery grants revoked; no other identity/access/MFA/business data changed; zero messages/provider actions.
8. Owner confirmation in the authenticated form: "I authorize this one credential reissue for exactly the five verified synthetic staging account IDs shown, preserving their roles, active flags and MFA, revoking their prior sessions/recovery grants, and sending no messages or provider requests."

The five target identities are now verified. Current MFA metadata, inventory hash, operation ID and expiry must be obtained from the deployed owner-only form; none is invented here. Candidate release/enablement and actual reset execution remain separate gates. No existing account has been changed.

## Recovery and rollback limits

- Before transaction commit, a database/audit failure rolls back all five updates and revocations together. No partial completion should be retained.
- If the response is lost, read operation state first. Completed means no retry/change is permitted; Planned means not completed, subject to unchanged inventory/expiry and owner approval. Do not infer success from a timeout.
- After commit, previous credentials are intentionally unavailable; there is no credential rollback. Do not copy old hashes or restore the staging database to recover them.
- Lost newly entered passwords require a separately reviewed manifest and new explicit owner-approved operation. Disable this feature/rollback code without restoring old business data or historical authorizations.
- No production, Staff AI, Quote 185, quote-revision or provider changes form part of recovery. No new QuickBooks check is requested.

## Wix Support update

Owner reports documentation/support-level resolution under case 031000271. This supersedes the original note's unresolved wording at that level. Real Wix behavior remains untested; all provider writes stay disabled. No Wix backend publishing or integration cutover is authorized here.

## Private role-session handoff for Muse

After separately approved execution, the owner uses the normal staging sign-in form in a browser the owner controls, takes over privately to enter each fresh password and any existing MFA code, and returns control only after sign-in. Muse receives a session, never a password. Use one role at a time: record visible account/role/build, perform the agreed checks, sign out, then let the owner sign in as the next role. Avoid parallel tabs sharing one cookie jar. For the inactive account, owner privately attempts normal sign-in; expected denial, no active session and no activation. Do not bypass MFA, create impersonation links, or use password-reset grants as test sessions. Muse must not record a real local review or approval during role tests; use only owner-approved denial checks and disposable mutations.

## Exact reset effect for owner review

Replace credentials only for WIX-PILOT-nonmaster-owner (owner/1), WIX-PILOT-manager (manager/1), WIX-PILOT-sales (sales/1), WIX-PILOT-crew (crew/1), WIX-PILOT-inactive (sales/0). Revoke their prior sessions and recovery grants. Preserve all IDs, roles, active flags and MFA. Record owner, reason, targets, time and terminal operation state. No messages or external calls. Do not press preparation or execution controls until the owner separately authorizes the exact deployed preflight.

## Read-only Wix qualification completed in this milestone

- Connected Wix account context identifies operational site 8911c013-d113-41ff-bca5-8484b699083b as Mysite, CAD, Canada, Velo enabled, Wix Invoices V1. This is connected-tool access, not proof of CrownOps adapter authentication.
- Official Contacts V5 Find Matching Contacts GET requires CONTACTS.CONTACT_READ (Read Contacts scope). One site-scoped GET for wix-readiness@example.invalid returned HTTP 200, contacts length 0, provider writes 0. No contact was created; no real customer details were fetched or reported. A future mapping must reject ambiguous matches and bind an owner-reviewed contact ID plus exact email.
- Official Velo getPriceQuote documentation currently imports { priceQuotes } from wix-billing-backend and returns id.id/version, customer.contactId/email, lineItems[].taxes (name/rate/code), taxes totals, currency, dates.validThroughDate, metadata.legalTerms and wasSent. The sample's Permissions.Anyone wrapper is NOT an acceptable integration authorization design and will not be copied.
- No supported external legacy Price Quote REST read endpoint was established from the searches. No native bridge is published/authenticated, and no arbitrary undocumented endpoint was probed. No existing Price Quote was fetched or modified in this pass.
- Exact live Price Quote GST/TPS and QST/TVQ codes, per-line applicability, rounding and readback reconciliation remain UNVERIFIED. Global checkout tax settings and e-commerce draft-order/tax endpoints are not substitutes. A securely authenticated read-only bridge and a reviewed existing quote ID or sanitized Price Quote export are needed to close this gap. No publishing is performed here.
- Wix Support 031000271: owner-provided documentation/support resolution says never-sent Draft with wasSent=false does not trigger the reminder. Real provider behavior remains untested.

Sources:
https://dev.wix.com/docs/api-reference/crm/members-contacts/contacts/contacts-v5/find-matching-contacts
https://dev.wix.com/docs/velo/apis/wix-billing-backend/price-quotes/get-price-quote
