# DocTrack defense-readiness verification

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**

Audit started 24 October 2026; final verification completed 25 October 2026. Starting revision: `1bebf45f76740f8d898a133c0f3820af78c244a8`.
Branch: `codex/defense-readiness-audit`.
Tested program revision: `540b9029f207f74bab147d12b0c3f6190f413e36`.
All final local verification gates pass. No unresolved reproduced application defect remains in the verified scope. This is not a guarantee that every possible bug is absent.

[Machine-readable results](defense-readiness-2026-10-24.json) preserve the tested revision, exact totals, skips, worker exit codes, coverage and dependency outcomes. The release adds documentation after the tested program revision; no application code changes are made after that run.

## Scope and isolation

The audit covers authentication and accounts, internal/External Tracking, Repository, file versions and recovery, previews/downloads, search, filters, counts, reports/exports, timing, notifications, deletion recovery/purge, extraction and background jobs. It also verifies the previously approved External custody/shared-office behavior that remained incomplete at the starting revision.

Every request-driven test, seed, migration and failure simulation uses disposable PostgreSQL databases named `test_doctrack_defense_20261024_*` on the owned loopback cluster at port 8775. Files are stored in temporary local audit directories. Original records and storage are not migrated, seeded, pruned or request-tested. OCR, cloud storage and email calls are disabled or simulated. Fixture passwords and raw request logs remain in ignored local evidence, outside Git.

No schema migration is introduced. Existing tracking numbers, stored file paths, OVPA lifecycle/timing logic and previously filed repository ownership are preserved. The unrelated local environment edit is excluded from commits. Remote main's Azure dependency update is merged, retaining the required R2/S3 SDK support as well.

The temporary audit database has durability settings relaxed for disposable test speed. These runs verify functional behavior and concurrency, not production durability or performance.

## Repairs and regression evidence

| Confirmed trigger | Result after repair | Main regression evidence |
| --- | --- | --- |
| An External case changes confirmed internal custodian, but original registration still controls later manual actions and filing. | One responsibility calculation follows confirmed custody, retains the last responsible internal office during external custody, and uses repository ownership for filed cases. Sending alone does not transfer responsibility. Old participants retain history access without continued mutation authority. | `test_external_custody_control.py`, `test_external_custody_repository.py`, `test_defense_external_logic.py` |
| An administrator needs an active External office registered elsewhere, or a forged creation context is submitted. | Active directory choices are shared; editing stays with the registering office's administrators/system administrators. Creation context is resolved per request, with inactive, repeated and unauthorized selections refused. Legacy ownership fields cannot override it. | `test_external_creation_context.py`, `test_external_custody_control.py` |
| A filed External document's owner differs from original registration, or two operations lock shared directory entries in opposite orders. | Rows, reports and exports include the legitimate filed ownership scope. Shared endpoint locks follow deterministic ID order; the reproduced PostgreSQL deadlock is covered by a two-thread regression. | `test_defense_external_logic.py`, `test_external_custody_repository.py` |
| External navigation, upload recovery, metadata review, deletion review, source review or signed review sampling drops `office=`. | Validated request-local perspective survives the relevant round trips without changing authorization or session state. Intentional Clear actions still discard their filters. | `test_external_creation_context.py`, `test_defense_external_logic.py`, `test_intelligence_ui.py` |
| File version upload, preview, audit pagination, draft removal or worksheet selection loses External context; a saved unfinished version preview returns to an unscoped form. | Context survives current/older file links, worksheet GET fields, audit pages and successful/invalid submissions. Saved-form return links use the authorized upload binding, preserve private upload slots and do not create an unintended version. | `test_external_file_context.py`, `test_upload_draft_views.py`, `test_upload_draft_security.py` |
| A template library error only appears when an otherwise empty screen is populated. | The source-review templates load their required tag libraries; an added regression compiles every template, independently of fixture contents. | `test_template_compilation.py`, `test_intelligence_ui.py` |
| The installed environment contains known vulnerable PDF/parser and supporting dependency versions, although a new resolver chooses safe versions. | Runtime minimums require patched pypdf and certifi; installed tooling dependencies are updated and scanned separately. Azure and R2 SDK extras remain installed. | Installed-environment and requirements vulnerability audits; file/extraction regressions and full suite |

Relevant implementation is in `apps/tracking/external_models.py`, `external_services.py`, `external_forms.py`, `external_views.py`, `apps/documents/external_services.py`, `services.py`, `views.py`, `intelligence_views.py`, `review_sampling.py`, `apps/core/upload_context.py`, `upload_forms.py`, `file_views.py`, `file_lifecycle.py`, `tasks.py`, `utils.py` and the corresponding templates. No file-content mutation permission is added to Repository.

Regressions were observed failing before their corresponding repairs. Initial External contract checks produced 45 failures and 15 passes; these failures were investigated rather than excluded. The deadlock and saved-preview context loss each have specific reproductions. The final focused file/recovery batch passes **81 tests**.

## Verification results

The final full PostgreSQL run at `540b902` collected **8,150 distinct test IDs: 8,144 passed, 6 skipped, 0 failed, 0 errors**. All four workers exited successfully, and the runner verified that every assigned ID was reported exactly once. Line coverage is **87.23%: 14,653 of 16,799 statements**. Earlier results at `78165fe` are retained separately as intermediate evidence, not substituted for this final-revision run.

| Check | Fresh outcome / evidence |
| --- | --- |
| Full PostgreSQL suite | 8,144 passed, six deliberate skips, zero failures/errors; all 8,150 exact IDs reconciled; 87.23% line coverage |
| JavaScript | 194 passed; zero failed/skipped |
| Template static checks | All 124 templates pass |
| Actual Django template compilation | Included in the 81 passing file/recovery/template batch |
| Ruff / whitespace / browser script syntax | Passed |
| Background-enabled regressions | 131 distinct IDs pass: 129 passed in the batch, and two interrupted during database connection setup pass when rerun separately on 25 October. The original setup/teardown errors remain recorded; they are not relabelled as an uninterrupted clean batch. |
| Live Chromium | 451 role/responsive checks and 34 filter/history/retry/session/no-JavaScript checks pass on 25 October. No JavaScript or unexpected HTTP errors. |
| Installed packages | 85 packages scanned, no known vulnerabilities; `pip check` passes |
| Runtime requirements | 43 resolved packages scanned, no known vulnerabilities |
| Operational migration | Disposable fresh/repeated migrations and migration-drift check pass |
| Workflow self-check | 21 checks pass |
| Page smoke | 218 requests across four roles; no unintended page errors |
| Counts | 19,810 checks across 26 scopes; no failures and zero recorded integrity violations |
| Filters | 291 checks across four roles; no failures |
| Office/stage reconciliation | 4,908 checks over 1,026 requests; no failures |
| Working-time calculation | 45 checks pass |
| Static build / deployment configuration | Build passes; deployment check has no errors with placeholder valid storage/email configuration. HSTS remains a reported warning. This does not test live provider credentials. |
| Independent review | All reported findings closed in source, including the staged External file preview return. Reviewer performed no database mutations and did not claim independently executing the suite. |

The count/filter/stage/operational results above were run earlier in this audit, before the final small file-navigation and dependency repairs. Their exact recorded outcomes are not presented as new reruns. Final-revision Django checks, drift, deployment configuration and static build were rerun successfully on 25 October. The complete final suite also reruns the count/filter oracle regressions against the final program revision.

The six full-suite skips are explicit:

- One direction example has no routed rows in that fixture: `test_every_routed_row_carries_a_direction[scope=incoming-med_admin]`. Other populated incoming/outgoing cases and independent result-ID checks pass; this empty example is not marked passed.
- Three schedule-registration cases require the background-enabled profile. All three pass within the 131 distinct background regression IDs.
- The two opt-in real-browser cases are run separately and pass: 451 core checks and 34 tail checks. They are skipped in the general run to avoid launching browsers alongside the database workers.

The count audits compare identities, not only equal totals, with mixed OVPA/External fixtures. Overdue is an overlapping attribute, not another stage to add to a partition. A monthly chart is compared against its stated period rather than an unrelated all-time total. The historical example record mentioned in an earlier screenshot does not exist in disposable data; the stage script explicitly reports that absent example.

## Coverage ledger

`scripts/audit_inventory.py` discovers 302 routes, including 191 raw administration routes; 62 forms; 54 explicit query keys; 101 service actions; 11 jobs; 20 management commands; and two action dispatchers. Resolver discovery completes with database connections forbidden. The [detailed feature ledger](defense-coverage-2026-10-24.md) maps these surfaces and candidate tests; discovery is not proof that every control or role combination was executed.

| Feature | Required behavior / roles | Executed evidence | Limits |
| --- | --- | --- | --- |
| Accounts, offices and sessions | Anonymous login/reset only; active accounts retain their role/office boundaries; suspended, office-less, transferred and revoked actors are rechecked. | Full suite: account password races, all-role security, office-scoped administration, session timeout and permission tests. Live login/CSRF, expired-session and no-JavaScript journeys. | Real password-recovery delivery unverified. |
| Internal Tracking | Draft → current-batch dispatch → recipient receipt → work → completion/pending filing → one repository filing; returns and parallel recipients keep established behavior. | Full suite: workflow, multi-office/current-batch receipts, file/version races, immutable history and office-stage tests; live creation-to-filing journey and saved database assertions. | Browser does not click every possible action for every account. |
| External Tracking | Shared active directory choices, protected directory editing, manual provenance, revision/chronology checks and responsibility following confirmed custody. | Custody/context/filing regressions; External workflow, correction, notification and timing tests; live manual dispatch-to-filing journey. | Original historical records receive no migration or repair in this audit. |
| Repository and deletion | Correct collection/owner visibility, immutable files, editable authorized metadata, once-only filing, 30-day recovery boundary, retry-safe cleanup. | Repository reconciliation, private download, retention/deletion, storage-key and concurrency tests; mixed-collection operational checks. | Live cloud deletion/restore behavior is simulated. |
| File versions and previews | Logical files count once; five retained versions, preserved audit evidence, private signed version access and safe read-only previews/recovery. | File version, retention, preview, discard, upload recovery/security and file-context tests; latest parser version installed. | Representative browser previews are covered by tests; not every accepted file's rendering is visually inspected. |
| Search, filters, counts and exports | Scope is narrowed from authorized populations; collection boundaries, status/direction partitions, exact identifiers, relevance disclosures, pagination and exports agree. | Full suite reconciliation/oracle tests; 19,810 count, 291 filter and 4,908 stage checks; browser combined identifiers/dates and exact record IDs, manual submit policy, automatic controls and history. | Ranked search is intentionally capped and fuzzy title matching is not exact identifier matching. |
| Timing and deadlines | Receipt waits pause processing; received/in-process work counts; current batches, overlapping intervals, calendar and exact boundaries are respected. | Timing/calendar/receipt-pause tests plus 45 operational checks; External timing remains separate. | Real production performance is not benchmarked. |
| Notifications, audit and administration | Inaccessible targets stay hidden; read and mutation authority differ; old participants cannot retain custody rights; raw admin remains system-admin-only. | Permission/notification/admin/race tests, External recipient regressions and page smoke. | Inventory of generated raw-admin URLs is not a claim that every generated form was clicked. |
| Extraction and jobs | Generation/idempotence protection, revoked actors/consent, failure rollback, schedule uniqueness and cleanup preserve files/history. | Background-enabled regression batch, extraction overlap/security/recovery and lifecycle tests. | OCR/cloud/email providers and a production worker are not contacted. |
| Interface | Existing Bootstrap/HTMX design, manual compound searches, immediate Dashboard controls, stable focus/scroll, usable errors and responsive themes. | JavaScript suite and live Chromium journeys; exact widths/themes and final totals recorded below after completion. | Physical devices, screen readers, browser chrome zoom and physical printing unverified. |

## Browser evidence and test-harness corrections

The live audit uses actual Django pages, login, CSRF validation, backend requests, database state assertions and authorized bytes. Only pinned static Bootstrap/HTMX assets are served locally; unexpected external requests are refused. The long journey is split into two separately timed cases without removing checks.

The filter/history/retry/session/no-JavaScript case passes **34 recorded checks**. The role/responsive case passes **451 recorded checks**, covering staff, office administrator, system administrator and viewer views in light/dark themes at 320, 390, 768, 900, 901 and 1366 pixels. Five actual login sessions also support the internal/External filing journeys. Database assertions verify the final stored states and one filed document per case. Browser assertions compare complete result IDs against both ordinary server responses and independently seeded records, and preserve a JavaScript marker to detect an unintended full reload.

Earlier browser runs exposed test assumptions: an obsolete Tracking date-field name, direct document URL expectations despite search-click redirects, and a fuzzy title query incorrectly expected to return only one document at zero relevance threshold. Those were repaired in the harness using actual form fields, normalized click targets and a unique identifier fixture; production search/ranking was not weakened. An earlier monolithic run timed out under competing tests; a later run encountered an isolated database connection timeout. Another run stopped during the interruption, leaving disposable fixture data. Its database was verified and cleared before restarting; the changed bundled browser-runtime path was also corrected locally. None of those incomplete/error runs is counted as a browser pass.

Advanced Search deliberately retains the documented 75% default display threshold. Even a title match can be below it; its hidden-result count and lower-threshold controls remain available. Repository search starts at zero. This established scoring policy is preserved and explained in the presentation checklist.

## Remaining limits and presentation setup

- The local verification environment is Windows, Python 3.13.7 and PostgreSQL 18. The repository CI configuration uses Python 3.12 and PostgreSQL 16; a local pass is not a claim that a remote CI run or deployment passed.
- No test suite proves the absence of every possible bug. Coverage below 100% and a route inventory leave explicit unexercised branches and combinations.
- Cloud storage credentials, free OCR.space quota/provider behavior, SMTP delivery, remote hosting/tunnels, real background-worker availability and production backups/restores require checks in the actual presentation environment.
- Deployment checks use placeholders and report an HSTS configuration warning. This audit does not silently disable production safeguards or certify a production deployment.
- Chromium widths and keyboard tests are browser emulation. Physical devices, screen readers, physical printing and actual browser/OS 200% magnification are unverified.
- No ISO 25010 certification or accessibility conformance certification is claimed.

Use [the presentation checklist](../DEFENSE_CHECKLIST.md) to prepare accounts, sample files, live integrations and a local fallback. The original database and uploaded files remain outside all disposable test actions.

## Evidence and reproduction points

The recorded local drivers, validated settings, exact-ID inventory, per-worker results, coverage data, request evidence and screenshots remain in the ignored `.vercel/defense-audit-20261024` folder. They are audit-specific, not production settings. The starting full-run evidence is preserved separately under `pre-final-78165fe`; incomplete browser and background runs are retained with their original errors.

Checks actually used include the four-database `run_full.py` driver; opt-in `tests/test_systemwide_live_browser.py` with its core/tail phases; the background regression batch plus its two setup retries; `scripts/audit_counts.py`, `audit_filters.py`, `audit_tracking_stages.py` and `audit_turnaround.py`; `manage.py selfcheck`, `check`, `check --deploy --fail-level ERROR`, `makemigrations --check --dry-run`, `collectstatic`; `scripts/smoke_pages.py`; all JavaScript test files; Ruff; template checks; and installed/resolved dependency audits. The standard repository CI entry points remain in `.github/workflows/ci.yml`.

Raw evidence is not committed because it contains private fixture state and machine-specific paths. The sanitized JSON, this report and the discovery ledger are the shareable record. Reproductions must use newly isolated databases and temporary storage, never original records.
