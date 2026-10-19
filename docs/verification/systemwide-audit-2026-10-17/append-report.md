# Append extraction generation follow-up

Cause: queued append calls passed generation=None, and legacy direct append
calls never captured a generation. Both therefore bypassed all existing
per-file and final write fences after a newer full retry completed. An older
append could overwrite file page/character metrics, append stale text, replace
notes/status, create suggestions and rebuild the search index.

## Focused repair

- apps/documents/services.py: _enqueue_extraction captures the document's
  current generation for every job. Only full retries increment it, so sibling
  append jobs remain valid together while newer full retries supersede them.
- apps/documents/tasks.py: legacy unversioned jobs capture the generation under
  the task-start document lock. Only full retries allocate a new generation.
  The redundant pre-lock permission cancellation was removed; the existing
  locked fresh actor check remains authoritative. Provider calls stay outside
  the short task transactions and all existing per-file/final fences remain.
- tests/test_systemwide_append_generation.py: four cases cover queued/direct
  old append versus newer full retry, delayed queued delivery, and sibling
  appends contributing unique text without advancing the generation.

## Evidence

All pytest runs used config.audit_settings, DOCTRACK_AUDIT_SLOT=storage,
ENABLE_BACKGROUND_TASKS=False and --reuse-db, sequentially on the disposable
local audit PostgreSQL database with temporary local storage. Queue submission
and provider results are simulated. No live database/provider or commit used.

- append-red.txt: 4 expected failures, 2 existing Django deprecation warnings,
  5.52 seconds, exit 1. Both overlap cases showed old 2-page provider results
  replacing newer 7-page metrics, stale text/notes and a second suggestion.
  The delayed append still opened its provider; sibling append generations
  were [None, None] instead of [5, 5].
- append-green.txt: the same 4 cases passed, 2 warnings, 3.64 seconds, exit 0.
- append-related.txt: 40 passed, 2 warnings, 15.75 seconds, exit 0. Includes
  the new cases plus existing overlap, append idempotence, fresh permissions/
  consent, storage jobs and reverse full-retry request/delivery order. Existing
  overlap tests still include files added while full retry processing runs.
- append-lint.txt: Ruff passed for the two changed product files and new test
  file, exit 0.

## Scope and diagnostic limits

The two new overlap cases deliberately use a same-thread reentrant provider
callback: pause A on its provider stack, finish B and snapshot all document
result fields, per-file metrics, suggestion count and search_vector, then
release A's old result. They prove the write interleave without an independent
DB thread. Existing full-retry regressions retain independent-thread coverage
and passed in append-related.txt.

append-red-thread-startup.txt preserves an earlier invalid race run: two queued
behavior failures were valid, but both threaded overlap cases failed opening
new PostgreSQL worker connections under host memory pressure. Those connection
failures are diagnostic evidence, not reproduced product race failures.
