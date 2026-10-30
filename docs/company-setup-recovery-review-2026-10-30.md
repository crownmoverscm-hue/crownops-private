# Company setup P1 correction and remaining release gates

Date: 2026-10-30 (Toronto). Candidate only; no deployment or existing-data changes authorized.

## Evidence and root cause

Muse reproduced Company setup → Security & imports → Open 3/3 on served build `9e0d79aed8b2a16d9ca5fb4e`, source `718c718b0f80cc16191a5b267d17b9f43a7bfd2a`.
DEV independently reproduced the exact `pv.rates.map is not a function` exception by executing that commit's original `settingsPage()` renderer with the legacy fictional rates object.

- `app/wix_draft_connector.py:prepare_fixture` inserted `WIX-TEST-UNAPPROVED-PRICE` with `rates_json='{}'`.
- `/api/pricing` JSON-decodes that field and returns `rates: {}`. The API does not turn it into an array.
- `app/static/ux.js` handles `settings-legacy` by awaiting `uxOldSettings()` before replacing the content. That renderer, originally in `app/static/app.js`, called `pv.rates.map` on the first snapshot. Its rejection is caught by the click handler and shown as a toast; the previous screen stays visible. This explains the toast also being seen while Pricing & rates remained on screen; it does not establish a separate failure in the annual-rate renderer.
- Actual stored staging `/api/pricing` has not been retrieved by DEV in this session; the data-shape attribution is established by fixture source, exact-code reproduction and Muse's observed error, not a new authenticated staging database inspection.

## Small correction

The technical settings renderer validates a seasonal rates list and row display fields before rendering. Malformed or unconfigured data gets a visible per-snapshot warning; it is not silently converted to an empty list or displayed as zero-dollar rates. Its approval button is withheld. Other settings sections still render. Numeric strings, invalid amounts, missing row labels and non-object rows are reported explicitly. This is display validation, not a replacement for backend pricing approval validation.

Future fictional fixture creation writes the correct empty-list shape `[]`. Idempotent fixture setup does not rewrite an already-existing fixture. No pricing, quote, revision, approval, account, database schema or provider records are repaired by this candidate. No migration and no changed authorization, reset or outbound-write gates.

## Tests and independent acceptance

- Node tests execute the actual technical settings functions for object/null/string/scalar/bad-row/invalid-amount rates, valid rows, unconfigured lists, escaping, GET-only calls and unchanged input objects.
- Disposable backend test proves a new fixture uses `[]`, and preparing an existing legacy `{}` fixture again preserves it.
- New browser suite `company_settings` uses a disposable authenticated app with the legacy `{}` fixture and blocks all non-GET/external browser requests. At 390 and 1440 pixels it opens Security & imports, Pricing & rates, and Launch checklist → Hosting & recovery; checks the explicit warning, expected sections, empty backup status, absence of page errors and zero browser writes. This does not prove deployed staging backup availability.
- Existing backup-integrity tests exercise checksum validation, SQLite integrity, upload completeness, session/grant revocation in the restored copy, private permissions, corrupt/missing data rejection and refusal to overwrite an existing target. Tests use disposable records only.
- Local browser execution and GitHub CI results must be reported separately; never call an unexecuted browser suite passed.

After an approved deployment, Muse should verify the new build first, open Security & imports once, observe the malformed snapshot warning (if the legacy snapshot remains), Team access and Fleet verification, then open Pricing & rates. Do not approve/import/edit rates. Carry forward PR91/92 and role acceptance; do not repeat unrelated tests.

## Read-only backup status: correct location and limits

The existing backup evidence surface is **Launch checklist → Hosting & recovery**, from authenticated GET `/api/hosting-status` (Owner/Manager). Security & imports is not the backup screen. Owner-only backup creation is POST `/api/owner-backups`; do not click **Create, verify & download backup** in a read-only audit. It writes an archive, temporarily restores it for verification, and updates settings/audit evidence. Download is private owner-only GET and is not necessary for the first status check.

On 2026-10-30 DEV read the exact isolated Railway service: source pinned to `718c718...`, latest deployment `f0c9b849-268d-45c7-b520-a13d962ddd7a` SUCCESS, one replica, `/data` volume `88243cc6-2ec2-4a3b-8611-40b4191cdd84`, size 1000 MB, no staged changes. Public release API still returns build `9e0d79...`. Unauthenticated GET `/api/hosting-status` returns 401. DEV has no authenticated runtime backup-status read in this run. Archive existence, age, hashes, last restore result, offsite copies, retention schedules and actual recovery RPO/RTO are therefore **UNKNOWN**, not absent and not passed. Mounted storage is not a backup policy.

Muse's next authorized read: GET `/api/hosting-status` under the owner's existing session and report `backup.available`, `created_at`, `verified_at`, `restore_verified`, `upload_files`, byte count, and backup/storage/HTTPS/encryption check states (no secrets or archive contents). False availability is not proof no archive exists elsewhere. A recorded restore verification is historical local evidence, not a proof that the full service can be recovered today.

## Separate restore-drill preflight (not executed)

1. Owner approves a specific existing synthetic-staging archive, expected SHA-256, source cutoff, and a NEW private drill directory/environment. No production archive. If no suitable archive exists, request separate approval to create exactly one staging backup first. Do not use `verify-backup` before approval: it performs a temporary restore.
2. Confirm the source is synthetic isolated staging, not production or Staff AI. Verify archive format and complete manifest/checksums, SQLite integrity and every referenced private upload. Record counts and selected protected identifiers/snapshot hashes, not private field contents. Stop for mismatches or unclassified data.
3. Use an empty destination with separate database/uploads paths; never attach the existing `/data` volume or restore into the current staging directory. Prefer an ephemeral local filesystem with network blocked, no public ingress, no worker and no provider clients. No new Railway resources are authorized by this proposal.
4. Restore with `manage.py --db <NEW_EMPTY_PATH>/crownops.sqlite3 restore <APPROVED_ARCHIVE>` in that isolated process. Original key, if required for a decryption check, is handled privately by the owner/operator and never printed, bundled, persisted in the report or copied to production. Do not start a recovered app with valid copied OAuth credentials or queued work while network access is possible. A no-network process is a stronger guard than feature flags alone.
5. Confirm source records unchanged, restored protected quote/revision/audit/single-send/reissue histories preserved, exact row counts and hashes; restored sessions/invitations/portal sessions/reset grants/OAuth states are empty as designed; MFA configuration preserved; uploads readable with expected hashes and private permissions. Do not replay outbox/work queues. Inspect restore behavior against any new grant tables before declaring all recovery tokens revoked.
6. Prove application boot and read-only recovery visibility in the network-isolated process using disposable test access established only in the drill copy under the separately approved plan. No existing source credentials are reset. Record elapsed restore + validation time, archive age/source cutoff (measured RPO), exclusions and operator steps. Confirm handling of the encryption key without outputting it. Full sign-in/service recovery is a distinct check from archive integrity.
7. PASS requires integrity, usable application/read-only business data, preserved terminal send/reissue state, zero external attempts, offsite retention/key custody evidence and owner-agreed recovery objectives. Stop on any discrepancy. Delete only the disposable target after evidence review; never delete the approved source archive or existing volume. Source service stays untouched throughout. Retention policy and numeric RPO/RTO need owner approval; do not invent them.

## Manager: one private owner-assisted attempt, then stop

Account stays `WIX-PILOT-manager`, manager, active 1, employee_id null; no reset or repeated attempts. Owner checks the exact email and enters the saved replacement password privately. Muse reads `/api/me` before checking Wix readiness/status allowed and dedicated reissue page/preflight denied, then signs out. No mutation-denial POSTs.

If that single attempt fails, record only UTC time, requested host/build, HTTP status and sanitized generic error; confirm no session via `/api/me`; inspect retained authentication/request metadata narrowly, with no request bodies, cookies, passwords, hashes, IP addresses or tokens in the report. Check whether 401 (generic inactive/credential/MFA denial), 429 (rate limit), 403/422 (request/origin/validation), or transport failure occurred. Do not infer a wrong password from 401. Existing code counts all login attempts by IP over 15 minutes and limits at 12; Muse reported approximately 14 inactive-account attempts, so shared-IP throttling is a plausible diagnostic hypothesis, not the established Manager cause. Wait for the natural cooldown if 429; do not clear rate limits. Do not instrument/log credential bodies. No new sign-in attempt without owner participation.

## Crew employee-mapping preflight (not executed)

All five test accounts are currently unlinked; Crew's empty My Jobs is an unmet fixture dependency. Proposed later writes: (1) one synthetic active employee with no real identity/payroll/driver/pay data, only if no appropriate fictional employee exists, then (2) link ONLY `WIX-PILOT-crew` to that employee through the existing authorized Team access workflow with fresh expected revision and CSRF. Keep crew role, active flag and MFA unchanged. No jobs, shifts, bookings, invoices or payroll runs are included. Owner approval must name the exact employee fields/ID, target user ID and the two writes separately if creation is needed.

IMPORTANT: existing `/api/users/{id}/access` linkage changes revoke that target's sessions, reset grants and unused invitations and append an account-access audit. Disclose and approve this before mapping; a mapping-only change is not operationally session-neutral. Role tests must be completed/captured first, then owner privately signs Crew in again if mapping is approved.

Muse acceptance: `/api/me` shows the approved employee_id; My Jobs no longer says unmatched, but remains empty if no jobs exist; only that employee's private data is accessible; no pay/other-staff access appears. Actual assigned-work visibility needs a separately authorized fictional work fixture later. Do not create a booking just to remove an empty state. Reversing the employee link also revokes sessions and requires an appropriate approved access change; deleting an audited employee is not a rollback strategy.

## Deployment proposal and rollback

Deploy ONLY the candidate's exact reviewed commit to existing quote-testing service `5de22c3a-ea7e-4fa6-bc96-2f9472431c7c`, environment `53900324-c678-40b0-91f0-b6560316a5d2`, project `bfe6eb95-e8f3-45ef-8fad-afe3aadcef3f`, AFTER separate owner approval and CI PASS. No migrations, new service, variable changes or fixture preparation. Preserve reissue flag 0, PR91 guards, all records and external write blocks. Pre-deploy confirm no unrelated pending patch; post-deploy verify release/footer/health then Muse's focused read-only check.

Rollback, if authorized, pins ONLY this service back to `718c718b0f80cc16191a5b267d17b9f43a7bfd2a` / `9e0d79aed8b2a16d9ca5fb4e` with the same volume and unchanged flags. It reintroduces the settings display error but preserves PR91/92 and all data. No database restore, old credential recovery, grant cleanup or provider reconciliation is part of rollback. Do not execute rollback without authorization except an independently documented emergency stop to prevent harm.

## Ordered launch blockers

1. Review/test/deploy this P1 display candidate only after owner approval; then Muse reads actual Hosting & recovery status.
2. Complete one owner-assisted Manager session or collect sanitized diagnostics; staff-access remains 6/7 until then.
3. Approve and execute the isolated restore drill after a concrete archive/target preflight; confirm offsite custody, retention, key recovery and measured RPO/RTO. No GO based solely on a backup file.
4. Review/approve minimal Crew employee mapping; actual job work remains separately gated.
5. Operational CRM acceptance: WordPress EN/FR intake in an approved harness; canonical pricing/terms approval; actual QuickBooks delivery confidence (never resend 185); acceptance isolated from legacy automatic tentative-job/deposit behavior; separately authorized exactly-once booking/capacity test. Wix can remain manual during initial use if the owner chooses; automated draft creation still requires native auth/tax/readback qualification and separate write approval.

These checks distinguish restricted synthetic pilot readiness from real-customer launch. Neither is automatically approved by a patch or a passing role report. No optional feature work, production deployment, Staff AI, n8n or provider writes are part of this milestone.
