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
| COM-2 | Branding | Crown identity in client pages, scripts, manifest and icons | P0 | `grep` 2026-11-05 (about 100 strings, 20+ files, Crown icon artwork) | Server-side text from `company_profile` (`03c3130`) plus `app/branding.py`: a white-label layer for every static asset, with icons from the logo or initials | COM-1 | **Verified** (`5c312ec`, `tests/test_white_label.py`) | Crown: every asset byte-identical. Configured and unconfigured companies: no Crown identity in any asset or page, and all rewritten scripts parse |
| COM-3 | Scheduling | Business timezone hardcoded to America/Toronto (37 `ZoneInfo` calls and 2 constants in 24 Python modules; 16 browser formatters in 11 scripts) | P0 | `grep` 2026-11-05 | `app/business_zone.py` (a `tzinfo` delegating to the company's `ZoneInfo`, including `fold`); loaded at startup and on profile save; `/business-zone.js` for pages | COM-1 | **Verified** (`423c50b`, `tests/test_business_zone.py`) | Toronto identical to `ZoneInfo` for every UTC hour across both 2026 transitions; Vancouver, Halifax and Regina booking holds, business days and presentation follow the company zone |
| SEC-3 | Security | Every installation shared one hardcoded owner-setup hash in a public repository; anyone with Crown's setup code could claim Owner on a new company's installation | P0 | `app/main.py:44` (before `502715e`) | Per-installation `CROWNOPS_SETUP_CODE_HASH`; `manage.py setup-code`; web setup closed without it | — | **Verified** (`502715e`, `tests/test_installation_security.py`) | Another installation's code is refused; no hash in source |
| COM-6a | Identity | New installations with no profile fell back to Crown's identity | P0 | — | Identity mode recorded once per database (`legacy-crown` if it already had users or leads, else `unconfigured`). Unconfigured installs refuse client messages, email, invoices and provider documents. Lead capture is never blocked. | COM-1 | **Verified** (`5503a28`, 6 tests) | Unconfigured install can save leads but sends nothing as anyone |
| COM-4 | Pricing | Crown's pricing rules were constants, and new installs were seeded with Crown's workbook rates as active, approved hourly rates | P0 | `sales_crm_v6.py` constants; `seed.py` and `review_features.seed_review_defaults` | `app/pricing_policy.py`: validated, owner-approved, versioned and audited policy. Crown keeps `LEGACY_POLICY`. New installs get no Crown rates, presets or defaults and must approve their own. | — | **Verified** (`f5f9805`, `tests/test_pricing_policy.py`, golden fixture) | 18 out of 18 Crown golden scenarios identical; company policy prices with its own values; estimates keep their snapshot |
| COM-5 | Onboarding | Fresh installs seed Crown's mailboxes into `company_communications`. **The billing settings form pre-filled Crown's legal name, address and GST/QST registration numbers for any company** | P0 | `app/ux_features.py:55-64`, `app/operations.py:263-274` | Configured companies get empty, unapproved mailboxes, their own issuer details, and **no** tax registrations. Legacy constants are centralized in `company_profile.py`. | COM-1 | **Verified** (`ConfiguredCompanyDefaultsTests`) | A new install shows no Crown address or tax number. Legacy install unchanged. |
| COM-6 | Finance | Hardcoded CAD, Canadian and Québec tax assumptions | P1 (Canada-only first market) | `providers.py`, `invoices.py` | Document as a market limit; make tax codes configurable | D13 | Not started | Documented |
| COM-8 | Admin UI | No owner screen for the company profile, pricing or branding | P1 | — | `app/static/company_settings.js` (Company profile page): identity, time zone, brand colour, logo, pricing draft and approval, setup checklist, links to the rate calendar and invoice setup | COM-1, COM-4 | **Verified** (`5c312ec`, browser suite `company_settings`) | A new-company owner completes setup in a browser; sales is read-only; no Crown defaults appear |
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

### 2026-11-05 — Session 3 (local commits only; repository still public)

| Commit | Item | Summary |
|---|---|---|
| `423c50b` | COM-3 + identity hardening | Company time zone everywhere (server, browser, imports); test-created databases are never recorded as Crown's legacy install; explicit `manage.py installation-identity` |

**COM-3 audit findings (2026-11-05):**
- **Storage:** timestamps (`created_at`, job `start_at`/`end_at`, audit) are stored in UTC ISO-8601. Service dates (`move_date`) are local calendar dates. No stored data is rewritten by COM-3.
- **Conversions:** every local interpretation now goes through `BUSINESS_ZONE`. `fold` is preserved, so the repeated November hour resolves exactly as `ZoneInfo` does: the first occurrence unless the code selects otherwise. Nonexistent March times get `ZoneInfo`'s offset.
- **Customer-facing dates:** reminders, booking confirmations, `booking_details`, invoices and the portal format in the company zone. Toronto keeps the "ET" label and "Montréal time" wording.
- **Known limits:**
  1. The zone is held per process. A change made with `manage.py company-profile` while the server is running takes effect on restart; the API applies it immediately.
  2. Changing the zone after bookings exist keeps the stored UTC instants and re-displays them in the new zone. Set the zone before the first booking. The onboarding runbook must say so.
  3. The browser suites ran in Toronto only; non-Toronto browser rendering is covered by the zone-script and page-wiring tests, not by a full browser run.
  4. Zone rules come from the system tz database (for example, British Columbia's current rules), so containers need an up-to-date `tzdata`.

**Installation identity review (requested item 4):**
- **Current detection:** a database with users or leads at first start of this release is recorded as `legacy-crown`.
- **Gap found and fixed:** a trial database created under `CROWNOPS_ENV=test` and later promoted to production would have been recorded as Crown. Such databases are now marked at creation and recorded as `unconfigured`.
- **Recommendation (D15, needs owner authorization because it touches production):** on Crown's production and staging installations, run `manage.py installation-identity --mode legacy-crown --reason "<who confirmed>"` once, so the identity is an explicit audited decision rather than an inference. Then plan the move from legacy mode to an explicit Crown company profile (`manage.py company-profile ...`).
- **For every new company:** the provisioning runbook sets `company-profile` before first start, which supersedes detection entirely.

### 2026-11-05 — Session 4 (local commits only; repository still public)

| Commit | Item | Summary |
|---|---|---|
| `f5f9805` | COM-4 + COM-3 safeguard | Company pricing policy; Crown golden outputs preserved; no Crown rates on new installs; the time zone is locked once scheduling data exists |

**Pricing rules moved into configuration:**
- truck minimums by crew size
- default, moving/packing and small-afternoon minimum hours
- local fuel per truck
- extra truck fee
- long-distance km rate, distance buffer and transport multiplier
- junk-removal tiers and extra labour
- default deposit
- limited-liability wording
- testimonial credit and its disclosure

The Montréal-based long-distance city presets are legacy-only. **Hourly crew rates** stay in the existing owner-managed rate calendar (`annual_rate_periods` / `daily_rates`), which new companies now fill themselves; Crown's workbook is no longer seeded for them.

**Evidence that Crown is unchanged:** `tests/fixtures/crown_pricing_golden.json` was generated from `b62757c` *before* the refactor. It holds 15 priced scenarios (local 2/3/4/6 movers, minimum floors, fixed transport and fuel override, other charges and discount, rate override with fractional hours, small afternoon, packing, French, long distance including a short drive, junk with extra labour and discount) and 3 refusals (truck minimums, small-afternoon window). All 18 match exactly after the change, and Crown results gain no new keys. Testimonial disclosure text and the $50 credit are unchanged.

**Pricing audit (2026-11-05):**
- **Rounding:** components and totals use `ROUND_HALF_UP` to the cent. In all 15 golden scenarios the line items sum exactly to the low total. Residual low risk: because lines and totals are rounded separately, they could differ by 1 cent when several fractional components combine; no case was observed.
- **Discounts:** a discount larger than the low estimate is refused (existing rule, unchanged).
- **Taxes:** estimates are before tax; invoices compute each tax line with half-up rounding from the invoice's own saved terms (`invoices.py:89-109`). Tax registrations for new companies come only from their own billing settings (COM-5).
- **Revisions and invoices:** quotes store `inputs_json` and `result_json`, and invoices store `terms_json`, so changing the policy or rates never alters a saved estimate or invoice (tested). Company-policy estimates record `pricing_policy_version`.
- **Not converted:** the Québec heavy-vehicle HOS planning limits (regulatory, not pricing) and the `deposit_required` 300 fallback used only when reading pre-existing Crown quotes that lack the field.

**COM-3 review:** daylight-saving transitions, customer-facing dates, restarts and reminders are covered by `tests/test_business_zone.py`. Separate installations each load their own zone per process. **New safeguard:** the time zone cannot change once jobs, availability or imported schedules exist (409).

### 2026-11-05 — Session 5 (local commits only; repository still public)

| Commit | Item | Summary |
|---|---|---|
| `5c312ec` | COM-8, COM-2c, rounding control, installation isolation | Owner settings screen; white-label layer for all static assets; estimate rounding reconciliation; two-installation lead-to-invoice test |

**COM-8 evidence:**
- **Browser suite `company_settings` (new installation, 390 and 1440 px):**
  - starts with identity, pricing and billing *incomplete*
  - no Crown name or prices are prefilled
  - the owner saves the profile (Regina time zone, brand colour) and uploads a logo
  - saving pricing as a draft shows *pending approval*; approving it shows *approved*
  - tax and invoice settings remain *incomplete* until set in the invoice setup screen
  - the portal page has no Crown identity
  - the sales role sees a read-only page
  - no page errors
- **Server side:**
  - all saves are Owner/Manager-only, validated and audited
  - the time-zone lock is still enforced
  - a pricing draft never prices
  - the legacy workbook quote route and invoice issue also require approved pricing
- **Logo upload:**
  - only PNG, JPEG and WebP are accepted; SVG is refused
  - files over 2 MB and images over 6,000 px per side are refused
  - images are re-encoded to PNG (trailing script data is removed)
  - the logo is served with `nosniff`, and uploads are audited

**COM-2c evidence:**
- **Crown's installation:** all static text assets, all icons and the manifest are byte-identical to the files.
- **Configured company named "O'Neil Moving & Storage":** every script, stylesheet, page and the manifest contains no Crown name, phone, email or standalone "Crown", and all rewritten scripts pass `node --check`.
- **Unconfigured installations:** no Crown identity either.
- **Icons:** generated from initials, then from the uploaded logo.
- **Caching:** conditional requests never receive a stale Crown asset.
- **Kept on purpose:** the "CrownOps" product name.

**Rounding control:**
- Estimate totals are unchanged: the exact amount is still rounded once, half-up.
- When the individually rounded lines differ from the total, an explicit "Rounding adjustment" line (at most 1¢ per line) is added, so every estimate reconciles. Example: $100.01 × 3.33 h + 1.37 h transport + $35 fuel gives lines summing to 50504¢ against a total of 50505¢, so a +1¢ adjustment line is added.
- Crown's 18 golden scenarios are unchanged, because none needed an adjustment.
- Invoices already reconcile by construction: rounded lines, rounded taxes, total = subtotal + taxes. This is tested with fractional hours, catalogue items, a discount and two taxes.
- New French copy "Ajustement d'arrondi" needs review by a fluent speaker.

**Independent installations (`tests/test_independent_installations.py`):**

| | A | B |
|---|---|---|
| Company | Alpha Fictional Movers | Beta Fictional Relocation |
| Time zone | America/Toronto | America/Vancouver |
| Language | English | French |
| Taxes | HST 13% | GST 5% + PST 7% |
| Rates, fuel, deposit, truck rules | Own | Different |

- **Lead to invoice:** each installation runs in its own process with its own database and secret key, and completes lead → estimate → client acceptance (stays tentative) → deposit → booking → completed job → issued invoice through the API. Totals match independently computed amounts, and each invoice names its own issuer.
- **Time zones:** the same 9:00 local arrival is stored as different UTC instants.
- **Live isolation:** both installations are served by live HTTP servers. Each rejects the other's session (401) and serves only its own public profile, zone and branding. The databases are separate files with disjoint client records.
- **Seeded rather than API-driven:** users and sessions, one rate-calendar row, approved client and billing terms, and the crew and truck assignment. Each is entered by staff in existing screens covered by other suites.

**New findings (not yet fixed):**
- **COM-4b:** seeded quote templates (`sales_crm_v6.py:375-380`) carry Crown's liability wording ("$0.60/lb per article") and fragile-item policies on every installation. They are owner-editable, but new companies should start with templates that require review.
- **Brand changes and caching:** browsers re-download branded assets (no-cache), but a page that is already open keeps the previous text until it is reloaded.

### 2026-11-05 — Session 6 (local commits only; repository still public)

| Commit | Item | Summary |
|---|---|---|
| `d263559` | Migrations, REL-3 | Versioned schema (`user_version` plus `schema_migrations`), atomic steps, newer-schema refusal, production backup gate, `db-status`; release identity `0.13.0-dev` |
| `ec0b289` | Backups and recovery | `backup-run` (restore test, retention, status), manifest versions, newer-schema restore refused, full recovery drill; retention ordering bug fixed |
| `c0f50b9` | White-label security | Context-specific encoding replaces the character blocklist; adversarial tests |
| `42c6b60` | COM-4b | Each company writes and approves its own quote templates and liability wording; Crown's templates and $35 material stay legacy-only; testimonial credit comes from the policy |
| `cf0db1a` | Monitoring | `ops-check` / `/api/ops/status`; all background jobs guarded (intake previously stopped on its first error) |
| `e1c28e6` | Provisioning and pilot validation | Two companies via documented commands and API only, lead to verified payment, backup and monitoring; two product defects fixed; deployment and operations runbooks |

**Defects found and fixed this session:**
- Backup retention could keep the wrong archives when several ran within the same second.
- The inquiry intake loop died silently on its first error.
- New companies could not create any estimate, because no pricing snapshot existed for them.
- Saving a job plan without `quote_id` unlinked the accepted estimate, which then blocked invoicing.
- The media program hardcoded a $50 testimonial credit.

**Backup drill (synthetic company, measured):**

| Step | Time |
|---|---|
| Backup | 0.025 s |
| Backup plus restore test | 0.047 s |
| Restore | 0.016 s |
| Archive size | 38 KB |

- **Restored:** 14 business tables plus company, pricing, logo, billing and terms settings, an uploaded document (byte-identical), and the invoice state.
- **After restore:** old sessions are rejected, and the restored app serves the issued invoice in the company's time zone.
- **Refused:** corrupt, truncated, tampered, newer-schema and missing archives, and any restore over an existing installation.
- **Recovery point:** the last successful backup.
- **Recovery time:** restore time plus redeploy. Real installations scale with data size.

**Migration evidence:**
- A clean install and a simulated existing (unversioned) Crown database with data and a missing column both reach version 2 with no data loss.
- Repeating initialization is a no-op.
- A failed step rolls back completely.
- A version gap is refused, and a newer database is refused before anything is written.

**White-label security:**
- **Inert hostile names:** names containing quotes, backticks, `${…}`, backslashes, emoji, `*/` or `'+alert(1)+'` evaluate to the exact original string in Node in single-quoted, double-quoted and template literals and in a regular expression, with no side effects.
- **No asset damage:** with a hostile profile, all 86 rewritten assets stay valid. HTML gains no tags or scripts, CSS gains no comment terminators, and the "CrownOps" product references are unchanged.
- **Never stale:** branding is re-read on every request (about 21 ms in total to rewrite all assets).

## 12. Commercial pilot release gates (2026-11-05)

| Area | Requirement | Status | Evidence or gap |
|---|---|---|---|
| Core CRM | Complete moving workflow passes | **Met (synthetic)** | `test_documented_provisioning.py`: inquiry → verified payment, two companies |
| Company onboarding | Repeatable without source changes | **Met (local)** | Deployment runbook plus documented-process test; operator steps use `manage.py` |
| Isolation | Separate installations verified | **Met (local)** | Separate processes, databases, secrets and sessions; cross-session access gets 401 |
| Security | No unresolved critical vulnerabilities | **Partially met** | No known critical defect in the code. The **repository is public** (D1). No external penetration test. MFA not yet tested on a real device. |
| Pricing | Approved company-specific policies | **Met** | COM-4; Crown golden outputs unchanged |
| Branding | No Crown leaks | **Met** | Byte-identical for Crown; no Crown identity in any asset or page for other companies |
| Legal templates | Company-approved terms required | **Met (mechanism)** | Templates and booking terms must be written and approved by the company. **Wording needs legal review (D14).** |
| Database | Versioned migrations tested | **Met** | `test_migrations.py` |
| Backups | Successful restore drill | **Met (local)** | `test_backup_drill.py`. Encrypted offsite storage is not set up (operator task). |
| Monitoring | Critical failures observable | **Met (local)** | `ops-check`; external alerting not connected (needs account authorization) |
| Operations | Deployment and recovery runbooks | **Met** | Both runbooks written |
| Tests | Required suites pass | **Met locally** | 723 Python, 20 Node, 12 browser suites. **GitHub CI not run** (repository public). |
| External integrations | Verified or listed as pilot limitations | **Pilot limitation** | Gmail, QuickBooks and Square have **not** been verified live for commercial installations |
| Business approvals | Licensing, privacy, IP, contracts | **Not met** | D13 licensing model, D14 legal/privacy/IP, the customer agreement and hosting region remain open |

**Readiness:** **PILOT CANDIDATE.** All technical gates pass locally on synthetic data. It is **not PILOT READY**: business approvals (D13, D14) are open, the repository is public (D1), there is no hosted deployment or GitHub CI run, and live provider sandboxes and real-device MFA are unverified.

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
| Session 3, isolated (after `423c50b`) | `python tests/run_isolated.py --min-tests 670 --jobs 4` | **58 modules, 670 tests, 0 failures** (one clean run after fixing a missed `__import__('zoneinfo')` call site that the first run caught) |
| Session 3, browser | All 11 CI suites | **11 out of 11 pass** |
| Session 3, JS | `node --check app/static/*.js`; `node --test tests/*.test.cjs` | All pass; 20 out of 20 |
| Session 4, isolated (after `f5f9805`) | `python tests/run_isolated.py --min-tests 679 --jobs 4` | **59 modules, 679 tests, 0 failures** |
| Session 4, browser and JS | All 11 CI suites; `node --check`; 20 Node tests | **All pass** |
| Session 5, isolated (after `5c312ec`) | `python tests/run_isolated.py --min-tests 694 --jobs 4` | **61 modules, 694 tests, 0 failures** |
| Session 5, browser | 11 CI suites plus the new `company_settings` | **12 out of 12 pass** |
| Session 5, JS | `node --check app/static/*.js`; 20 Node tests | All pass |
| Session 6, isolated (after `e1c28e6`) | `python tests/run_isolated.py --min-tests 720 --jobs 4` | **66 modules, 723 tests, 0 failures** |
| Session 6, browser and JS | 12 browser suites, `node --check`, 20 Node tests | **All pass** |
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

**Readiness (updated 2026-11-05, session 5):**
- Crown internal use: **PILOT CANDIDATE** (unchanged).
- Commercial use: **PILOT CANDIDATE (technical)**. A new company can be configured end to end and run lead to invoice in isolation with its own identity, branding, zone and pricing.
- **Not PILOT READY yet.** The remaining blockers are operational and business ones:
  - OPS-1 backup and restore drill
  - OPS-2 provisioning runbook
  - migration versioning
  - COM-4b template wording
  - GitHub CI (blocked by D1)
  - D13 licensing, D14 legal

_Previous note:_ Crown internal use **PILOT CANDIDATE** (unchanged). Commercial use **NOT READY**. The release blockers are COM-3, COM-4, COM-2c, COM-8, OPS-1, OPS-2, a CI run on GitHub (blocked by SEC-1), and D13/D14.

## 10. Next executable task

**Next priority: a hosted pilot rehearsal (needs authorization for any paid resource).**
1. With Jonathan's approval, provision one disposable hosted installation following the deployment runbook (TLS, secrets store, scheduled `backup-run`, `ops-check` alerting). Measure real backup and restore times.
2. Run provider **sandbox** verification (Gmail test mailbox, QuickBooks sandbox, Square sandbox) on that installation only.
3. Make the repository private (D1), then push, open a draft PR and run GitHub CI.
4. In parallel, the business track: licensing model (D13), legal review of the template and terms process and privacy (D14), customer agreement.

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
