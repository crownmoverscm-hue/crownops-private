# CrownOps 0.11.1-rc1

Private CRM and moving-service workspace for Crown Movers. Start with [START_HERE.md](START_HERE.md) for the team process and [WORKFLOW_RELEASE_2026-10-18.md](WORKFLOW_RELEASE_2026-10-18.md) for the base workflow release. See [INTAKE_CORRECTION_2026-10-18.md](INTAKE_CORRECTION_2026-10-18.md) for the subsequent live-intake correction.

This is a tested workflow release. Publication and deployment evidence are tracked in [pull request #60](https://github.com/crownmoverscm-hue/crownops-private/pull/60) and the existing Railway service. A source archive alone does not prove that a deployment is live. No real client was emailed or charged during the isolated release checks.

## Team access

| Role | Workflow |
| --- | --- |
| Owner / Manager | All operational sections, financial review, expenses, team accounts and company settings. A linked employee account also appears in My Jobs when assigned. |
| Sales | Client intake, CRM, estimates, follow-ups, scheduling and operational invoice tracking. Sensitive financial approvals, expenses and security settings remain restricted. |
| Crew | My Jobs, limited All Jobs visibility, assigned-service preparation, truck check, actual hours/materials, invoice preparation and delivery. Price changes require management review. |
| Client | Private moving file, prefilled information form, media and folder links, callback request, estimate and invoice. An invoice-only payer link exposes only its approved invoice. |

Permissions are enforced by authenticated server actions. Client access is separate from staff access. Changes use version checks; issued invoice revisions preserve the issued document.

## Run the source locally

Python 3.11 or later is required. This release was tested with Python 3.12.14. The Railway Dockerfile targets Python 3.13.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python manage.py init --email owner@example.invalid --name "Owner"
.venv/bin/python manage.py serve --host 127.0.0.1 --port 8765
```

Use fictional information in a disposable local installation. The initialization command requests the password interactively. Configure the installation using the documented environment variables; no credentials, production database or private uploads are included in this package.

## Validate

```sh
.venv/bin/python -m unittest discover -s tests -v
node --test tests/local_estimator.test.cjs tests/move_navigation.test.cjs
```

Server tests include role permissions, CRM intake, quote editing, uploads, booking, crew preparation, invoice actuals, verified deposits and mocked provider reconciliation. Node tests cover the calculator and schedule navigation. The separate browser audit used disposable records and captured email MIME with simulated provider responses. It does not prove live email delivery or live payment settlement.

## Deploy and operate

Use [RAILWAY_SETUP.md](RAILWAY_SETUP.md) and [GO_LIVE_RUNBOOK.md](GO_LIVE_RUNBOOK.md). Preserve the existing database, uploads, persistent volume and encryption key. Existing installations receive a restore-verified backup before this release's schema migration. Verify the startup evidence and download an off-service recovery copy.

The source must be reconciled with the current company GitHub repository before publishing. See [GITHUB_SYNC.md](GITHUB_SYNC.md). Do not redeploy older repository contents over this release.

QuickBooks authorization, read checks and accounting writes are separate. Sandbox writes require `CROWNOPS_ALLOW_QBO_SANDBOX_WRITES=1`; production writes require production mode and `CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES=1`. Confirm the company, currency and mappings before enabling writes. General email permissions do not authorize QuickBooks writes.

Invoice payment checks require current invoice identity, CAD amounts and linked payment evidence. Owner/Manager reconciliation imports proved receipts with duplicate protection. Credits, ambiguous local receipts, changed invoices or unavailable evidence require review. Expenses are a private internal ledger and are not automatically exported to QuickBooks.

Review and approve the company sender and Reply-To settings only after confirming the Gmail identity and testing any Send-as alias. Outgoing client messages remain reviewed actions. Unknown delivery outcomes require reconciliation instead of a blind resend.

Uploads require the configured private storage, safety scanner and clearance workflow. Shared photo/video folder links are also supported. This single-instance SQLite release requires one mounted persistent volume; do not run multiple replicas against a shared network disk.

The app prepares gross payroll drafts rather than statutory net payroll or wage payouts. Tax/accounting setup and actual crew protocols require management review. A proposed service date is not a confirmed booking; acceptance, payment arrangement, date availability and dispatch remain explicit steps.

## Before general launch

Publish this release, verify its health version and migration backup, then complete one controlled real email/intake and QuickBooks invoice/payment walkthrough. Confirm team accounts, the shared crew guide, company billing/material rates, uploads, permanent hosting and recovery ownership. Run a small team pilot before inviting all clients.
