# CrownOps v5.6 - Private Review Build

> **Current private review build: v5.6** — week-first schedule, truck colour coding, fluid Gmail intake, dedicated Estimates / Follow-ups / Invoices browsers, simplified client/job workflow, mobile-responsive staff access, and role-based team collaboration. Live outbound Gmail, QuickBooks production writes, card payments and production schedule sync remain off until final review.


Private single-company web application for Crown Movers: sales, reviewed estimates, dispatch, client portal, job invoicing, collections, payroll drafts, private uploads, fleet, inventory, and integration controls. Built from the v3 CrownOps pilot, not from Carl's deployed schedule source.

**Start with START_HERE.md.** Runnable server code is the deliverable. No real accounts, private records, payment credentials or API keys are shipped in this source package. No deployment or live provider validation is implied.

## QuickBooks release safety

QuickBooks authorization and QuickBooks writes are deliberately separate.

- `QBO_ENV=sandbox` selects the Intuit sandbox.
- Sandbox writes require `CROWNOPS_ALLOW_QBO_SANDBOX_WRITES=1`.
- Production writes require both `CROWNOPS_ENV=production` and `CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES=1`.
- The general `CROWNOPS_ALLOW_EXTERNAL` switch does not enable QuickBooks writes.
- QuickBooks mappings can be read from the connected sandbox and saved from CrownOps Connections without enabling writes.
- Never enable production QuickBooks writes until customer, estimate, invoice, payment, duplicate-protection and amount-matching tests pass in sandbox.

## v5.6 final-workflow polish

The sales sidebar now separates **Sales & clients**, **Estimates**, **Follow-ups**, and **Communications**. Estimates and invoices have searchable status browsers and overview metrics; follow-ups have due/overdue/upcoming/completed views. Claiming a lead or inbox conversation assigns unowned open follow-ups to that staff member. Today surfaces CRM tasks and payment exceptions instead of historical Gmail call noise.

## Review-build purpose

v5.1 is intentionally **not** the final CrownOps. Jonathan and management are reviewing the product one section at a time before a staging/live release. The newest review workflows are: simplified mover My Jobs/All Jobs, Time Off approval + scheduling blocks, whole-crew hour entry with individual exceptions, manager yearly rates, editable materials, and client Documents & Approvals. See `REVIEW_CHECKLIST.md` and `CHANGES_v5_1.md`.

## Requirements and local test

Python >=3.11 (tested 3.13), with pip and a modern browser. Windows/Mac/Linux launchers create a virtual environment and install the pinned requirements. Use:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_demo.py
```

The demo creates random persistent credentials and fictional records in `demo_instance/`, binds only to loopback (`127.0.0.1`), and removes all external-provider credentials from the process. It prefers port 8765 but automatically reserves another local port if 8765 is already occupied. The browser opens only after the health endpoint responds. It does not read `.env`. Never use the demo launcher for company records. Use `Stop_Demo.command` / `Stop_Demo.bat` or Control+C to stop the recorded local server.

For a blank private test installation instead:

```sh
cp .env.example .env
python manage.py init --email owner@your-company.example --name "Owner"
python manage.py serve
```

Set the actual public URL and database/upload paths for your installation. Passwords are requested interactively. `.env` parsing accepts literal `KEY=value` lines only, without shell expansion. User management creates revocable one-use invitation links; sending or securely sharing those links is a separate action.

## Application routes

- `/`: staff application.
- `/portal`: project-scoped customer access using a one-use emailed/shared link.
- `/request`: native English/French lead request form.
- `/api/health`: health and initial-setup flag; no private records.
- `/manifest.webmanifest`: installable app metadata.

Every substantive business operation is an authenticated server action. A sidebar is not a permission boundary. Client sessions are distinct from staff sessions. CSRF checks protect browser mutations. Client uploads are private and cannot be retrieved before safety clearance.

## Roles

- `owner`: trusted manager, all sections and sensitive approvals.
- `sales`: all operational modules including CRM, quote preparation, schedule, invoices, payment tracking, fleet, stock, portal activity and reports. Manager approval is still required for formal quote approval, global rates, sensitive account/security changes and financial actions.
- `crew`: movers/team leads; lands on **My Jobs**, can switch to **All Jobs** for read-only company visibility, and can work/invoice only assigned jobs. Crew may request Time Off and manage their own requests. No sales quoting, unrelated employee wage data, secrets or payment-verification authority.
- `dispatch`, `accounting`: compatibility roles retained for existing installations; not sales quote access. Assign Sales or Owner where the new broad operational role is intended.

Individual authentication uses Argon2 password hashes, revocable sessions and optional TOTP MFA. Privileged account MFA must be enrolled as part of launch. One company/workspace per installed database; separate companies require separate installations in this version.

## Data and migration

SQLite WAL with explicit transactions and version checks is used for this single-instance release. Do not run replicas sharing a disk over a network filesystem. A larger multi-instance rollout would require a separate database adaptation and load testing.

The previously prepared private migration database is supplied separately. It includes imported leads/calls/tasks, employee directory records and the source-calendar overlay. Imported dates/statuses are not proof of paid deposits or accepted service contracts. The overlay is read-only until sales/management resolves source issues and promotes an entry to a working **tentative** job. Existing project selection connects multiple service phases to the same financial record. Promotion creates no payments or payroll.

```sh
python manage.py import /PRIVATE/PATH/CrownOps.xlsx
python manage.py import-schedule /PRIVATE/PATH/Crown-Schedule.xlsx --start 2026-09-27 --end 2027-12-31
```

Never import both an entire prepared database and the same data into a different independently authoritative calendar without reconciliation. Source event IDs are preserved. Repeated imports are duplicate-safe; changed sources require review. The separate source overlay also preserves original notes, comments, links, flags and text summaries from nine linked documents. Linked photographs are not bundled.

## Pricing and invoices

Rate source is the uploaded workbook snapshot, initially unapproved. Choose explicit minimum and fuel rules to resolve overlapping source settings. Manager-published exact-day rates override manager annual date-range rates, which override the source seasonal fallback for new quotes. Accepted snapshots never change retroactively. This release calculates local estimates from staff-reviewed crew, truck, service duration and transport assumptions. It does not implement a universal long-distance pricing or certified load-fit engine.

Accepted quote -> confirmed job -> invoice billing terms -> actual hours/material quantities -> completed job -> issued immutable invoice revision. Subsequent correction produces another revision. Customer actual time and minimum are displayed separately. Fixed accepted other charges are preserved. Materials use approved catalogue prices and actual quantities.

Tax lines are configurable, not inferred as one province's rules for every move. All invoice lines currently share the reviewed tax treatment; mixed-jurisdiction or nonuniform tax invoices need accountant review/manual accounting. Test invoices cannot create production Square charges or production QBO invoice exports.

Field collection records include receipt, collecting staff, request ID and pending status. Manager/accountant verifies actual receipt and allocates it atomically. Card confirmation requires a verified Square webhook with matching order/location/currency/amount. A checkout or screenshot is not payment confirmation. Deposits are project credits, not new revenue. Corrections and receipts are retained.

## Payroll

Payroll review uses **one total paid-hours value per employee**. The crew-hours workflow applies one common value to selected assigned staff and lets the manager adjust only individual exceptions. Ordinary exceptions do not require a reason. Existing older service/transport records are summed for compatibility; new simplified entries are stored as total paid time for payroll review.

Payroll managers choose any From/To date range and CrownOps totals approved/submitted time automatically. Private manager views show employee pay rate and a straight-time gross estimate; crew cannot see wage data. Approved periods remain auditable and later changes should be handled as reviewable adjustments rather than silently rewriting history.

Gross straight-time estimates only: no statutory net payroll, deductions, overtime entitlement determination, bank files or wage payouts. Confirm the applicable employment regime and payroll-provider mapping before using the export for pay processing.

## AI and integrations

All external actions are OFF by default. See deployment/INTEGRATIONS.md. OpenAI Responses extraction returns reviewed suggestions, not edits or prices. Token logging, per-user throttling, a monthly reservation budget and duplicate request protection are included. No model or key is silently selected. Only chosen message/project context is sent; no bulk workbook/payroll data is sent.

Gmail OAuth supports approved sending. Incoming email/forms/call logs/GPS use the HMAC bridge. QuickBooks OAuth supports approved unsent estimates, invoice export/update and verified fully allocated payment posting. Square supports deposits and final-balance checkout plus verified callbacks. Unknown writes remain blocked for reconciliation rather than blindly retried.

Square deposit testing can be enabled separately from general external actions. Keep `SQUARE_ENV=sandbox`, configure the Sandbox access token/location and signed payment webhook, then set `CROWNOPS_ALLOW_SQUARE_SANDBOX_DEPOSITS=1` and `CROWNOPS_SQUARE_SANDBOX_TEST_EMAILS` to the exact company test client email(s), separated by commas. With general external actions disabled, only projects whose stored client email matches this list can request a deposit checkout. Matching ignores letter case and surrounding whitespace; it does not allow wildcards. This permission does not enable production payments, invoice checkout, Gmail sending or QuickBooks writes. Both new settings default to disabled/empty. The Connections page shows presence and permission checks without returning credentials; a successful payment test is still required.

## Background work and alerts

Enable reviewed automation rules, then either `CROWNOPS_RUN_WORKER=1` on the single web process or `python manage.py worker`; do not run both unless intentional. Every 60 seconds, eligible preparation/review/collection messages are made as **drafts**, and fleet tasks are created. No automatic message, card charge or payroll release is triggered by the worker. A staff member reviews and sends through the outbox. Existing source notifications stay suppressed until activated individually.

## Tests and checks

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

See VALIDATION.md for actual results and boundaries. Provider-contract tests mock external responses. Browser checks use real API handlers through a TestClient transport bridge because browser navigation is blocked in the build environment. A separate loopback HTTP process checks saved data/login persistence. Neither proves public TLS deployment, real provider authorization, malware-scanner deployment or penetration-test certification.

## Backup, restore and recovery

```sh
python manage.py backup /PRIVATE/BACKUPS/crownops-YYYY-MM-DD.zip
python manage.py --db /NEW/EMPTY/instance/crownops.sqlite3 restore /PRIVATE/BACKUPS/crownops-YYYY-MM-DD.zip
python manage.py reset-mfa --email employee@example.com --reason "Identity verified by owner, recovery ticket ..."
```

Backups contain sensitive database records and uploads. Store encrypted, restrict access, retain/rotate according to company policy, and test restoration. The original `CROWNOPS_SECRET_KEY` must be kept separately in a secure vault to recover encrypted OAuth/MFA secrets. The restore command revokes sessions/invitations and never overwrites a database. Restore with external actions off until provider state and bookings are reconciled.

## Deployment and release ownership

Use deployment/DEPLOYMENT.md for private hosting. Docker/Caddy/ClamAV configuration is provided but was not executed against a Docker daemon or public domain here. Run a separate staging environment, verify scanner/permissions/backup restoration, review privacy and financial policies, then pilot with four staff and controlled clients before general rollout. Monitor and support the running service; this is not a managed SaaS service delivered with hosting.

## v5.4 final-review workflow
See `CHANGES_v5_4.md` and `VALIDATION_V5_4.md`. The primary office schedule is now Team view. Client/job screens have been simplified around client contact, service details, quote, photos, invoice and team hours.
