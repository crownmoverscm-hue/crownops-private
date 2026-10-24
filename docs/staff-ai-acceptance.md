# Staff AI stabilization — 2026-10-24

Scope: existing staff-only Responses adapter. No customer chatbot, agent framework, pricing activation, integration reconnection or production deployment.

## Evidence and release boundary

Baseline branch `feature/ai-staff-controls`, commit `a5de3a1055ed28569b75e3ffb7ad28a42b11541f`. Baseline staging deployment `68378f54-2eb9-4166-b824-42366a8b08b3` succeeded. The owner's English (605 input/485 output) and French (632/378) UI results demonstrate useful draft behavior, not independent provider provenance. Their provider request IDs, response model, latency and billing were not retained, and cannot be reconstructed honestly.

The production variable-name inventory contains no OPENAI_API_KEY, OPENAI_MODEL, CROWNOPS_AI_ASSIST_ENABLED or staging AI test flag. Staging has those names and a separate database/volume established during setup. OAuth withholds variable values; current values and database contents require authenticated runtime evidence. Do not report them as read back.

## Targeted changes

- Keep structured fields and `/v1/responses`; retain consent, role checks, default-off pause, pricing gate, reservations, timeouts and per-user limits.
- Concise natural English/Canadian French, two to four prioritized questions as appropriate, specialty/commercial/long-distance escalation. Prompt instructions are not semantic test results.
- Prefill the latest incoming message, falling back to inventory/special items. Only the selected lead's allowed fields reach the provider. Office roles currently have access to all client records; this is not per-assigned-salesperson isolation.
- Add service-specific AI entry to the client file. Standalone stays manual-paste and creates no client.
- Editable draft, copy edited content, explicit save confirmation, idempotent server draft/summary saves tied to the originating user and run's lead. Original communications stay intact. Save returns `sent=false`.
- Disable concurrent submit, reuse request IDs for identical retries, retain language choice and interface language, accessible loading/output state, avoid applying late responses to a different modal.
- Retain provider request/response IDs, actual model, completed status, latency and usage for future successful requests; include cached retries. Owner-only recent evidence is available from `/api/ai/status`. Metadata logs contain no prompt, client fields or secret.

## Cost and reliability

Current standard short-context gpt-6-luna prices verified against https://developers.openai.com/api/docs/models/gpt-6-luna on 2026-10-24: USD 0.10/M input, 0.01/M cached input, 0.50/M output. A displayed estimate is computed only for this model family, standard/default tier, <=272K input and zero cache writes. Unsupported pricing cases return no estimate. It is not an invoice or billing reconciliation. The earlier UI usage would estimate USD 0.0003030 English and 0.0002522 French with no caching; actual billing remains unverified.

The application reserves conservative USD cents and tokens before provider requests. Reservations remain after provider errors; this intentionally fails closed. Four requests/minute/user; 14K message characters; 2200 max output tokens; 10s connect/80s read timeout; zero automatic retries. Identical completed retries reuse output; unresolved retries return 409. Pause using CROWNOPS_AI_ASSIST_ENABLED=0 and deploy. The staging-only flag bypasses only the AI endpoint's general external gate and never authorizes production or another provider.

Structured results remain in existing ai_runs and explicitly saved client timeline records. This change does not introduce automatic deletion; owner-approved retention policy remains to be settled. Provider `store=false` is retained; it does not imply zero provider-side retention.

## Tests and honest statuses

`tests/fixtures/ai_scenarios.json` contains 26 English/French semantic scenarios with expected behavior, including commercial and specialty moves. All are live-model NOT RUN on this stabilization build until independently executed. `tests/test_ai_controls.py` runs deterministic/mocked contracts, not live semantic certification. `tests/browser/staff_ai.cjs` checks four widths, route isolation, role checks and zero opening writes; edit/save/repeated-submit sections explicitly mock model/UI responses.

Real staging evaluation is BLOCKED: secure sign-in returned “Email or password is incorrect” on 2026-10-24. No credentials were inspected or retried. No real customer message, booking, charge or accounting write was attempted.

## Muse test handoff — one source-code owner

Implementation stays in PR #87. Muse tests independently; report defects before editing these same files. Use the deployed commit/build, secure staging login and fictional TEST — MUSE data. Do not change flags, connect live providers, issue messages/payments, or alter production.

1. Record the displayed version/build, `/api/health` staging mode and owner-only `/api/ai/status` evidence (redact secrets and client details).
2. Run each of the 26 fixture inputs through the real Staff AI Workspace, selecting the fixture language. Capture expected vs observed, PASS/FAIL, request/response IDs, actual model, status, input/cache/output tokens, latency and cost estimate. Respect the four/minute user limit. Do not claim mocked tests as live passes.
3. On one fictional client/service, record the source message, generate with known context, edit/copy, authorize a draft and summary save, retry the same save, and confirm exactly one message per identical save on the correct timeline with Not sent. Confirm original input unchanged and no outbound/job/payment/QBO records.
4. Verify a second client's context is absent. Test separate Owner/Manager/Sales/Crew sessions and anonymous access. Office access is broad by current approved model; crew AI is forbidden. Do not invent an assignment-only rule.
5. Retest phone/tablet/desktop, keyboard, loading, failed/paused requests and switching reply language without changing UI language. Verify quote templates, Inbox, staff Chat, Gmail and QBO displays still work. Live integrations stay disabled; display tests are not provider end-to-end tests.
6. Safely prove emergency pause, application cost/token/rate rejection and failed-provider behavior using controlled staging test configuration/fixtures. Do not exhaust paid credits or alter live account billing. Report controlled fault-injection separately from real provider outcomes.

Recommendation remains CONDITIONAL / NOT approved for production until the new-build live evidence, client-save walkthrough and remaining critical tests pass. Owner approval is required for any production rollout.
