# CrownOps recovery implementation proposal
Date: 2026-10-31
Status: DESIGN FOR REVIEW ONLY — exporter not implemented or qualified; Approval B/C execution BLOCKED.

## Scope and evidence
This proposal follows Approval B preflight v2; it specifies the smallest implementation rather than reopening accepted QA. No backup, key generation, download, transfer, temporary restoration, deployment, or provider operation was performed for this proposal.

Source baseline: 24d68189095e3ba5f9e4527f09b3c6e52332d323, parent accepted intake 758d74a. Source inspection is not live-service verification. PR #93 and the 7/7 role boundary results remain accepted.

Inspected app/backups.py (blob b09bb85b3d97a0152677106c6e2147dd4b18eb32) and app/production.py (blob eebc5b5b3796847c5a19de4a7461a7c8edcc49ae):
- Existing SQLite capture uses the backup API and integrity_check, rather than a raw live-database copy.
- Existing export is an unencrypted ZIP. Its manual endpoint performs temporary restoration, updates backup status/audit, and permits download. It is NOT the proposed secure export path.
- General upload references cover attachments and fleet_documents. Employee licence files use a separate employee_uploads directory and need independent coverage.
- The existing recovery helper clears six capability tables but omits newer invoice/participation access.
- Document approvals have bearer token hashes. Their public GET returns content even for closed/expired approvals. Changing expiry alone therefore does not revoke document disclosure.

Actual resolved paths, file inventory, headroom, runtime topology, first successful Railway snapshot, and offsite destination have NOT been independently established by this proposal.

## Proposed implementation: operator-only utility
Add a dedicated recovery_bundle package and disposable tests. No new public backup endpoint, existing UI change, or automatic job in this milestone.

| Module | Responsibility |
|---|---|
| inventory.py | Resolve approved service identity, database and all upload roots; enumerate references and regular files; count bytes; reject escapes/symlinks and missing required files. |
| capture.py | Create a consistent SQLite copy using its backup API under a qualified, quiesced capture window; collect private files without changing source records. |
| manifest.py | Versioned inventory, file SHA-256 and bytes, schema/code versions, cutoff interval, audit watermark and explicit exclusions. |
| encrypt.py | Stream archive through a pinned, qualified age public-recipient binary; publish ciphertext exclusively and atomically. |
| verify.py | Independent ciphertext readback and, only under separate authorization, isolated decryption and complete archive validation. |
| restore.py / revoke.py | Approval C only: pristine extraction, separate working copy, capability revocation and application recovery evidence. |
| cli.py | Separate plan, capture, verify and restore operations; explicit scope manifests and single-use operation journal. Default mode performs no capture. |

No replacement ingestion, finance, acceptance or booking implementation belongs here.

## Source identity and data scope
Approved target for future execution: dynamic-joy / Staging / crownops-quote-acceptance / crownops-quote-acceptance-data mounted at /data.
Recorded IDs:
- Project bfe6eb95-e8f3-45ef-8fad-afe3aadcef3f
- Environment 53900324-c678-40b0-91f0-b6560316a5d2
- Service 5de22c3a-ea7e-4fa6-bc96-2f9472431c7c
- Volume 88243cc6-2ec2-4a3b-8611-40b4191cdd84

These must be revalidated once before execution. Expected database /data/crownops-quote-acceptance.sqlite3 and general uploads /data/uploads require runtime confirmation. Employee uploads resolve relative to the database parent. Overrides must be resolved before authorization; guesses are not execution inputs.

Include the complete database and all files in each approved private-upload root, including unreferenced regular files unless an explicit reviewed exclusion applies. Map every database file reference to its archived path. Exclude backup archives, caches, logs, temporary files, WAL/SHM sidecars after logical database capture, environment files, executable secrets, and application/backup decryption keys. Do not silently drop an unknown directory: classify it before approval.

Encrypted provider credentials and MFA ciphertext already stored inside the database remain encrypted historical database content. No key is bundled, and no provider credential is decrypted for export. Real staging data is sensitive even when the main fixture is fictional.

## Consistency and WAL
SQLite backup API provides a database snapshot; it does not freeze filesystem uploads. A backup flock only serializes exporters.

Require a qualified maintenance window that drains existing mutations and prevents all web, worker, upload and deletion writes throughout database/file capture. The exact operator access, drain/stop and restart mechanism remains BLOCKED until the execution host is selected. Do not assume a read-only mount prevents another process writing the original volume; do not freeze a process mid-transaction.

Use SQLite backup API into a new private destination, with busy limits and explicit failure handling. Never copy a live database plus guessed WAL files; never change journal mode or force a checkpoint. Run integrity_check and foreign_key_check on the captured copy. Record cutoff start/end and audit watermark, not an invented instantaneous filesystem timestamp. Missing references, file changes during capture, failed checks or incomplete inventory abort publication.

Primary references: https://www.sqlite.org/backup.html and https://www.sqlite.org/c3ref/backup_finish.html

## Archive, encryption and publication
Proposed inner format v3:
database/database.sqlite3; uploads/general/...; uploads/employee/...; manifest.json.
Manifest includes each file path, byte count, SHA-256, reference mappings, totals, capture interval, source/schema/application versions, tool identities and exclusions. No plaintext secrets or credential hashes appear in reports.

Use upstream age recipient encryption, with the archive decryption identity kept off the source host. Pin the version and binary checksum before qualification; none has been selected or generated here. Encryption is not permission to export.

Private temporary database/files may exist during capture and must be disclosed in B authorization. Prefer encrypted scratch storage; a mode-0700 directory alone is not encryption at rest. Capacity and cleanup must be qualified. Pipe archive bytes into encryption before any download/offsite transfer. Publish only completed ciphertext through an exclusive temporary descriptor and atomic no-overwrite operation. Do not use an output option that can replace a known-good archive.

Read back the complete ciphertext and compare SHA-256/bytes. This proves transfer integrity only. A separately authorized isolated decryption must verify authenticated decryption, manifest/member hashes, database integrity and required uploads before declaring a verified portable copy. Neither step proves full application recovery.

Reference: https://github.com/FiloSottile/age

## Destination, custody, costs and monitoring
Recommended destination: owner-controlled independent versioned object storage with restricted backup-writer/readback permissions, retention protection where supported, and separate recovery-reader credentials. A restricted Drive folder is an alternative, but does not imply immutable retention. Exact provider/account/bucket or folder, region, access principals and retention remain OWNER DECISIONS. No destination exists by virtue of this proposal.

The owner appoints a primary recovery custodian, backup operator and escalation contact. Jonathan and a separately nominated backup person are proposals, not appointments. The owner retains the age identity in a password manager or offline custody outside both archive storage and source server; require a separately stored recovery copy and custody verification. The original server encryption key is a different recovery requirement and needs separate secure custody to recover encrypted application data. Losing either necessary key can prevent full recovery.

Costs depend on measured ciphertext size, retained versions, storage, retrieval, egress and operator runtime. No zero-cost assumption or unverified price quote. Obtain a bounded estimate after inventory and destination selection.

Proposed objectives for approval: synthetic staging RPO 24h / RTO 4h; operational CRM RPO 1h / RTO 4h requires a correspondingly stronger backup schedule and qualification. Existing Railway schedule retention is not portable-archive retention. Define archive retention, maximum backup age, failure owner and alert destination before automation. No alert messages or monitoring resources are created by this proposal.

## Approval C: disposable application recovery
Archive identity, checksum, size, capture cutoff and measured restore time remain PENDING B.

Never restore over staging. Verify an empty disposable destination and OS-enforced outbound-network denial BEFORE opening recovered data. Disable background workers, provider routes, legacy acceptance, booking, email and payments. No real provider credentials in process environment. Prove network isolation independently; application mocks alone are insufficient.

Retain a pristine verified extraction and use a separate working copy. On the working copy, revoke sessions, invitations, portal sessions/invites, account resets, OAuth state, invoice access sessions/invites and participation access. Complete a schema-and-handler capability inventory before claiming completeness. Preserve users, IDs, roles, active flags, password hashes, MFA and business/audit history.

For document_approvals, expiry alone is inadequate. Proposed revocation replaces bearer token hashes in the disposable working copy with unrecoverable per-row values generated without storing raw tokens. Preserve document/status/signature/history fields. This needs separately authorized implementation and negative tests. Do not delete historical approval rows. Security-field differences are recorded as explicit restore exceptions, without publishing hashes.

Verify quote revisions, frozen financial snapshots, audit and credential-reissue history, terminal QuickBooks send locks, private uploads and read-only application usability. Estimate 185's consumed send state must remain terminal, with no replay. Use disposable known-token tests to demonstrate old-link rejection; actual source grants with unavailable plaintext tokens cannot be reported as individually replay-tested.

Evidence: archive ID/time/checksum, restored counts and selected immutable fingerprints, revocation inventory, app startup/read results, elapsed time, cutoff/exclusions, denied egress tests and zero provider traffic during the drill. Archive validation is not full recovery. Network-denied attempts must be reported, not hidden under a zero-success count.

## Qualification and failure handling
Planned tests, NOT executed:
- WAL-active capture, busy database, concurrent filesystem mutation and quiescence failure.
- Complete general/employee uploads, missing references, symlink/path traversal, duplicates and size limits.
- Corrupted/truncated ciphertext, wrong key, manifest mismatch, missing uploads.
- Partial export/transfer, full disk, permission denial, interrupted operation and uncertain publication.
- Exclusive publication and reconciliation without overwrite or duplicate good copies.
- Complete restored-copy revocation, replay rejection and preserved business history.
- Network isolation, no workers/provider side effects, terminal send records unchanged.

Test implementation and actual disposable archives/keys require narrow authorization. Existing tests of the old plaintext helper do not qualify this new utility. No such tests ran for this document.

On uncertainty, lock the operation, inspect receipt/hash independently, quarantine incomplete files and preserve the last good copy. Do not retry blindly. Cleanup only operation-owned temporary paths after the result is reconciled. Secure deletion on SSD/snapshots is not guaranteed; encrypted scratch and key lifecycle matter. Never remove source records or prior backups as rollback.

## Decisions and separate authorizations
1. Review/authorize implementation plus disposable crypto/archive tests; no real service data.
2. Select execution host, capture/quiescence mechanism, storage destination, principals, custodians, key custody, budgets, RPO/RTO and retention.
3. Review actual new source/tests/tool pins and a completed runtime inventory.
4. Approval B execution must name every maintenance operation, temporary file, key-generation action, ciphertext transfer, isolated integrity extraction, audit/status write and cleanup. No implicit use of the existing manual backup endpoint.
5. Approval C separately names the immutable archive, disposable restore host, network controls, access revocations, application checks and cleanup.

Muse's next recovery review should examine the implemented package and actual test evidence, not this design as proof of execution. All implementation, B export and C drill gates remain BLOCKED until their inputs and authorizations exist.

## Parallel QA handoff
Adapter 330391079eb7df9b2f3f7db70791ae4c73b9e9f0 pins core 758d74a. Current-mail patch 24d6818 is a separate branch; an adapter PASS does not accept that patch. Review the adapter with documented mock tests only; no Docker, server or deployment. The new mail fixture's lower sections are modeled, not verified full MIME. Old website mapping and hosted intake are deferred for the email-first internal pilot.

Owner business inputs still required: approved rate book, transport calculation, minimum rules, tax treatment and terms. These are independent of technical QA. Future customer journey: verified delivery, acceptance-only, separately authorized tentative booking, then scheduling/dispatch. No production or provider action is authorized here.
