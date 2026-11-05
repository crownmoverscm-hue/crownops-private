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
| SEC-1 | Security | Repository is public and exposes personal login emails and Railway IDs | P0 | GitHub API `private:false` | Owner makes it private, then a full secret scan over history | D1 | Blocked (owner) | API reports `private:true` |
| REL-2 | Release | No branch protection; merges bypassed CI (#94 `[skip ci]`, #95 merged before checks) | P0 | GitHub API | Require the `validate` check and PRs on `main` | D2 | Blocked (owner) | A direct push to `main` is rejected |
| REL-3 | Release | `main` and production both report `0.12.7-rc6` | P0 | `app/main.py:237` | Bump the version on the next release; record the `/api/app-release` revision | D7 | Not started | Version differs from the deployed version |
| REL-4 | CI | `core-v1-isolation.yml:35` hash gate fails after #95 | P1 | `requirements.txt` sha changed `0463b4…` → `92c2aa…` | Re-pin after the D4 decision | D4 | Not started | Workflow passes on its branch |
| COM-1 | Communications | Client emails, the From header, QuickBooks memo, Square checkout and receipts hardcode Crown Movers' name, phone, email and Montréal time | P0 | 35+ sites | `app/company_profile.py`: stored profile, legacy Crown default, owner/manager API, `manage.py company-profile`, `doctor` flag | — | **Verified** (`tests/test_company_profile.py`, 14 tests) | With a configured profile, no generated message contains Crown identity; the legacy install output is byte-identical |
| COM-2 | Branding | Remaining Crown references in the client-facing pages and static UI: portal, approval, invoice-access, request and error pages, manifest, icon, about 20 JS files; plus `portal.py`, `invoice_access.py`, `client_participation.py`, `error_pages.py`, `ai.py` prompt | P0 | `grep` count 2026-11-05 | Serve `/api/company/public-profile` to pages; per-company logo upload | COM-1 | **Partial.** COM-2a done (staff invitations, recovery emails, QuickBooks estimate memo). Pages are not started. | Brand guard test extended to all of `app/` passes |
| COM-3 | Scheduling | Business timezone hardcoded to America/Toronto (about 50 sites in 36 files, Python and JS) | P0 for companies outside Eastern time | `grep` | Single `business_zone(db)` from the profile; JS reads it from `/api/me` | COM-1 | Not started | Schedule, availability and payroll tests pass with a non-Eastern profile |
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

## 7. Test results

| Run | Command | Result |
|---|---|---|
| Baseline `main`, single process (CI method) | `python -m unittest discover -s tests` | 622 tests, **12 errors** (same as CI) |
| Pristine `main` (`db59167`), isolated | `python tests/run_isolated.py --min-tests 622 --jobs 4` | **55 modules, 622 tests, 0 failures** |
| After COM-1 commit, isolated | `python tests/run_isolated.py --min-tests 636 --jobs 4` | **56 modules, 636 tests, 0 failures** |
| After COM-5 / COM-2a, isolated | `python tests/run_isolated.py --min-tests 640 --jobs 4` | **56 modules, 640 tests, 0 failures**; browser estimate_delivery and staff_portal pass |
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

## 9. Decisions requiring the owner (Jonathan)

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
| D12 | Approve **one isolated deployment per company** as the first commercial architecture. Multi-tenant SaaS is deferred. |
| D13 | Commercial model: licence or subscription price, billing provider, and who supports customers. **No billing code until decided.** |
| D14 | Legal: software IP ownership (Crown Movers vs CrownOps business), terms of service, privacy policy (Québec Law 25 and PIPEDA), data processing agreement, hosting region. Needs professional review. |
| D15 | Whether Crown's production deployment should explicitly store its own profile (`manage.py company-profile`), so the legacy fallback can later be removed. |

---

## 10. Next executable task

**COM-2b:** brand the client-facing pages from `/api/company/public-profile`:
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
