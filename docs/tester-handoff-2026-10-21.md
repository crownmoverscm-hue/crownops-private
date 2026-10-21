# Independent tester handoff — October 21, 2026

## Fixes in 0.12.6-rc2

- Unknown browser pages return branded English/French recovery with Crown Movers' (514) 606-4030 call action, latest private-email-link advice and team sign-in. HTTP 404/410 status is preserved; APIs and missing static assets keep JSON errors. URL tokens/details are never reflected. Existing client link handlers continue their own recovery workflow.
- The loaded version and content-derived 24-character build appear on sign-in, in the staff navigation footer and Help & install. Report both, and use Update & relaunch before retesting. `/api/app-release` reports the current server build; manifest asset cache numbers were never the product release.
- Sign-in says “Private team workspace” instead of “Private pilot · Human-approved automation.” This wording change is not a launch-readiness certification.
- Password login with optional configured TOTP remains intentional for this release. Magic-link login and SSO are not implemented. Do not remove authentication to facilitate tests. Crew password-manager/autofill and persistent session behavior should be tested first; consider passkeys or magic links separately.

## Corrections and limits

Clint's signed-in and provider workflows remain **Not tested by Clint** until actually exercised. Existing automated fixtures are separate evidence, not a substitute for his test pass or real inbox delivery. Inspecting password-recovery copy alone does not verify email delivery or a completed reset.

QuickBooks estimate export exists at POST `/api/quotes/{id}/quickbooks`, but creates an **unsent single summary line**, with staff selecting the low or high estimate endpoint. It does not mirror the full material/quantity/transport itemization or send estimate expiry and terms. Customer and tax mappings require review. This is a known gap if the QBO estimate is to be the client-facing quote. Use the CrownOps client quote for the current acceptance flow; do not describe QBO estimate parity as verified.

My Portal shows the employee's own shifts/hours/documents. The separate Schedule **All Jobs** view intentionally shows shared job/team information, as previously requested. Do not count that intended visibility as a defect without revising the requirement. Private hours, wages, documents, requests and owner-only financial data still require negative permission tests. The 2026-10-21 request's owner-only expense/bank/card/margin rule takes precedence over older manager-access requirements.

## Hosted staging setup needed

No hosted staging URL was verified in this session. Local fictional test installations already exist and are used for automated browser checks. They are not publicly accessible to Clint.

Direct Railway reads returned `UNAUTHORIZED` / `oauth_token_invalid_grant`; the ChatGPT Railway connection needs renewed authorization. GitHub repository reads succeeded. GitHub-triggered deployment can ship code without this chat's Railway connection, but does not provision staging by itself.

Provision a separate staging service/environment, persistent volume, empty database, uploads/backups and fresh encryption key. Do not clone production data, secrets, OAuth grants or integrations. Use the same reviewed source and Docker image recipe, HTTPS secure cookies and exact staging public URL. Set `CROWNOPS_ENV=test`, `CROWNOPS_ALLOW_EXTERNAL=0`, `CROWNOPS_RUN_WORKER=0`, `CROWNOPS_ALLOW_AUTH_EMAIL=0`, `QBO_ENV=sandbox`, `CROWNOPS_ALLOW_QBO_SANDBOX_WRITES=0`, `CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES=0` initially. Apply the existing safe photo/video processor configuration before upload tests.

Create fictional Owner, Manager, Sales, Crew A and Crew B identities linked to separate fictional employees. Use private invitations with tester-chosen passwords; never publish invitation/reset links in GitHub or chat. The client uses a private client link, not a staff role/account. Staff emails are unique: obtain separately approved addresses if emailed invitations are required; do not invent aliases. Owner-distributed private invitation links are an alternative to sending five invitations. Client test recipient is only `jonathanprobonas15@gmail.com`.

## Integrations to enable incrementally

| Integration | Initial staging state | Evidence needed before calling it tested |
|---|---|---|
| Gmail | Disconnected; no copied production grant | Authorize a suitable sender; permit only `jonathanprobonas15@gmail.com` in client and auth email allowlists; verify sender, exact recipients, no staff notifications, one real delivery and correct private-link origin. Owner confirms inbox receipt. |
| QuickBooks | Sandbox, writes locked | Authorize a sandbox company; review service/tax/account mappings; enable sandbox writes only for controlled tests; reconcile remote customer/estimate/invoice/tax/credit results. Never use production accounting. |
| Wix | Disconnected | Dedicated test site or explicit fixtures; test signed event, duplicate protection, client mapping and requested dates; do not import production contacts. |
| UnifiCX | Disconnected | Provider-supported test call/webhook credentials and documented payload; prove answered/missed/voicemail event, deduplication and client association. A label does not prove actual call logging. |
| Samsara | Disconnected | Available test account/device data or explicit fixtures; prove one permitted read/event. |
| ElevenLabs | Disconnected | Dedicated test configuration and confirmed supported integration; prove the agreed event. Do not assume the product has a completed integration because an account exists. |

Enabling controlled Gmail delivery requires reviewing actual send paths and both `CROWNOPS_TEST_EMAIL_ALLOWLIST` and `CROWNOPS_AUTH_EMAIL_TEST_ALLOWLIST`; authorization and external-action gates must be configured deliberately. No actual emails were sent as part of this patch.

GitHub access for Clint is separate from this assistant's connection. Clint must connect his own GitHub integration to this private repository or provide the GitHub identity to invite. No account credentials or tokens should be pasted here. Paste-ready findings remain usable while that access is unavailable.

## Retest

Open `/quote`, `/help`, `/inquiry`, `/contact` in a browser; verify EN/FR, call link, latest-email advice, team sign-in and HTTP 404. Check 320/390/768/1440 widths. Check version/build while signed out and each staff role's Help & install. Compare with `/api/app-release` after relaunch. Viewport emulation is not physical-device testing. Preserve independent video credit, unpaid optional review and unrestricted free draw entry in all downstream tests.
