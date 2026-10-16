# Count and filter reconciliation implementation plan

**Goal:** Verify that dashboard, reports, Tracking, Repository, search and exported results agree about the records counted, including role/office boundaries and all workflow states; fix confirmed inconsistencies without changing unrelated behavior.

**Architecture:** Retain the existing permission-scoped querysets and shared tracking/filter/analytics helpers. Compare actual record IDs and explicit independent expected sets, not just matching copies of a count formula. Centralize only definitions that demonstrably diverge.

**Stack:** Django 5, PostgreSQL, Bootstrap 5, HTMX, Windows PowerShell.

**Spec:** User request in this chat, 12 October 2026. Preserve existing work and data; no commit/push requested.

## Invariants

- Filters only narrow `visible_to(user)`; only system administrators choose another office's Tracking perspective.
- Stage partitions use distinct document IDs and reconcile with their stated denominator. Completed and pending-filing work do not enter current-work totals.
- Incoming and Outgoing are relative to a selected office and use the current routing batch. Draft/other categories must be explicit when needed to reconcile.
- Overdue is a cross-cutting subset, not an extra status; endorsements count routing events, not unique documents.
- Each tile, chart segment, folder, tab, search total, pagination count and export uses the same records as its destination URL.
- Invalid, repeated, stale and unauthorized office/filter values cannot silently widen access or mislabel the result.
- Removed repository documents remain outside ordinary lists, searches and reports.

## Tasks

- [x] Inspect shared query definitions, consumers, existing reconciliation coverage and UI request state; audit independent domains in parallel.
- [x] Run the rollback-based live count audit and focused agreement tests to establish current evidence.
- [x] Reproduce confirmed gaps with focused regressions covering exact record sets, all relevant roles and URL/filter combinations.
- [x] Apply minimal fixes in shared definitions or the incorrect consumer, preserving compatible URLs and existing workflows.
- [x] Verify changed paths, browser filter/history behavior, live reconciliation, template/lint checks, and the full backend suite. Six outdated full-suite expectations were corrected and passed in the final 70-case rerun; background-only checks passed separately.
- [x] Record precise counting definitions, fixes, coverage and remaining verification limits in `docs/verification/count-filter-reconciliation-2026-10-12.md`.

Only one PostgreSQL test suite runs at a time. Live audits use rollback transactions and do not mutate existing documents, files or routing history.
