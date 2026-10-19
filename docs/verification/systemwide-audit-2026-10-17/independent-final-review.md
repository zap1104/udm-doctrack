# Independent final code review

Reviewed on 18 October 2026. Baseline: `bb84ac863937a27dc871e115db694b2e84a78f73`.
Initial product snapshot: `666b7b3` (`e1edaa2`, `f5f4810`, `666b7b3`).
Scoped repair recheck: `05e49f39b353016292c320eddfaeae308232f64b`.

This reviewer did not implement the reviewed changes, mutate application source,
run database tests, or access business data. Findings below come from the actual
diff, current source, and inspected regressions. The coordinator is responsible
for isolated runtime reproduction and final test results. Full-suite and browser
runs were still in progress when this review was written.

## Finding addressed after review

### Addressed P2: A cancelled clean-state append could disappear behind a successful sibling

Locations: `apps/documents/tasks.py`, `_cancel_extraction` (lines 50-57), and
`_extract_document_task` finalization (lines 234-241).

Trigger:

1. Start with a document whose extraction is DONE and has no failure latch.
2. User A adds file A asynchronously, so the file is saved and its extraction job
   captures the current generation.
3. Revoke A's write authority before delivery. The job cancels to SKIPPED with
   explanatory notes but does not set `extraction_failed_generation`.
4. Authorized user B adds file B. Its successful append belongs to the same
   generation and reads B only.

The finalizer sees successful text and no failure latch, writes DONE, and replaces
the cancellation notes. A remains saved with unextracted text, but the document
now reports successful extraction. A cancelled full retry can produce a related
gap because it has already superseded older append generations. Existing
`test_cancelled_retry_cannot_hide_failure_when_authorized_sibling_succeeds`
starts with an earlier FAILED state, so it does not cover the clean-state trigger.

The initial state transition was confirmed from source. The saved reproduction
log `extraction-clean-cancel-red.txt` confirms both normal-append and full-retry
cancellation cases failed for the expected DONE-versus-FAILED assertion before
the repair. This reviewer read that evidence but did not execute the sequence.

Commit `05e49f3` now retains a generation marker and cancellation notes for every
current cancelled extraction, while preserving its persisted and returned
SKIPPED status. A later targeted append inherits that marker, so its success
cannot conceal the unread file; an authorized complete retry clears it only
after covering all current files. Generation and active-row guards remain in
place, and work never runs under the revoked actor. The new clean-state test
checks both cancellation paths, unread-file metrics, retained text and notes,
complete recovery, and rejection of a late original job. The saved related
result `extraction-clean-cancel-related-final.txt` reports 95 passed, two existing
warnings, in 36.32 seconds. Source and inspected assertions support closing this
finding; this is not a claim that the reviewer reran those tests.

## Accepted synchronous transaction boundary

### Synchronous retry rolls back the finalization failure marker

Locations: `apps/documents/services.py`, `re_extract_document` (lines 422-439);
`apps/core/permissions.py`, `locked_mutation` (lines 28-32); and
`apps/documents/tasks.py`, `_extraction_finalization`.

With background tasks disabled, the public retry service invokes the extraction
task inside `locked_mutation`'s outer transaction. If suggestion creation or
index finalization raises, `_extraction_finalization` records FAILED after its
own inner transaction has rolled back and rethrows the original exception.
That exception then exits the outer `locked_mutation` transaction, rolling back
the newly recorded FAILED status and retry notes as well. The standalone worker
tests do not exercise this public synchronous service boundary.

Previous status, text and index remain protected, and the request errors rather
than returning false success. There is no externally committed RUNNING state
from this rolled-back request. The coordinator accepted this boundary as the
existing synchronous atomic contract. Commit `05e49f3` adds public-service
controls for suggestion and index failure, asserting exact preservation of
document status, text, notes, index, generation, timestamps and file metadata,
plus stored bytes and absence of a committed suggestion. No transaction refactor
is requested. The final
report must limit the durable FAILED-and-retry-note guarantee to standalone
background worker finalization; it must not claim that guarantee for an
unexpected exception escaping a synchronous atomic retry request.

## Reviewed changes with no additional actionable finding

- Notification lists, unread totals and read actions now inherit referenced
  record/document visibility for staff and viewers as well as office admins.
- Private internal download signatures bind account, file and file kind, expire,
  and retain fresh object access checks. Inspected template links use the signed
  helper, and responses disable private-file caching.
- Storage compensation is outside the relevant atomic blocks. Registered files
  whose rows survive a committed callback error are retained; rolled-back new
  objects are eligible for cleanup. Outer request scopes cover grouped mutations.
- Appends capture a generation before provider I/O. Result and metric writes are
  fenced after fresh document reloads. Successful complete retries retire covered
  generation jobs and callbacks under the document lock.
- Queue/provider failure latching covers same-generation siblings and only a
  complete successful full retry clears that failure. Existing usable text is
  retained when retries are incomplete.
- Finalization compensation runs outside the standalone worker's failed result
  transaction and checks current generation and deletion state before writing.

## Test infrastructure recheck

The reviewer also read the current `config/audit_settings.py` and
`scripts/run_systemwide_tests.py`; no additional actionable issue was found.

- Audit settings validate the worker slot and override both NAME and TEST.NAME
  to the same `test_doctrack_audit_...` database. Local file storage, locmem email,
  disabled provider credentials, and a fixed database cache table prevent
  inherited business storage/email/cache use. The complete CACHES assignment
  replaces a configured Redis service rather than extending it.
- The runner forces audit settings in subprocess environments and constructs
  worker slots only as `full_0` through `full_3`. Preparation verifies its audit
  prefix and exact equality of the configured NAME and TEST.NAME to that slot
  before database preparation and flushing. It rechecks the target after
  creation and rejects non-database cache backends before cache clearing.
- Preparation and flushing occur sequentially before any worker is started.
  Concurrent workers receive disjoint collected node lists, database names,
  media directories and coverage outputs. Failure or an incomplete reported
  count cannot produce a successful final result.

These are source-level safeguards. This reviewer did not run the preparation,
cache probe, database workers or browser harness. The full suite must still be
completed and reconciled at the final source snapshot.

## Verification limits

This is a source review, not proof that every route or business rule is correct.
The reviewer inspected regression assertions but did not independently execute
them. No final full-suite count, live browser outcome, physical-device result,
screen-reader result, real OCR/storage/email integration, deployment or release
is asserted here. The final report must cite completed results for the final
repaired revision and retain any coverage gaps.

## Final wording-only delta review, 19 October 2026

A separate reviewer inspected the final Reports literal and its two regression
cases. Reversing that single literal reproduces the frozen template hash; no
query, count, status or template structure changed. The tests cover both empty
and retained extracted text and preserve the skipped count. No actionable issue
was found in this narrow delta. The reviewer inspected saved runtime evidence
and did not rerun database tests.
