# Staff theme review branch

Base: 717eea595c3112a3efe341ab7ad6069f88460e40 (verified current staff UI source).
Branch: feature/staff-three-view-themes. No merge/deployment/provider activation.
The accepted Core v1 branch/candidate 442d248 is untouched. MFA, recovery/session revocation and real backup/restore remain separate launch gates. No UI test closes them.

Black View is default; Light View and Ocean Blue View retain the same official orange-gradient Crown Movers asset. icon.svg and PNGs are byte-identical to the base. Only the containing box gains 8px interior padding, with object-fit:contain and no crop/filter/transform. Mobile branding includes CrownOps and CROWN MOVERS; no generic C/crown replacement. No owner-original branding screenshot was supplied with this assignment; screenshot comparison is against the existing verified official asset, not a claimed pixel match to an unavailable reference.

New tokens and controller load only from the staff index. Customer portals, print/customer estimates/invoices, brand.css, backend pricing/permissions/integrations and logo files are unchanged. app.js adds only UI context calls at shell construction and login-screen rendering. No authentication decision or business action changes.

Preference key is crownops.staff-theme.v1:<encoded staff id>, stored in localStorage without a database field, token or email. Account context applies synchronously before the shell is inserted, then controls attach via a microtask. Logout/login-screen resets visible theme to Black; the next authenticated identity restores only its own choice. No last-user theme is read before identity is known. Switching does not reload, navigate, log out or replace an existing form. Blocked storage uses in-memory fallback and cannot persist across a fresh browser process; this limitation is intentional and safe.

Native labelled selects are available in staff navigation and Company setup/Settings. All three options retain the exact requested names in both languages. Theme label/help/live announcements support EN/FR; the rest of the existing application's French coverage is unchanged and is not newly certified. Focus outlines, 44px controls, contrast token checks and reduced-motion behavior are included. Success/warning/error classes retain their semantic colors.

Focused qualification commands:

    node --test tests/staff_themes.test.cjs tests/company_settings.test.cjs tests/schedule_calendar.test.cjs tests/schedule_week.test.cjs tests/move_navigation.test.cjs
    node tests/browser/staff_themes_cdp.cjs

Browser command requires Node with built-in WebSocket and preinstalled Chrome; it downloads no browser/npm packages. Fixture is a disposable local Python 3.13 installation using recorded hash-locked official-PyPI wheels. It seeds only fictional users, read-only sessions, three inquiries and labelled UI-only rate fixtures. ALL non-GET/HEAD application requests are denied by test middleware and browser interception. It saves/calculates/sends no quote, creates no booking/payment/invoice, contacts no provider and never uses a real account. Existing financial records remain zero in the fixture readback. Screenshots are real browser rendering of actual staff pages on fictional data; they are not deployed-editor or backend business-flow acceptance.

CI artifact binds screenshots/results/engine version to the final commit. A prepared harness is not a browser PASS; require successful browser-result.json and no browser-failure.json. Screenshots include the three desktop/mobile theme views, mobile menu open, Settings, French selector and logo padding. Browser checks also visit clients/Inbox/calendar and open a real quote-editor form without saving, preserving its unsaved values across theme changes. Native keyboard/focus, refresh/account isolation and meaningful colors are targeted checks, not a complete accessibility audit, full French acceptance or PWA/cache-upgrade certification.

Use a draft/source review only. Existing services, production, accepted Core v1 and D-01 candidates are not changed or redeployed by this branch. Independent Muse review and separate launch approvals remain required.
