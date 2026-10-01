# CrownOps v5.6 Final Review Checklist

Use this checklist before enabling any production external actions.

## 1. Staff access
- [ ] Owner login works on desktop and mobile.
- [ ] Manager invitation works and creates a unique staff account.
- [ ] Sales user can access Sales & clients, Estimates, Follow-ups, Communications, Schedule and Invoices.
- [ ] Crew user sees only permitted field-work data.
- [ ] MFA is enabled for privileged accounts.

## 2. Lead intake and sales
- [ ] Website quote request creates one lead with no duplicate.
- [ ] Missed call matches an existing client by normalized phone number.
- [ ] New caller creates one lead and one follow-up.
- [ ] Voicemail attaches to the right client and creates a follow-up.
- [ ] Claiming a lead assigns unowned open follow-ups to the claimant.
- [ ] Estimates can be created, searched, filtered, approved and accepted.
- [ ] Follow-ups can be searched, filtered and completed.

## 3. Communications
- [ ] Incoming client email appears once in Communications.
- [ ] One staff member can claim a conversation.
- [ ] Claiming the conversation assigns the client when unassigned.
- [ ] Answered conversations can be resolved.
- [ ] Gmail exceptions show payment notices only; normal calls/voicemails live in CRM activity.
- [ ] Outbound Gmail remains disabled until a controlled live send test is approved.

## 4. Operations
- [ ] Weekly schedule looks correct on desktop, tablet and phone.
- [ ] Truck colour, crew assignments and multiple same-day jobs display correctly.
- [ ] Confirmed jobs enforce accepted quote/deposit/resource checks.
- [ ] Time-off blocks and crew hours behave correctly.

## 5. Billing and payments
- [ ] Invoice list shows Draft / Balance due / Partial / Paid correctly.
- [ ] Invoice total and balance agree with the saved invoice revision.
- [ ] Verified deposits allocate correctly.
- [ ] Corrections create new invoice revisions instead of overwriting issued invoices.
- [ ] Payment records are never auto-verified from email alone.

## 6. QuickBooks sandbox
- [ ] OAuth connects to the intended sandbox company.
- [ ] Customer create/match does not duplicate.
- [ ] Estimate export matches CrownOps totals.
- [ ] Invoice export matches CrownOps totals.
- [ ] Verified payment posting matches the intended invoice.
- [ ] Retry/reconciliation protections prevent duplicate QBO writes.

## 7. Production release
- [ ] Database backup created and restore tested.
- [ ] CROWNOPS_SECRET_KEY stored separately in a secure vault.
- [ ] Production domain configured (recommended: app.crownmovers.ca).
- [ ] Google OAuth redirect URI updated for the production domain.
- [ ] QuickBooks production credentials configured only after sandbox approval.
- [ ] External actions enabled one provider at a time.
- [ ] Review-build banner removed only after management sign-off.
