# Confirmed sibling extraction failure disclosure defect

Confirmed and repaired on 18 October 2026 after the initial final integration
review. The focused/related verification below is fresh evidence for this
repair. The coordinator owns independent acceptance and the final combined
source-tied suite. The initial no-unresolved-defect assessment was withdrawn
while this defect was open.

## Contract and trigger

`Document.ocr_status` and `ocr_notes` describe document-level extraction and
operator retry information. Existing
`test_failed_added_file_preserves_text_and_records_failure`,
`test_retry_extraction_reads_all_files_and_reports_missing_secondary_file` and
`test_failed_retry_preserves_last_searchable_text` establish that a failed saved
file remains discoverable and that usable text survives failure.

Two added files receive append jobs in the same extraction generation. In one
case, the first job cannot be submitted to the queue. In the other, its provider
returns FAILED. The other file's job subsequently succeeds.

Expected: both saved files and usable text remain, and the document continues
to disclose the unresolved extraction failure with retry notes.

Actual in both cases: the document becomes DONE with empty notes, although the
failed file's text has never been extracted. The successful file's text and the
previous usable text survive. This is false completion and hidden retry
information, rather than file loss.

## Reproduction and evidence

New regression: `tests/test_systemwide_sibling_failure.py`, parameterized for
queue and provider failures. It uses real service transactions, PostgreSQL,
temporary private files and extraction task writes. Only queue/provider
boundaries are simulated. The expected state is asserted directly, without
using production state-derivation helpers to compute it.

Command environment: `config.audit_settings`,
`DOCTRACK_AUDIT_SLOT=sibling_review`, `ENABLE_BACKGROUND_TASKS=True`.

    .venv\Scripts\python.exe -m pytest tests/test_systemwide_sibling_failure.py --reuse-db -q

Fresh result: **2 failed**, 2 existing Django deprecation warnings, 10.32 seconds,
exit 1. Evidence: `sibling-failure-red.txt`.

Both fail at the intended assertion: expected persisted FAILED, actual DONE.
Diagnostic assertion values show empty `ocr_notes`, three saved files, zero
extracted characters for the failed file and positive extracted characters for
the successful file. This was not a setup, connection or provider-call error.

## Root cause and smallest repair direction

- `apps/documents/services.py::_enqueue_extraction` records queue failure only
  while the document is PENDING. The failure is held only in transient status
  and notes. A later add resets the status to PENDING.
- `apps/documents/tasks.py::_extract_document_task` sets RUNNING at startup and
  derives final status/notes from its selected files. A successful sibling does
  not retain another same-generation job's failure.

Persist current-generation failure provenance independently of transient
running state. A narrow generation-scoped failure latch plus retained operator
notes is one option; explicit per-file outcomes are another. Successful append
jobs must retain unresolved same-generation failure information. A successful
full retry that covers all files may clear it. Keep fresh authorization,
generation fencing, existing text and storage behavior unchanged.

Also cover a queue submission failure while a sibling is already RUNNING; the
PENDING-only failure update must not silently suppress that failure. Old jobs
must remain unable to mark a newer generation failed. Do not infer failure from
zero extracted characters, since legitimately empty/skipped files can have zero.

## Implemented repair and final local verification

After the coordinator assigned implementation to this agent:

- Added nullable `Document.extraction_failed_generation` in additive migration
  `0013_document_extraction_failed_generation`. Migration 0012 is unchanged.
  No bulk rewrite of historical records is performed.
- A bounded, deduplicated operator note and unresolved failure latch survive
  sibling PENDING/RUNNING transitions and successful append results. Existing
  FAILED rows adopt the latch when an authorized mutation next touches them.
  Synchronous added-file failures follow the same rule.
- Queue callbacks lock the fresh active document and match their captured
  generation. Failures are disclosed even when a sibling is RUNNING or DONE.
  Old-generation callbacks cannot change a newer generation.
- Cancelled or skipped full retries do not clear an unresolved failure. Actor
  revocation still cancels that task; a different authorized actor's later
  append cannot hide the original failure or its notes.
- Permission cancellation also records an attention latch when the document
  previously had no failure. Its persisted and returned state remains SKIPPED;
  a later successful sibling still discloses the unread file until an authorized
  complete full retry succeeds. This covers a cancelled append and a cancelled
  full retry that superseded an earlier append.
- Only a successful full retry covering all files clears the latch. Targeted
  replacements do not clear it or advance the full-retry generation. A complete
  successful retry retires its consumed generation, so delayed append jobs or
  queue callbacks for files it already covered cannot falsely reintroduce a
  failure. Uploads made after its locked commit capture the next generation.
- Usable text, saved bytes, permissions and the existing generation checks are
  retained. No per-file outcome model, new provider or integration was added.

The expanded callback test initially mocked the queue too late, after callback
registration. `sibling-expanded-red.txt` therefore contains three invalid
harness failures and is not accepted reproduction evidence. The corrected
`sibling-expanded-red-fixed-harness.txt` records **8 intended failures and 1
passing stale-generation control**. `sibling-clearing-red.txt` adds two correct
boundary failures (2 failed, 10 passed). `sibling-cancel-red.txt` records the
cancelled-retry defect's intended failure before its repair.

Final targeted cases total **15**. They cover both original failures, queue
failure during/after sibling success, historical and synchronous failures,
successful full clearing, queued/direct targeted replacement, old callback
rejection, late already-covered callbacks, skipped full retry and cancellation
after actor revocation.

An independent follow-up found that clean-state permission cancellation lacked
an unresolved marker. `extraction-clean-cancel-red.txt` records its two intended
failures and two passing synchronous rollback controls, before the narrow
cancellation correction. The current code always retains cancellation
provenance while leaving the cancelled task SKIPPED.

Fresh final command in `sibling_review`, background tasks enabled:

    .venv\Scripts\python.exe -m pytest tests/test_systemwide_extraction_finalization.py tests/test_systemwide_sibling_failure.py tests/test_processing_regressions.py tests/test_security_maintenance.py tests/test_queued_extraction_security.py tests/test_deletion_purge.py tests/test_systemwide_storage_jobs.py tests/test_systemwide_append_generation.py tests/test_systemwide_outer_storage.py --reuse-db -q

Result: **95 passed**, 2 existing Django deprecation warnings, **36.32 seconds**,
exit 0. Evidence: `extraction-clean-cancel-related-final.txt`. This includes
15 sibling cases and six finalization/synchronous-boundary cases. The earlier
87-case and focused 12-case passes predate later cancellation/finalization
regressions and are not the final result.

Fresh Ruff check for all changed product/migration/test files and
`git diff --check` passed. Fresh `makemigrations --check --dry-run` reported
`No changes detected`; `showmigrations documents` showed migration 0013 applied
in the disposable `test_doctrack_audit_sibling_review` database. The business
database was not migrated or used.

Product source was frozen and handed to the coordinator for independent review
and full combined verification. No commits or pushes were made by this agent.
