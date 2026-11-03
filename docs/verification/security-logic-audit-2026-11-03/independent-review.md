# Independent final security/logic repair review - 2026-11-03

Reviewed the current uncommitted change set on `codex/security-logic-audit-20261103` against `8fc45b08c7e38fd74e2a28ddc52825f73c84bf17`.

## Verdict

No outstanding important findings in the reviewed changes. No code changes made by the reviewer.

## Scope inspected

- Shared ready-administrator predicate; application forms/toggle guards; raw Django user-admin edit, password-disable, single-delete and bulk-delete guards.
- Existing `SystemAdminSite.admin_view` account-wide advisory serialization, current actor reload/OTP check, new target row locks, and frontend account-write serialization.
- Profile personal-field updates preserving a concurrently replaced email factor.
- Document-admin extraction read-only fields, actual submitted collection on creation, and interaction with content-field exclusion and policy enforcement.
- External Repository final-page OCR redaction, authorized metadata visibility, and collection-specific capabilities.
- External file-history capability context and legacy download lifecycle/storage-identity checks.
- Locked active routing destinations and External dispatch/deadline chronology.
- PDF-parser requirement floor raised to 6.21.4 without an incompatible major upgrade.
- New regression modules and expanded opt-in browser journey, including persisted-state assertions.

## Observations

- Raw-admin safety checks execute inside the existing account-wide serialization boundary. The target is loaded under a row lock before Django builds and saves its model form.
- Readiness deliberately matches the approved system-administrator/staff/usable-password/verified-email definition; it does not silently grant roles, staff access, or superuser status.
- Content redaction affects the bounded rendered page; rows/counts remain derived from the same authorized filtered population.
- Endpoint validation locks in deterministic office-PK order while retaining requested recipient order, preserving parallel routing behavior.
- Repository file checks reject unavailable/current-storage mismatches while retaining the existing signed legacy URL format and parent authorization.

## Verification limits

This was a read-only source and test-design review. The reviewer did not execute database tests, browser journeys, external-service calls, physical-device checks, or screen-reader checks. Final test outcomes must come from the coordinator's fresh verification run. The review is not a guarantee that every possible defect is absent.

## Addendum: demo-seed authorization and failure reporting

Read-only review of the subsequent `seed_demo.py` and `tests/test_seed_demo_authorization.py` delta found no outstanding important issues.

- `_archive` and `_bulk_archive` now call the unchanged `set_tags` authorization path using the already-seeded active `SYSTEM_ADMIN`; they do not disable capabilities, widen office rules, or add a bypass.
- `uploaded_by=users.get("records")` remains unchanged, separating preserved historical uploader provenance from the actual authorized tagging actor. The regressions assert both uploader and tag-review actor.
- The command still commits master data and accounts in their existing separate transactions before sample phases. A failed sample phase now raises `CommandError` after its transaction rolls back, instead of printing a misleading final success; the traceback option retains its original propagation behavior.
- The complete-seed regression correctly treats `--records` as the target total population, preserves all narrative subjects/archive titles, and exercises generated archive creation.

No database tests were run by this reviewer. Fresh red/green and final integration outcomes remain the coordinator's responsibility.

## Addendum: broad live-browser harness and mandatory OTP

Read-only review of `tests/test_systemwide_live_browser.py` and `scripts/check_systemwide_live.cjs` found no outstanding important findings in the supplemental harness changes.

- Fixtures prepare verified email addresses only; browser authentication still submits the actual password, waits for the OTP page, and proves a protected Tracking request remains unauthenticated before verification.
- Codes are read from the isolated in-memory mailbox and passed through child-process stdin. They are not written to the manifest, report, stdout events, URLs, or screenshots; saved stderr receives six-digit code redaction. No session/proof shortcut is introduced.
- The JavaScript-disabled journey also enters the emailed code through the real form. Browser login continues to use the live Django server with CSRF checks.
- Existing filter comparisons still compare HTMX output against ordinary live backend responses. Python retains post-journey persisted state checks for internal and External completion and exactly one filed document in the correct collection.
- Added TXT preview checks assert original bytes, the visible version label, and Back navigation. Closing the readline/stdin bridge prevents successful runs from remaining alive merely because the input channel is open.

This review did not execute the browser journey. Only fresh successful core/tail attempts should be reported as passing; crashed or dirty-database attempts remain harness/environment failures rather than evidence of application correctness.
