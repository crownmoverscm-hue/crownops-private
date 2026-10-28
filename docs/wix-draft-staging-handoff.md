# Wix draft readiness: implemented CrownOps milestone, 2026-10-28

## Scope and location

Authenticated page: https://crownops-quote-acceptance-staging.up.railway.app/wix-draft-sync

This release implements local CrownOps readiness and a disabled read-transport adapter. It is NOT an operational CrownOps-to-Wix draft creator. No Wix code has been published; no contact/quote has been created. The earlier offline prototype's 35 tests are not evidence of deployed behavior.

Exact site: `8911c013-d113-41ff-bca5-8484b699083b` (Mysite). All provider mutation routes unconditionally return 503, even if flags are enabled. No send/accept/invoice/booking/payment method exists in this adapter. The old unrelated Wix inbound workflow remains unchanged.

## Role permissions (actual active database roles, real session and CSRF)

| Role | Read page/status/preflight | Local snapshot review | Read mapped provider status | Prepare synthetic local fixture/accounts | Wix writes |
|---|---|---|---|---|---|
| Active Master owner | Yes | Yes; explicit reason, confirmation, fresh hash | Only existing mapping; adapter disabled | Yes, exact isolated service only | Never |
| Other owner | Yes | No | No | No | Never |
| Manager | Yes | No | No | No | Never |
| Sales | Yes | No | No | No | Never |
| Crew | No (403) | No | No | No | Never |
| Inactive user | No (401) | No | No | No | Never |
| Anonymous | No (401) | No | No | No | Never |

Reuse the owner's private existing staging sign-in. The Master-only panel can prepare five fictional local role accounts. Random passwords are Argon2-hashed, shown once only inside the authenticated no-store response, never emailed/logged/included in this document. Repeated setup does not disclose or reset passwords. The owner must transfer test credentials privately to Muse; do not paste them into chat. Master-owner identity is not changed. Existing session/MFA policies are inherited; no new assertion of MFA rollout is made.

## Safe local fixture

Owner may click `Prepare fictional local quote fixture — no Wix writes`. This prepares local SQLite rows only, idempotently:

- Quote `WIX-TEST-READINESS-01`, lead `WIX-TEST-READINESS-LEAD`, revision 1.
- TEST Wix readiness — fictional only; `wix-readiness@example.invalid`.
- Unapproved test-only price version; no global pricing or legal terms changes.
- Service 2026-12-31, expiry 2027-01-31, explicit fictional legal terms.
- Labour 2 x $50.00; transportation $35.00; fuel $35.99; materials 2 x $5.00.
- Subtotal $180.99; GST $9.05; QST $18.05; total CAD $208.09.
- Existing QuickBooks delivery rejects this test-only fixture. It is not Estimate 185 or a revision of its family.

Reading preflight does not mutate data. Recording `local snapshot review` is an explicit local audit mutation; it DOES NOT approve provider creation, email or acceptance. Muse's initial read-only pass should leave that button untouched. Functional review tests may use the disposable local fixture only if the owner authorizes local review evidence.

## Implemented normalized field mapping

| CrownOps source | Frozen normalized field / provider binding | Status |
|---|---|---|
| Exact allowlisted site | site | Implemented |
| Quote ID/revision, selected low/high basis | quote_id, revision, basis | Implemented; any newer revision blocks old |
| Client account association, original billing identity | client_id; customer name/email/billing | Frozen; Wix contact ID match remains UNVERIFIED |
| Itemized labour/hours, transport, fuel, materials | lines: stable local ID, description, decimal quantity, integer unit/total cents | Reconciled; zero-value information retained in notes; negative/discount lines fail closed |
| Approved selected pretax cents | totals/subtotal_cents | Exact line sum required |
| GST 5%, QST 9.975% | totals with independent half-up cent rounding | Expected Quebec calculation only; actual legacy Wix tax-code mapping UNVERIFIED |
| Currency | CAD | Only CAD accepted |
| Service date / explicit expiry | service_date / expiry | Frozen ISO expiry required |
| Client notes / liability / fragile / payment / versioned terms | notes; full_legal_terms; terms_version | Full content retained, never historic Wix pricing |
| Quote, lead, legal terms | source_hash + canonical whole snapshot SHA-256 | Fresh hash compared when reviewing/reserving |
| Provider ID/version/status | durable wix_draft_slots binding and readback schema | Schema and mocks only; no real mapping created |

SQLite review and evidence records reject UPDATE/DELETE; evidence is hash chained. Unique site/quote and site/remote-ID constraints plus transactional reservation prevent concurrent duplicate claims. A reserved slot cannot be deleted or rebound, and restarting does not free it. A production create adapter and unknown-result reconciliation worker remain unimplemented; there is no usable create operation in this release.

## Provider qualification and outstanding blockers

Official native Velo `wix-price-quotes-backend` create/get capabilities were reviewed; no external REST replacement was established. The transport here is a PROPOSED custom signed HTTP-function `quote.read` envelope, not an official Wix response schema or deployed endpoint. It pins exact HTTPS host/path, site, timestamp/nonce/HMAC, timeout, response-size/schema checks, no redirects/retries/proxy inheritance. Receiver-side authentication, replay defense, native permissions, response normalization and deployment still need implementation/qualification before it is enabled. Never expose an unauthenticated create endpoint. Native Wix app authentication is not assumed to authorize HTTP functions.

Authorized read-only Wix tax-settings GET returned HTTP 200 with `taxIncludedInItemPrices: false`. This global checkout setting does not prove legacy Price Quote tax IDs, compound behavior or rounding. GST/QST IDs and actual contact matching remain blockers.

Draft-reminder safety remains UNKNOWN. No Support case number/definitive answer is available in this implementation. Existing dashboard automations stay active and unchanged. A never-sent draft might trigger future expiry email; do not test on the operational site until definitive evidence or a separately approved isolated alternative resolves that risk.

## Muse acceptance: record PASS / FAIL / BLOCKED separately

1. Record exact footer build, environment, URL and role. Sign in normally; no bypass. Confirm all write buttons disabled, reminder UNKNOWN and native bridge unqualified.
2. Owner privately prepares synthetic role accounts and local quote fixture if needed. Check proposed data and totals above. These are local records, not Wix contacts/quotes. Do not touch Estimate 185.
3. Master reads fixture preflight: exact site/revision, CAD $208.09, full terms, hash, contact/tax UNVERIFIED. Repeat GET; no review or provider action should appear.
4. Manager/sales/other-owner can read; cannot record review/setup/readback. Crew denied; inactive sign-in denied; anonymous page/API denied. Use prepared private accounts, not forged sessions. Check denied API requests only within this namespace.
5. Optional, separately authorized local-fixture review: fresh CSRF/confirmation/reason records one immutable local review; repeat returns duplicate and no provider authorization. Keep secrets redacted. Do not mutate operational records to test stale guards.
6. At 320/390/768/1440 widths, check readable data, no horizontal overflow, labels and keyboard controls. This is a real UI; provider traffic remains absent.
7. Inspect CI results for stale revision/price/terms, protected Estimate 185 family, append-only data, concurrent claim, crash rollback, transport failures and response mismatches. These are executed mock/disposable tests, NOT live Wix guarantees. Do not fault inject deployed data.
8. Verify Estimate 185 still consumed/provider-reported sent, approval/history unchanged, downstream off using read-only existing view. Do not resend or reapprove it.
9. Report real provider contact match/native authentication/tax/draft readback/expiry reminder as BLOCKED or NOT TESTED, not PASS. No write, send, share, accept, decline, expire, conversion, invoice or booking tests.

## Deployment / rollback checklist

- Unit/security/integration tests use disposable SQLite records and mocked requests; JavaScript syntax/unit tests and CI browser suites must pass for the exact commit.
- Draft PR targets development branch only. Never merge main or change production/Staff AI.
- Railway project dynamic-joy, environment `53900324-c678-40b0-91f0-b6560316a5d2`, service `5de22c3a-ea7e-4fa6-bc96-2f9472431c7c` ONLY.
- Stage source pinned to exact passing commit. Inspect entire environment patch: only that service source may change, no variable/volume/other service changes. Apply authorized isolated deployment, wait for SUCCESS, verify health and content build.
- Keep existing database volume, OAuth secrets and mail/provider gates untouched. Schema addition is additive; original records are not rewritten.
- Rollback pins this isolated service to previous source commit `28969238c26bff16eb0dd5fa590e32e326a9553b`, branch `fix/core-launch-workflow`, with same full-patch review and deployment checks. Leave additive evidence tables intact; never restore an old database over Estimate 185 history.

## Later separate approvals

Before any operational write: resolve reminder risk, finish/publish authenticated Wix backend or verified supported alternative with separately approved permissions, prove contact and tax mapping, then present exact refreshed preflight. Contact creation (if required) and draft creation each require separate owner approval. Neither local review nor staging deployment grants them. No automatic send, acceptance, invoice, booking or QuickBooks creation is part of this connector.
