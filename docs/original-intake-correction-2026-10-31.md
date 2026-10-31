# Original notification intake correction — local-only handoff

Parent commit: 442ccd0b6c93a3e462e2cfee1404f9e04bf25f33. Candidate identity is recorded in the delivery package SOURCE.json and GitHub commit. No deployment or live inbox replay is authorized.

## Correction

`app/legacy_notifications.py` recognizes only the exact known EN/FR form subjects with their expected rendered prefixes, or the calculator subject with Client Details/Follow-Up sections. Fixed legacy labels are interpreted inside those bounded sections. The original canonical builder parser remains the fallback. No whole-message contact regex is used, so the office signature and follow-up draft cannot replace missing customer contact fields. Duplicate identical anchor-display values collapse; conflicting customer email/phone values force identity review. Blank customer names remain blank and cause explicit validation review rather than creating a shell.

The FR form's flattened departure/postal fields conflict and lack an explicit destination section. Both address fields remain blank; exact raw address/postal fields and narrative are retained in event parsed/source dictionaries and full message body. A review task and draft blocker identify the ambiguity. Elevator value is retained as `Ascenseur: Oui`, not an invented access assumption. Calculator summaries (including FR currency/rates) remain `calculator_source.approved=false`; they are never approved pricing. Calculator language is inferred from submitted content and explicitly flagged for review. Empty inventory/specialty/photo fields remain empty. A following heading is not a photo link.

`app/gmail_sync.py` permits a distinct new service date among several existing services only for form/quote intake, a valid explicit date, BOTH matching email and phone, matching identifiers on every candidate service, one common client link whose account identifiers also agree, distinct existing date strings, and no closed/completed/lost candidate. Incomplete identity, conflicting identifiers, missing dates and unverified links remain in review. A unique exact existing date still selects its own service. The single-existing-service branch now checks closed status before allowing a different-date inquiry. No booking or acceptance path was changed.

Parser review reasons propagate to `prepare_draft` in `app/main.py` and `app/automatic_intake.py`. They are explicit missing/review blockers, not prices or invented source values. Existing repair code receives the same blockers, but no existing records or backfill were processed. There is no schema migration.

## Actual local results

Run from the candidate root with repository requirements in a disposable virtual environment:

```
PYTHONPATH=.:tests CROWNOPS_ENV=test CROWNOPS_ALLOW_EXTERNAL=0 python -m unittest test_original_notification_intake test_form_language test_wordpress_intake_contract test_intake_resilience test_gmail_ambiguity test_launch_workflow -v
PYTHONPATH=.:tests CROWNOPS_ENV=test CROWNOPS_ALLOW_EXTERNAL=0 python tests/replay_original_notification_intake.py /tmp/crownops-corrected-intake-results.json
```

The delivery includes actual logs. These are focused local tests, NOT a GitHub CI pass or deployed functionality. No full backup/recovery, browser or role-matrix suites were rerun.

Unchanged four fixture bodies produce:

| Item | Actual expected count/value |
| --- | --- |
| client_accounts | 1 |
| client_account_leads | 4 |
| leads | 4 |
| integration_events | 4 |
| incoming messages | 4 |
| tasks | 7: four inquiry tasks and three parser-review tasks |
| intake_drafts | 4, all Needs details, quote_id null |
| quotes / outbound_messages / jobs / payment_links / deposit_links / payments / job_invoices | all 0 |
| exact retry | 4 duplicates; zero new events/leads; stored rows unchanged |

| Fixture | Email | Normalized phone | Moving date | Language | Address handling |
| --- | --- | --- | --- | --- | --- |
| EN-FORM-RC5 | jonathanprobonas15@gmail.com | 5146064030 | 2026-11-10 | English | H3A 1A1 → H2Y 1C6 |
| FR-FORM-RC5 | same | same | 2026-11-09 | French | blank / blank; conflicting original values preserved and flagged |
| EN-CALC | same | same | 2026-11-11 | English, inferred | H3A 1A1 → H4N 2B2 |
| FR-CALC-RC5 | same | same | 2026-11-12 | French, inferred | H2Y 1C6 → H3A 1A1 |

The replay exports all parsed and stored fields plus exact original message bodies, event payloads, intake draft inputs/missing reasons and per-field equality results. Source field dictionaries retain ambiguous/multiple original values. Runtime IDs/timestamps may differ. The fixture-body hashes must remain identical to the accepted failure-reproduction package.

## Regression boundaries

Tests cover the four untouched fixtures, stored field equality, source preservation, repeat idempotency, same Gmail ID with altered body not overwriting records, blank customer contact vs office footer, blank names, conflicting repeated email/phone, missing approved pricing, missing dates, conflicting identity, email-only multi-service requests, closed services (single and multiple), changed client linkage, exact date reuse and separate new notifications not overwriting existing service scope. Existing canonical/EN/FR, ambiguity, signed-webhook and validation rollback regressions are included.

Gmail idempotency remains based on immutable Gmail message ID: a repeated ID is skipped without refetching or replacing its original body. It is not a 409 changed-body detector. The signed webhook's separate content-hash/409 semantics remain unchanged. No generalized bypass, forced contact merge or stale-snapshot exception was added.

## What is NOT established

- Raw MIME/HTML behavior: these are rendered-text fixtures supplied by paste; original Markdown byte identity remains unverified.
- The other eight historical payloads were not accessed or replayed.
- The source website plugin/platform is not established. Historical evidence supports Gmail notification ingestion; current WS Form exports and notification routing still require read-only confirmation.
- Complete quotes are deliberately NOT generated: pricing, travel, arrival, inventory/access or address review remains required. No source calculator price is adopted.
- No staging/production behavior, live endpoint delivery, customer email, acceptance, booking, capacity or accounting acceptance is claimed.
- The legacy acceptance handler's automatic job/deposit path is unchanged, remains out of scope and must not be used.

## Muse acceptance

Use the attached corrected ZIP, not the old failure-reproduction ZIP. Verify SHA256SUMS.json, fixture hashes, SOURCE.json and patch. Run only run.sh in a fresh /tmp extraction and disposable venv. The actual parser/new_lead path must yield one client/four dated services without any injected success values. Inspect independently generated results and field table; require all consequential counters zero. Check the negative tests and explicit draft blockers. Do not treat the focused local pass as operational intake acceptance. Return PASS/FAIL/BLOCKED per layer and STOP.

No deployment is authorized. A later HTTP/browser test needs separate owner approval for an exact synthetic intake environment (not the live website or quote-testing staging), approved fixtures, commit and egress controls. Provider writes and customer acceptance/booking remain disabled.
