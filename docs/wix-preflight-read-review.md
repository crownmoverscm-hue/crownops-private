# Wix local preflight read correction — candidate only

Base: PR91 deployed commit 4c1e981cbd9bfb9cdd77b93f6ecfef5a5735ce71 / build 1cb6b860a47a5ca171c04abc. No staging deployment, fixture mutation, review, recovery grant or credential change is performed by this preparation.

Muse independently passed the exact build, five account identities, four disabled legacy controls, inactive no-reset state and dedicated can_prepare=false/can_execute=false/operation=null. These completed checks need not be repeated on the unchanged build. Muse's newly exposed account creation timestamps may be recorded as observed metadata; original setup attribution remains UNKNOWN.

## Reproduced defect and limits

The current page renders the Read local preflight button enabled before its click handler is installed. The handler is installed only after awaited session/status reads complete. A controlled delayed-status reproduction showed enabled=true and handler absent. There is no request timeout. This is a reproduced initialization race and a missing failure boundary; we cannot establish that it explains every reported Muse stall without that session's request evidence. Do not claim a provider, authentication or fixture-data failure from the UI stall.

## Narrow correction

Disable the read button in HTML and synchronously during initialization; enable only after session/status and handler are ready. Add a ten-second fetch/body timeout, visible initialization/read progress and errors, disabled duplicate reads and an explicit retry opportunity for GET only. Clear old snapshots/review state and ignore late responses after selection changes. No automatic request retries. No changes to snapshot calculation, source hashing, taxes, records, review authorization, recovery policies or provider adapters.

Seven Node regression tests exercise the actual page script for delayed initialization, success, read timeout, initialization timeout, HTTP denial, stale-selection discard and no review submission after read failure. The existing four-width real fixture browser suite now holds the initialization response and checks the read control is disabled before releasing it. Browser execution remains a CI check; local Chromium was unavailable.

## Existing safe fallback

An authorized Master can open one GET directly:
https://crownops-quote-acceptance-staging.up.railway.app/api/wix-drafts/preflight/WIX-TEST-READINESS-01?basis=high

This is the same existing endpoint used by the UI. It does not bypass authentication or record a review. Expected historical values (compare, do not alter): subtotal_cents 18099; gst_cents 905; qst_cents 1805; total_cents 20809; currency CAD; terms_version WIX-FICTIONAL-v1; service_date 2026-12-31; expiry 2027-01-31; snapshot_hash 50423b3a8c0da52d475d44c237b143c6d7008682b2c52564b37a5c58214a150b; review null; writes_enabled false. A direct GET error must be returned with status/detail, not retried repeatedly or corrected through a write. Values have not been freshly verified by DEV against an authenticated live fixture.

## Backend evidence handoff

CrownOps_PR91_Backend_Evidence_2026-10-30.txt contains authenticated GitHub-retrieved excerpts at the exact deployed PR91 commit, full-file Git blob references, cross-flow test source and actual successful CI excerpts from run 40999013588 (795 backend/20 JS/15 browser suites). It is an accessible source/test-review report, not independent execution by Muse, a cryptographic attestation or a live denial test. No source, CI count or controls from this new UI candidate should be presented as already deployed.

## Release and Muse gates

Separate owner approval is needed to deploy this UI-only candidate to the same isolated quote staging service after CI passes. Preserve the database/volume and all flags. No reset execution is authorized. Muse should confirm the new build, disabled initial read while loading, snapshot rendering of exact expected taxes/total/terms/hash, no review recorded, and explicit bounded failure rather than an indefinite enabled no-op. Any live timeout/fault injection needs separate scope; use disposable/mocked tests for injected failures. Backend recovery proofs remain the PR91 source/tests; no live recovery POST is requested.
