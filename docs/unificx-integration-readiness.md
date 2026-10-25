# UnifiCX connection — evidence and required contract

October 25, 2026. This is a connection readiness record, not a live connector.

## Verified scope

CrownOps has shared Inbox calls, client/service matching, call outcome records,
claimable follow-ups and a client history. Gmail intake can import missed-call and
voicemail notices. This does not prove a direct, complete UnifiCX call feed.

The provider advertises CRM/API integration, call activity, reports and voicemail:
https://www.unificx.com/
https://www.unificx.com/ecosystem
No account-specific API specification, webhook signature contract or credentials
have been verified. Do not infer that one PBX model's API applies to this account.

## Information needed from the actual UnifiCX account / support

- Exact tenant/admin portal and PBX product/version; API access included in this plan?
- Official CDR/call-history API and event subscription documentation, including auth,
  least-privilege scopes, pagination/cursors, rate limits and history retention.
- Documented webhook/event authentication, signature verification, replay window,
  retries, event ordering and stable call/event identifiers.
- One redacted official payload per inbound answered, inbound unanswered, voicemail
  and outbound call, plus transfer/queue examples and event updates if supported.
- Fields for calling/called number, direction, extension/agent, queue, start/answer/end
  timestamps, ring/talk durations and final disposition.
- Recording/voicemail access controls, expiry, retention and permitted test environment.

Enter any credentials directly in protected backend configuration. Never put them
in chat, screenshots, repository files or client-visible URLs. Do not enable outbound
calling, recording or dial-plan changes merely to obtain a call-history feed.

## Required behavior after the contract is verified

Reuse existing calls, Inbox and client history; do not create a second CRM.
Normalize phone numbers and match only a unique authorized client/service. Shared
numbers, hidden caller IDs and multiple plausible services require manual linking.
Retain unmatched events for review instead of inventing a contact.

Store one logical call, with provider-supported event IDs for idempotency. A final
answered event updates the same call; transfer legs or an agent's missed ring must
not falsely become a separate abandoned customer call. Define these semantics from
the provider's documentation and fixtures first.

Answered calls remain visible with an Answered badge. Missed calls/voicemails create
or update the appropriate claimable follow-up. A completed callback must not be
reopened by a stale event. An explicitly scheduled follow-up has its own due status.

Display occurred time in America/Toronto; store timezone-aware timestamps. Show
inbound/outbound, answered/missed/voicemail, agent, durations and last sync only when
supported by actual source data. Do not fabricate recordings, transcripts or live
call counts. Keep staff call notes/recordings private from client portals by default.

Gmail notices and a future direct feed require cross-source duplicate protection
using verified identifiers; phone number and timestamp alone are not conclusive.

## Acceptance before activation

- Invalid signature/auth, replay, oversized payload and cross-tenant access denied.
- Answered, missed, voicemail, outbound and transferred calls verified against source.
- Duplicate and out-of-order events do not duplicate calls/tasks or downgrade status.
- Exact client matching and ambiguous/unmatched review work; no cross-client leakage.
- Dashboard counts reconcile to a defined provider reporting period/timezone.
- Last success, stale feed, cursor recovery and failure status are visible.
- No provider writes, calls, emails, accounting or booking actions occur during import.

## Independent Muse handoff — core workflow first

Use only https://crownops-quote-acceptance-staging.up.railway.app/ . Record displayed
build and role. Do not deploy or edit source while the coding workstream owns it.

1. Run the existing Check QuickBooks connection action. Record the actual read-only
   API result: environment, OAuth realm/company ID, provider company name and country
   if exposed. Required sandbox realm is 9341458310238189. A saved grant alone fails
   this check. Do not reconnect if an existing grant works.
2. Open Review tax / item setup. Read actual service items, tax codes and accounts;
   record IDs/names and missing mappings. Do not choose/save accounting mappings,
   enable writes, create estimates or send messages.
3. Record Gmail as disconnected until authorized and tested. An empty Gmail exceptions
   panel does not prove intake works. Retrieve only the four original TEST form
   fixtures from an already authorized source if available; do not run a full mailbox
   sync, import real customers or alter TEST records.
4. Check the UnifiCX account's documented API/CRM settings through an authorized
   secure session. Obtain the non-secret contract above; do not paste API credentials
   into reports or switch on production delivery.
5. Report PASS / FAIL / BLOCKED per action, exact build, observed result and smallest
   owner action. No claims based on generic Connected labels or an empty dashboard.

Financial writes, test-email delivery and actual quote-to-booking acceptance remain
separately gated. Keep the main milestone ahead of optional phone analytics work.
