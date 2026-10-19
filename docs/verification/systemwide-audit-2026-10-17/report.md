# DocTrack systemwide audit and repairs

Baseline: `bb84ac863937a27dc871e115db694b2e84a78f73`.
Branch: `codex/systemwide-bug-audit`.
Verified product repair commit: `e1edaa2`.
Count/filter/calendar regression commit: `f5f4810`.
Extraction recovery follow-ups: `666b7b3` and `05e49f3`.
Audit tooling and expanded matrix: `cd4316a`.
Final Reports wording correction and product revision: `9e2d34c`.
Final source provenance and accepted run summaries: `revision-final.json` and
`acceptance-summary.json` beside this report. Report finalized on 19 October 2026.

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**

## Result and boundaries

Focused product repairs are implemented and independently reviewed. Follow-up
checks reproduced and repaired hidden sibling failures, cancelled work and stuck
background finalization. The complete PostgreSQL run, focused final wording
checks, background regressions and real-browser journeys passed. No unresolved
reproducible product defect remains from the tested scenarios and inspected
changes. Testing cannot guarantee every possible bug is absent.
The coverage ledger explicitly retains unproved routes, roles and interactions.

All request actions, database changes, concurrency/failure simulations, seed
commands and workflow checks used separately named disposable PostgreSQL
databases and temporary local storage. OCR, cloud storage and email checks
remained simulated. The developer's business database and existing records were
not migrated or mutated. Existing unrelated captures and reports were preserved.
Nothing was pushed or deployed.

## Repaired defects

| Trigger and original behavior | Focused correction | Regression evidence |
| --- | --- | --- |
| Office notifications could reveal an inaccessible legacy tracking/document title or message to some roles | Notification lists/counts/read actions now also require current referenced-object visibility for all roles | Access RED: 26 failures; focused final: 39 passed; related: 442 passed |
| Internal attachment and repository file endpoints accepted a private direct URL without the established signed-link contract | Require expiring signatures bound to file kind, file ID and current user; recheck current visibility; templates emit signed URLs | Wrong/missing/tampered/expired/cross-user/cross-kind signatures and stale permissions covered by access cases |
| File bytes survived failed row writes or later rollback, including request-level groups of otherwise successful file operations | Track new writes before SQL save; compensate after rollback; preserve prior/shared/committed bytes and the original failure | Storage RED: 13 failures; focused final: 19 passed; outer-request RED/GREEN: four cases each |
| A queue submission failure happened after committed files, producing misleading failure/success or a stuck extraction state | Preserve committed work/text, record actionable extraction failure, and show a truthful retry message | Queue failure and saved-file assertions; related outer/processing run: 16 passed |
| Older full retries or append jobs could finish after a newer full retry and overwrite usable text, file metrics, suggestions, index or status | Add an extraction generation fence; full retries supersede older work, sibling appends share the current generation; keep fresh permission/consent checks | Append RED/GREEN: four cases; related: 40 passed; full retry interleavings include actual worker threads |
| A newly added file's failed synchronous extraction could leave the document looking successfully processed | Preserve usable existing text while recording failure details/status and accurate UI feedback | Storage/processing regressions and complete suite |
| A successful sibling attachment could hide another file's queue/provider failure or permission cancellation, including after an unsuccessful retry | Persist unresolved extraction attention separately from temporary run states. Keep cancellation SKIPPED; later success cannot hide it. Only a successful complete retry clears it and retires covered jobs/callbacks | Fifteen sibling cases covering clean-state cancellation, permission changes, late callbacks, partial/complete retries and retained text |
| Background result finalization failed during index/suggestion updates, rolling back the result but leaving committed startup RUNNING | Record safe retryable FAILED after rollback, only for the active captured generation; retain original worker exception and previous text/index/files | Index/suggestion RED cases; newer-generation/deletion guards; six finalization and public synchronous rollback cases |
| A cancelled extraction could retain searchable text while Reports claimed every skipped document was metadata-only | Replace the misleading hint with “skipped or cancelled”; preserve counts and extraction behavior | Two failing-then-passing cases, with and without retained text; related report run: 56 passed |

Two additive migrations add `Document.extraction_generation` and
`Document.extraction_failed_generation`. Apply migrations 0012 and 0013 through the normal reviewed
release process before running this branch against an existing business database.
They have been applied only to isolated audit databases during this task.

## Diagnostic and documentation corrections

The count/filter tools had faulty expected populations and edge assumptions.
They now separate OVPA from External, handle chart rows without links, compare
displayed draft identifiers, retain intentionally searchable retired offices,
use intended OVPA master-data usage, avoid inactive tag probes and walk beyond
the 500-row `all` cap. The new expected-ID matrix defines answers from fixture
facts rather than reusing production filtering functions. See `filters-report.md`.

Two existing External timing tests assumed the next day contained office hours.
Their fixtures now use known weekdays; explicit lunch, weekend, holiday and
opening/closing boundaries were added. No business-calendar rules changed.

Smoke checks now generate the required file signatures and require successful
downloads for their known seeded files. Browser checks require exact download
bytes, correctly trigger bounded range controls and assert the actual theme.
README now describes administrator-approved filing instead of automatic filing.

## Verification

| Check | Accepted outcome and source |
| --- | --- |
| Complete PostgreSQL suite on `cd4316a` | **7,568 passed, four skipped**, zero failures, across 7,572 unique cases in four prepared disjoint workers |
| Final Reports hint correction | **56 passed**, including two new regressions and 54 cases overlapping the full suite; no full-suite rerun after this literal-only change |
| Application statement coverage from the complete run | **87%**: 9,212 of 10,588 statements covered, 1,376 missing |
| Background-enabled processing, security, queued extraction, deletion, storage, append, sibling and finalization cases | **95 passed**, no failures |
| Independent count/filter/oracle matrix, including populated Incoming/Outgoing x three stages x four roles | **137 passed**, no failures |
| New calendar boundaries plus External workflow | **54 passed**, no failures |
| Signed-download smoke and related access/outer request cases | **44 passed**, no failures |
| JavaScript | **172 passed**, no failures |
| Templates and lint | **108 templates clean; Ruff clean** |
| Isolated schema, ordinary Django checks and migration drift on `cd4316a` | Passed; migrations 0012/0013 applied, no pending migrations, six isolated document rows retained, no model drift |
| Isolated workflow self-check, earlier audit snapshot | **21 passed**; historical within this audit, not rerun on the final revision |
| Page smoke, earlier audit snapshot | **231 requests across four roles**, no unexpected page errors; not rerun on the final revision |
| Production-shaped deployment configuration check | Passed at warning failure level; no issues silenced; provider connection attempts blocked and zero attempted |
| Dependency compatibility and vulnerability audit | Compatible; **72 packages audited, zero known vulnerabilities, zero skipped**; no package changes |
| Final expanded live browser on `cd4316a` | **One test passed: 306 checks, 187 filter updates, 321 navigations, 96 role/theme/width cases**; zero JavaScript or unexpected HTTP errors. See `live-browser-report.md`. |

These counts overlap and must not be added into a purported unique test total.
The final collection contains 7,574 unique cases: the complete 7,572-case run
plus only the two new wording regressions. `acceptance-summary.json` reconciles
actual testcase IDs, including the four full-run skips, rather than counts alone.
Two existing Django `CheckConstraint.check` deprecation warnings remain; they
did not fail checks and do not justify rewriting historical migrations here.

The complete-suite skips were two background-only cases (passed separately),
the opt-in live browser (run separately), and one existing office-admin Incoming
parameter whose fixture has no routed rows. The last remains an explicit fixture
gap, not a pass. All four roles now have separate populated Incoming/Outgoing
fixtures for all three working stages, with exact expected IDs and totals.

The durable post-finalization FAILED update applies to standalone/background
workers. A synchronous retry runs inside the service's existing outer transaction:
an unexpected finalization error raises and rolls the entire retry back to its
prior valid status, text, index and file metrics. It does not leave RUNNING.
No synchronous transaction semantics were changed to manufacture a partial commit.

The deliberately local audit settings correctly fail deployment checks for local
storage, the audit secret, disabled CSP/HTTPS and insecure test cookies. A separate
process-only production-shaped configuration passed. This verifies configuration
guardrails, not the actual hosting environment or provider credentials.

## Evidence, review and limits

`coverage.md` and `inventory.json` inventory 246 routes, 55 form classes, 40
explicit query-control references, 95 service functions, six public task entry
functions plus five private task helpers, and ten
management commands. `execution-ledger.md` connects areas to executed evidence
without converting every discovered item into a pass. `independent-final-review.md`
records the latest independent combined review and its bounds; `final-review.md`
retains the earlier review and diagnostic follow-ups.

Machine-local raw runs, JUnit, coverage and browser JSON are kept beside this
report. `acceptance-summary.json` records final exact test-ID reconciliation,
with the accepted complete-run artifacts under `full-suite-verified/`.
Earlier failed/interrupted runs remain diagnostic history and are not counted as
fresh passes. Reproduction helpers and focused regressions are in the branch.

The frozen 452-file manifest matches final source byte-for-byte except for the
intentional one-line Reports hint correction. The only additional source file
is its two-case regression. `tested-source-before.json` and
`tested-source-after.json` retain that distinction. The full suite and final
browser predate this wording-only delta; the focused 56-case run, template
checks and lint verify the final delta. No backend code changed after the
complete run.

Browser responsive coverage is Tracking and External Repository at 320, 390,
768, 900, 901 and 1366 pixels in both actual themes for four roles. The 96
combinations do not cover every page. Browser filtering checks compare current
backend IDs and headings; the separate fixture matrix provides independent
expected IDs and totals. A removed session cookie simulates lost authentication,
not a real 15-minute wait. Print invocation and print CSS were checked; 200%
zoom used CSS emulation.

Unverified exhaustively: every raw Django-admin action, every button/link for
every role/state, every possible filter combination, and all pages at every
viewport. Physical devices, real screen readers, physical printer output,
browser/OS magnification, actual cloud/email/OCR delivery, production TLS/proxy,
running worker operations and backup restoration remain unverified. No business
decision is currently required for a reproduced product defect found in this
audit. These verification limits remain part of the delivery.
