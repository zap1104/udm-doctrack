# Count and report reconciliation

Verified against the local PostgreSQL data on 1 October 2026. These are a
snapshot, not constants to place in the application.

| Measure, all offices as system administrator | Verified count |
| --- | ---: |
| All tracked records, including completed history | 280 |
| Records still listed in Tracking | 84 |
| Pending Receipt stage | 13 |
| Received stage | 11 |
| In Process stage | 20 |
| Completed, pending upload | 40 |
| Completed and approved into the repository | 196 |
| All finished tracking records | 236 |
| Overdue, excluding both completed stages | 32 |
| Active filed repository documents | 256 |
| Historical repository documents | 60 |

Tracking's current 84 = 13 + 11 + 20 + 40. Reports' lifetime 280 = 84 + 196.
Finished work is 236 = 40 awaiting filing + 196 filed. The repository's 256
includes 196 approved tracking records and 60 historical documents. Adding
280 and 256 would count the 196 approved records twice. The dashboard's
combined current tracking/repository total is 340.

## Findings and fixes

| Finding | Final behavior | Code |
| --- | --- | --- |
| Reports called 21 documents Pending Receipt, including 4 Received and 4 In Process documents with an outstanding recipient | The headline counts the actual stage, 13. The 8 partial confirmations are explained separately. The headline and stage chart open the same report record list | `apps/core/views.py`, `templates/reports/reports.html` |
| An office's Overdue headline counted only its next actions while opening all scoped overdue records | The headline and linked record list count the same overdue population. The office's responsibility is stated separately | `apps/core/views.py`, `apps/core/report_filters.py`, `templates/reports/_records.html` |
| Report CSV ignored the filtered record list | List and CSV share stage, type, month, query, and overdue filters. The sheet names its filters and existing 5,000-row cap | `apps/core/report_filters.py`, `apps/core/views.py` |
| Repository type, tag, and retention badges counted an unfiltered folder while links retained other filters | Each facet counts the exact result of its link, preserving other valid filters. Retired types remain usable for existing records | `apps/documents/views.py` |
| Whole-folder and list-tab totals opened narrowed lists | Folder and Filed/Pending tabs open their stated whole-office population. List filters are reset when switching those populations | `apps/documents/views.py` |
| A denied folder's zero-count tabs could reset to all accessible records | Tab and form URLs retain the denied folder; they remain empty and disclose no document or office name | `apps/documents/views.py`, `templates/documents/repository.html` |
| Search treated its browse/candidate limit as the total | Total matches count the complete permission-scoped query. Evaluated candidates, displayed results, and below-threshold candidates are separate; truncation is disclosed | `apps/search/services.py`, `templates/search/_results_repository.html` |
| Administration cards counted active entries but opened lists including retired entries | Active labels and filters now match their destinations | `templates/administration/home.html` |
| Tag usage displayed a stored counter that can be stale or include inaccessible records | The tag table counts active documents visible to its administrator, with stable pagination | `apps/core/views.py` |
| The overdue office total treated the “Other offices” remainder as one office | It includes each office in the remainder | `apps/core/analytics.py` |

## Verification performed

- Full live reconciliation: **12,851 comparisons**, **26 role/office views**, no mismatches.
- Follow-up reconciliation: **3,795 comparisons**, six representative scopes across all four roles, no mismatches.
- Final checks after the last changes: **197 checks**, covering exact CSV record sets, capped search totals, individual file badges, denied-folder navigation, stable tag ordering, and compilation of every HTML template. All passed.
- The matrix covers dashboard totals, Action Centre queues, chart segments, stage drilldowns, monthly additions, cumulative activity, handovers, confirmed receipts, monthly turnaround sample counts and averages, annual/daily graph points, completion deadlines, retention links, repository facets, notifications, administration counts, and tracking/search agreement.
- Data integrity checks found zero Pending Receipt records without a current outstanding step, finished records without completion timestamps, completions before creation, or Received/In Process records without first-receipt timestamps.
- Django application checks and Ruff passed; the diff has no whitespace errors.

All live audit sessions and request log writes were rolled back. No stored
document data was corrected or removed. Charts count documents, receipt events,
or handovers according to their stated units; these are not interchangeable.
Tracking office scopes include routing history, while repository folders use
the document's owning office and pending folders use its originating office.
These legitimate scope differences must remain explicit.

`scripts/audit_counts.py` provides the repeatable audit; `--quick` selects
representative scopes. An optional output path writes aggregate results without
document content. `tests/test_metric_drilldowns.py` adds regression coverage for
the failures above. The pytest/CI suite was not run in this change-only turn,
following the user's instruction to reserve it for pushing. Nothing was pushed.
