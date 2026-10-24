# CrownOps AI audit and delivery boundary

## Existing verified implementation

`app/ai.py` already calls Responses `/v1/responses` with strict JSON Schema, `store:false`, a configured model and a 2200 output-token limit. `POST /api/ai/analyze` is authenticated office staff only, requires explicit processing authorization and a stable request identifier. It selects a narrow client context, reserves monthly tokens atomically, limits each user to four calls/minute and saves suggestions in `ai_runs`. Repeat completed requests return the saved result. Ambiguous/failed calls are not automatically retried. No customer chat session, autonomous function executor, approved knowledge base or AI lead-saving flow exists. Team Chat and the client Inbox are separate components.

The current configured production model/key and actual provider functionality have not been certified. No live OpenAI call was made during this change. No API-key provisioning capability is available in this tool set; tests use mocks and fake credentials only.

## This patch

- `CROWNOPS_AI_ASSIST_ENABLED=1` is now required before analyze can call a provider; absent means paused. Existing external-action and credential gates also apply.
- English/French input selection is validated; draft prompt explicitly bars invented sender identity, payment/provider status, prices and operational facts.
- Quote pricing context defaults off; `CROWNOPS_AI_APPROVED_PRICING=1` must remain unset until the canonical rates/fee are approved and the calculator-to-accounting tests pass.
- The monthly token cap remains, alongside a conservative USD reservation cap. Both `AI_MAX_USD_PER_MILLION_TOKENS` (reviewed upper bound across input/output rates for the exact configured model) and `AI_MONTHLY_BUDGET_USD_CENTS` are required and must be positive. One ceiling applies to all reserved tokens, including cached tokens, so savings are not assumed. Reservations are retained after completion/failure. This sacrifices utilization to avoid blind retries or understated spend. It is not provider billing reconciliation. Existing zero-cost-provenance runs block further use pending owner reconciliation.
- `GET /api/ai/status` reports pause/configuration/model/mode; only owners receive USD budget/reservation data. A visible spend meter remains to be built; this endpoint is its backend source.
- Nothing sends client replies, saves extracted fields, changes bookings, calls QuickBooks or charges money. No unrestricted model tools are exposed.

## Architecture and exact integration work remaining

Keep the existing Responses adapter and local `ai_runs` audit. Add authenticated, server-owned conversation sessions bound to staff identity or verified portal lead scope; never use a model-supplied client ID as authorization. Add owner-reviewed versioned EN/FR knowledge records; only retrieve relevant approved excerpts. Source text, email and retrieved text remain untrusted data.

Use explicit backend tool contracts, validated independently of the model:

| Function | Inputs | Output / authority |
|---|---|---|
| approved service lookup | approved knowledge key, language | approved text + version; no database search tool |
| proposed intake | session-bound scope, explicit fields | validated proposal + missing/conflicting fields; no write |
| commit intake | proposal ID, user confirmation, idempotency key | existing client/service IDs via existing lead/dedupe paths; confirmation and authorization rechecked server-side |
| estimate preview | authorized service ID, reviewed inputs, approved pricing version | deterministic calculator result or missing/blocked fields; no model arithmetic |
| availability read | date/window and authorized service scope | permitted availability summary; no employee private data or resource promise |
| callback request | authorized scope, client consent, preferred window, idempotency key | existing Inbox task ID and verified creation status |
| customer lookup | authenticated portal session only | allowlisted fields for that exact client/service; no arbitrary email/name lookup |

Do not wire these write tools before the broken form/dedupe path is fixed and tested. Current staff drafts can be copied into existing manual inquiry/quote screens, with review; that is not automatic lead saving.

For QuickBooks, keep accounting credentials inside `providers.py`, item/tax/customer mappings and reviewed delivery services. The AI can request a proposal or display a server-returned state; staff must approve the actual estimate/export/email. The current QuickBooks-native delivery is distinct from the requested future Gmail-based sandbox delivery. Never assert that the latter exists. Surface three independent states: accounting estimate ID/status; email provider status plus separate inbox receipt proof; acceptance sync and tentative job ID. Failures remain errors/unknown, never success prose.

Before any AI booking mutation exists, add a durable human-verification-call record linked to the service and reviewed quote version. Enforce it in a single shared confirmation gate, including `sales_crm_v6.confirm_job` and every other confirmation path, plus atomic availability recheck. The current implementation does not add or certify this gate and gives AI no booking mutation capability.

## User journey target, not implemented end to end

Client chooses EN/FR and consents to the relevant processing → answers necessary missing questions → reviews a structured intake proposal → authorizes saving → existing backend creates/matches client + service without duplicates → deterministic estimate is prepared only from approved rates → sales reviews → existing accounting/email workflow sends → provider/client acceptance is reconciled → one tentative job → verified deposit, availability and logged human verification call → authorized final confirmation. Ambiguity goes to a human with a concise summary and source references. No raw model text is an accounting or booking event.

## Evaluation evidence

`tests/fixtures/ai_scenarios.json` defines 22 scenarios with expected behavior and live-model status NOT RUN. `tests/test_ai_controls.py` executes mocked API isolation contracts for those inputs plus six controls (pause, missing cost settings, exhausted budget, dedupe, provider timeout and crew denial). Passing these contracts proves isolation/draft-only responses under mocks, NOT bilingual quality, factual grounding or prompt-injection resistance of a live model. Production requires actual candidate-model evaluation and human grading per scenario. `ai-evaluation-results.md` records this distinction.

## Muse build/test order

1. Verify rc6 baseline/permissions; keep AI default off and providers sandbox/test.
2. Run the deterministic tests and finish the original intake/parser fixtures; do not replace them with AI extraction.
3. Add approved knowledge + scoped conversation persistence and consent, then staff-only EN/FR UI with takeover/pause and explicit draft labels.
4. Wire only allowlisted read/proposal tools. Add confirmation-bound lead/callback commits using the existing dedupe/API paths; no competing CRM.
5. Implement and test the human call gate before booking tools; verify all old confirmation paths and role denials.
6. Complete the separately requested Gmail/QuickBooks sandbox proof. Check totals to the cent, external IDs, unknown results, duplicate operations and no real charges.
7. Run all 22 scenarios against the pinned candidate model; record actual outputs, grader, PASS/FAIL/NOT RUN and costs without unnecessary PII. Include EN/FR injection and cross-client access tests.
8. Finish spend meter, latency monitoring, retention/deletion procedure, alerts and kill switch. Owner reviews approved rollout cohort and explicitly approves activation; no model upgrades without re-evaluation.

## Owner inputs and launch acceptance

Required: approved canonical rates/fee; approved company content and deposit instructions; model snapshot with verified access and price ceiling; monthly USD budget; approved staff pilot cohort; processing/retention policy and client consent copy; secure server API key; sandbox integrations; owner confirmation of inbox receipt. Still missing: four exact intake exception bodies. No passwords or keys in chat.

Launch acceptance: live model quality passes all required scenarios; no unauthorized client/owner-financial retrieval; spend/rate limits and pause work; timeout/retry never duplicates provider actions; human takeover works; sender and client identity proven; no invented availability, price, payment or sync status; end-to-end sandbox record/e-mail/acceptance/deposit lifecycle proven; actual-phone usability checked; owner explicitly approves activation. This patch does not meet that full launch checklist.
