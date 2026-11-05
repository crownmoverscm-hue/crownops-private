# CrownOps — Security and Reliability Review (2026-11-05)

**What this is:** an internal code review plus local tests, done by the development agent on branch `claude/vigilant-carson-1eevyy`.

**What this is not:**
- an external penetration test
- a third-party audit
- a test against a hosted installation or a live provider

Nothing here should be presented to a customer as an independent security assessment.

**Baseline before this review:**
- commit `5c0cd67`, `APP_VERSION 0.13.0-dev`, schema version 2
- 723 Python tests, 20 Node tests and 12 browser suites passing

## Verdict

Five high or medium defects were found. Four were fixed locally with regression tests:
- dependency vulnerabilities
- unencrypted backups
- no backup scheduler that fits a single-volume host
- silent MFA lockout after a restore under the wrong key

One high item stays open because fixing it needs approval: the **G5-pinned Core v1 pilot lockfiles still pin the vulnerable dependency set**.

Commercial-integration gaps (CAD-only, Quebec tax mapping, and no in-app resolution for some Unknown provider outcomes) are recorded as pilot blockers, not as security defects.

## Findings

| ID | Severity | Area | Finding | Status | Evidence |
|---|---|---|---|---|---|
| SR-1 | High | Dependencies | `pip-audit` 2.10.1 found 70 advisories across 5 pinned packages (detail below). Starlette and python-multipart parse every request, and Pillow parses every uploaded image. | **Fixed** in `requirements.txt`. The new set audits clean. | commit `b9e8776`; 733/733 tests and 12/12 browser suites on the new set |
| SR-2 | High | Dependencies (Core v1 pilot) | `tools/pilot/requirements-offline.lock` and `requirements-python313.lock` pin the same vulnerable versions. They are hash-pinned in `g5/CONTEXT-PINS.json`. | **Open — needs approval.** A re-pin changes G5 evidence. | `grep` of both lockfiles |
| SR-3 | High | Backups | Archives held every customer record and upload in plaintext. The runbook relied on the operator to encrypt them. | **Fixed (opt-in).** `CROWNOPS_BACKUP_KEY` gives streamed AES-256-GCM with a per-chunk counter, a last-chunk flag and an authenticated header. In production, `ops-check` warns while it is unset. | `tests/test_backup_encryption.py` (9 tests: tamper, truncate, reorder, duplicate, header swap, wrong key, CLI) |
| SR-4 | High | Backups (hosting) | The Docker image has no scheduler. On hosts where a volume mounts on one service only (Railway, per its docs), a separate scheduled container cannot reach the database. | **Fixed (opt-in).** `CROWNOPS_BACKUP_HOUR` runs a daily backup in-process. `CROWNOPS_BACKUP_OFFSITE_COMMAND` runs the operator's copy command. Failures go to `ops-check`. | `tests/test_backup_schedule.py` (6 tests, including the real app lifespan) |
| SR-5 | Medium | Restore | MFA secrets and provider tokens are encrypted with `CROWNOPS_SECRET_KEY`. A restore or redeploy with a different key made MFA fail silently: every MFA user was locked out and connections broke. | **Fixed (detection).** `ops-check` reports `secret_key_mismatch` (critical) with counts only. Recovery uses `manage.py reset-mfa` (local, audited) and reconnecting providers. | `test_backup_drill.py::test_5` |
| SR-6 | Verified | Restore | Restore revokes sessions, invitations, portal sessions, portal invites, password resets and OAuth states. It keeps accounts, password hashes and MFA enrolment. | **Verified by test.** The owner signs in with password and TOTP on the restored copy; the pre-restore session cookie gets 401. | `test_backup_drill.py::test_5` |
| SR-7 | Verified | Webhooks | All four inbound endpoints fail closed when unconfigured (503): bridge, intake, Square and Wix. Intake and bridge require HMAC plus a timestamp within ±300 s. Square uses HMAC over URL and body, with dedupe by event id and by payment reference. Wix uses a hashed connection key. | No change needed. Square had **no tests**, so 6 were added. | `tests/test_square_webhook.py` |
| SR-8 | Medium | White-label | Client and staff API errors said "Ask Crown…", "Crown staff only." and similar. The branding middleware rewrites pages and static files, but not API error JSON. | **Fixed.** An AST scan now fails the build if an `HTTPException` message names Crown. | `tests/test_white_label_messages.py` |
| SR-9 | Low | Information exposure | Public `/api/health` reveals `mode`, `version` and `setup_required`. `/api/app-release` reveals a content hash. Both help an attacker fingerprint the release. | Accepted for the pilot. Monitoring and the browser refresh depend on them. Revisit before general sale. | production rehearsal output |
| SR-10 | Low / design | Payments | Assigned crew can create a Square checkout for an **issued** invoice's balance (on-site collection). They cannot change the amount. | Design question for Jonathan, not a defect. | `providers.py` `square_link`, `platform_common.invoice_job` |
| SR-11 | Medium | Operations | Some Unknown or Mismatch outcomes have no in-app resolution: an Unknown Square checkout blocks new links indefinitely; Unknown or Mismatch QuickBooks invoice exports; Unknown non-invoice client emails. `ops-check` reports them. | **Open.** Each needs an owner-only reconcile action with evidence. | integration inventory, `docs/INTEGRATION_TEST_PLANS.md` |
| SR-12 | Medium | Commercial scope | `CAD` is hard-coded in QuickBooks and Square payloads and in webhook checks. QuickBooks tax mapping is the Quebec GST/QST picker. | **Open — pilot scope limit.** Pilot only with a Canadian company whose tax setup is verified. US companies are blocked. | inventory: `providers.py:180,658,684`, `deposits.py:47,64`, `invoice_payments.py:51-58` |

**SR-1 package detail:**

| Package | Advisories | Upgraded |
|---|---|---|
| starlette | 10 | 0.50.0 → 1.10.0 |
| python-multipart | 6 | 0.0.29 → 0.0.33 |
| requests | 2 | 2.32.5 → 2.34.2 |
| cryptography | 13 | 46.0.4 → 50.0.2 |
| Pillow | 39 | 12.3.0 → 13.0.0 |

FastAPI was also moved from 0.143.0 to 0.162.0.

## Areas reviewed with no new defect found

These were re-read in this session. Their existing tests already cover them.
- **Owner setup:** closed unless `CROWNOPS_SETUP_CODE_HASH` is set. The rehearsal confirmed a wrong code gets 401.
- **Login:** same message for an unknown user and a wrong password; 12 attempts per 15 minutes per IP; Argon2id; MFA optional or required.
- **Production startup:** refuses an `http://` public URL or insecure cookies (rehearsal: exit 1). Uploads stay disabled unless a scanner or the restricted image processor is configured.
- **Security headers in production:** HSTS, CSP (`script-src 'self'`), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer` (rehearsal output).
- **Logo uploads:** re-encoded with Pillow, 2 MB and 6000 px limits, SVG refused (`test_company_profile.py`).
- **White-label escaping:** context-specific encoding for JavaScript, HTML, JSON and CSS (`test_white_label.py`, 12 tests).

## Not done (do not claim these)

- **External penetration test:** none. Recommend one before general sale, not before a supervised pilot.
- **Hosted TLS, proxy and header verification:** blocked, because nothing is deployed.
- **Container image build:** blocked; there is no Docker daemon in this environment.
- **Load or concurrency testing:** only a single-process SQLite design check.
- **Dependency audit of system packages** (ffmpeg, the Debian base image): not done.
