# CrownOps mobile review — 0.12.1-rc1

Phone users can find their current screen in the fixed CrownOps header, open and
close the navigation drawer, and search clients without scrolling past the sales
guidance. Suggested sales actions and healthy import status expand on request;
import warnings remain expanded. Hidden navigation is removed from keyboard focus.

## Corrections

- Communications now retains its loaded outgoing messages so **Open** displays the
  chosen email instead of failing on a missing cached record.
- On small screens, the follow-up action workspace scrolls as a whole. Its outcome
  controls no longer consume almost the entire dialog and obscure the client details.
- Search fields use the available phone width. Long connection/setup labels wrap.
  Small buttons have a minimum 44-pixel height and dialog close buttons have a
  44-pixel target. Form controls remain 18 pixels.
- The staff offline notice remains visible in production even when the test banner
  is hidden. Failed requests preserve the open form and give a clear error. There
  are no automatic retries for payments or other writes.
- Closing or leaving an edited staff dialog prompts before discarding typed changes.
  Dialog sizing accounts for a reduced visual viewport, with regular scrolling as
  the fallback. Real device keyboard behavior still needs checking.
- The client file avoids duplicate navigation controls on phones. File selection
  comes before the optional folder link, and detailed upload guidance expands when
  needed. Existing limits and upload safety processing remain enforced.
- Staff sign-in explains where the current six-digit authenticator code comes from.
  The offline launch page has a readable mobile layout and retry link.

## Verification scope

The review uses disposable fictional installations. No production client records,
company settings, financial entries or outbound messages are changed by the tests.

- All available Owner, Manager, Sales and Crew navigation pages: 214 viewport/role
  checks across widths 320, 390, 430, 768, 1024 and 1440 pixels.
- Staff forms/dialogs cover intake, quotes, follow-up outcomes, communications,
  booking availability, invoices, payments, time off, document templates, files,
  incidents, employees, invitations, contacts, expenses, access, rates, materials,
  fleet, policies, account setup and crew protocols.
- Focused checks cover drawer focus, closing, Escape, sales search, expandable
  guidance, unsaved edits, interrupted saves, short-height viewports, outgoing email
  preview, invoice search, My Jobs/All Jobs, date controls, and French form state.
- A phone-sized client-to-team journey covers inquiry, generated email links,
  prefilled details, saved progress surviving reload, submitted updates, manager
  review, image upload, rejected-image retry state, a short MP4, shared-folder link,
  callback, French/English, editable material charges, estimate delivery and acceptance,
  verified deposit, manager/crew/truck assignment, pre-use check, service completion,
  actual invoice hours, deposit allocation, generated invoice email and private access.
  Gmail MIME is captured locally; the accounting link and payment state are simulated.

The existing server and calculator/navigation test suites must pass before release.
This review does not certify every browser/device combination, provider account,
real email delivery, a live charge, or physical truck/employee readiness.

## Repeat the browser checks

Use an isolated development environment with Python dependencies from
`requirements.txt`, Node, Playwright, Chromium and the media tools installed.
Playwright is a development-only dependency. If installed outside the repository,
set `NODE_PATH` to that installation's `node_modules` directory. Optionally set
`PLAYWRIGHT_BROWSERS_PATH` and `CROWNOPS_BROWSER_OUTPUT` for the browser cache and
test evidence. The runner uses only a disposable fixture on localhost port 8095.

```sh
python tests/browser/run_mobile.py mobile_smoke
python tests/browser/run_mobile.py navigation_audit
python tests/browser/run_mobile.py dialog_audit
```

## Remaining launch walkthrough

1. Install on a real iPhone and Android used by the team. Check the keyboard,
   photo library/camera picker, a real HEIC/MOV file, map handoff, background/resume,
   text-size preference, sign-in and authenticator switching.
2. Complete the controlled real email and production QuickBooks/payment walkthrough.
3. Confirm company billing, sender identity, terms, staff pay and real fleet readiness.
4. Complete the small team pilot and confirm sufficient private-media storage.
