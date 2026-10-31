# Synthetic intake adapter — implementation and preflight v2

Date: October 31, 2026. Scope: local source development and mocked, in-process qualification ONLY. No listening server, Docker/container/image build, endpoint environment, deployment, mailbox read or provider request occurred. The accepted core milestone (758d74a, 62/62 tests) remains closed and was not rerun.

## Source identity and implementation

- Parent / unchanged core commit: 758d74a3a4ae07ced4fc5b873f4345d63b97ee95.
- Core content build: b377d4b1342981c5f5892950; computed, not served.
- New adapter commit and fingerprint: see SOURCE.json in the delivered package. No GitHub CI result or hosted deployment is claimed.
- `synthetic_intake/core_manifest.json` pins all 188 accepted app/config/requirements files. Factory admission verifies these bytes and the core build before initializing a database. No file under app/ or the accepted parser/matching code changed.
- `adapter.py` exposes only GET /health, /identity, /test-status, /test-evidence and POST /synthetic/intake/replay. No docs/OpenAPI, core app routes, staff authentication, live intake, provider, recovery, acceptance, booking, invoice, payment or upload route is mounted.
- It constructs the actual core app against the explicit new DB only to obtain its actual new_lead callback. The core lifespan is NEVER entered. The ingestion call is actual sync_gmail_readonly with a frozen synthetic feed; no successful contact/date values or replacement writer are injected.
- Frozen fixture and expected-field JSON copies preserve all four accepted bodies and hashes. The HTTP manifest may name only scenario `oct24-rendered-v1` plus the exact JSON fixture-set SHA-256. No arbitrary records, contact fields, URLs, uploads or source-body overrides are accepted.
- Private synthetic capability required for evidence/status and replay; 30-minute expiry; no cookie/origin authorization or Master impersonation. Mock qualification uses an explicitly fake capability, never a real credential. Runtime serving later requires a private owner-only capability file, never a command-line secret.

## Idempotency, failure handling and evidence

One initial batch request and exactly one identical retry are permitted. A thread lock prevents a second concurrent claim. An atomic fsynced journal and append-only adapter audit record claim/completion. Core message IDs are OCT24-EN-FORM-RC5, OCT24-FR-FORM-RC5, OCT24-EN-CALC and OCT24-FR-CALC-RC5; core event keys prefix these with gmail:.

Any partial result, exception, mismatch or blocked outgoing attempt locks the run as AMBIGUOUS. An interrupted process leaves FIRST_RUNNING/RETRY_RUNNING and cannot be replayed. The factory refuses every nonempty run directory: restarting or recreating an existing run never resets the slot. No reset/delete/force endpoint exists. Evidence stays read-only for reconciliation; failed or unknown batches are not blindly retried. Exact successful retry compares the full business-table snapshot, including core audit rows, without overwriting records. Cursor/settings metadata is separate.

The immutable Gmail-ID behavior remains unchanged: core skips that ID without refetch; it is not a changed-body 409 detector. The adapter prevents arbitrary changed bodies by accepting only a fixed reviewed manifest. These are distinct safeguards.

## Qualification actually executed

Python 3.12.14, disposable /tmp SQLite and ASGI TestClient calls (no listener). One command:

```
QUALIFY_PYTHON=/absolute/path/to/disposable-venv/bin/python sh qualify-synthetic-intake.sh
```

The wrapper clears inherited environment and creates only disposable local qualification/evidence directories. Installing dependencies is a separate preparatory step. It does not run Docker, uvicorn or the accepted 62-test parser suite.

22 new adapter tests PASS. Logs and independent-readable JSON are in dev-reported-evidence/. Tests cover route allowlist, authorization, expiry, browser/cookie rejection, frozen-manifest/hash/size/duplicate-key validation, real ingestion and exact retry, empty pricing, source preservation and 68 field comparisons, denial guards, source hash mismatch, empty-path/credential/environment admission, concurrency claim denial, partial result lock, interruption journal, non-reopenability, no capability leakage and mocked namespace checks.

| Record | Actual qualified count |
| --- | --- |
| client_accounts | 1 |
| client_account_leads / leads | 4 / 4 |
| integration_events / messages | 4 / 4 |
| tasks | 7 |
| intake_drafts | 4, all Needs details / quote_id null |
| audit | 8 |
| quotes / outbound / jobs / assignments / payment links / deposit links / payments / invoices | all 0 |
| real network/provider attempts in successful run | 0 |
| mocked Gmail calls after initial + retry | 8 |

All four drafts explicitly include `approved pricing setup`, because this adapter starts with NO pricing seed. FR form address values remain blank and review-required, while raw conflicting fields and narrative are preserved. Calculator values stay approved=false; inferred language is flagged. No business price, address or missing answer is invented.

Qualification negative tests deliberately invoke guarded fake network operations; they are intercepted before native transport and never contact any provider. Their positive blocked counters are distinct from the successful-run zero-attempt evidence. A run that records even one blocked attempt cannot be reported as zero-outbound PASS.

## Isolation: implemented vs unqualified

Implemented process guards deny requests, urllib/http transports, socket connect/connect_ex/create_connection/sendto/sendmsg, DNS, shell and subprocess launch. No real provider token is fetched. Environment admission rejects provider/platform secrets, production mode, workers, external writes, proxy inheritance and unknown CROWNOPS settings. All original app business routes are absent, not merely hidden. Namespace inventory code reads proc files only and rejects non-loopback interfaces/routes. The serving CLI refuses startup without explicit later-approval markers, a digest-shaped image identity and a private capability file; there is no default serving app.

NOT QUALIFIED: Docker/OS isolation, a real HTTP listener, startup of the serving CLI, Python 3.13 image compatibility, native-image dependency installation, container cleanup and actual image digest. Mocked proc inventories are NOT OS isolation proof. The evidence deliberately says os_isolation_qualified=false. Process monkeypatches do not replace an operating-system network boundary or immutable image provenance.

## Exact proposed environment / immutable-image plan

Proposed execution-host alias: `crownops-intake-qa-01`, an OWNER-APPROVED dedicated Linux Docker host. This is a proposed alias, not an existing or verified machine. Actual owner/host ID and operator access are PENDING. The current local qualification executor has Linux but no Docker CLI and is NOT the deployed-endpoint host.

- Environment: crownops-intake-synthetic-01.
- Container: crownops-intake-endpoint-01; single process, one Uvicorn worker; restart policy no.
- Image tag: crownops-synthetic-intake:<new-adapter-commit>. Tag is NOT the immutable identity. Actual built image digest is PENDING; no image was built.
- Build recipe: Dockerfile.synthetic-intake. Requires an owner-reviewed, already-cached python:3.13-slim@sha256:<actual digest> plus a separately approved, hashed wheelhouse for existing pinned requirements. No floating base and no runtime package installation. No wheelhouse or base image was acquired under this authorization. Dockerfile performs an offline pip install and removes source bytecode caches; no upload/registry push is proposed.
- Build source: exact new adapter commit including unchanged accepted core. Record source/blob manifest, base digest, wheel hashes, build log and final immutable image ID before deployment approval. No source changes or substitute core may be hidden behind the same core UI build.
- Running network: `none`, loopback only; no published ports, bridge, host networking, DNS forwarding, external route or tunnel. Drop all capabilities, nonroot UID/GID10001, no-new-privileges, read-only root, no Docker socket or production mounts. Suggested limits: one CPU, 1GiB RAM, 256MiB test tmpfs; host wall-clock timeout30min.
- Actual proposed URL: http://127.0.0.1:18765 INSIDE that new container only. It is not a public staging URL and is not reachable from Muse's separate cloud browser. Test client must run in that same approved namespace via an authorized operator. If browser-accessible hosting is required, this plan needs review; do not silently add a tunnel or public port.
- Database: new /run/crownops-intake-test/intake.sqlite3 on dedicated empty tmpfs, initialized only when serving is separately authorized. No original data/users/pricing/provider rows, restored records or business account credentials.
- Capability file: separate private ephemeral tmpfs path, outside the empty data directory, UID10001/mode0600. Later creation/removal is part of the separate execution approval, not any existing credential rotation. Do not put the capability in image/environment/logs.
- Environment: clear inheritance; only CROWNOPS_ENV=test, CROWNOPS_ALLOW_EXTERNAL=0, CROWNOPS_RUN_WORKER=0, QBO_ENV=sandbox and the two explicit synthetic serving/image markers. Do not inherit CROWNOPS_DB/UPLOADS or Railway/provider/AI variables. DB path is passed directly by the adapter.

Docker image build and namespace inspection have NOT been executed. The current static image recipe and namespace guard are proposed code for later qualification, not a working deployed service. Standard Railway outbound IP/IPv6 options are not assumed to be a deny-all firewall. No Railway target is used.

## Exact future operations and required evidence — no execution now

A — Owner approval to create an empty isolated runtime: confirm actual dedicated host, cost/resource cap and operator. Create only the named empty container/namespace/tmpfs from a separately reviewed image once available; no clone, credentials, business data, server or requests. Image provisioning/build prerequisites must be explicitly included or held for B. Stop on paid-host changes, insufficient privileges or unexpected mounts.

B — Separate owner approval to build/deploy exact code: identify this new source commit, base digest/wheelhouse and final image digest; build once from reviewed source with no runtime networking, record all hashes; stage only that image/config in the named isolated runtime, no server startup or test request. If base/wheels are absent, stop rather than pulling/installing unexpectedly. Prohibit production/Staff AI/quote-testing staging changes. Image/source digest mismatch is STOP.

C — Separate owner approval to start and execute: inspect host/container NetworkMode=none, no published ports/host mounts/capabilities and read-only root; privately prepare the ephemeral test capability, enter the explicit serving gate and confirm identity/namespace readback. No serving until all identities are approved. Run only fixed batch POST once plus one identical retry; private read-only evidence/status GETs. Expected records are the qualified counts above; four service dates remain Nov09/10/11/12,2026. Require full field/source preservation, explicit pricing/review blockers, zero consequential rows, zero denied/native/provider attempts in this successful run. Wrong routes/malformed/auth failures remain fail-closed; any extra operational fault-injection needs explicit scope. Client is inside the isolated namespace, never the live website/mailbox or quote-testing staging.

Evidence must include actual host/container/image IDs, core+adapter identities, namespace inventory and docker inspect output, method/path/status-only access record, business counts, original-body hashes, field comparisons, replay summaries, operation journal, capability-free audit and outbound counters. Socket-guard evidence and UI inactivity alone cannot prove OS isolation. Do not probe real providers to test a firewall.

If a timeout, partial write, mismatch, outbound attempt or unexpected background process occurs, STOP; no retry/reset. Preserve sanitized local evidence; inspect the new DB read-only. Terminate only this new container/process if needed. Do not delete evidence or touch any old staging service. Successful final cleanup: operator copies sanitized report/evidence through approved host tooling (no app egress), stops/removes only the named runtime/tmpfs and ephemeral capability; no global prune, existing volume deletion, backup, restore or source-data edits. Failed-run evidence remains in the approved host's isolated quarantine until owner review.

## WordPress source evidence and exact next source action

Owner relayed Muse's read-only public inspection on October31: current forms render via WordPress/WS Form; current French form has separate Départ and Destination groups and both have postal fields. DEV did not repeat that public inspection. Current exact form/field/action IDs, notification placeholders, routing, plugin/version and configuration modification timestamp are still NOT PROVIDED. The historical October24 flattened email mapping remains UNKNOWN. Do not infer historic IDs/direction from today's form or populate old conflicts automatically.

See docs/ws-form-readonly-export-request.md and source_mapping_status.json. Administrator help is prepared, not sent externally. No authenticated WordPress access or source export was supplied, and no mailbox access was requested. Receiving source configuration is independent of mocked adapter acceptance; the addresses stay blank until evidence resolves them.

## Coordinated remaining launch gates (status only, no broad audit)

1. Recovery: existing Approval B v2 documents the encrypted portable-copy design; secure exporter and encryption qualification, exact runtime inventory/destination/key custody remain blocked. Approval C remains archive-dependent. No backup/key/transfer/restore action was performed by this work.
2. Approved pricing/terms: owner canonical pricebook and required rounding/tax/terms checks before real quoting. Historical calculator prices remain unapproved.
3. Delivery: actual recipient receipt unresolved for QuickBooks Estimate185; its send slot must remain consumed. Never resend it. Any new controlled delivery requires separate exact approval.
4. Acceptance: must record acceptance only. Core legacy automatic job/deposit handler is unchanged and must remain unreachable/disabled; future correction/test separate from this adapter.
5. Tentative booking: separate authorized staff action, exactly-once mapping/idempotency, capacity checks, no automatic deposit/payment/crew/truck assignment or final confirmation.
6. Scheduling/dispatch/internal use: operational fixtures/capacity and employee mapping, MFA/recovery readiness and staff scenarios remain separate. Accepted role matrix and PR93 are not reopened. Production launch is not authorized.

## Muse's next independent work

Review source diff and all identities first; verify package checksum, core manifest and unchanged fixture hashes. Execute ONLY `qualify-synthetic-intake.sh` with a disposable venv under /tmp; do not run Docker, build the image, invoke --serve-approved, launch a listener or rerun the old parser suite. Record actual test count/results and read independent adapter-results.json/log/audit. Separate mock-only PASS from unqualified container/HTTP behavior and operational acceptance BLOCKED. Report defects and stop. Actual endpoint environment creation/deployment/execution remain three separate owner decisions.
