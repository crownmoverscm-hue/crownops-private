# CrownOps deployment — October 16, 2026

## Outcome

Production deployment `0ace549c-ee1e-4f1b-9a70-4421ecad5fbf` is SUCCESS. CrownOps reports version `0.7.0-rc1`, production mode, existing initial setup, and private photo availability. Railway reports one running replica, no crashed replicas, no current issues and no pending changes.

App: https://crownops-private-production.up.railway.app/

Target: existing `dynamic-joy` project / `crownops-private` service / `production` environment. Existing 500 MB volume remains mounted at `/data`. No new service, project, account, role assignment, provider authorization, plan upgrade or production email/payment action was created.

## Recovery and migration evidence

Startup completed at 07:01 UTC / 03:01 Montreal time. Before altering the legacy role constraint, the new startup guard made and restore-tested a portable database/upload backup:

- File: `/data/backups/crownops-20261016T070116Z-2226e6afd9e43f6b.zip`
- SHA-256: `c9dad9ec30f1f61a354e9bbdc6cec1e99a05f99251a3b719cc1d2282a074fde3`
- One private uploaded file included and checked.
- Runtime log: `Manager-role migration complete: 4 accounts preserved; foreign-key check passed.`

The original encryption setting was retained. Credential values were not retrieved for verification: automatic review rejected a broad production-variable read because it would fetch secrets. A safer redacted settings-name check and runtime health checks completed instead.

## Verification

- 241 Python tests and 13 Node tests pass.
- Local Chromium expense workflows pass at desktop, tablet and phone sizes; the phone crew invoice workflow passes with mocked email and QuickBooks transports.
- Public production health returns HTTP 200, version `0.7.0-rc1`, `mode=production`, `setup_required=false`.
- All 40 deployed JavaScript/CSS/entry HTML files match the clean deployment source byte for byte.
- Signed-out expense list/export, invoice delivery state and QuickBooks payment-link routes all return HTTP 401.
- Railway runtime logs confirm verified recovery and account-preserving migration before application startup.
- Signed-in production role workflows and actual Gmail/QuickBooks transactions were not performed. A production browser smoke attempt encountered the workspace proxy certificate trust issue; production HTTP checks passed with the workspace's configured CA. Local browser results are recorded separately from production checks.

## Remaining work

1. Synchronize this source with GitHub before deploying from the linked repository again. Previous live/source commit was `247844fc1308abf19eb5cc0e0c1efb2be621a581`; this release was uploaded directly and is not a GitHub commit.
2. Verify Owner/Manager/Crew workflows with the company's actual accounts and verify privileged-account MFA. Assign Manager access and employee links explicitly in Team access where appropriate.
3. Perform a controlled real company-test-client email and QuickBooks payment/reconciliation test. No live email, payment or accounting write is established by mocked tests.
4. Download the verified app recovery copy and retain it outside this volume. Railway rejected both volume snapshot creation and backup scheduling: those features require a Pro workspace. No automatic backup schedule was enabled and the workspace plan was not changed.
5. Before December 1, 2026, review Railway's migration from `railway.toml` to its current infrastructure configuration. The CLI warned that the existing configuration format remains supported until that date. This release preserves the working build/health configuration.
