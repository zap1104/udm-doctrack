# Extraction finalization failure repair

Confirmed and repaired on 18 October 2026 after the sibling extraction repair.
The coordinator owns independent acceptance and final combined verification.

## Confirmed defect

For a direct/background extraction task, startup committed RUNNING. The provider
then succeeded, but an exception while inserting its metadata suggestion or
rebuilding the index rolled back the result transaction. Usable text, the old
index and files survived, but RUNNING remained indefinitely with no retry note.
The worker exception itself was already observable.

`tests/test_systemwide_extraction_finalization.py` reproduces both failures with
real task execution, PostgreSQL transactions and temporary private files. Only
the provider and named failure boundary are simulated.

Fresh RED: **2 failed**, 2 existing warnings, 4.25 seconds, exit 1. Both fail at
the intended persisted-state assertion: expected FAILED, actual RUNNING.
Evidence: `extraction-finalization-red.txt`. Earlier assertions establish that
text/index rollback, file preservation and exception propagation worked.

## Repair

`apps/documents/tasks.py::_extraction_finalization` surrounds the existing final
result transaction. After a failure has rolled back that transaction, it locks
the fresh active document matching the job's captured generation and records a
failure latch with safe retry instructions. The original exception is raised
again so queue failure reporting and retries still observe it. Failure details
stay in the server log rather than operator-facing notes.

The handler does not restore deleted records or overwrite a newer extraction
generation. If the database is itself unavailable while recording the failure,
that problem is logged and the original task exception is still preserved.
This latter recovery path is source-reviewed behavior, not a claim that a live
database outage was simulated in these four cases.

Two additional cases directly exercise the post-rollback error boundary
after a newer full retry has committed, or after the document becomes inactive.
They verify the fresh-row guards. They are deterministic boundary tests, not
independent concurrent-worker tests.

Two further controls invoke the public `re_extract_document` service with
background processing disabled and inject the same index/suggestion-write
errors. Its existing outer locked transaction rolls back the entire operation,
including startup, file metrics, generation and result writes. Original
status/text/index/files remain exactly unchanged and the original exception is
raised. This synchronous boundary did not have the committed RUNNING defect;
its transaction semantics were deliberately preserved.

No extra schema change beyond the sibling repair's additive migration 0013 is
required. This repair does not change file storage, business permissions,
provider selection or the saved extraction text on failure.

## Fresh final related verification

Environment: `config.audit_settings`, `DOCTRACK_AUDIT_SLOT=sibling_review`,
`ENABLE_BACKGROUND_TASKS=True`. All file/provider boundaries remain local or
simulated. No business database was used.

    .venv\Scripts\python.exe -m pytest tests/test_systemwide_extraction_finalization.py tests/test_systemwide_sibling_failure.py tests/test_processing_regressions.py tests/test_security_maintenance.py tests/test_queued_extraction_security.py tests/test_deletion_purge.py tests/test_systemwide_storage_jobs.py tests/test_systemwide_append_generation.py tests/test_systemwide_outer_storage.py --reuse-db -q

Latest result: **95 passed**, 2 existing Django deprecation warnings,
**36.32 seconds**, exit 0. Evidence:
`extraction-clean-cancel-related-final.txt`. This includes six finalization and
synchronous-boundary cases, plus all 15 sibling cases after an independent
clean-state cancellation review. The earlier 91-case run in
`extraction-finalization-related-final.txt` predates those four added boundary
cases. Fresh Ruff checks and `git diff --check` also passed.

Product source was frozen and returned to the coordinator for independent
review and a new complete source-tied suite. No commits or pushes were made by
the implementing agent.
