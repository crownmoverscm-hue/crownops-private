# Focused reset-safety investigation

Inspected deployed source: c845bce8515dfc14ef5305ac15d566be37ab5456 / build 4ff60386a8381ba7bbda3438. No live reset request, account change or deployment was performed during this investigation. All mutation tests use disposable local SQLite databases.

## Finding and containment

Muse's four enabled **Private reset link** buttons are the pre-existing general recovery workflow, not the new five-account reissue. The UI calls its POST immediately and opens the link modal only after a grant has been created. There is no confirmation screen before grant creation. Clicking is a write, even though it neither changes the password immediately nor sends email.

Characterization on unpatched disposable records: the Master viewer could issue four active synthetic-account grants (HTTP 200, sent false) while CROWNOPS_WIX_TEST_REISSUE_ENABLED=0; inactive returned 404; new reissue operation count remained zero. This confirms an alternate authorized recovery path around the intended synthetic-test process. It does NOT demonstrate an anonymous/crew privilege escalation or that the new disabled endpoint is executable.

Classification: dedicated-feature fail-open P0 **NOT CONFIRMED**; separate staging recovery-policy gap **CONFIRMED**, a blocker for the controlled five-account reset acceptance. Current owner/manager general-recovery permissions are explicit existing application policy. Tightening production administrative recovery is a separate decision, not part of this patch.

Until an owner-approved patch is deployed, keep testing paused, do not click legacy recovery buttons or redeem links, and do not enable the new reissue. No live grants are deleted/revoked as containment. If issuance is actually observed, preserve audit evidence and seek approval for narrowly scoped revocation. The proposed server guard blocks both new legacy grants and redemption of existing grants for these five accounts without deleting them.

## Routes, components and permission boundaries

| Flow | Component and route | Authorization / behavior |
|---|---|---|
| Team access display | app/static/account_access.js, Team access renderer; GET /api/account-access in app/account_access.py | Active owner/manager session. Target-relative can_manage controls buttons. Four active eligible targets show recovery; inactive does not. |
| General private grant | account_access.js reset-link click handler; POST /api/users/{uid_}/reset-link; register_security.reset_link in app/security.py | Active owner/manager session and valid session CSRF. protect_owner_account limits Master and other-owner targets. Creates/replaces one 30-minute grant, stores token hash, audits actor/target, returns private bearer URL; no email or credential update. No five-account feature flag. |
| General completion | POST /api/reset-password; register_security.reset | Public bearer-token recovery: unexpired, unused grant joined to active account, rate limit, password validation and same-origin middleware. No cookie-session CSRF dependency; token is the authorization proof. Changes password, deletes all that account's reset grants/sessions, audits, preserves MFA, does not auto-sign-in. May send separately gated account notification. |
| Email recovery | POST /api/forgot-password and account_access.deliver_recovery | Rate-limited public request with generic response; independently gated account emails. Resolves active registered recipient, creates general grant and can send recovery email. Not gated by the synthetic-reissue flag. |
| New synthetic preflight | GET /wix-test-credential-reissue and GET /api/wix-drafts/credential-reissue/preflight; app/wix_test_credential_reissue.py | Current active session must match configured Master ID, not only role owner; exact environment/service/host/DB checks. Read-only safe metadata. |
| New synthetic plan/execute | POST /api/wix-drafts/credential-reissue/plan and /execute | Same Master identity/session/scope plus CSRF, server-reviewed exact five-account manifest and CROWNOPS_WIX_TEST_REISSUE_ENABLED=1. Missing variable fails closed (503); other active roles 403, inactive/anonymous 401. One ten-minute, owner-bound, inventory-bound, terminal operation. No general recovery grant or notification. |

General target protections: a manager cannot manage any owner account; a non-Master owner cannot manage another owner or the Master; Master can manage other owners. The backend also permits eligible self-recovery even though the Team UI hides self-management buttons. Roles and these target checks are authoritative, not UI booleans.

GET /api/users calculates **can_manage per returned target using the requesting viewer**: viewer must be owner/manager, target must differ from viewer and Master, and an owner target requires the Master viewer. It does not mean the returned user can manage anyone. Active status is not part of this boolean because management includes restoring inactive access. /api/account-access applies corresponding target logic after its owner/manager route gate. Neither boolean is a reset authorization grant.

CSRF distinction: cookie-authenticated administrative mutations use the session CSRF check. Public forgot-password and token-redemption routes do not require that session token; they rely on their public-request controls and, for redemption, the secret one-use recovery token. Cross-origin browser POSTs are rejected by middleware. Do not claim every password-change route requires a session CSRF token.

## Proposed patch — NOT deployed

- Add app/synthetic_recovery_guard.py for exactly five known IDs/emails, scoped to the existing isolated quote-testing service/database; disposable test databases exercise the same policy. Match either known ID or known fictional email to prevent metadata mismatch from silently removing protection.
- General private-link issuance and token redemption reject protected targets before changing credentials/grants/sessions. Email recovery returns before issuing/sending for those targets, even if the separate email gate is enabled.
- Team access receives a specific general_recovery_blocked_reason; its active synthetic targets show disabled **Private reset link — restricted test account** buttons. Other management actions retain existing policy; they are not authorized for this test.
- The dedicated five-account endpoint retains all of its own gates and stays default-disabled. General grants and synthetic operation IDs are not interchangeable.
- Production and other staging services (including Staff AI) keep their existing recovery behavior. No migrations, grant cleanup, account changes, new flags or provider calls are introduced by this patch.

Targets: WIX-PILOT-nonmaster-owner (owner/1), WIX-PILOT-manager (manager/1), WIX-PILOT-sales (sales/1), WIX-PILOT-crew (crew/1), WIX-PILOT-inactive (sales/0). Creation attribution remains UNKNOWN.

## Test evidence boundaries

Existing 19 reissue tests cover exact allowlist, Master-only role/CSRF denials, expiry, concurrent/single execution, MFA/inactive preservation, recovery/session revocation, transaction rollback and audit secrecy. Existing 27 account-access tests cover general token expiry/replay, inactive denial, MFA retention, notifications, sessions, target protections and rate limits. The new boundary suite tests missing-variable failure, legacy grant issuance/redemption blocks, email suppression, viewer-relative metadata, cross-flow token rejection, ordinary-account recovery/replay, CSRF/role denials, general-reset audit-failure rollback, UI restriction and production/other-stage non-applicability. No live denial POSTs were invoked.

## Proposed deployment and Muse retest

Separate owner approval is required before deploying this corrective candidate to service 5de22c3a-ea7e-4fa6-bc96-2f9472431c7c / environment 53900324-c678-40b0-91f0-b6560316a5d2. Preserve current /data volume/database and all records. Keep reset execution disabled. Do not deploy elsewhere. Reverting the patch would reopen the legacy synthetic-recovery path; rollback therefore requires containment review, not blind deployment of the previous code. No credentials were changed, so there are no passwords to roll back.

After approved deployment, Muse should read-only verify the exact new footer/API build; the same five IDs/roles/active flags; four disabled restricted private-link buttons and none for inactive; dedicated can_prepare=false and can_execute=false with no operation; unchanged fixture/local review; and no new reset-related audit activity. Do not click recovery, prepare, execute, access-edit or notification controls. Live permission-denial POST testing requires a separate agreed scope; automated security and mutation tests remain disposable only.
