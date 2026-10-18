# CrownOps finalization pass — October 16, 2026

## Source and deployment state

Based on the uploaded main-branch archive at commit 247844fc1308abf19eb5cc0e0c1efb2be621a581.
This archive contains reviewed local source changes. It has not been pushed to GitHub or deployed to Railway.

## Changes

- Back to move preserves unsaved assignment selections, timing/title fields, and route details in the current signed-in browser session. Existing version checks still discard stale drafts after another saved change. Sign-out clears drafts.
- Returning to a move or its schedule restores a copy of the original calendar week, My Jobs / All Jobs selection, page scroll, and horizontal calendar position. Navigating weeks no longer mutates the remembered date.
- Move and schedule photo/file counts use the same current-assignment rules as the file list. Ordinary crew no longer see counts for files belonging exclusively to other teams. Office and designated team-lead access remains as configured.
- GitHub CI runs both JavaScript test files. Changed JavaScript asset URLs are versioned for refresh after deployment.

No database schema changes are required for this navigation-only pass. The subsequent Manager and Expenses changes do require a schema migration; see LAUNCH_CHANGES_2026-10-16.md for the combined release and current validation.

## Validation

- Before changes: 224 Python unittest tests passed.
- After changes: 227 Python unittest tests passed with external actions disabled and QuickBooks set to sandbox.
- 13 Node tests passed: 8 existing estimator checks and 5 navigation/draft regressions.
- All 5 navigation regression cases fail against the uploaded original JavaScript and pass with the changes.
- Python compilation, syntax checks for every application JavaScript file, and isolated application creation passed (274 routes).
- API tests use disposable databases. JavaScript navigation tests use a simulated page environment; they are not browser layout or physical-device tests.

## Still required before calling the application finalized

1. Submit the changed source to the company repository, let CI pass, and deploy through the existing Railway project. Confirm the actual deployed revision.
2. On the deployed application, sign in and verify the original week/view, unsaved assignment return, saved crew/truck names, file counts and incident-report flow with actual Owner and Crew accounts. Check a phone as well as desktop.
3. Complete the account/hosting/provider checks already listed in FINAL_REVIEW_CHECKLIST.md and GO_LIVE_RUNBOOK.md. This pass does not establish production backup restoration, privileged-account MFA, live email/payment/accounting integration success, or physical-device certification.

## Modified application files

- app/static/job_planning.js
- app/static/move_navigation.js
- app/static/index.html
- app/platform_common.py
- app/portal.py
- app/final_features.py

## Tests and automation

- tests/move_navigation.test.cjs (new)
- tests/test_file_permissions.py
- .github/workflows/ci.yml
