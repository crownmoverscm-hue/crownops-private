# Current Gmail quote notification — narrow new coverage

Scope: sanitized synthetic fixture and disposable local tests only. No mailbox, Gmail labels/read state, historical import, server, deployment or real customer record was accessed/modified. Replacement website integration and old form mapping remain deferred. The accepted 758d74a parser milestone and its original four fixture bodies/tests are preserved; the accepted 62-test suite was not rerun.

## Evidence boundary

The owner screenshots establish label `New Quote Request` (already the FEEDS quote mapping), the subject family `New Crown Movers Quote Request - [name] - [date]`, and a Client Contact Info card containing Name / Phone / Email, repeated link text and visible `">` remnants. Only the Move Summary HEADING is visible; its contents/labels and the original raw HTML/MIME are not supplied. No screenshot customer name, address, email or telephone was copied into the fixture or output.

The new fixture is deliberately synthetic. Its contact card follows that visible layout, with TEST Current Layout / current-layout@example.invalid / reserved fictional 5145550140. Its complete summary uses existing canonical labels Moving Date, Pickup Address, Destination Address and Additional Details solely to model test data. These lower-summary labels are NOT verified as the production template's actual labels. The cropped-summary test leaves unseen fields blank and explicitly reviews the subject-derived date. A complete sanitized notification body/HTML is still needed before claiming actual full current-template coverage. Do not access real mail to obtain one under this task.

## Narrow corrections

1. A baseline replay of ONLY the NEW synthetic fixture on accepted core758d74a showed email recovery but phone blank: `_digits` rejects the repeated 20-digit display string. This is distinct from the prior four-fixture concatenated-label failure. No old parser suite was rerun.
2. `legacy_notifications.py` adds one bounded styled-template branch: recognized subject and website-lead heading, customer-only Client Contact Info section before Move Summary, normalization of identical repeated phone/email, existing conflicting-contact protection, explicit modeled summary labels and notes. Missing/conflicting dates remain empty/reviewed; a subject date is used only when a body date is absent, with a review reason. Conflicting multiple body dates never silently fall back to the subject. Office footer fields cannot supply missing customer contacts. No prices or missing addresses are invented. Customer language is explicitly review-required, not inferred from a person's name.
3. `gmail_sync.py` now classifies notification families before extracting contacts in the quote feed. A matching Gmail label alone cannot create a customer: system or unknown subjects remain local Needs review events with normalized body preserved and a classification-review task, not automatically parsed into clients/services. They are not deleted, relabeled, marked read or silently discarded. Known prior EN/FR/calculator and canonical-builder families remain recognized. Subject/body shape recognition is not sender authentication or proof the inquiry is genuine.

## Results

9 NEW tests PASS (2.938s in initial final run; runtime variance expected). The delivered logs capture actual runs. One new synthetic full-summary message produces:

- client_accounts1; client_account_leads1; leads1; integration_events1; incoming messages1; tasks2; intake_drafts1; audit2.
- Name TEST Current Layout; email current-layout@example.invalid; normalized phone5145550140; date2026-11-29; pickup TEST pickup, Montreal; destination TEST destination, Laval; notes TEST ONLY. Two chairs and a table. Access and box count not supplied.
- Parsed = stored for all seven requested fields. Draft Needs details / quote_id null, with approved-pricing, box/access and other explicit blockers.
- Quotes/outbound/jobs/assignments/payment links/deposit links/payments/invoices all0.
- Exact retry: duplicates1, no new records, business snapshots unchanged. Same immutable Gmail ID with changed body is ignored without overwrite; this is not a changed-body409 detector.
- Two separate non-lead/unknown messages with plausible footer contacts under the SAME mock quote label: clients/services0, Needs review events2, classification tasks2; the contact parser is not invoked. Normalized bodies preserved.

Tests also cover cropped summary, missing customer contacts vs footer, conflicting repeated contacts, subject/body dates, multiple contradictory body dates, rendered/plain equivalent extraction, and recognition of existing subject families without replaying their accepted parser tests. All provider reads are mocked; requests/socket/DNS calls raise. No real provider request occurs. Core stores normalized rendered text, not original raw HTML/MIME; exact raw-source archive retention is not claimed.

## Independent run

With requirements installed in a disposable venv, from a new /tmp extraction:

```
QUALIFY_PYTHON=/path/to/disposable-venv/bin/python sh qualify-current-styled-mail.sh
```

Run only this new9-test coverage and its new-fixture export. Do not rerun the old62 suite, use Gmail or submit a form. No actual mail or live endpoint verification is claimed. No CI/deployment was run. This change is a separate branch based on758d74a; it does not silently replace or requalify the separately pinned3303910 adapter core.
