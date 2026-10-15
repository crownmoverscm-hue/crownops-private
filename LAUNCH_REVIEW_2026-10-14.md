# CrownOps launch and maintenance review — October 14, 2026

## Decision and limits

Proceed with an invited internal pilot once owner MFA, automatic backups and billing/alerts are confirmed. CRM, reviewed draft estimates, schedule review, important contacts and individual staff access are the first rollout. Activate customer email, live payments, accounting writes and photo/video uploads separately after their own end-to-end checks. A deployment healthcheck is not a complete business-workflow test.

This review covers the current source, CI evidence, existing device-layout fixtures and Railway service evidence. It is not a physical-device certification, an independent penetration test or a production accounting reconciliation. No employee invitation, customer message, payment or accounting write was made during this review.

## Observed hosting evidence

- Live target: `dynamic-joy` / `crownops-private` / `production`.
- Public address: https://crownops-private-production.up.railway.app/
- Production deployment `f0551a7f-57d5-41f5-b99b-7e90cc09f5cc` succeeded; one running instance with a `/data` persistent volume, 500 MB capacity.
- Recent disk usage was approximately 52 MB. This is sufficient for the current client database; it is not a photo/video capacity plan.
- Latest observed health response reported production mode and existing accounts. Private account/hosting endpoints reject signed-out access.
- Two HTTP 502s at 00:28 UTC on October 15 coincided with the preceding deployment rollout; current deployment is healthy. The attached-volume setup can have deployment downtime. Use planned release windows and continuous alerts.
- A second project, `humorous-growth`, also follows this repository and has no persistent volume. Review its purpose and charges before any cleanup. No services or data were deleted.
- Native backup schedules, billing plan, external uptime alerts and off-service backup/key retention could not be verified through the available connector. Do not mark these complete from the app's runtime checks.

## Improvements in this release

- Crew file access requires a current assignment to the client project. A file linked to a specific job also requires assignment to that job. Shared project files remain available to assigned crew; office and client-portal access retain their separate boundaries.
- File lists omit attachments for other crews' jobs. Assignment removal takes effect on the next request, including direct download URLs.
- Recovery creation refuses a low-disk operation before starting a new archive. It reserves estimated archive size plus 256 MB of operating headroom and keeps existing recovery copies. This is a preflight check, not a guarantee against concurrent disk growth.
- Seven additive indexes support project/job/file lookups, task queues, session removal and login throttling as records accumulate. No pricing, issued invoice, payroll or imported business record is changed.
- Dependabot is configured to propose weekly Python dependency updates and monthly GitHub Actions updates. Changes need CI and human review; updates are not merged automatically.

## Team workflow

1. Sales starts from Today / Sales & clients, claims each inquiry, checks duplicates and records the next follow-up with a responsible person and date.
2. Prepare estimates using reviewed rates, move details and calculator results. Staff review scope, access, crew/trucks, taxes and terms before approval. Client acceptance remains distinct from final booking confirmation.
3. Dispatch reviews tentative work, deposits, driver/truck readiness, resource conflicts and handoff notes before confirming work.
4. Crew uses its assigned work, important contacts, safety checks and submitted hours. Review pay rates and driver verification before operational dispatch.
5. Accounting reconciles verified receipts, issued invoice revisions and balances; owners review financial access and exceptions. QuickBooks is the accounting system of record when its production integration is enabled and reconciled.

Current roles are Owner, Sales, Dispatch, Crew and Accounting. There is no distinct Manager role. Do not grant Owner merely to approximate a manager; review required duties. A manager who needs both sales and dispatch permissions warrants a dedicated role in a later access-control release.

## Storage plan

Keep client/job/invoice metadata on the existing database volume initially. Keep one instance with SQLite. Do not increase replicas against the same local database. Consider a deliberate PostgreSQL migration when multi-instance availability, significant concurrent writes or measured database bottlenecks justify it; migrate with reconciliation and a restore rehearsal.

Before activating images, PDFs or videos in production:

- Add private S3-compatible object storage and an application adapter; creating a bucket alone does not move or protect existing uploads.
- Preserve per-client/per-job authorization and scanner quarantine. Use authenticated streaming or short-lived download URLs after access checks; never make the company photo collection public.
- Configure and test scanning, file type/size limits, mobile image compression, thumbnails and an iPhone HEIC conversion path. Current accepted image formats are JPEG, PNG and WebP.
- Establish deletion/retention rules by record purpose and approved company requirements. Keep a recoverable file manifest and verify backups include the new storage backend.
- Add storage/failed-upload alerts and reconcile any existing files before a migration. Test customer and employee access on real phones.

Railway's published bucket storage rate at review was USD 0.015/GB-month: approximately USD 1.50/month for 100 GB averaged across a month, before app hosting, service egress, scanning, backups, taxes or currency conversion. This is an illustrative storage-only budget, not the user's verified subscription or total operating cost. Source: https://docs.railway.com/storage-buckets/billing

## Personal AI accounts

Prefer an optional, permission-scoped CrownOps connector used from an employee's ChatGPT or Claude account first. Start with summaries, missing-information questions and reviewed drafts. The server enforces current CrownOps access and records actions; model instructions are not an authorization mechanism. Removing staff access must invalidate connector grants too.

AI embedded in CrownOps using a personal subscription requires provider-specific support. Hosted private apps need eligible/approved OpenAI plan-use integration; Claude's supported authentication and credit arrangements also need validation. Do not present a working Connect button until real authorization, revocation and a complete request succeed. AI remains independent of the core pilot and does not invent rates or approve financial actions.

## Remaining launch checks

- Each active owner completes authenticator setup and verifies sign-out/sign-in. Keep the original encryption key separately in a private company vault.
- Confirm Daily and Weekly native Railway backups, one current manual snapshot and the downloaded restore-verified portable copy outside the service. Assign one owner to recovery operations.
- Confirm an active appropriate hosting plan and billing; set continuous downtime/error/storage alerts with an owned destination. Railway deployment healthchecks do not continuously monitor service availability.
- Invite the actual team using verified email addresses and reviewed roles. Have a manager and sales user complete signup, login/recovery, test inquiry/follow-up, draft estimate and schedule/contact handoff. Crew testing also checks assignment isolation.
- Record tests on iPhone Safari, Samsung Android Chrome, Mac and Windows. Existing Chromium viewport fixtures support layout confidence but do not replace these device tests. The current web app requires internet; do not promise offline saves.
- Before customer-facing financial use, reconcile one controlled real transaction per enabled provider. A sample Square webhook returning 200 proves endpoint handling, not a successful CAD deposit/refund or accounting reconciliation.

## Operate for the next four to five years

- Weekly: review failed jobs/integrations, unassigned/overdue follow-ups, backups and dependency pull requests.
- Monthly: apply reviewed security updates, review storage growth/access departures, and verify one recovery copy. Set clear ownership; software cannot be left unattended for years.
- Quarterly: rehearse recovery in an isolated environment, test important real-device flows, review roles/rates/terms and review permissions on AI/provider connections.
- Before growing the team or enabling customer media: complete the dedicated manager role and private file-storage release. Keep owner-only financial reports as a separate permission.
- Before additional developers or unattended releases: enable protected main-branch checks and Railway's wait-for-CI source setting. The current repository main branch is unprotected and Railway `checkSuites` was false at review; this review's release is merged only after CI passes.
- Keep the company-controlled repository, hosting, domain, billing and recovery ownership documented. Prefer `app.crownmovers.ca` when ready, and update all OAuth/webhook callback URLs and retest after changing the domain.

Useful official references: https://docs.railway.com/volumes/backups, https://docs.railway.com/deployments/healthchecks, https://docs.railway.com/storage-buckets, https://developers.openai.com/siwc/token-sharing-open-source, https://claude.com/docs/connectors/building.
