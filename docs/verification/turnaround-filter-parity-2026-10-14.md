# Turnaround and workspace filter verification

## Requested behavior

Received time remains part of processing. Processing pauses during endorsement
receipt waits and resumes after confirmation. Tracking and Repository gain the
appropriate filters already available on Search, with automatic dropdown updates.

## Implementation

- Dashboard, Reports, daily/monthly charts and Tracking CSV share the same
  processing calculation. It merges overlapping receipt waits, includes routing
  history before the selected completion month, and caps superseded waits at the
  next valid batch. Invalid future sends cannot alter completed processing.
- Received idle time remains included. Total lifetime and each office's timeline
  custody measurements retain their existing meaning. Receipt averages remain
  per endorsement; processing averages remain per completed document.
- Tracking and Tracking Search share text queries, queue/stage/office/ownership
  filters, stable ordering and CSV selection.
- Filed Repository documents gain document dates, owning office and real Search
  relevance controls. Relevance is shown only for text searches. Pending filing
  retains tracking metadata and does not offer filed-document scoring controls.
- Repository ranks and paginates all qualifying matches. Facets, result totals
  and below-threshold counts use the same qualifying document set. Raw text
  remains searchable before its full-text index is ready.
- Repository's default relevance minimum is 0%, preserving existing queries;
  Search retains its configured default. Both use the same scoring service.
- Permission-scoped base querysets remain the starting point. Office parameters
  only narrow access, including code/ID links and unavailable office folders.
- Dropdowns, dates and relevance changes use the existing HTMX update behavior.
  Text searches submit through Search or Enter. Errors stay visible, zero values
  render accurately, and no Apply button was added.

## Verification evidence

- Final complete suite: 7,169 passed, 3 skipped, 2 existing Django deprecation
  warnings. Two skips require background jobs to be enabled; the other uses an
  office-admin fixture with no incoming rows.
- Background-enabled regression: 46 passed, including both skipped schedule
  checks. Final theme/accessibility regression after the checkbox correction:
  100 passed.
- Final focused regression: 61 passed, including the future intermediate batch
  case, permission/score/pagination parity and accessible field labels.
- JavaScript suite: 172 passed.
- Browser: 28 page/theme/viewport configurations and 8 genuine HTMX filter
  interactions passed. Desktop/mobile and light/dark layouts were inspected.
  The relevance checkbox sizing regression was reproduced and corrected.
- Turnaround audit: 45 checks, no failures, no persistent database changes.
- Django configuration check: no issues. Migration check: no changes detected.
- Independent source reviews covered timer arithmetic and filter/permission
  boundaries. Screen-reader operation was not manually tested.

The complete result is recorded in `turnaround-filter-full-suite-final.txt`.
The initial full run exposed a missing
field assumption in the shared relevance partial; the existing accessibility
test reproduced it, and the correction passed the focused run.

Changes are local on `codex/turnaround-and-filter-parity`. Existing unrelated
settings edits were not changed by this task.
