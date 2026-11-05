# CrownOps — Pilot Acceptance Matrix

**As of 2026-11-05.** Branch `claude/vigilant-carson-1eevyy`, local commits only; nothing has been pushed.

## Classification: NOT PILOT-READY

Local synthetic testing is strong. However:
- **No hosted installation exists.**
- **No real provider (Gmail, QuickBooks, Square) has been exercised.**
- The **Core v1 pilot lockfiles still pin vulnerable dependencies.**

Passing local suites does not make CrownOps pilot-ready.

## Key to the tables

**Status values:**

| Status | Meaning |
|---|---|
| verified | Evidence exists and was checked in this repository or session |
| reported | Stated in a document or earlier handoff but not re-checked |
| partial | Some of the gate is evidenced |
| blocked | Cannot be tested without an approval, account or environment that does not exist |
| failed | Tested and does not meet the gate |

**Evidence class** (prefix in the Evidence column):

| Prefix | Meaning |
|---|---|
| **[L]** | Local synthetic: this machine, fictional data |
| **[H]** | Hosted: a real host, TLS and volume |
| **[P]** | Real provider sandbox or live account |

**Owner:**
- **J** = Jonathan
- **Dev** = development agent or engineer
- **Ops** = whoever operates the installation
- **Counsel** = lawyer

## A. Product and data correctness

| Gate | Status | Evidence | Risk | Owner | Next action | Approval |
|---|---|---|---|---|---|---|
| Full regression suite | verified | [L] 746 Python tests in 70 isolated modules, 0 failures; 20 Node; JS syntax; 12/12 browser suites (phone, tablet, desktop) | Low | Dev | Run on CI once the code is somewhere CI can see it | — |
| Crown pricing unchanged | verified | [L] `fixtures/crown_pricing_golden.json` generated from pre-COM-4 code | Low | Dev | — | — |
| New company fails closed until configured | verified | [L] `test_installation_security.py`, `test_company_profile.py` | Low | Dev | — | — |
| Two independent companies, setup to paid invoice | verified | [L] `test_documented_provisioning.py`, `test_independent_installations.py` | Low | Dev | Repeat on the hosted pilot (H-gates) | — |
| White-label: no Crown identity in pages, assets or API errors | verified | [L] `test_white_label.py` (12), `test_white_label_messages.py` (AST scan) | Low | Dev | — | — |
| Business time zone per company | verified | [L] `test_business_zone.py` (Toronto, Vancouver, Halifax, DST fold) | Low | Dev | — | — |
| Quebec French templates and terms are correct | blocked | [L] Owner-written by design; the agent cannot proofread Quebec French | Medium | J | A fluent reviewer checks every French client-facing text for the pilot company | J |

## B. Security

| Gate | Status | Evidence | Risk | Owner | Next action | Approval |
|---|---|---|---|---|---|---|
| Application dependencies free of known advisories | verified | [L] `pip-audit` 2026-11-05: 70 advisories → 0 after upgrade (`b9e8776`) | Low | Dev | Re-audit before each release | — |
| Core v1 pilot lockfiles free of known advisories | **failed** | [L] `tools/pilot/*.lock` still pin starlette 0.50.0, Pillow 12.3.0, cryptography 46.0.4, python-multipart 0.0.29, requests 2.32.5 | **High** | Dev / J | Re-pin, regenerate hashes, update `g5/CONTEXT-PINS.json`, rerun G5 evidence | **J must approve G5 re-pin** |
| Internal security review | partial | [L] `SECURITY_RELIABILITY_REVIEW_2026-11-05.md`: 12 findings, 5 fixed | Medium | Dev | Fix SR-11 (Unknown resolution) | — |
| External penetration test | blocked | None performed | Medium (pilot) / High (general sale) | J | Commission one before general sale | J (cost) |
| Webhooks fail closed and reject bad signatures | verified | [L] code review of 4 endpoints; `test_square_webhook.py` (6) | Low | Dev | Repeat SQ-6 against the live sandbox | — |
| TLS, HSTS and proxy headers on the real host | blocked | [L] headers verified in a production-mode local run; [H] none | Medium | Ops | Preflight P1, P2 | J (hosting) |
| Owner MFA on a real device | blocked | [L] TOTP verified in tests | Medium | J | Preflight P4 | — |
| Repository confidentiality | **failed** | Repository is public; visibility change not approved | **High** (IP, commercial) | J | Decide visibility; counsel C1 | **J** |

## C. Backups, restore and monitoring

| Gate | Status | Evidence | Risk | Owner | Next action | Approval |
|---|---|---|---|---|---|---|
| Consistent backup with restore test | verified | [L] `test_backup_drill.py` (5): 37 KB, backup 0.028 s, restore 0.015 s | Low | Dev | — | — |
| Backups encrypted | verified (opt-in) | [L] `test_backup_encryption.py` (9); rehearsal: archive has 0 plaintext matches | Low | Ops | Set `CROWNOPS_BACKUP_KEY` on the pilot; keep an offline copy | J (key custody) |
| Scheduled backup on the host | partial | [L] in-process `CROWNOPS_BACKUP_HOUR`, tested through the app lifespan; [H] none | Medium | Ops | Preflight P6 | — |
| Offsite copy | partial | [L] operator command hook tested with a local "offsite" folder; [H/P] no storage account | **High** until done | Ops / J | Choose storage (preflight H3); preflight P6 | **J (purchase, region)** |
| Restore preserves accounts and revokes sessions | verified | [L] owner signs in with password and MFA after restore; old cookie gets 401 | Low | Dev | Preflight P7 on the host | — |
| Wrong application key detected | verified | [L] `secret_key_mismatch` critical; `test_backup_drill.py::test_5` | Low | Dev | Preflight P8 | — |
| Measured recovery time on the host | blocked | [L] restore 0.015 s on the drill company; [H] none | Medium | Ops | Preflight P7, timed | — |
| `ops-check` alerts reach a person | blocked | [L] exit codes tested; [H] no alert channel | Medium | Ops | Preflight P9 | J (tool) |

## D. Hosting and operations

| Gate | Status | Evidence | Risk | Owner | Next action | Approval |
|---|---|---|---|---|---|---|
| Reproducible container build | blocked | No Docker daemon in this environment | Medium | Ops | First hosted build from a pinned SHA | J (hosting) |
| Production-mode provisioning steps | verified | [L] full rehearsal (`HOSTED_PILOT_PREFLIGHT.md` §6) | Low | Dev | — | — |
| Disposable hosted installation | blocked | Not purchased or deployed (not authorized) | **High** | J / Ops | Approve host and region (H1, H2), then run preflight P1–P12 | **J** |
| Upgrade and rollback on the host | blocked | [L] migrations tested; newer-schema refusal tested | Medium | Ops | Preflight P10 | — |
| Hosting region acceptable for a Quebec company | blocked | Railway lists no Canadian region (docs, 2026-11-05) | **High** (legal) | J / Counsel | Counsel C5; decide H1 | **J** |
| Operating cost known | partial | [L] dated list prices give a floor of about $16–$30/month; labour unmeasured | Medium | J | Log operator and support hours during the pilot | — |

## E. Provider integrations

| Gate | Status | Evidence | Risk | Owner | Next action | Approval |
|---|---|---|---|---|---|---|
| Gmail send, read and reconcile | blocked | [L] 5 mocked suites; [P] none | High | J / Ops | Create OAuth app and test mailbox; run GM-1 to GM-10 | **J (accounts, activation)** |
| QuickBooks sandbox | blocked | [L] 4 mocked suites; [P] none | High | J / Ops | Intuit sandbox (CAD, GST/QST); run QB-1 to QB-10 | **J** |
| Square sandbox | blocked | [L] webhook (6) and deposits (8) mocked; [P] none | High | J / Ops | Square sandbox app and webhook; run SQ-1 to SQ-12 | **J** |
| Unknown outcomes resolvable in the app | **failed** | [L] no route for Unknown Square links, Unknown or Mismatch QuickBooks exports, or Unknown non-invoice emails (SR-11) | Medium | Dev | Build owner-only reconcile actions with evidence | — |
| Non-Quebec or non-CAD companies | **failed** (scope) | [L] CAD and GST/QST hard-coded (SR-12) | Medium | J | Restrict the pilot to a Quebec, CAD company, or fund the generalisation | J |
| Live email, accounting or payment | blocked | Not authorized | High | J | Only after the sandbox gates pass | **J** |

## F. Commercial and legal

| Gate | Status | Evidence | Risk | Owner | Next action | Approval |
|---|---|---|---|---|---|---|
| Licensor entity and IP ownership | blocked | `PROPRIETARY_NOTICE.md` names Crown Movers; ownership unconfirmed | **High** | J / Counsel | C1, C2 | J |
| Customer agreement (licence, data, liability) | blocked | Not drafted | **High** | Counsel | C3 | J |
| Privacy program (Law 25, PIPEDA) | blocked | Not reviewed | **High** | Counsel / J | C4, C5, C11 | J |
| Pilot fee and terms | blocked | No prices set; no market research done | Medium | J | B2, B3 | J |
| Data return on cancellation | partial | [L] backup archive plus restore guide; no CSV export | Medium | Dev | Build a CSV export | — |
| Read-only suspension mode | failed (missing) | Not implemented | Low (pilot) | Dev | Build when licensing needs it | — |

## What "pilot-ready" requires

1. **J approvals:** hosting region and provider; offsite storage; Core v1 lockfile re-pin; provider sandbox accounts; a decision on repository visibility.
2. **Preflight:** P1–P12 pass on the disposable hosted installation.
3. **Sandboxes:** GM, QB and SQ sandbox tests pass, or are explicitly waived in writing for the pilot.
4. **Unknown outcomes:** SR-11 Unknown-outcome resolution is built.
5. **Legal:** counsel signs off on C1, C3, C4 and C5 before any real customer data is loaded.
