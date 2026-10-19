# Executed coverage and remaining gaps

Read this alongside `coverage.md` and `inventory.json`. Discovery maps each route,
form, action, query key, job and command to source and candidate tests. Discovery
alone is not proof that every role/state on that surface works. The inventory
contains 246 resolved routes, 218 named routes, 161 raw Django-admin surfaces,
55 form classes, 95 service functions, 11 task-module functions (six public entry
functions and five private helpers), and ten management commands.

## Completed automated evidence

The complete PostgreSQL run on `cd4316a` collected **7,572 unique test IDs**.
Four disjoint workers each reported all 1,893 assigned cases and exited zero:
**7,568 passed, four skipped, zero failures**. See
`full-suite-verified/summary.json`, its collection, worker logs and JUnit files.
The final report and acceptance summary record the independent ID reconciliation.
Earlier `full-suite/` results are previous source snapshots, not this run.

After that full run, one Reports hint was changed from a blanket metadata-only
search claim to “skipped or cancelled.” No query, model, status rule or backend
behavior changed. The two new wording regressions and related report tests passed:
**56 passed**, two existing warnings, 68.31 seconds
(`extraction-wording-green.txt`). The full 7,572-case run was not repeated on that
final template literal. Fifty-four of the 56 focused cases overlap the main
collection; these counts must not be added as 56 new unique tests.

| Area | Intended behavior and role boundary | Executed evidence | Result and scope |
| --- | --- | --- | --- |
| Accounts and sessions | Authenticate active users; protect account/office administration, lockouts, password and idle-session changes | Full-suite account/password/role/security/session modules; live authenticated journeys | Collected cases passed. Does not prove every account-administration browser control. Browser cookie removal simulates lost authentication, not a real idle-time expiry wait. |
| Office visibility and private files | Start from permitted objects; read grants never imply write authority; signed downloads bind object, kind and current user | `test_systemwide_access.py`, existing all-role and office-boundary modules; signed smoke regression | New focused 39 passed, related 442 passed; final full suite passed collected cases. |
| Internal Tracking | Draft, numbering, current-batch routing/parallel receipts, work, return, complete, approved filing, reopen and immutable history | Full-suite lifecycle/receipt/numbering/concurrency/history modules; earlier selfcheck; persisted live filing journey | Collected cases passed. Earlier selfcheck: 21 passed, not rerun after the final extraction repair. Parallel-recipient behavior retained. |
| External Tracking | Separate collection/numbering; managing-office authority, participant custody, provenance, chronology, corrections and filing | Full-suite External workflow/view/security/repository/notification modules; persisted live filing journey | Collected cases passed. A complete live physical external-office process remains a simulated representation. |
| Counts, filters and exports | Same permitted population for IDs, totals and drilldowns; distinguish direction/office, partitions, overlaps and caps | `test_systemwide_audit_oracles.py`, `test_systemwide_filter_matrix.py`, existing agreement/report/search/export modules | Combined **137 passed**, including 24 populated direction/stage cases for all four roles. Explicit expected-ID sets are independent of production filters. See `filters-report.md`. |
| Timing and calendar | Processing pauses for receipt waits; office calendar, overlap and boundaries; External time kept separate | Full-suite timing/wait/calendar/stress modules; new explicit calendar boundaries | Added boundary/External workflow run 54 passed; full collected cases passed. Two calendar-dependent fixtures corrected, without changing business-calendar rules. |
| Repository and deletion | Unique documents despite related rows; review/restore/purge boundaries; Tracking history retained | Full-suite repository/deletion/security/storage modules; background-enabled purge checks | Collected cases passed, including dedicated failure/concurrency tests. Providers remain simulated. |
| Uploads and extraction | Rollback new bytes without removing committed files; truthful failures; fresh permission/consent; stale jobs cannot overwrite newer work; successful siblings cannot hide unresolved failures | New storage, outer-request, append-generation, sibling/cancellation and finalization regressions; existing queued security/processing cases | Background final **95 passed**, 36.32 seconds. Full-retry test uses actual worker threads; append races use deterministic provider interleaving. Public synchronous retry preserves its outer rollback semantics. |
| Reports extraction wording | Skipped/cancelled processing must not imply previously retained text is unsearchable | `test_extraction_report_wording.py`, reconciliation and separate-report modules | Final focused **56 passed**, including two new cases with and without retained text. A label-only change after the complete suite. |
| Supporting pages and commands | Page rendering and expected role refusals; schema and initialization/workflow commands work in isolation | Earlier 231 page requests across four roles, initialization and selfcheck; fresh isolated schema/system/drift and production-shaped configuration checks | Earlier page smoke/selfcheck were not repeated after the final extraction repair. Fresh schema/configuration checks passed on `cd4316a`. See `commands-report.md`. Neither proves every state-changing button or command option. |
| Frontend controls | Existing chart/filter JavaScript remains functional | Full Node test suite and template/lint checks | **172 JavaScript tests passed**; 108 templates and Ruff clean before the final label-only change. Final static/browser outcomes are recorded in the final report. |

## Statement coverage

The complete PostgreSQL run on `cd4316a` measured **10,588 application statements**:
**9,212 covered and 1,376 not covered, 87%** rounded. See
`full-suite-verified/coverage.json` and `coverage.txt`. This includes migrations.
It is statement coverage, not the percentage of roles, features or possible
behaviors verified. The configured 60% gate passed. The subsequent Reports hint
and two test cases do not change the measured application Python statements.

| App | Covered / statements |
| --- | ---: |
| Accounts | 774 / 987 |
| Core | 3,302 / 3,853 |
| Documents | 2,100 / 2,476 |
| Search | 422 / 433 |
| Tracking | 2,614 / 2,839 |

## Skips and verification gaps

Two full-suite skips require background processing. Both were exercised in the
separate **95-passed** background-enabled run. The browser case is opt-in and
has its own final run. One existing parametrized office-admin Incoming test has
no routed fixture rows and deliberately skips. It remains a skip. The separate
new matrix now has populated Incoming/Outgoing fixtures for Pending Receipt,
Received and In Process for system administrators, office administrators, staff
and viewers, checking exact IDs and totals on Tracking and Tracking Search.

Browser evidence and its final revision are recorded in `live-browser-report.md`.
Its response-parity checks compare displayed IDs/headings with ordinary backend
responses; the independent matrix separately supplies expected data. Responsive
checks cover two workspaces at six widths in both themes for four roles, not
every page. The 200% check is CSS zoom; printing checks invocation and print CSS.

Not exhaustively proven: every raw Django-admin action; every button/link for
every role and state; every possible filter combination; every viewport on
every page; physical printing/devices; actual screen-reader output; real browser
chrome/operating-system zoom; provider durability/delivery/quotas; production
hosting, running-worker operations, backups and restore. These remain gaps,
not hidden passes. Real providers stayed simulated as requested.

The final report records the accepted outcomes and revision. Testing and source
review cannot guarantee the absence of every possible bug.
