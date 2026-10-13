# Independent lifecycle recheck: 13 October 2026

The user requested a fresh verification of the previous correction. The recheck used current source, independent reviewers, real PostgreSQL tests, rollback-only live audits and newly rendered browser fixtures. It did not assume that earlier test results or the selected reasoning setting proved correctness.

## Lifecycle confirmation

The reported slip `UDM-OVPA-REC-2026-10-0044` has confirmed HR custody and an HR work update, while MED has not confirmed receipt. A fresh live recheck at 11:44 confirmed HR In Process, MED Pending Receipt, and outgoing/all-offices Pending Receipt with zero assertion failures (`status-lifecycle-recheck-reported-slip.json`). Each recipient advances only from its own current-batch receipt and subsequent work. Forwarding repeats the cycle; global completion and administrator filing policies are preserved.

Independent model/migration review found no additional lifecycle defect. Tracking, Search, Dashboard, Reports and CSV consistently use the shared stage selection and permission scope. The fresh live audit passed **10,790 checks over 1,026 requests and 15 role/office representatives**, with zero failures. Audit sessions and access logs were rolled back. See `status-lifecycle-recheck-live-audit.json`.

## Additional corrections

Four concrete gaps were found and corrected:

1. An existing overdue notice retained receipt wording until the next daily check. Confirmation now immediately updates the existing office notice to work wording, without creating another notice or changing its read markers.
2. The daily chase could write from an old snapshot after a receipt, forwarding or completion. Reminder generation now locks each candidate tracking record and reloads the current batch and outstanding conditions before writing. Real simultaneous chases produce one active sender reminder.
3. Global completion left other recipients' ROUTED notices actionable. Completion now resolves them; daily reconciliation also resolves legacy terminal receipt notices. The local repair resolved **59** such alerts, leaving **zero** obsolete completed-work receipt alerts. No notification history or document/routing history was deleted.
4. Administrator detail form submissions discarded the chosen office, switching the displayed stage to the overall summary. All seven detail forms and their detail return paths preserve an explicitly selected canonical office/all value through the existing resolver. Success and validation errors retain the perspective. Mutations still use the real user and existing permissions. Filing success continues to open the repository document.

There are **zero existing duplicate active UNRECEIVED/OVERDUE groups** in the local live database. Existing duplicates would be updated together rather than merged, preserving history; record locking prevents the confirmed source of new duplicates.

## Verification evidence

- Office navigation regressions before correction: 8 failed, 44 passed (`status-lifecycle-recheck-office-red.txt`).
- Reminder regressions before correction: 8 failed, 9 passed (`status-lifecycle-recheck-reminders-red.txt`).
- Legacy terminal receipt cleanup regression failed before correction (`status-lifecycle-recheck-legacy-red.txt`).
- Actual concurrency test with record locking disabled only in that test process reproduced two notices instead of one (`status-lifecycle-recheck-concurrency-red.txt`). Source files and database configuration were not changed by this negative check.
- Final targeted regression: **108 passed**, including the same concurrency test with normal record locking (`status-lifecycle-recheck-focused.txt`).
- JavaScript: **172 passed** (`status-lifecycle-recheck-js.txt`).
- Worker schedule checks with background tasks enabled: **2 passed**, covering checks skipped in the default configuration (`status-lifecycle-recheck-background.txt`).
- Final-code browser detail/print checks: **16 passed** for both recipient perspectives at desktop/mobile widths and in light/dark themes (`status-lifecycle-recheck-detail-browser.txt`).
- Final-code filter browser checks: **19 passed** across enhanced workspaces, all-office controls/Reports and native flows without JavaScript (`status-lifecycle-recheck-browser.txt`).
- Preview-only live stage reconciliation: **zero corrections needed**, confirming the earlier repair remains idempotent (`status-lifecycle-recheck-reconciliation.txt`).
- Static analysis, Django system checks and migration consistency checks passed. Existing unrelated line-ending notices and Django constraint deprecation warnings remain.
- Full backend run: **7,105 passed, 6 failed, 3 skipped**, in 872.06 seconds (`status-lifecycle-recheck-full-suite.txt`). All six failures referenced the removed, unrendered `desk_queues` context. The tests were updated to check the actual direction/stage and attention controls instead, retaining count-to-destination and filing separation checks and adding disjoint child-stage partition checks. No production code changed after this full run.
- Follow-up dashboard/scope regression: **116 passed**, including all six previously failing cases, direction-child partitions, every office's count links, filing previews and dashboard/report agreement (`status-lifecycle-recheck-dashboard-final.txt`). No failing case from the full run remains unresolved. The full command itself is not represented as having exited successfully and was not repeated after these test-only corrections. The 116 cases overlap the full run.

Two full-run skips concern background schedules and were separately verified with workers enabled. The third is an office-admin fixture with no routed incoming records; other recipient and role regressions exercise populated incoming queues.

The first full recheck run was stopped when the review found additional defects, so the final full run tests the corrected code. It is not counted as a completed run. Test counts overlap and should not be added into a unique total.

Changes remain local. No commit, push or deployment was requested or performed. Manual screen-reader verification remains pending.
