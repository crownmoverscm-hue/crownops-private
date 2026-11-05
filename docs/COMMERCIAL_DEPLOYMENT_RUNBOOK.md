# CrownOps — Commercial Deployment Runbook (one installation per moving company)

**Version 0.13.0-dev · 2026-11-05.** Approved architecture: each moving company gets its own installation, which means its own application process, database, upload storage, backups, secrets and provider accounts. There is no shared multi-tenancy.

**Validated by** `tests/test_documented_provisioning.py`. That test provisions two fictional companies using only the commands and APIs in this runbook. Each company goes from setup to a verified invoice payment, then through backup, `db-status` and `ops-check`.

**Not validated by that test:**
- a real hosting provider
- TLS
- live email, QuickBooks or Square
- a real owner's MFA device

These remain pilot checks; see section 17.

**Conventions:**
- Commands run in the installation's own environment.
- `manage.py` always takes `--db <path>`.
- Never paste secret values into tickets, chat, Git or this document.

---

## 1. Hosting and runtime prerequisites

| Need | Requirement |
|---|---|
| Runtime | Python 3.13, the pinned `requirements.txt`, and `ffmpeg` for photo and video processing. The provided `Dockerfile` builds this. |
| Processes | **One** web process per company. SQLite allows only one writer. Never run replicas against the same database. |
| Storage | A private persistent volume for the database (`CROWNOPS_DB`) and uploads (`CROWNOPS_UPLOADS`). Use a **different** location for backups (`CROWNOPS_BACKUPS`), ideally a separate volume. |
| Network | HTTPS in front of the app (`CROWNOPS_PUBLIC_URL` must be `https://…`). If a proxy is used, set `CROWNOPS_TRUSTED_PROXY_IPS`. |
| Time | The system `tzdata` must be current. Company time zones use it. |
| Monitoring | Something that can run `manage.py ops-check` on a schedule and alert on its exit code (section 16). |

## 2. Application installation and version selection

Deploy a specific, reviewed commit or tag. Never deploy a floating branch.

After deployment, record two values:
- the `version` from `GET /api/health`
- the `revision` from `GET /api/app-release`, a content hash that identifies the exact files

Keep the previous release available for rollback (section 19).

## 3. Database initialization and migrations

The first command against a new database path creates the schema and applies every migration. Running it again is a no-op.

```bash
python manage.py --db "$CROWNOPS_DB" db-status
```

This command is read-only and needs a database that already exists, so run it after section 5. It exits `0` when the schema is current, and `1` if migrations are pending, the schema has problems, or the database is newer than the application.

How migrations behave:
- Migrations are ordered and recorded in `schema_migrations` and `PRAGMA user_version`.
- Each one runs in a single transaction.
- A failed step is rolled back completely.
- A database newer than the release is refused before anything is written.

See `app/migrations.py`.

## 4. Unique secrets and the owner setup code

Generate a **new** application key for every company. Never reuse one.

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Store the output only in the hosting provider's secret store as `CROWNOPS_SECRET_KEY`.

Generate the one-time owner setup code:

```bash
python manage.py --db "$CROWNOPS_DB" setup-code
```

This prints two things:
- a setup code: give it to the company owner privately, once
- `CROWNOPS_SETUP_CODE_HASH=…`: store this as a secret in the deployment

Remove `CROWNOPS_SETUP_CODE_HASH` after the owner account exists. Without it, web setup is closed.

**Required environment for production:**

| Setting | Value |
|---|---|
| `CROWNOPS_ENV` | `production` |
| `CROWNOPS_SECRET_KEY` | the new key generated above |
| `CROWNOPS_DB` | path on the private volume |
| `CROWNOPS_UPLOADS` | private folder on the volume |
| `CROWNOPS_BACKUPS` | separate private folder or volume |
| `CROWNOPS_PUBLIC_URL` | the `https://` address |
| `CROWNOPS_SECURE_COOKIES` | `1` |
| `CROWNOPS_REQUIRE_MFA` | `1` (recommended) |
| `CROWNOPS_ALLOW_EXTERNAL` | `0` until section 14 is complete |
| `CROWNOPS_BACKUP_KEY` | output of `manage.py backup-key`; never the same as `CROWNOPS_SECRET_KEY`, and never stored with the backups |
| `CROWNOPS_BACKUP_HOUR` | local hour (0–23) for the in-process daily backup, when the host cannot run a scheduled job against the same volume |
| `CROWNOPS_BACKUP_OFFSITE_COMMAND` | the operator's copy command; the archive path is appended (section 15) |

## 5. Explicit installation identity

```bash
python manage.py --db "$CROWNOPS_DB" installation-identity --mode unconfigured --reason "New customer <company> provisioned by <operator>"
```

- This records that the installation is **not** Crown Movers. The step is audited.
- New installations never inherit Crown's name, legal identity, tax numbers, mailboxes, prices, templates or review links.
- Only Crown's own installation is ever `legacy-crown`. Changing that on production requires owner authorization.

## 6. Company profile

```bash
python manage.py --db "$CROWNOPS_DB" company-profile --name "<Company>" --phone "<phone>" --email "<client email>" --timezone "<IANA zone>"
```

The owner can change these later on the **Company profile** screen. Until a profile exists, the installation refuses to:
- send client email
- issue invoices
- create provider documents

Inquiries are still captured.

## 7. Branding and logo

The owner can upload a logo on the Company profile screen:
- PNG, JPEG or WebP only; SVG is refused
- up to 2 MB
- re-encoded to PNG on upload

The owner can also set an optional brand colour. Pages, scripts, the manifest and icons then show the company's identity.

## 8. Time zone

Set the business time zone **before the first booking**. It cannot change once jobs, availability or imported schedules exist, because booked arrival times would shift. If the server is running, a change made with the command (section 6) takes effect after a restart; changes made on the screen apply immediately.

## 9. Pricing policy

On the Company profile screen, an Owner or Manager enters every pricing value:
- minimum trucks for each crew size
- minimum hours
- fuel and extra-truck fees
- long-distance factors
- junk-removal tiers
- default deposit
- liability wording

They can save a draft and then **approve** it. CrownOps supplies no default prices. Estimates are refused until a policy is approved, and each approval is recorded as a pricing snapshot for estimate history.

## 10. Hourly rates and services

- **Hourly crew rates:** the owner enters them in **Pricing & rates**, by date range and crew size (`POST /api/annual-rates`).
- **Quote templates:** under Company profile → Quote templates, the owner writes the company's own service and liability wording and approves each template it will use. New installations receive no Crown wording.

## 11. Tax and invoice settings

In **Operating rules → invoice setup**, the owner enters:
- the issuer's legal name and address
- tax names, rates and **the company's own** registration numbers
- a review note, then approval

In **booking terms**, the owner enters the company's own reviewed terms and approves them. Clients cannot accept estimates without approved terms.

## 12. Staff accounts

The owner invites staff in **Team access**, choosing Sales, Dispatch, Crew, Accounting or Manager. Invitations are single-use and expire after 48 hours. Recommend MFA for every account.

## 13. Trucks and crews

**Fleet:**
1. Add each vehicle.
2. Activate it with a review note.

**Employees:**
1. Add each employee.
2. Verify each driver's licence and authorization, with a note. A truck needs a verified driver before a job can start.

**Dispatch** assigns crew and truck on the job plan and marks capacity reviewed.

## 14. Email, accounting and payment providers

Each company connects its **own** accounts. Never share credentials between installations.
- Gmail sender and alias, under Connections
- QuickBooks, with sandbox first
- Square, if used

Keep `CROWNOPS_ALLOW_EXTERNAL=0` until each connection is verified in its sandbox and the owner approves live use. **Live email, accounting and payment have not been verified for any commercial installation yet.**

## 15. Backups

Run the backup command on a schedule, at least daily:

```bash
python manage.py --db "$CROWNOPS_DB" backup-run --keep 14
```

If the host cannot run a scheduled job that mounts the database volume, set `CROWNOPS_BACKUP_HOUR` instead. The application process then runs the same backup once a day, after that local hour. It retries no sooner than an hour after a failure.

**Encryption.** Set `CROWNOPS_BACKUP_KEY` (from `manage.py backup-key`) and archives are written as `crownops-backup-*.zip.enc`, using streamed AES-256-GCM. `verify-backup` and `restore` detect encryption automatically and need the same key. Keep an offline copy of the key with the owner: **without it, encrypted backups cannot be restored.** In production, `ops-check` warns while backups are unencrypted.

**Offsite copy.** Set `CROWNOPS_BACKUP_OFFSITE_COMMAND` to a command that copies one file to approved storage, for example `rclone copyto --config /run/secrets/rclone.conf remote:bucket/`. The archive path is appended as the last argument, and the command runs without a shell. Only its exit status is recorded. A failure makes `backup-run` exit `1`, and `ops-check` reports `offsite_copy_failed` as critical. CrownOps never selects or contacts a storage provider itself.

Each run:
- takes a consistent SQLite online-backup snapshot and includes uploads
- writes a checksummed manifest
- runs a full restore test into a temporary location
- applies retention, removing only its own `crownops-backup-*.zip` files
- records the result for monitoring

It exits `1` on failure.

**Without `CROWNOPS_BACKUP_KEY`, archives are not encrypted by CrownOps.** In every case:
- Copy each archive to offsite storage that only the company's operators can reach.
- Keep `CROWNOPS_SECRET_KEY` in a **separate** secret store. Never keep it alongside the backups.
- Set retention to match the agreed policy.
- Never send archives to a provider the customer has not approved.

To restore, always use a **new, empty** location:

```bash
python manage.py --db /new/path/crownops.sqlite3 restore /path/to/archive.zip
```

Restore behavior:
- It refuses to write over an existing database or upload folder.
- It refuses a backup made with a newer schema.
- It revokes all sessions.

Before switching traffic, check the result with `verify-backup`, `db-status` and `ops-check`.

**Measured on the synthetic drill company** (`tests/test_backup_drill.py`):

| Step | Time |
|---|---|
| Backup | 0.025 s |
| Backup plus restore test | 0.047 s |
| Restore | 0.016 s |
| Archive size | 38 KB |

Real installations scale with database and upload size.

**Recovery point:** the last successful backup. **Recovery time:** the restore time plus the time to redeploy.

## 16. Monitoring and health checks

| Check | How it's used |
|---|---|
| `GET /api/health` | Public liveness check. No private data. |
| `python manage.py --db "$CROWNOPS_DB" ops-check` | Exit `0` ok, `1` warning, `2` critical. Alert on `2`, and review `1` daily. |
| `GET /api/ops/status` | The same report for Owners and Managers. |

`ops-check` covers:
- database integrity
- schema version
- backup state and age (`CROWNOPS_BACKUP_MAX_AGE_HOURS`, default 26)
- client messages needing reconciliation
- accounting or payment operations needing reconciliation
- background jobs failing repeatedly (`CROWNOPS_JOB_FAILURE_ALERT`, default 3)
- stored MFA secrets or provider tokens that the configured `CROWNOPS_SECRET_KEY` cannot read (`secret_key_mismatch`, usually a restore under the wrong key)
- the offsite copy outcome, and in production, unencrypted backups or a missing offsite command
- free disk space (`CROWNOPS_DISK_WARN_PERCENT` 15, `CROWNOPS_DISK_CRITICAL_PERCENT` 5)

It reports counts only, never customer data. See `docs/OPERATIONS_SUPPORT_RUNBOOK.md`.

## 17. Initial verification

1. `doctor`: `company_profile_configured: true` and `installation_identity: configured`.
2. `db-status` exits `0`.
3. Company profile → setup checklist is all **Approved**.
4. Complete one fictional inquiry, from estimate through acceptance, booking, completion and invoice, using `.invalid` email addresses. Then delete it or mark it as a test.
5. `backup-run` succeeds.
6. `ops-check` is `ok`.
7. **Pilot-only checks, still open:** TLS, MFA on a real device, the live provider sandboxes, and the owner's review of all legal wording.

## 18. Upgrade procedure

1. Read the release notes and check for migrations marked as requiring a backup.
2. Run `backup-run` and confirm it succeeded.
3. Deploy the new release. Startup applies migrations automatically. A migration listed in `BACKUP_REQUIRED` takes a verified backup first and is not applied if that backup fails.
4. Confirm `db-status` exits `0`, `ops-check` is `ok`, and the `/api/health` version is the new one.

## 19. Rollback and recovery

- **Application-only rollback** (no schema change): redeploy the previous release.
- **Rollback after a migration:** an older release refuses a newer schema. Restore the pre-upgrade backup into a **new** location, point the previous release at it, and verify. Data entered after that backup must be re-entered.
- **Corrupted database** (`ops-check` reports `database_integrity`): stop writes, restore the latest verified backup into a new location, verify, then switch over.

## 20. Offboarding and data export

1. Agree the export scope and retention period with the customer in writing.
2. Run `backup-run` to get a consistent archive of the database and uploads. Hand it over through an approved secure channel, together with the restore instructions.
3. Revoke staff sessions by disabling users. Disconnect providers, and revoke any OAuth grants on the provider side.
4. After the retention period, and only with written confirmation, destroy the database, uploads, backups and secrets. Record the destruction.

## Actions without an owner- or operator-facing screen yet

| Action | Current path |
|---|---|
| Installation identity, setup code, initial profile, backup, `db-status`, `ops-check` | `manage.py` commands (operator) |
| Driver verification and vehicle activation | Existing staff screens |
| Data export for offboarding | Backup archive only; no per-record export format yet |
