# CrownOps — Commercial Licensing and Legal Readiness

**Purpose:** set out the commercial model options and the legal review list for selling CrownOps to independent moving companies. Each company gets its own installation.

**This document does not:**
- set prices
- choose a payment provider
- state that CrownOps complies with any law

**Market pricing was not researched.** Every price-shaped figure below is a **cost floor** derived from `HOSTED_PILOT_PREFLIGHT.md` (provider prices dated 2026-11-05), not a recommendation of what to charge.

Section A holds the technical recommendations. Section B holds the decisions that belong to Jonathan. Section C is the checklist for a qualified lawyer.

## A. Technical recommendations

### A1. Licence model — recommendation: contract-enforced licence on operator-hosted installations; no kill switch in code for the pilot

**Confidence:** high for the pilot, medium for later.

**Why:**
- Each installation runs on hosting the operator (Jonathan's side) controls.
- Activation, expiry and suspension are therefore operational actions: scale to zero, set read-only, or hand over the export. No licence code is needed.
- A code-level licence check adds a failure mode that can lock a paying company out of its own bookings. That is worse than the revenue risk at pilot scale.

**What would change this:** customer-hosted (on-premises) installations, or more than about 10 companies, where manual operations stop scaling. At that point, build a signed licence file that is checked at startup and degrades to read-only, never to deletion.

### A2. Lifecycle mechanics available today, or small additions

| Lifecycle event | Mechanism | Exists? |
|---|---|---|
| Activation | Provision per runbook sections 1–13; setup code; owner MFA | Yes |
| Renewal | No technical action | — |
| Expiry or non-payment, grace period | Notify the owner. Keep the service running for the agreed grace period. | Process only |
| Suspension | Stop the service; data stays on the volume, and backups continue only if a separate job is kept | Operational. A **read-only mode** does not exist and would be safer (staff can still view and export). Recommended build item, about 2–3 days. |
| Cancellation | Run `backup-run`, deliver the archive and restore guide through an approved channel, then destroy after the agreed retention (runbook section 20) | Yes. **No per-record export format** (CSV/PDF) exists; the archive needs CrownOps to read it. A CSV export of clients, jobs, invoices and payments is recommended before the first paid contract, about 3–5 days. |
| Upgrades | Per-installation deploy of a pinned SHA; migrations run on start; a backup gate exists for destructive migrations | Yes |

### A3. Cost floor per company (inputs for pricing, not prices)

| Component | Basis | Monthly floor |
|---|---|---|
| Hosting | Railway Pro ≈ $20, or a VM ≈ $15.60 (2026-11-05 list prices, USD) | $16–$30 |
| Offsite backup storage | B2 $6.95/TB; about 7 GB | < $1 |
| Monitoring | Free-tier uptime check (assumption) | $0–$10 |
| Operator time: backups, restore drill, updates, `ops-check` alerts | **Unknown.** Measure during the pilot (hours per month × Jonathan's rate) | ? |
| Support (staff questions, reconciliation help) | **Unknown.** Measure during the pilot | ? |
| Setup (provisioning, pricing policy, templates, terms, training) | One-off. Measure the first pilot's hours | ? |

**Recommendation:** run the pilot at a written fixed fee that covers at least the hosting floor, and log every operator and support hour. Set monthly and annual prices from measured hours, not from guesses. Confidence: high.

### A4. Tiers and SaaS

- **Tiers:** not recommended before there is pilot data. The real cost drivers are storage (photos and video) and support hours, so a storage-based tier is the most defensible later.
- **Shared multi-tenant SaaS:** not approved (decision A2 of 2026-11-04), and not technically ready:
  - one SQLite writer per company
  - settings that apply to the whole process (time zone, OAuth tokens keyed by provider only)

  This is a re-architecture, not a configuration change.

## B. Decisions for Jonathan (none made by the agent)

| # | Decision | Notes |
|---|---|---|
| B1 | Who is the licensor | `PROPRIETARY_NOTICE.md` names **Crown Movers** as copyright holder. Selling to other movers may call for a separate entity or a written assignment or licence from Crown Movers. Needs counsel (C1). |
| B2 | Monthly vs annual billing, and the setup fee | Use A3 inputs after the pilot. No market prices were researched. |
| B3 | Pilot fee and term | Fixed fee and term, with an explicit exit and data-return clause |
| B4 | Hosting region and provider | Railway has **no Canadian region** (2026-11-05). See preflight H1/H2 and C5. |
| B5 | Who operates installations | Jonathan alone, or a contractor (then a confidentiality and IP agreement is needed, C2) |
| B6 | Support hours and response times | Must be realistic for a solo operator. Do not promise 24/7. |
| B7 | Billing tool for licence fees | **No billing was set up**, per the 2026-11-04 instruction. Choose only after B1–B3. |
| B8 | Pilot company profile | Canadian, Quebec GST/QST, CAD, accepts Gmail, QuickBooks and Square limits (integration plans QB-11, QB-12, GM-11) |
| B9 | Repository visibility | The repository is **public**. Jonathan did not approve changing it. This is the largest IP and commercial risk (C1). |

## C. Legal human-review checklist (for a qualified lawyer; nothing here is legal advice)

| # | Topic | Question for counsel | Why it matters here |
|---|---|---|---|
| C1 | IP ownership and the public repository | Who owns CrownOps (Crown Movers, Jonathan, contributors)? What protection remains for source published in a public repository? What is the copyright status of AI-assisted code? | Trade-secret protection is likely lost for published code. `PROPRIETARY_NOTICE.md` itself says rights need confirmation. |
| C2 | Contributor and contractor agreements | Confidentiality and IP assignment for anyone with repository or hosting access | Required before outside help is used |
| C3 | Customer licence or SaaS agreement | Licence scope, fees, term, suspension, termination, data return and deletion, warranty disclaimer, liability cap, indemnities, governing law (Quebec?) | Nothing drafted yet |
| C4 | Privacy: Quebec Law 25 (Act respecting the protection of personal information in the private sector, as amended) and PIPEDA | Roles (the moving company as the responsible enterprise, the operator as service provider); a written service-provider agreement; incident-notification duties; the person in charge of personal information; retention | CrownOps stores client names, addresses, phone numbers, emails, inventories, photos and payment references |
| C5 | Transfers outside Quebec | Is a privacy impact assessment required before hosting a Quebec company's data in the US (e.g. Railway) or using US providers (Google, Intuit, Square)? | Railway has no Canadian region |
| C6 | Consumer-facing terms | Each company writes its own booking terms and liability wording in CrownOps. Does the operator need disclaimers that it does not provide them? | New installations get no Crown wording by design |
| C7 | Language | Charter of the French language requirements for software, contracts and client-facing text sold in Quebec | The French templates are owner-written. **The agent cannot certify Quebec French; flag for a fluent reviewer.** |
| C8 | Payment-card scope | Confirm that card data never touches CrownOps (Square-hosted checkout) and what PCI self-assessment, if any, applies | Checkout links are Square-hosted. Webhooks carry payment metadata only. |
| C9 | Third-party licences | Review the dependency licences (all permissive: MIT, BSD, Apache-2.0, MPL-2.0 for certifi, ISC, PSF). Also review the native libraries bundled in `pillow-heif` wheels and the Debian **ffmpeg** build in the container image (GPL/LGPL family). | Relevant if container images are ever delivered to customers rather than operated |
| C10 | Provider terms | Google API Services User Data Policy (Gmail restricted scopes may require a security assessment for production OAuth apps), Intuit app terms, Square developer terms | The Gmail `gmail.readonly` and `gmail.send` scopes are restricted scopes. **Verify current Google verification requirements before going live.** |
| C11 | Retention and destruction | Default retention for backups (14 archives), audit logs and uploads; deletion on request | Retention is configurable; there is no policy yet |
| C12 | Insurance | Professional liability or cyber cover for the operator | Solo operator holding client personal information |
