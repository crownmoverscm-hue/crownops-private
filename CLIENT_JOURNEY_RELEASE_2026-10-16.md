# CrownOps client journey release — 0.8.0-rc1

This release extends the live 0.7.0-rc1 role, fleet, schedule and invoicing work. Deployment evidence is recorded below after verification. Earlier dated deployment notes describe the previous release.

## Changes

- Production checks connected Gmail quote-form, missed-call and voicemail labels every two minutes. The inbox operation reads messages without changing Gmail labels/read state. Owners/Managers can pause imports; Sales can check immediately. The inbox shows import errors, last successful check and callback requests.
- Website forms and Gmail forms enter the CRM. The same company JavaScript calculator prepares local-moving guidance on the server. A real Draft quote is created only when required service details, travel, arrival window, approved pricing and a service template are available. Incomplete submissions have a preparation record listing missing information. Nothing is automatically approved, sent or booked by estimate preparation.
- Original form text and multiline access instructions are retained. Parsing repairs fill blanks only in untouched, unassigned Gmail forms without quotes or jobs; reviewed records are preserved. Matching uses existing contact rules and flags conflicting identities.
- Owners, Managers, Sales and legacy Dispatch can close/reopen a company date in Schedule. New bookings and reschedules cannot use a closed day. Existing confirmed work remains; existing confirmed clients retain payment access. Public requests can still be submitted and warn when a requested day is closed.
- Quote finalization reuses a valid draft link and never revokes an already-sent link just because Finalize is clicked again. Old approvals are superseded; stale unsent quote messages are cancelled. Changes pending review block acceptance and booking confirmation.
- Client email has plain-text and styled HTML alternatives, a private-file button, a clear next step, contact information and EN/FR copy. Invoice delivery includes a private invoice link plus the existing document attachment. Booking confirmation includes its service date and a private-file link. Public form acknowledgment is queued once; sending follows the existing external-action permission.
- The private portal offers quote acceptance, photos, reviewed detail changes, callback requests, invoice viewing/download and checked QuickBooks payment links. Mobile quote item prices display as readable rows. Language switching keeps public-form input. Requested dates and confirmed bookings have distinct wording.
- Clients can request a fresh email access link without revealing whether an address has a company file. Requests are rate-limited. Links are single-use, scoped to one service and expire after 14 days for delivery/recovery; staff-created manual invites expire after 48 hours. Sessions last 12 hours. Reopening the same link works in its matching active session. Changing the project email invalidates previous sessions/invites.
- Duplicate callback/detail submissions are handled safely. Sales sees callback requests and can mark them completed. Completed/cancelled service display and pending-payment wording avoid implying a new booking or a second payment is needed.
- Crew client-project listings are restricted to their assignments; Owner/Manager expense privacy and assigned-crew invoice checks remain enforced.
- Production startup verifies a fresh portable database/upload restore before introducing this release's schema. Failure stops before the new schema is added. The configured encryption key is retained.

## Validation

- Full Python regression: 265 tests passed. Includes client and role permissions, stale and duplicate requests, estimate supersession, single-use access, email changes, automatic estimate preparation, original-form recovery, date closures across Montréal midnight, received-payment preservation, and recovery before migration.
- Node calculator/navigation checks: 13 passed.
- Chromium: clients at 1440×1000, 820×1180 and 390×844 accepted fictional estimates, uploaded photos, requested callbacks, submitted changes and switched language. Mobile invoice viewing and access-link requests passed. A French public inquiry retained entered information and submitted a callback request on a closed date without booking it.
- Owner/Manager/Sales/Crew opened 26/26/23/9 visible navigation sections, respectively; My Jobs/All Jobs and date controls were checked. This is navigation coverage, not a claim that every action in every section was manually exercised.
- Expense create/edit/void passed at all three viewports. Assigned crew prepared/finalized an invoice, checked a mocked QuickBooks link and sent a mocked invoice email. Browser runs reported no JavaScript page errors. Visual inspection caught and corrected mobile quote pricing visibility and invoice-button contrast.
- Provider transports were mocked; no real client email, charge or accounting write was performed in local QA. Viewport testing does not certify physical iPhones, Safari or provider deliverability.

## Remaining account-dependent launch checks

1. Sign in to the deployed app with company Owner/Manager/Crew accounts, verify employee links and privileged-account MFA.
2. Use one explicitly designated company test client for the actual Gmail inbox, quote link, acceptance, booking, invoice and QuickBooks payment/reconciliation sequence. Do not treat mocked transports as provider certification. A real payment must be completed by an authorized person.
3. Synchronize source with `crownmoverscm-hue/crownops-private`. GitHub is not connected in this session; do not redeploy the older repository contents over this release. The updated source package is prepared for sync.
4. Download the verified backup from Launch checklist and retain an encrypted copy outside this Railway volume, with the existing encryption key stored separately. Railway volume snapshots/schedules require a Pro workspace; no subscription or recurring backup schedule was changed.
5. Start with a small pilot after those checks. Review collection remains deferred.

## Deployment evidence

Deployment is prepared and locally verified. The existing production app remains at 0.7.0-rc1. The local Railway upload session expired before upload; a fresh sign-in is required. No new project was created and no production data was changed by the attempted upload.
