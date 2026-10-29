# Synthetic credential reissue - preparation only

Date: 2026-10-29 (Toronto). Base: 75ba357ef627d365cd7ae5c6e9b4c94719f9a383.
No deployment, enablement, real reset or account changes are authorized by this preparation.
Current deployed staging build remains 1c522693c3fb32bb95d8c01c.

## Six answers to the original DEV Note

1. **How/when/by whom?** The preparation control shipped in commit 75ba357, but account creation is not a deployment/startup hook. It requires an explicit authenticated Master-owner POST with CSRF and exact staging checks. Retained request logs showed HTTP 200 at 2026-10-28T09:29:18Z (05:29:18 Toronto) and 2026-10-28T21:08:42Z (17:08:42 Toronto). The note's approximately 17:08 observation matches the latter. Both creation and existing-account results return 200; these logs do not establish creation time, actor or credential receipt. Origin remains UNKNOWN until metadata/audit correlation supports it.
2. **Intended accounts or discard?** The five emails match the intended synthetic test names, but `existing: true` proves only an email match. The deployed preparation path does not validate the existing ID, role or active flag. Preserve them; do not discard/recreate. Actual identities/roles/status remain pending Muse's sanitized report.
3. **Original passwords?** New accounts receive random passwords in the first authenticated response only. The database stores one-way credential representations. Setup does not return existing credentials. Their private handover/retention is unproven. Do not retrieve original response bodies, password hashes, browser password stores or credentials.
4. **Secure handover?** If the owner retained them privately, use the owner's private password-manager sharing or owner-controlled authenticated browser sessions per role. No credentials in chat, tickets, GitHub, screenshots, logs or reports. No original receipt is inferred from an HTTP 200.
5. **Reset/reissue?** A prepared, not deployed, Master-owner-only mechanism is described below. It accepts five owner-entered fresh passwords privately rather than displaying or generating new credentials for handover. It revokes the five accounts' sessions and reset grants, preserves metadata and MFA, and cannot automatically retry a completed operation. No reset is authorized yet.
6. **Local only/no side effects?** The reviewed helper inserts local users and appends local evidence; it has no invitation/email/provider call. Its deployed guard requires the exact isolated quote service, environment, host, database and sandbox realm. This establishes the implementation's behavior; it does not independently prove historical origin of the existing rows. CI users are created in disposable temporary databases; the browser fixture uses localhost. No live creation was performed by this preparation.

## Live inventory gate

Expected emails (not yet confirmed target IDs):

| Email | Intended role | Intended active flag | Verified live ID |
|---|---|---|---|
| wix-pilot-nonmaster-owner@example.invalid | owner, non-Master | 1 | PENDING MUSE |
| wix-pilot-manager@example.invalid | manager | 1 | PENDING MUSE |
| wix-pilot-sales@example.invalid | sales | 1 | PENDING MUSE |
| wix-pilot-crew@example.invalid | crew | 1 | PENDING MUSE |
| wix-pilot-inactive@example.invalid | sales | 0 | PENDING MUSE |

No new Master-owner browser session is requested. Muse uses the authorized session and existing read-only /api/users endpoint, which excludes credentials. Creation timestamp may be unavailable in that endpoint; preserve UNKNOWN. Correlate any available creation metadata with retained local evidence. Do not rerun setup to investigate it. Unknown origin need not be invented or guessed in order to review known identities.

## Prepared implementation

- `app/wix_test_credential_reissue.py`: actual CrownOps authenticated routes, dedicated metadata preflight, plan and execute core; no provider or notification hooks.
- `app/static/wix_test_credential_reissue.html` and `.js`: separate Master-owner preflight and five private password/confirmation pairs. No local/session storage, console output or automatic retry. Inputs clear after submission, including uncertain results.
- `config/wix_synthetic_accounts_verified.json`: EMPTY, pending Muse metadata and owner review. No guessed live IDs.
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

## Exact owner-approval preflight - incomplete until Muse report

The future approval screen must show:

1. Exact deployed candidate commit/build/CI after separate staging deployment approval. No candidate is deployed by this preparation.
2. Host crownops-quote-acceptance-staging.up.railway.app; environment 53900324-c678-40b0-91f0-b6560316a5d2; service 5de22c3a-ea7e-4fa6-bc96-2f9472431c7c; database /data/crownops-quote-acceptance.sqlite3.
3. All five actual account IDs and emails matched to Muse's report and owner-reviewed manifest. Intended role/status table above is not a substitute.
4. Original creation actor/time evidence, or explicitly UNKNOWN; original credential handover UNKNOWN unless independently established.
5. Current role/active/MFA flags, inventory hash, reviewed manifest reference, reason, exact operation ID and expiry.
6. Five private new-password/confirmation inputs; no existing passwords requested, no credentials echoed or exported.
7. Exact consequences: five credentials replaced; their existing sessions/recovery grants revoked; no other identity/access/MFA/business data changed; zero messages/provider actions.
8. Owner confirmation in the authenticated form: "I authorize this one credential reissue for exactly the five verified synthetic staging account IDs shown, preserving their roles, active flags and MFA, revoking their prior sessions/recovery grants, and sending no messages or provider requests."

This is a preflight specification, NOT an approval request with verified target IDs. Still needed: Muse metadata, owner review, approved exact candidate release/enablement and then the owner's separate one-use action. Do not enable the flag, fill the manifest or execute on existing accounts based on this document alone.

## Recovery and rollback limits

- Before transaction commit, a database/audit failure rolls back all five updates and revocations together. No partial completion should be retained.
- If the response is lost, read operation state first. Completed means no retry/change is permitted; Planned means not completed, subject to unchanged inventory/expiry and owner approval. Do not infer success from a timeout.
- After commit, previous credentials are intentionally unavailable; there is no credential rollback. Do not copy old hashes or restore the staging database to recover them.
- Lost newly entered passwords require a separately reviewed manifest and new explicit owner-approved operation. Disable this feature/rollback code without restoring old business data or historical authorizations.
- No production, Staff AI, Quote 185, quote-revision or provider changes form part of recovery. No new QuickBooks check is requested.

## Wix Support update

Owner reports documentation/support-level resolution under case 031000271. This supersedes the original note's unresolved wording at that level. Real Wix behavior remains untested; all provider writes stay disabled. No Wix backend publishing or integration cutover is authorized here.
