# CrownOps quote and media update — 0.9.0-rc1

Prepared October 16, 2026. This source includes the earlier 0.8 client journey changes. Preparation and tests do not prove production deployment; verify production health and the Railway deployment before describing this version as live.

## Quote editing

- New quote and Edit draft share editable material rows. Select from active catalogue items or add a custom material/extra, description, unit price and whole quantity. Zero quantity keeps an optional item on the estimate without charging it.
- Owners and Managers can save a custom material into the reusable Materials catalogue. Sales can add custom quote items and edit quote prices without changing company catalogue prices. Existing stock and costs are not invented.
- Reviewed item prices are frozen with each quote revision and copied into invoice preparation. Accepted quote history is retained; editing creates a new draft revision that requires review and approval.
- Guided view and Line-item view share the same values. The document view permits editing service description, hourly price, estimated hours, transportation and item rows. The estimate remains a range before tax; it is not an unrestricted accounting editor.
- Billing contact, company, email, phone, address and payment terms are saved with the revision. Accepted billing name/company, address, email and material quantities/prices carry into the prepared invoice; payment terms start in invoice notes. Quote delivery still uses the saved client's verified contact email.
- Optional expiration is visible to the client and blocks new acceptance after that day in Montreal time, including externally recorded acceptance. Previously accepted services are not expired retroactively.
- Local transportation defaults to an explicitly entered fixed amount, with a $35.00 placeholder. The placeholder never silently becomes a charge. Existing hourly transportation drafts retain their method. Separate local fuel remains visible and editable, including 0 if included. Long-distance calculations remain unchanged.

## Photos, video and folder links

- Staff and clients can save HTTPS Drive/Dropbox/other folder links alongside uploads. Assigned crew can open project links and links for their own jobs. Unassigned crew cannot read another project. The app never fetches external folder contents; the folder owner must permit the team to view them.
- Upload forms offer multiple selection and drag/drop. A rejected file does not stop the remaining batch; only failed files remain selected for retry. Existing identical images are reused even when the upload quota is full.
- JPEG, PNG, WebP and HEIC are decoded, resized to 2048 px and encoded into a fresh JPEG with metadata removed. Input limits are 30 MB / 64 megapixels. A real 48 MP JPEG and HEIC sample passed conversion tests.
- MP4, MOV and WebM walkthroughs up to 100 MB / 3 minutes are re-encoded into private H.264/AAC MP4s, at most 1280 px, with metadata removed. FFmpeg is installed in the container. Conversion has time, dimension, duration, thread and output-size limits. These checks do not claim antivirus scanning.
- Existing project/global quotas and free-disk headroom remain enforced. The present 500 MB production volume is not suitable for an unlimited video archive. Shared folder links are the immediate option for large collections; separate object storage would be a future change.
- New saved links are scoped to the existing project/job authorization. Invalid historical legacy links are omitted from clickable lists. New tables trigger the existing pre-migration backup and restore verification before production startup.

## Validation

- 275 Python tests passed, including quote snapshots, invalid-input rollback, billing/material invoice handoff, real JPEG/HEIC/MOV conversion, retry deduplication at quota, unsafe legacy links, expiration and access revocation.
- 13 Node calculator/navigation tests passed.
- Browser checks passed on desktop, tablet and phone: quote view switching, custom/catalogue rows, edited prices, draft save/reopen, billing, folder links and failed-file batch continuation for staff and clients. Existing owner/manager/sales/crew navigation, schedule scopes, expenses, crew invoice preparation, client quote acceptance, callbacks, client changes, French form state and private invoice access also passed with no page errors.
- Browser fixtures used fictional clients, disposable databases and mocked Gmail/QuickBooks; no real customer email or payment was sent.

## Production handoff

1. Upload this clean source into the existing CrownOps Railway service; do not create another project or replace its data volume.
2. Observe terminal SUCCESS, verify `/api/health` reports 0.9.0-rc1, confirm the pre-migration backup and confirm the new scripts and upload policy are served.
3. Check the signed-in production quote editor and a company-owned client test file. A real Gmail/QuickBooks journey still needs the approved test recipient and account access.
4. Synchronize this source to the GitHub repository before relying on GitHub auto-deploys. An old repository push could otherwise replace the locally deployed source.

No production secrets, client records, uploaded media or database files belong in this source package.
