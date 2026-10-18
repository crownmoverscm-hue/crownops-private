# CrownOps Go-Live Runbook

Current source candidate: **0.11.0-rc1**. Read `WORKFLOW_RELEASE_2026-10-18.md` first for current validation and deployment status. Earlier release notes describe historical checkpoints.

For the permanent **team workspace** launch, follow [RAILWAY_SETUP.md](RAILWAY_SETUP.md#permanent-team-launch). Core team use can launch with customer email, Square and QuickBooks production writes still disabled. Their separate provider activations below do not block staff invitations, CRM work, draft estimates or schedule review. Account invitation/recovery emails use `CROWNOPS_ALLOW_AUTH_EMAIL`, independently of general customer email.

The stages below cover additional customer-facing and financial integrations; perform the applicable checks when activating each provider.

## Stage 0 - Keep the working app safe

- Keep the existing Railway volume mounted at `/data`.
- Keep `CROWNOPS_DB=/data/crownops.sqlite3`.
- Keep `CROWNOPS_UPLOADS=/data/uploads`.
- Keep `CROWNOPS_ALLOW_EXTERNAL=0`.
- Keep `CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES=0` or unset.
- Keep production uploads disabled until malware scanning exists: `CROWNOPS_UPLOADS_ENABLED=0`.
- Store `CROWNOPS_SECRET_KEY` somewhere outside Railway as well. A database backup is not useful for OAuth credentials without the same encryption key.

## Stage 1 - Prove QuickBooks sandbox

1. Keep `QBO_ENV=sandbox`.
2. Confirm the QuickBooks card says authorized.
3. Open QuickBooks Setup and choose:
   - moving service item,
   - Canadian sales-tax code,
   - discount account if discounts will be exported,
   - deposit account.
4. Run Test connection and confirm the displayed company is the sandbox company.
5. Enable only sandbox accounting writes:
   `CROWNOPS_ALLOW_QBO_SANDBOX_WRITES=1`.
6. Do not enable `CROWNOPS_ALLOW_EXTERNAL` just for QuickBooks.
7. Create one obvious test client, e.g. `QBO TEST - DO NOT BILL`.
8. Export one approved estimate to the sandbox.
9. Export one issued test invoice and confirm the QuickBooks total equals CrownOps.
10. Record/allocate one test payment and export it.
11. Confirm a second click does not create duplicates.
12. Turn `CROWNOPS_ALLOW_QBO_SANDBOX_WRITES=0` again after the test.

## Stage 2 - Staff acceptance test

Use [TEAM_ONBOARDING.md](TEAM_ONBOARDING.md) to delegate setup by role and record real-device results. The in-app **Help & install** page explains Mac, Android, iPhone and desktop installation. The icon is a shortcut to the hosted app; internet and an individual account are required.

### Shared contacts and fleet setup

After merging the company-information release and confirming Railway has deployed it:

1. Sign in as the owner. Open **Fleet → Import company information** (also available in **Important contacts**).
2. Select the separately supplied `Crown_Company_Information.xlsx`. Review the contacts, truck matches, service history and source questions. Save the reviewed import once. Repeated uploads are detected; conflicting plate/VIN matches block the batch.
3. In **Important contacts**, verify the phone numbers and service areas. All signed-in staff can search and call these contacts; owner and dispatch users maintain them. Employee emergency contacts remain in the existing restricted staff profile workflow.
4. Open each truck in **Fleet**. Confirm registration, VIN, fuel, current odometer, rated payload, GVWR, measured cargo length, service schedule and the exact inspection expiry date. Source inspection months are reference notes, not verified expiry dates. Resolve imported undated repairs with the workshop and record completed work.
5. Owner or dispatch records the review evidence and uses **Review & activate** for trucks that are available. The import itself does not activate trucks or create current safety checks. Complete the normal pre-use check before a job starts. Current defects and imported review requirements continue to block dispatch/start.
6. Review employee pay rates and activate current staff; verify the drivers who will be assigned. Directory imports do not create login accounts. Link each crew login to its employee record through the existing account workflow.
7. Use the owner **Launch checklist** to identify remaining setup items. Verify a sales/accounting account can read Fleet without the new editing controls, and a crew account sees Important contacts and its assigned work rather than the office fleet directory. Server permissions enforce these restrictions.

The workbook contains private operational contacts and identifiers. Keep it with the company records, outside the source repository. Deployment adds the screens and schema; a reviewed owner import is still required to load the supplied data.

Test with at least one owner/manager and one sales user on desktop and mobile:

- login and MFA,
- invite/login,
- Sales & clients,
- lead claim,
- Communications,
- website inquiry intake,
- missed call / voicemail intake,
- Estimates,
- Follow-ups,
- Schedule,
- Invoices,
- Payments,
- QuickBooks setup screen,
- search,
- logout/login.

Do not enable outbound Gmail yet.

## Stage 3 - Back up before cutover

Create a private CrownOps backup and perform one restore drill to a separate temporary installation. Preserve the current `CROWNOPS_SECRET_KEY` separately.

## Stage 4 - Give CrownOps its production address

Recommended address: `https://app.crownmovers.ca`.

After the DNS/custom-domain change:
- set `CROWNOPS_PUBLIC_URL=https://app.crownmovers.ca`,
- update Google OAuth redirect URI,
- update Intuit Development redirect URI if sandbox testing continues on the custom domain,
- later update the Intuit Production redirect URI,
- verify `/api/health` after deployment.

## Stage 5 - Connect real QuickBooks without writes

1. In Intuit Developer switch to Production credentials only after Intuit permits the production app.
2. Put the Production Client ID and Client Secret in Railway.
3. Set `QBO_ENV=production`.
4. Keep `CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES=0`.
5. Reconnect QuickBooks and choose the real Crown Movers company.
6. Use QuickBooks Setup to choose the production service item, tax code, discount account and deposit account.
7. Run Test connection.
8. Verify the company name is the real company.
9. Do not proceed if the company or mappings are wrong.

Sandbox mappings are stored separately and cannot silently become production mappings.

## Stage 6 - Enable production QuickBooks writes

Only after the production connection and mappings are reviewed:

- set `CROWNOPS_ENV=production`,
- set `CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES=1`,
- leave general external actions off unless another provider is intentionally being activated.

First real accounting test:
1. choose one controlled real client/job,
2. review the CrownOps estimate/invoice total,
3. sync once,
4. confirm the QuickBooks record and total,
5. reconcile before doing a second client.

## Stage 7 - Enable outbound Gmail separately

First verify the connected Gmail account. Review company Send-as and Reply-To in settings, test any alias in Gmail, and approve the identity only after the real recipient confirms it.

Only after a controlled test recipient succeeds:
- configure `CROWNOPS_TEST_EMAIL_ALLOWLIST` while still testing,
- approve one draft,
- send one message,
- verify Gmail Sent and the CrownOps timeline,
- only then set `CROWNOPS_ALLOW_EXTERNAL=1` when all general external actions intended for production have been reviewed.

## Stage 8 - Final production checks

Before removing the review banner:
- CI green,
- Railway deploy green,
- database backup current,
- restore drill passed,
- owner MFA enabled,
- role permissions tested,
- QuickBooks production sync tested,
- Gmail send tested,
- no unresolved provider operations in Unknown/Mismatch/Failed/Running,
- uploads remain off unless malware scanning is configured,
- duplicate Railway projects/services removed or disabled,
- custom domain working on mobile and desktop.

Then remove the review banner in a final small release PR.
