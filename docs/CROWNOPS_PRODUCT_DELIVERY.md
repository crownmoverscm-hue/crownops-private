# CrownOps — Product Delivery Plan (living document)

Last updated: 2026-11-05. Keep this file current; it is the hand-off point between sessions.

**Status labels:** Verified (inspected and covered by a passing test), Reported (documented only), Partial, Missing, Broken, Proposed.

**Readiness labels:**
- Crown Movers internal use: **PILOT CANDIDATE** (unchanged). Production runs the `b6fb81b` baseline.
- Commercial use (other moving companies): **NOT READY**.

---

## 1. Current verified state

| Fact | Status | Evidence |
|---|---|---|
| Production runs `b6fb81b` (0.12.7-rc6) | Verified 2026-11-05 | The live `/api/app-release` revision `b3d6a27532e94813bcbb070e` equals the content hash of `b6fb81b`. |
| `main` (`db59167`) CI red: 622 tests, 12 errors | Verified locally and in CI runs 378, 379, 380 | All 12 errors are `RuntimeError: Subprocess execution is disabled in Core v1` (`app/core_v1.py:104`), leaked through the shared test process. |
| Root cause is test-process isolation, not product code | Verified | Running a pristine `db59167` with `tests/run_isolated.py` gives 55 modules, 622 tests, 0 failures. |
| Stack | Verified | Python 3.13, FastAPI, SQLite (WAL), a vanilla-JS single-page app, Docker on Railway with one replica and one volume. |
| Tenancy | Verified | Single company. No company or tenant key in any table (`app/platform_schema.sql`, `app/store.py`). |
| Core v1 / G5 pilot boundary | Verified | `app/`, `config/` and `tools/pilot/` on `main` equal the accepted anchors `a56c575` and `55b431a`. 184 out of 184 G5 pins matched before this branch. |
| GitHub repository visibility | Verified: **public** | Needs owner decision D1. |
| Branch protection | Verified: none | Needs owner decision D2. |

**Pilot boundary note:** commercial work changes `app/`. The Core v1 / G5 pilot must be built **only** from its pinned commits (`55b431a` / `91cea01`), never from this branch. The pins in `g5/CONTEXT-PINS.json` will (correctly) stop matching on this branch.

---

## 2. Selected commercial architecture

**Decision (proposed, needs owner approval D12): one isolated deployment per moving company (Option A) for the first paid pilot.**

| Factor | Option A: isolated deployment | Option B: multi-tenant SaaS |
|---|---|---|
| Fit with existing code | Uses the single-company schema as it is | Every one of about 120 tables and every query would need a tenant key |
| Isolation strength | Process, database, volume and secrets are separate | Depends on every query being correct |
| Effort to first paid pilot | Weeks (identity, configuration, provisioning) | Months (rewrite plus re-qualification) |
| Operating cost per customer | Higher (one service and volume each) | Lower |
| Migration path | Later: shared control plane, then tenant key | — |

Tenant isolation for Option A is enforced by infrastructure: a separate Railway service, volume, `CROWNOPS_SECRET_KEY`, OAuth credentials and domain per company. **Cross-company access can only be tested at the deployment level.** A test plan is item OPS-2.

---

## 3. Commercial MVP scope

Lead → Customer → Estimate → Acceptance → Booking → Dispatch → Crew Execution → Actuals → Invoice → Verified Payment, for **one company per deployment, operating in Canada, in English and French, in CAD**.

Out of the MVP (P3): AI intake and chatbot, route optimization, predictive scheduling, marketing automation, multi-currency, US tax.

---

## 4. Feature completion matrix

The test counts come from test names that match each capability. They show coverage density, not proof of completeness.

| Module | Status | Evidence | Commercial gap |
|---|---|---|---|
| Authentication / MFA / recovery | Verified | `tests/test_account_access.py`, `test_authenticator_disable.py`, `test_launch_access.py`; 16 MFA/recovery tests | Staff invitation emails still use Crown branding (COM-2) |
| Roles and permissions | Verified (owner, manager, sales, dispatch, crew, accounting, client) | 22 role tests; `app/platform_common.py:12-14` | None found for single-company use |
| Company administration | **Partial** | The new company profile API (COM-1). `company_communications` exists. | No owner UI for the profile (COM-8). Pricing rules are hardcoded (COM-4). Timezone is hardcoded (COM-3). |
| Lead / customer management | Verified | `test_crm_polish.py`, `test_sales_crm_v6.py`, `test_inbox_tasks.py` | Branding in UI (COM-2) |
| Estimates and revisions | Verified | `test_client_journey.py:114`, `test_sales_journey.py`, `test_estimate_delivery.py` | Pricing is configured for Crown's rate workbook (COM-4) |
| Estimate acceptance ≠ booking | Verified | Jobs stay `Tentative` until explicit confirmation (`client_communications.py:deposit_request_context`). `test_client_journey.py` asserts that no job is booked. | — |
| Booking / dispatch / schedule | Verified | 42 schedule/assign tests, `test_job_planning.py`, `test_schedule_scope.py` | Timezone is fixed to America/Toronto (COM-3) |
| Crew workspace and actuals | Verified | `test_staff_workflow.py`, `test_worked_hours_costs.py`, `test_invoice_finish.py` | — |
| Additional charges need client agreement | Verified | `test_invoice_finish.py:92,103` | — |
| Invoicing (issued revisions protected) | Verified | 31 invoice tests, `test_invoice_guest_access.py` | Invoice document branding (COM-2: `invoices.py`) |
| Payments, deposits, reconciliation | Verified (mocked providers only) | 41 payment tests, `test_quickbooks_reconciliation.py`, `test_square_deposit_permissions.py` | Live settlement not verified. Each company needs its own QuickBooks and Square accounts. |
| Communications (no blind resend) | Verified (mocked Gmail) | `test_email_identity.py`, `test_client_journey.py` | **COM-1 done.** Gmail-only, no SMS provider. |
| Client portal | Verified | `test_client_journey.py`, `test_invoice_guest_access.py` | Portal pages hardcode Crown (COM-2) |
| Dashboards / reporting | Partial / Reported | The Today workspace exists. No revenue or conversion reports were found. | P2 |
| Website intake integration | Partial | `/api/request` (public form) and a signed webhook (`app/workflows.py:126-135`, `x-crown-*` headers) | Needs a versioned, documented, per-company API contract (INT-1) |
| Backups and restore | Verified (local) | 24 backup tests, `manage.py backup` / `verify-backup` | Offsite drill not executed (OPS-1) |
| Monitoring | Missing | Health endpoint only | OPS-3 |
| Licensing / subscription | Missing | — | Needs business decision D13 |
| Accessibility | Reported | Browser suites cover layout on phone, tablet and desktop | No WCAG audit (P2) |

---

## 5. Prioritized backlog

| ID | Module | Problem | Priority | Evidence | Proposed solution | Depends on | Status | Acceptance criterion |
|---|---|---|---|---|---|---|---|---|
| REL-1 | CI | `main` is red: the Core v1 guard leaks across the shared test process | P0 | CI runs 378–380; local reproduction | `tests/run_isolated.py` runs one interpreter per module with a minimum test count; `ci.yml` uses it | — | **Verified locally**; GitHub CI not run (no push authorized) | CI unittest step green with ≥636 tests and 0 errors, and the browser steps run |
| SEC-1 | Security | Repository is public and exposes personal login emails and Railway IDs | P0 | GitHub API `private:false` (re-checked 2026-11-05) | Owner makes it private, then pushes. **Jonathan has deferred this (2026-11-04).** No push or PR is allowed while it is public. A history scan found **no live credentials** (all 108 branches, 585 commits, 1,581 blobs; only fictional test passwords), so nothing needs rotating. | D1 | Blocked (owner deferred) | API reports `private:true` |
| REL-2 | Release | No branch protection; merges bypassed CI (#94 `[skip ci]`, #95 merged before checks) | P0 | GitHub API | Require the `validate` check and PRs on `main` | D2 | Blocked (owner) | A direct push to `main` is rejected |
| REL-3 | Release | `main` and production both report `0.12.7-rc6` | P0 | `app/main.py:237` | Bump the version on the next release; record the `/api/app-release` revision | D7 | Not started | Version differs from the deployed version |
| REL-4 | CI | `core-v1-isolation.yml:35` hash gate fails after #95 | P1 | `requirements.txt` sha changed `0463b4…` → `92c2aa…` | Re-pin after the D4 decision | D4 | Not started | Workflow passes on its branch |
| COM-1 | Communications | Client emails, the From header, QuickBooks memo, Square checkout and receipts hardcode Crown Movers' name, phone, email and Montréal time | P0 | 35+ sites | `app/company_profile.py`: stored profile, legacy Crown default, owner/manager API, `manage.py company-profile`, `doctor` flag | — | **Verified** (`tests/test_company_profile.py`, 14 tests) | With a configured profile, no generated message contains Crown identity; the legacy install output is byte-identical |
| COM-2 | Branding | Crown references in client-facing pages and static UI | P0 | `grep` 2026-11-05 | Server-rendered text from `company_profile`; static pages read `/api/company/public-profile` | COM-1 | **Server side verified** (`03c3130`): portal, invoice access, participation and review links, printed estimate, quote export, error page, invoice footer. **Static HTML/JS (about 20 files, manifest, icon) not started (COM-2c).** | Brand guard extended to `app/static` passes |
| COM-3 | Scheduling | Business timezone hardcoded to America/Toronto (about 50 sites in 36 files, Python and JS) | P0 for companies outside Eastern time | `grep` | One `business_zone()` resolved from the profile at startup and on profile change; JS gets the zone from the server; all sites switch in one change | COM-1 | **Not started.** Interim guard verified (`d438980`): the profile accepts only America/Toronto, so no partial switch can shift booked times. | Schedule, availability, payroll and browser suites pass with a non-Eastern profile |
| SEC-3 | Security | Every installation shared one hardcoded owner-setup hash in a public repository; anyone with Crown's setup code could claim Owner on a new company's installation | P0 | `app/main.py:44` (before `502715e`) | Per-installation `CROWNOPS_SETUP_CODE_HASH`; `manage.py setup-code`; web setup closed without it | — | **Verified** (`502715e`, `tests/test_installation_security.py`) | Another installation's code is refused; no hash in source |
| COM-6a | Identity | New installations with no profile fell back to Crown's identity | P0 | — | Identity mode recorded once per database (`legacy-crown` if it already had users or leads, else `unconfigured`). Unconfigured installs refuse client messages, email, invoices and provider documents. Lead capture is never blocked. | COM-1 | **Verified** (`5503a28`, 6 tests) | Unconfigured install can save leads but sends nothing as anyone |
| COM-4 | Pricing | Truck minimums, service minimums and rate import are Crown policy in code | P1 | `sales_crm_v6.py:60-82`, `seed.py`, `config/workbook_pricing_snapshot.json` | Move rules into owner-approved pricing settings; new installs start unapproved | — | Not started | A new company sets its own rules; Crown's results are unchanged |
| COM-5 | Onboarding | Fresh installs seed Crown's mailboxes into `company_communications`. **The billing settings form pre-filled Crown's legal name, address and GST/QST registration numbers for any company** | P0 | `app/ux_features.py:55-64`, `app/operations.py:263-274` | Configured companies get empty, unapproved mailboxes, their own issuer details, and **no** tax registrations. Legacy constants are centralized in `company_profile.py`. | COM-1 | **Verified** (`ConfiguredCompanyDefaultsTests`) | A new install shows no Crown address or tax number. Legacy install unchanged. |
| COM-6 | Finance | Hardcoded CAD, Canadian and Québec tax assumptions | P1 (Canada-only first market) | `providers.py`, `invoices.py` | Document as a market limit; make tax codes configurable | D13 | Not started | Documented |
| COM-8 | Admin UI | No owner screen for the company profile (API only) | P1 | — | Add a Settings → Company profile form | COM-1 | Not started | Owner edits the profile in the browser; a browser test covers it |
| OPS-1 | Operations | Offsite backup and restore drill not executed | P1 | `release/OWNER-ACTIONS.txt` step 9 | Run the drill on a disposable deployment | D3 | Not started | Restore to a new path verified, with timing recorded |
| OPS-2 | Isolation | No provisioning runbook for one deployment per company | P1 | — | Template: service, volume, secrets, domain, `manage.py init` plus `company-profile`; cross-deployment access test | D12 | Not started | Two disposable deployments cannot read each other's data |
| OPS-3 | Monitoring | No alerting | P1 | `release/RAILWAY-PROPOSAL.txt` | Uptime check plus backup-age alert | — | Not started | An alert fires in a drill |
| SEC-2 | Security | `CROWNOPS_ALLOW_EXTERNAL=0` is bypassed by 8 Gmail and QuickBooks token paths in the full app | P1 | `providers.py:71,83,458,485,501`, `account_access.py:108`, `automatic_intake.py:121`, `invoice_management.py:23` | Document as intended, or add a separate read-only flag | — | Not started | Documented decision plus a test |
| INT-1 | Integrations | Intake webhook uses Crown-named headers; no versioned contract | P1 | `app/workflows.py:126-135` | `/api/v1/intake` with HMAC, idempotency key and correlation ID; keep the old route | — | Not started | Contract documented; replay and duplicate tests |
| BIZ-1 | Commercial | No licensing or billing model | P1 | — | Decide the model first; no billing code before that | D13 | Blocked (owner) | Approved model |

---

## 6. Implementation progress log

### 2026-11-05 — Session 1 (branch `claude/vigilant-carson-1eevyy`, local commits only, not pushed)

**REL-1 (test isolation):**
- Added `tests/run_isolated.py`.
- `.github/workflows/ci.yml` now uses `run_isolated.py --min-tests 636 --jobs 2`.
- No `app/` change was needed for this.

**COM-1 (company identity):**
- Added `app/company_profile.py`.
- Wired it into:
  - client communications: `app/client_communications.py`
  - sales and workflow modules: `app/sales_crm_v6.py`, `app/workflows.py`, `app/extensions.py`, `app/sales_journey.py`, `app/review_features.py`, `app/conversations.py`
  - send path, background work and QuickBooks delivery: `app/providers.py`, `app/worker.py`, `app/invoice_quickbooks_delivery.py`
  - route registration: `app/main.py` (legacy profile only)
- `manage.py` gained the `company-profile` command and a `doctor` flag.
- Tests: `tests/test_company_profile.py`.

**COM-5 / COM-2a (second commit):**
- The billing issuer and tax pre-fill now come from the company profile; there are no foreign tax registrations.
- Mailbox seeding is neutral for configured companies.
- Staff invitation, recovery and password-change emails use the company name.
- The QuickBooks estimate memo uses the company name and phone.
- Legacy Crown defaults are centralized in `app/company_profile.py`.

**Language note:** French sentences were only re-parameterized (the company name, phone and time label are substituted). No new French copy was written. A fluent reviewer should still confirm generated FR messages for a non-Crown company.

**Test results:** see section 7.

---

### 2026-11-05 — Session 2 (same branch; local commits only; not pushed because the repository is public)

| Commit | Item | Summary |
|---|---|---|
| `502715e` | SEC-3 | Per-installation owner setup code; the shared hardcoded hash is removed |
| `5503a28` | COM-6a | Unconfigured new installations fail closed; Crown's install is recorded as legacy; lead capture is never blocked |
| `03c3130` | COM-2b (server) | Client pages, documents, review links, quote export, error page and invoice footer use the company profile |
| `d438980` | COM-3 guard | Only America/Toronto is accepted until COM-3 switches every site together |

**Preservation:** a verified git bundle and patch series of every commit since `db59167` are delivered to the owner as a download. The repository remains public by Jonathan's decision, so nothing has been pushed.

## 7. Test results

| Run | Command | Result |
|---|---|---|
| Baseline `main`, single process (CI method) | `python -m unittest discover -s tests` | 622 tests, **12 errors** (same as CI) |
| Pristine `main` (`db59167`), isolated | `python tests/run_isolated.py --min-tests 622 --jobs 4` | **55 modules, 622 tests, 0 failures** |
| After COM-1 commit, isolated | `python tests/run_isolated.py --min-tests 636 --jobs 4` | **56 modules, 636 tests, 0 failures** |
| After COM-5 / COM-2a, isolated | `python tests/run_isolated.py --min-tests 640 --jobs 4` | **56 modules, 640 tests, 0 failures**; browser estimate_delivery and staff_portal pass |
| Session 2, isolated (after `03c3130`) | `python tests/run_isolated.py --min-tests 658 --jobs 4` | **57 modules, 658 tests, 0 failures** |
| Session 2, browser | All 11 CI suites after `5503a28`; 6 client-facing suites after `03c3130` | **All pass** |
| Session 2, legacy byte-identity | 404/410 page from `db59167` vs this branch, EN and FR | **Identical SHA-256** |
| JavaScript syntax | `node --check app/static/*.js` | All pass |
| Node tests | `node --test tests/*.test.cjs` | 20 out of 20 pass |
| Browser suites (Playwright, pre-installed Chromium) | `python tests/browser/run_mobile.py <suite>`, all 11 CI suites with CI environment variables | **11 out of 11 pass** after COM-1 (wix_sync, inbox, payroll_periods, hours_costs, staff_portal, client_participation, browser_recovery, estimate_delivery, mobile_schedule, today_workspace, team_chat) |
| Smoke create | CI `create_app` snippet | 427 routes; `/api/company/profile` and `/api/company/public-profile` registered outside Core v1 |

**Environment:** Python 3.13.18 and Node 22.23.4. `requirements.txt` was installed in a disposable virtualenv. Dependencies are as pinned on `main` (FastAPI 0.143.0, which production does not yet run; see D4).

---

## 8. Deployment and operational requirements (first paid pilot)

1. One Railway service and volume per company, one replica, `CROWNOPS_ENV=production`, a unique `CROWNOPS_SECRET_KEY`, and a separate backup key.
2. Run `manage.py init --email … --name …`, then `manage.py company-profile --name … --phone … --email … --timezone …`.
3. `manage.py doctor` must report `"company_profile_configured": true` before any client email is sent.
4. Each company connects its own Gmail, QuickBooks and Square accounts. CrownOps never shares provider credentials between deployments.
5. Before first use: a verified backup, an offsite copy, and a restore drill (OPS-1).

---

## 9. Owner decisions

### Approved by Jonathan on 2026-11-04 (recorded 2026-11-05)

| # | Decision | Consequence for engineering |
|---|---|---|
| A1 | Commercialize CrownOps for independent moving companies | No Crown-specific behavior on new installations. The legacy install keeps its behavior. |
| A2 | Separate installation and database per company; no shared multi-tenancy (**resolves D12**) | Isolation is per deployment. No tenant key work. Provisioning, backup and upgrade tooling are per installation. |
| A3 | Configurable branding and white-label | COM-1, COM-2 (server done, static pages next), logo and colours (COM-2c) |
| A4 | Company-specific pricing, timezones, services and operational settings | COM-3, COM-4, services catalogue |
| A5 | Finish, secure and test existing CRM before advanced AI | AI features stay off the roadmap until the pilot-readiness gates pass |
| A6 | Prepare for controlled paid-pilot customers | Section 11 pilot readiness gates |
| A7 | Plan licensing or subscription; **no real billing without separate approval** | Design only (section 11). No billing provider code or charges. |
| A8 | Prepare legal, privacy, IP and compliance reviews | D14 checklist below; needs professionals |
| A9 | Autonomous local development, fixes, documentation and tests | Continue without per-change approval; outward actions still need approval |
| A10 | Preserve the commits and prepare a draft PR, **subject to the repository being private** | Bundle delivered. PR held because the repository is public. |

**Not approved:** changing repository visibility. Consequence: **no push, branch or PR on GitHub** while the repository is public, because branches in a public repository are public.

**What a safe push requires:** the repository is made private (owner action), `gh api repos/crownmoverscm-hue/crownops-private --jq .private` returns `true`, and the owner then authorizes the push. An alternative is a separate **private** repository that the owner creates and attaches to the session.

### Still open

D1–D11 are listed in the 2026-11-05 takeover assessment. They cover:
- D1: repository visibility
- D2: branch protection
- D3: Railway deploy trigger
- D4: whether to keep FastAPI 0.143.0
- D5: whether to accept #94 as the baseline
- D6: the authoritative repository
- D7: version and tag policy
- D8: when production moves forward
- D9: authorization to push, open PRs and run CI
- D10: the Nov 9 pilot date
- D11: providing the PDFs

**Added in this session:**

| ID | Decision |
|---|---|
| D13 | Commercial model: licence or subscription price, billing provider, and who supports customers. **No billing code until decided.** |
| D14 | Legal: software IP ownership (Crown Movers vs CrownOps business), terms of service, privacy policy (Québec Law 25 and PIPEDA), data processing agreement, hosting region. Needs professional review. |
| D15 | Whether Crown's production deployment should explicitly store its own profile (`manage.py company-profile`), so the legacy fallback can later be removed. |

---

## 11. Commercial readiness evaluation (Step 4, 2026-11-05)

| Requirement | Current state (evidence) | Gap to paid pilot | Next step |
|---|---|---|---|
| Repeatable installation | Dockerfile and `railway.toml` exist. `manage.py init`, `company-profile`, `setup-code` and `doctor` exist. | No per-company deployment template or runbook. Railway volume ownership is unresolved (`release/RAILWAY-PROPOSAL.txt` blocker 1). | OPS-2: provisioning runbook plus a disposable two-installation isolation test |
| Company onboarding | Profile via CLI or API. `doctor` reports `company_profile_configured` and `web_setup_enabled`. | No owner UI for the profile (COM-8). No first-run checklist. Static UI still Crown-branded (COM-2c). | COM-8 settings screen and a launch checklist |
| Secure configuration and secrets | Secrets come from the environment. No live credentials in history. Per-installation setup code (SEC-3). | No documented secret inventory per installation. Provider OAuth is configured per installation by hand. | Secret inventory table plus a rotation procedure |
| Backups and restore | `backup`, `verify-backup` and `restore` exist; 24 backup tests | No executed offsite drill (OPS-1) | Run a drill on a disposable install and record timings |
| Versioned migrations and upgrades | Idempotent `initialize()` plus ad-hoc `ALTER`; production backs up before known migrations | No migration version table and no downgrade path | Add a `schema_version` table and numbered migrations, preserving the current idempotent steps |
| Error monitoring and support | `/api/health`, `/api/app-release` revision, audit table | No alerting or error aggregation. Version string is stale (REL-3). | OPS-3 uptime and backup-age alerts; a support diagnostics export |
| Licensing and subscription | None (by decision A7) | Model, price and provider undecided (D13) | Design note only: licence key per installation, checked offline, with no billing calls |
| End-to-end pilot acceptance | Unit, browser and Node suites pass locally. GitHub CI is not re-run (no push possible). | No scripted Scenario A–H run on a clean install. Payments only mocked. | Scripted acceptance suite on a disposable install, using fictional data |

**Readiness:** Crown internal use **PILOT CANDIDATE** (unchanged). Commercial use **NOT READY**. The release blockers are COM-3, COM-4, COM-2c, COM-8, OPS-1, OPS-2, a CI run on GitHub (blocked by SEC-1), and D13/D14.

## 10. Next executable task

**COM-3 (all sites in one change):** add `business_zone()` resolved from the company profile; replace every `America/Toronto` / `MONTREAL` use in `app/*.py`; serve the zone to the browser and replace the JS literals. Lift the `SUPPORTED_TIMEZONES` guard only when the schedule, availability, payroll and all browser suites pass with a non-Eastern profile. Then COM-4 (pricing policy settings) and COM-2c (static pages).

_Previous next-task note (completed server side in `03c3130`):_ COM-2b: brand the client-facing pages from `/api/company/public-profile`:
- `portal.html` / `client*.js`
- `invoice_access.*`
- `approval.*`
- `participate.*`
- `request.*`
- `link_error.html` / `error_pages.py`
- the manifest

Run the browser suites `inbox`, `client_participation` and `browser_recovery`. Then extend `CompanyBrandGuardTests` to all of `app/`.

The invoice HTML (`app/invoices.py`) already renders the issuer from billing terms; it was fixed through COM-5.

Acceptance: with a configured profile, the full isolated suite passes and the brand guard finds no Crown identity outside `app/company_profile.py`.
