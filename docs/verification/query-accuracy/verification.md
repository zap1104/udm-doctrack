# Query and count accuracy audit, 8 October 2026

Scope: repository and pending filing, Tracking queues and CSV, dashboard cards and charts, Reports, search and autocomplete, retention, notifications, administration counters and lists. Preserve permission-scoped querysets and the existing lifecycle. No production records were edited or migrated.

## Confirmed findings and corrections

1. Repository total included 257 documents, while monthly columns included only 249. Eight documents preceded the twelve-month chart window. The chart now includes an Earlier bucket, a Later dates bucket when needed, a reconciliation statement, and source/table totals. Every active visible document belongs to exactly one bucket.
2. Native Tracking's CSV link discarded the page filters and exported filed history. The native link now carries repeated filter parameters and a source marker; the table and export share one queryset builder. Legacy report-filter exports retain their history behavior and CSV formula escaping. The 5,000-row export cap remains explicitly stated.
3. Tracking, Tracking search, notifications, and the system audit log had no unique final sort key for identical timestamps. Their pagination now uses the primary key to resolve ties. Separate regression cases reproduced the problem on notifications and the audit log before the fix.
4. Repository pending filing excluded a legacy archived flag even when the authoritative state and absent repository-document relation still said pending filing. Removed that extra exclusion so the two queues agree.
5. Duplicate document titles and title/tag overlaps could consume autocomplete slots. Suggestions now group titles case-insensitively before applying the remaining limit.
6. Plain search fallback omitted extra metadata such as descriptions and author/recipient/signatory fields. It now includes the existing metadata index.
7. Search could evaluate every candidate but silently drop results at the output cap while reporting no truncation. Result-cap metadata and messaging now distinguish evaluated matches, relevance exclusions, and results beyond the output limit. Search retains its configured candidate and output ceilings.
8. Unclassified repository documents could be hidden inside Other when enough main types existed. Unclassified stays separately visible; type rows still partition all documents once.
9. An overdue report with only unattributed work could say Nothing is overdue. The empty-office state now describes the missing attribution accurately.
10. The extraction attention count was labelled Without extracted text, although it deliberately counts FAILED and EMPTY states and lists queued/skipped states separately. Renamed it Extraction needs attention to describe the actual metric.

## Definitions preserved

- Current-work Reports exclude Completed and Completed Pending Filing. Dashboard Tracking workload includes the separate pending-filing queue. Completed history remains available to the repository and historical analytics.
- Main-type report partitions count each document once. Multi-type filters can intentionally overlap, since one document can have several selected types.
- Office Incoming/Outgoing describes current office routing. Drafts and passed-on records can belong to Total without belonging to either current direction.
- Handovers count routing steps, not distinct documents. Historical turnaround samples and current queues use different dates and populations intentionally.
- SYSTEM_ADMIN alone may select other offices. Every base query remains permission-scoped before applying filters.
- Long-list All, relevance search, and CSV retain their existing safety limits. Limited results must be explicitly described.
- Office administrators' Repository filing queue remains restricted to records created by their office. Its description now explicitly states this. Tracking can include other visible routing work; access was not widened to make different populations match.

## Verification evidence

- New regressions reproduced 30 search/export/pagination failures before the corresponding corrections, plus two chart partition failures. The first integrated focused run passed 38 cases.
- `repository-reconciliation.json`: live report total 257, chart total 257, recent months 249, earlier 8, later 0.
- Live page checks run in rolled-back transactions; session/request logging does not persist. The audit scripts compare chart partitions, filters, facet links, queue links, CSV row counts, turnaround samples and office permission scopes with independently counted records.
- Updated the existing audit scripts to use current Reports drilldown URLs and multi-type filtering. Their obsolete expectations for removed report tables and global master-data access by office administrators were corrected without changing access permissions.
- Live counts: **21,224 checks**, 26 role/office scopes, no mismatches. Four checked lifecycle/timestamp integrity conditions returned zero invalid records. See `live-counts.json`.
- Live filters: **663 checks** across SYSTEM_ADMIN, ADMIN, USER, VIEWER, no mismatches. See `live-filters.json`.
- Targeted verification: **192 passed** across regressions, chart rendering, pinned query counts, repository workspace and permissions. The subsequent CSV check passed **28 cases**, including invalid filter metadata and formula escaping.
- Python lint passes; Django system checks found no issues; migration dry run found no changes; all **90 templates** pass the template checker; `git diff --check` passes.
- Headless Chrome rendered-template previews checked desktop light/dark and exact 320px/390px iframe viewports. Document widths matched both mobile viewports, and the automatic table fallback was exposed. These are template previews, not a signed-in interactive-browser or screen-reader audit. See `layout-checks.json` and screenshots. Temporary browser profiles and rendered HTML were removed after verification.
- The first full run passed 6,818 tests and found three failures: compact mobile month labels had been removed, and two pinned query budgets still expected 44 reads. Restored compact labels; the consolidated chart now performs 43 report queries instead of 44. The 192-case run verified these corrections.
- **Final full suite: 6,828 passed, 2 skipped, 2 existing Django deprecation warnings, 465.92 seconds.** See `pytest-final.txt`. One skip is the intentionally empty office-admin Incoming fixture; the other is exercised in the background-enabled verification step.
- **Background-enabled CI subset: 26 passed**, including processing regressions, queued-extraction security, and maintenance. See `pytest-background.txt`.
