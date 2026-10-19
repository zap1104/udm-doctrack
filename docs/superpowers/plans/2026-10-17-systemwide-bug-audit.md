# DocTrack: Systemwide Bug Audit and Repairs

Approved source: the user's implementation request in this chat, 17 October 2026.
Baseline: bb84ac863937a27dc871e115db694b2e84a78f73.

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**

Goal: audit every existing feature, reproduce defects, make focused repairs and
verify the final revision. Do not promise absence of every possible bug.
Use branch `codex/systemwide-bug-audit`; preserve UI, data, office boundaries,
numbering and history. All actions/failure tests use disposable PostgreSQL and
temporary local files. Storage/OCR/email remain simulated. No push/deployment.

## Tasks and acceptance

- [ ] Establish baseline, route/feature/filter/job/command coverage ledger and
  isolated test settings. Preserve unknown local captures and reports.
- [ ] Repair audit_counts/audit_filters collection isolation, optional links,
  displayed draft identifiers, retired-office choices and tag-usage oracle.
  Add independent expected-ID mixed-collection/filter reconciliation checks.
- [ ] Audit all roles, notification object visibility and signed downloads;
  reproduce before repair. Keep read and mutation authority distinct.
- [ ] Reproduce and repair storage rollback, extraction queue failure and
  out-of-order extraction risks. Verify concurrency, permissions and history.
- [ ] Audit internal and external tracking loops, current batches, partial
  recipients, completion/filing/reopen, manual corrections, timers/calendar,
  search/ranking/exports, deletion recovery/purge and supporting account,
  administration, notification and background features. Extend existing tests.
- [ ] Add live browser verification with real login/CSRF, backend responses and
  persistence checks. Exercise filters, rapid changes, delayed responses,
  errors/retry, reset/pages/history/session expiry, role-allowed/refused actions,
  print/downloads, keyboard/zoom and light/dark widths 320/390/768/900/901/1366.
- [ ] Run complete PostgreSQL and background tests, JS, lint/templates, system/
  deployment and migration checks, isolated migrations/selfcheck/smoke, and
  reconciliation. Obtain independent review and address confirmed defects.
- [ ] Deliver final-revision report: repaired defects, exact outcomes, ledger,
  limitations and any unresolved business decision. Correct stale docs.

For each defect: record reproduction and cause; watch a regression fail; apply
the smallest fix; pass related checks; commit scoped changes. Compare entity
sets, not just counts. Explain overlapping facets and partial chart periods.
Invalid filter behavior remains endpoint-specific. SYSTEM_ADMIN alone has
global administration; office ADMIN remains scoped; staff/viewers retain
existing legitimate shared/history visibility. All state changes require their
own authority. External additions must not alter legacy OVPA populations.

Prioritize notifications referencing inaccessible objects; download signatures;
orphan storage; committed work followed by queue failure; reverse retry order.
Every feature/role/filter/job/command must be marked passed/failed/unverified,
including missing fixtures and skipped scenarios. Physical devices, screen
readers, real providers and deployment remain unverified unless actually tested.
