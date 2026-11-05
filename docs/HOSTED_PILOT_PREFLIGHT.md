# CrownOps — Hosted Pilot Preflight (disposable, non-production installation)

**Status:** this is a plan only. Nothing has been purchased, created or deployed.

**Purpose:** prove that one fictional company's installation can be deployed reproducibly, operated, backed up, restored, monitored and rolled back on real hosting. This must happen before any paying company's data is loaded.

**Local evidence behind this plan (2026-11-05):** a production-mode rehearsal on this machine. It used a real `uvicorn` server, `CROWNOPS_ENV=production`, a fictional company, and external actions off. Every step below except TLS, the host's volume and real offsite storage ran and passed (section 6).

## 1. Decisions needed before anything is created

| # | Decision | Options | Recommendation | Confidence |
|---|---|---|---|---|
| H1 | Hosting region | US-hosted (Railway: California, Virginia, Amsterdam, Singapore; **no Canadian region**, per Railway docs fetched 2026-11-05) vs a Canadian region | For a Quebec pilot company, a **Canadian region**. Otherwise a privacy impact assessment for transfer outside Quebec is needed first (legal review item L5). | Medium. Counsel decides whether US hosting is acceptable. |
| H2 | Host type | Managed container platform (Railway) vs a small VM in a Canadian region with Caddy for TLS | Railway is fastest and already used for G5. A VM gives a Canadian region but adds OS patching and TLS work. | Medium |
| H3 | Offsite backup storage | S3-compatible object storage in an approved region | Any S3-compatible storage the customer approves, reached through `CROWNOPS_BACKUP_OFFSITE_COMMAND` (e.g. `rclone`). Encrypt with `CROWNOPS_BACKUP_KEY` first. | High (mechanism); provider choice is Jonathan's |
| H4 | Who holds the keys | `CROWNOPS_SECRET_KEY` and `CROWNOPS_BACKUP_KEY` | Host secret store, plus an offline copy of the backup key held by Jonathan. Never stored next to the backups. | High |
| H5 | Pilot domain | Subdomain for the disposable installation | A subdomain Jonathan controls, for example `pilot-<n>.<domain>` | High |

## 2. Cost assumptions (dated; not quotes)

Prices come from the providers' own pages, fetched 2026-11-05, in USD. Usage figures are **assumptions**, not measurements. Re-check prices before purchase.

| Item | Source price (2026-11-05) | Assumed usage, one company | Assumed monthly |
|---|---|---|---|
| Railway plan | Hobby $5/mo or Pro $20/mo, each including the same amount of usage credit (docs.railway.com/pricing/plans) | Pro for business use | $20 floor |
| Railway RAM | $10/GB/month | 0.5 GB average | $5 |
| Railway CPU | $20/vCPU/month | 0.1 vCPU average | $2 |
| Railway volume | $0.15/GB/month | 10 GB (database plus uploads) | $1.50 |
| Railway egress | $0.05/GB | 10 GB | $0.50 |
| **Railway subtotal** | | usage about $9, under the Pro credit | **≈ $20 per workspace**, then usage above credit |
| Alternative: DigitalOcean droplet | 1 vCPU / 2 GiB / 50 GiB SSD: $12/mo; daily backups 30% of droplet cost (digitalocean.com/pricing/droplets) | 1 droplet | $12 + $3.60 = **≈ $15.60** |
| Offsite object storage | Backblaze B2 $6.95/TB/month, free egress up to 3× storage (backblaze.com/cloud-storage/pricing) | 14 daily archives × 0.5 GB = 7 GB | **< $0.10** (minimums may apply; verify) |
| Domain and TLS | Let's Encrypt via platform or Caddy: $0 | — | $0 plus the domain Jonathan already holds |
| Monitoring | Uptime check on `/api/health` plus `ops-check` alerting | Free-tier uptime monitor (assumption, not priced) | $0–$10 |
| **Per-company hosting floor** | | | **≈ $16–$30/month** before support labour |

**Caveats:**
- **Railway price lines disagree.** The marketing page shows per-second RAM and CPU rates that annualise differently from the docs table. The docs table was used here; confirm on the billing page before relying on it.
- **Regions not checked:** DigitalOcean's Canadian region and B2's regions were not verified in this session.
- **Upload volume drives everything.** Move photos and videos dominate storage. The synthetic drill archive is 37 KB; a real company with photos could reach several GB a year.

## 3. Reproducible deployment

1. **Pin the release.** Pick a reviewed commit and record its SHA. The image is built from `Dockerfile`: `python:3.13-slim`, ffmpeg, the pinned `requirements.txt`, and a non-root uid 10001. **Local image build is blocked here (no Docker daemon); the first hosted build is the first real build.**
2. **Separate project per company.** One service, one volume, mounted at `/data`. Never run replicas, because SQLite allows one writer.
3. **Secrets** go in the host secret store only. Generate each one in the operator's own shell; never paste values into tickets, chat or Git.

   | Variable | Value |
   |---|---|
   | `CROWNOPS_ENV` | `production` |
   | `CROWNOPS_SECRET_KEY` | new Fernet key |
   | `CROWNOPS_BACKUP_KEY` | `manage.py backup-key` |
   | `CROWNOPS_PUBLIC_URL` | `https://…` |
   | `CROWNOPS_SECURE_COOKIES` | `1` |
   | `CROWNOPS_REQUIRE_MFA` | `1` |
   | `CROWNOPS_ALLOW_EXTERNAL` | `0` |
   | `CROWNOPS_BACKUPS` | `/data/backups` (local copies; offsite is the real copy) |
   | `CROWNOPS_BACKUP_HOUR` | e.g. `3` |
   | `CROWNOPS_BACKUP_OFFSITE_COMMAND` | e.g. `rclone copyto --config /run/secrets/rclone.conf remote:bucket/` (the archive path is appended) |
   | `CROWNOPS_TRUSTED_PROXY_IPS` | if the platform proxies |
   | `CROWNOPS_SETUP_CODE_HASH` | temporary |

4. **First start** creates the schema (migrations 1–2) automatically.
5. **Provisioning:** follow `COMMERCIAL_DEPLOYMENT_RUNBOOK.md` sections 5–13 (identity, profile, setup code, pricing, templates, terms, staff, fleet), using fictional data and `.invalid` email addresses.
6. **Version identification:** record `GET /api/health` → `version` and `GET /api/app-release` → `revision` next to the commit SHA.

## 4. Hosted checklist (each item needs evidence: screenshot, command output or log line)

| # | Check | Pass condition |
|---|---|---|
| P1 | HTTPS only | `http://` redirects or refuses. Certificate valid. HSTS header present. |
| P2 | Startup guard | Removing `CROWNOPS_SECURE_COOKIES` makes startup fail (then restore it). |
| P3 | Private storage | The volume is not publicly reachable. Uploads are only served through authenticated routes. `/data` is owned by uid 10001. |
| P4 | Owner setup | Setup code works once. `CROWNOPS_SETUP_CODE_HASH` is removed afterwards. Owner MFA is enrolled on a **real phone**. |
| P5 | Fictional end-to-end | Inquiry → estimate → acceptance → booking → completion → invoice → manual payment, with no email leaving the system. |
| P6 | Scheduled backup | After `CROWNOPS_BACKUP_HOUR` passes: `ops-check` shows backup ok, `encrypted: true`, `offsite.state: ok`, and the object exists in offsite storage. |
| P7 | Restore drill | Download the offsite archive to a **separate** disposable environment, then `restore`, `db-status`, `ops-check`. The owner signs in with password and MFA. Time it (this is the recovery time). |
| P8 | Wrong-key detection | In the restore environment, set a different `CROWNOPS_SECRET_KEY`. `ops-check` must report `secret_key_mismatch`. |
| P9 | Monitoring | An external check on `/api/health` alerts on failure. `ops-check` exit 2 alerts. Test both by stopping the service. |
| P10 | Upgrade and rollback | Deploy a second commit, confirm the version changes, roll back to the first. Restore the pre-upgrade backup into a new environment. |
| P11 | Logs | Logs show no customer data, tokens or secrets after P5 to P10. |
| P12 | Teardown | Delete the service, volume, offsite objects and secrets. Record the destruction (runbook section 20). |

## 5. Rollback

| Situation | Action |
|---|---|
| Application-only change | Redeploy the previous SHA. |
| After a migration | An older release refuses the newer schema. Restore the pre-upgrade archive into a **new** volume or service and point the previous release at it. Data entered after the backup is re-entered. |

`BACKUP_REQUIRED` is empty today (no destructive migrations).

## 6. Local production-mode rehearsal (2026-11-05)

| Step | Result |
|---|---|
| `installation-identity`, `company-profile` (America/Halifax), `setup-code`, `db-status` | all exit 0 |
| `doctor` | production, external off, secret present, backup encryption configured, secure cookies, identity configured, web setup enabled |
| Server `/api/health` | `{"ok":true,"mode":"production","setup_required":true,"version":"0.13.0-dev"}` |
| Headers | HSTS, CSP, `X-Frame-Options: DENY`, `nosniff`, `no-referrer` present |
| Wrong setup code; login with no users | 401 and 401 |
| Startup with `http://` public URL | refused, exit 1, "Production requires HTTPS public URL and secure cookies" |
| `backup-run` | ok, `encrypted: true`, restore verified, 28 KB, 0.075 s; offsite command ok and file present |
| `ops-check` | exit 1, because only `disk_low` was reported (this container genuinely has about 12% free) |
| Restore to a new path, then `db-status` | exit 0 and exit 0 |
| Restore with no backup key | refused with a clear message |
| Company name in archive bytes | 0 occurrences (the archive is encrypted) |

**Not covered locally:** TLS termination, the host's volume semantics, the platform proxy, real offsite storage, a real MFA device, the container build.
