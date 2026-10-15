# CrownOps Railway hosting

Use the existing CrownOps service and existing volume. Its current online address can remain the permanent team address; a custom domain is optional.

Important:
- Do not commit any `.env` file, database, uploads, passwords, OAuth secrets, or customer data.
- For the first staging deploy keep `CROWNOPS_ALLOW_EXTERNAL=0`.
- Use a Railway Volume mounted at `/data` before importing real data.
- Set `CROWNOPS_DB=/data/crownops.sqlite3` and `CROWNOPS_UPLOADS=/data/uploads` after the volume is attached.
- Railway injects `PORT`; the Dockerfile listens on it.
- Health endpoint: `/api/health`.

## Permanent team launch

1. Confirm the existing service has a persistent volume. Keep its current mount path and database file; the usual paths are `/data`, `/data/crownops.sqlite3`, `/data/uploads` and `/data/backups`. Do not attach an empty replacement volume or change a working database path during launch. Railway supplies `RAILWAY_VOLUME_MOUNT_PATH` automatically.
2. In the service's Backups tab, enable daily and weekly volume backups and create a manual backup before cutover. This requires a real hosting setting; the app cannot infer that a schedule is enabled. Keep a separate private portable copy outside the volume because deleting the volume also deletes its native backups.
3. Keep the existing `CROWNOPS_SECRET_KEY`, Google credentials and connected accounts. Save the original encryption key in the company password vault separately from backup files.
4. Keep one running web instance for this SQLite database. Use the existing paid Railway workspace, verify its billing and usage alerts, keep the service available during working hours, and configure continuous monitoring of the app's `/api/health` address. The deploy health check is not continuous uptime monitoring.
5. The tracked `railway.toml` uses the Dockerfile, checks `/api/health` before accepting a deployment, and restarts a failed process up to ten times. Keep GitHub CI checks required before deployment. The app accepts Railway's documented health-check hostname. Do not use a volume-dependent pre-deploy command.
6. Sign in as master owner, open **Launch checklist → Hosting & recovery**, and use **Create, verify & download backup**. It checks every archived file and restores a disposable copy; it never replaces the running database. Store the download privately off this service. Portable copies contain encrypted OAuth/MFA credentials but do not include the original encryption key.
7. Enable owner authenticator protection in **My account**. Verify every active owner has MFA. Invite staff with individual accounts and deliberately selected roles; Owner means full administrative access. Restore removed accounts in **Team access**.
8. After storage, backup and owner security are verified, set `CROWNOPS_ENV=production`, `CROWNOPS_SECURE_COOKIES=1`, `CROWNOPS_ALLOW_AUTH_EMAIL=1` and keep the existing HTTPS `CROWNOPS_PUBLIC_URL` (or supplied Railway domain). Keep `CROWNOPS_UPLOADS_ENABLED=0` until a real malware scanner is configured and tested. This mode change allows invitations and recovery for eligible staff without the staging email allowlist.
9. Keep `CROWNOPS_ALLOW_EXTERNAL=0`, `CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES=0` and the currently reviewed Square/QuickBooks environments while launching core team work. Staff account emails use their dedicated permission. Real customer email, payments and accounting exports have separate activation and reconciliation steps; they are not prerequisites for using the team workspace.
10. Deploy, verify the health endpoint, then test sign-in, one invitation, recovery, removal/restoration, a saved inquiry, a draft estimate and the schedule on real team devices. A Railway deployment attached to a volume can have brief downtime; no zero-downtime promise is made.

Production on Railway refuses to start if its database/uploads/backups paths are outside the attached persistent volume or the existing database is missing. Repair the path or restore the existing data instead of allowing an empty replacement database. The owner status page reports runtime evidence separately from hosting settings that still require verification.

## Recovery tools

`python manage.py --db /data/crownops.sqlite3 backup /PRIVATE/crownops-recovery.zip`

`python manage.py verify-backup /PRIVATE/crownops-recovery.zip`

`python manage.py --db /NEW/EMPTY/crownops.sqlite3 restore /PRIVATE/crownops-recovery.zip`

Never restore over the live database. Coordinate a maintenance window, stop writes, restore to a new private location with the original key, verify records and integration state, then deliberately switch the service. Restore invalidates staff/client sessions, staff/client invitations, reset links and pending OAuth sign-ins. It preserves passwords and MFA enrollment. External actions remain subject to the hosting settings and must be kept off during recovery.
