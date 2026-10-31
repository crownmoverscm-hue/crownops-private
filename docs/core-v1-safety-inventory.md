# Core v1 baseline safety inventory

Baseline: b6fb81b41930a9c38e820e6b5dbcb684253c36d0.

Generated from all 69 Python modules under app/ before application changes. The inventory contains 423 route declarations across 42 modules, plus 352 sensitive helper/callback/worker candidates.

Every baseline route other than the five limited health/auth/root routes is classified DENIED for Core v1. All existing effectful helper candidates are DENIED in this profile. Unlisted paths and methods also deny by default. The root path must render only the isolated Core v1 manual inquiry shell. This inventory is the source of the focused route denial manifest; the test must compare runtime registered routes with the inventory, not only sample endpoints.

The files core-v1-route-inventory.csv and core-v1-helper-inventory.csv are generated from the exact baseline source. They identify source file, line, handler/helper, classification, and a focused test identifier. If implementation discovers an omitted handler, worker, callback or effectful helper, add it to the inventory and denial tests before qualification.

## Limited baseline ALLOWED surface

- GET /api/health
- POST /api/login
- GET /api/me
- POST /api/logout
- GET / — isolated Core v1 shell only

## Baseline DENIED surface

All other listed routes, including all business APIs, GET routes that contact providers or expose provider state, public intake, webhooks and callbacks, client portal/acceptance/payment paths, legacy recovery, quote/estimate creation or approval, booking/job mutations, deposit/payment/invoice operations, dispatch, employee/truck operations, notifications, AI, imports, and external integrations.

## Focused denial manifest

- test_core_profile_denies_every_registered_route_except_allowlist: exercise every denied registered method/path using fictional path values and complete fictional request bodies; assert denial before handler validation, with no database or transport effects.
- test_core_profile_denies_direct_business_helpers: invoke inventoried helpers with fictional arguments through profile guards; expect policy denial before mutations.
- test_core_profile_denies_callback_helper_entrypoints: invoke callback/intake helper entry points with synthetic payloads; expect denial before mutations.
- test_core_profile_denies_provider_helpers_without_transport: invoke provider, OAuth, email, and credential helpers; expect policy denial and zero transport.
- test_core_profile_never_starts_or_executes_workers: verify application lifespan does not start workers and direct worker calls are no-ops/denied.
- test_core_profile_zero_external_transport: instrument socket, requests/httpx, urllib, and subprocess network paths; assert zero attempts.
- test_core_profile_allowed_surface_only: verify authentication/session/MFA behavior and isolated UI shell remain functional.

This is a source inventory, not qualification evidence or production approval.