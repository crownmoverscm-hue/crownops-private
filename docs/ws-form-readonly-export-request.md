# WordPress administrator — narrow read-only WS Form source export

Purpose: establish CURRENT French source field IDs and actual notification mappings; seek retained October24 configuration separately. No live form edit, save, publish, import, submission, email/test action, customer record export or backup/restore is requested. This request has been prepared for the owner; no message has been sent to an administrator.

## Confirmed vs missing

Owner relayed Muse public inspection: WordPress/WS Form renders today's public quote forms; French form has separate Départ and Destination sections, each with a postal field. Exact IDs, configured email action, notification placeholders/routing, plugin version and configuration timestamp remain unverified. Historical October24 mapping cannot be inferred from today's form.

## Read-only collection steps

1. Sign in privately through the owner's existing authorized WordPress admin access; no password/code/token in chat. Open the CURRENT French quote form from the WS Form form list. Record site/environment, page URL, form title and form ID as displayed. Do not click Save/Publish or any field toggle.
2. Record each Départ/Destination section's displayed ID, field IDs/types/labels and group membership: departure address/postal code and destination address/postal code. Include any conditional visibility or same-address rule affecting notification output. Record UNKNOWN where the UI does not expose a value.
3. Use the EXPORT icon at the top of the layout editor to download its form-definition JSON. This is configuration, NOT submission CSV. WS Form's official export instructions describe that JSON download. Do not IMPORT it into a site to inspect it: WS Form documents that imported field IDs change. Preserve the downloaded original privately; inspect/sanitize a copy offline.
4. Open the form's Actions area read-only. Identify EACH enabled notification-generating action, its ID/row identifier and trigger (save/submit/other), enabled status and any conditions. For Send Email, copy the actual configured From/Reply-To/To/CC/BCC, subject, raw HTML/plain-text body and placeholders without editing. Include `#email_submission` options if actually present, and any `#field(...)` IDs. Screenshot/readback is acceptable if action data is omitted from the JSON. Do not run/re-run an action, send test mail or preview using a real submission.
5. Record the installed WS Form edition/version and relevant mail/routing plugin name/version read-only. Describe the ACTUAL configured transport/bridge if visible; do not assume SMTP behavior from the form renderer or collect SMTP passwords/API keys. Record export timestamp separately from any displayed configuration modification/publish timestamp; if historical modification time is not available, mark UNKNOWN rather than using the downloaded file's creation time.
6. Look only in already-retained form exports, configuration/source history or documentation for an October24 version. Record provenance and dates if found. Do not create/download database backups or restore a website to obtain it. If no retained source exists, mark historical mapping UNKNOWN; current JSON is not historical proof.
7. Before sharing, remove secrets, auth headers, API keys, signed URLs and real submission values from a COPY, with each removal marked REDACTED. Preserve IDs, group membership and placeholder syntax. Keep business recipient routing where authorized; use an owner-private channel if addresses/configuration are sensitive. Do not attach customer submissions or full SMTP/site credentials.

## Return these exact artifacts

- french-form-current-sanitized.json (form configuration only).
- french-notification-actions-sanitized.txt or read-only screenshots, including routing/template placeholders and action identifiers.
- source-version-and-time.txt: site/env/page/formID, plugin/edition/version, export time/timezone, actual configuration timestamp if known, export-file SHA256 if locally available.
- field-map.csv with columns: source_version, form_id, section_label, section_id, field_label, field_id, field_type, notification_action_id, exact_placeholder, intended_direction, ambiguity.
- retained October24 source export/mapping only if it already exists, clearly separate from current configuration.

Expected rows: Départ address, Départ postal, Destination address, Destination postal. Do not fill actual IDs from guesses. No form change is required to deliver these artifacts. If viewing/export requires enabling a feature or altering permissions/configuration, stop and report the exact access requirement.

Official references (consulted October31): https://wsform.com/knowledgebase/import-export/ and https://wsform.com/knowledgebase/send-email/ . These establish export mechanics and available email-action settings, not this site's actual values.
