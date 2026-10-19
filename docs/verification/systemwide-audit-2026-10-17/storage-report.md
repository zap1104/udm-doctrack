# Storage and extraction audit

All tests use `config.audit_settings`, audit slot `storage`, disposable local
PostgreSQL and temporary FileSystemStorage. OCR results and queue failures are
simulated. No provider, live database or deployment was contacted.

## Reproduced causes and repairs

- Upload, repository add-file, internal tracking attachments and external
  tracking attachments wrote storage bytes before a later SQL/service failure.
  SQL rollback removed rows but left bytes. `apps/core/storage.py` registers
  newly uploaded FieldFiles before model save and compensates failed mutations.
  Nested scopes accumulate successful child writes for outer transaction
  failure. Existing file names and original attachments are preserved. Before
  deleting, cleanup checks the exact surviving row and file name, so an
  unrelated post-commit callback failure cannot remove committed bytes.
- Queue submission ran after commit without catching queue exceptions. Saved
  work could therefore report a request failure and retain PENDING indefinitely.
  Submission now catches failures and marks the pending extraction FAILED with
  retry instructions while preserving files and last usable text. Full retries
  condition that update on their generation so older failures cannot alter a
  newer request. Root-owned views provide fresh status feedback.
- Overlapping full replacements wrote their results unconditionally. A slower
  earlier worker could overwrite newer text and per-file page metrics. Additive
  migration 0012 adds `Document.extraction_generation`; full retry requests
  allocate a generation and workers check it before opening files and before
  result, metrics, suggestion and search writes. Direct maintenance full retries
  allocate under the document lock. Provider calls remain outside the worker's
  short lock transactions. Existing authorization/consent reloads remain.
- Synchronous add-file persisted failure notes only when extraction returned
  text. Failed/skipped added files now update status/notes while retaining the
  existing searchable text and storing the original attachment.

## Evidence

- `storage-red.txt`: 13 expected failures before product edits, covering four
  storage operations at model-save and later-write boundaries, three queue
  failures, reversed provider completion and failed added-file feedback.
- `storage-green.txt`: the same 13 cases passed after initial repairs.
- `storage-related.txt`: 65 passed, 1 skipped (background schedule requires
  background-enabled settings), 2 existing Django deprecation warnings.
- `storage-postcommit-red.txt`: self-review regression failed because a
  callback exception after commit caused compensation to remove committed
  bytes. `storage-callback-order-red-sequential.txt`: 1 failed, 1 passed when
  testing callbacks registered before and after file creation. The final helper
  verifies persisted row/name rather than relying on callback order.
- `storage-lint.txt`: all changed storage/service/task/model/test files passed
  Ruff.
- `storage-related-sequential.txt`: 135 passed, 1 skipped, 2 failed. Both
  failures are outside storage: `test_external_workflow.py::test_timing_keeps_external_time_and_receipt_wait_separate`
  and `test_external_workflow.py::test_external_custody_still_reported_for_internal_office_perspective`.
  Both expected positive external business time but received zero on Saturday
  17 October 2026. These have been reported to the root audit for investigation;
  the report does not claim a passing external timing suite.
- `storage-background.txt`: 98 passed, 2 failed. The synchronous missing-file
  endpoint test extracted correctly but its expected detailed flash text was
  lost by the concurrent root-owned view feedback change; this was reported to
  root. The EXTERNAL reverse-completion test timed out waiting for its worker
  under host load. Its event deadline was increased from 15 to 30 seconds and
  early worker exceptions/results are now surfaced rather than hidden by the
  synchronization assertion.
- `storage-focused-final.txt`: all 19 final storage regressions passed with
  background-enabled settings, including both collection races, aborted outer
  transaction error preservation, both post-commit callback orderings and
  reverse queued delivery. Two existing Django deprecation warnings remain.

`storage-related-final.txt` and `storage-callback-order-red.txt` are invalid
verification runs: two test commands accidentally shared the same disposable
storage database, causing fixture foreign-key failures. Those artifacts are
retained for transparency. `storage-related-sequential.txt` is the replacement
sequential run. An earlier command referenced nonexistent
`tests/test_external_tracking.py` and collected no tests; it was corrected to
`tests/test_external_workflow.py` before the recorded replacement run.

## Self-review and limits

The new regression file also covers outer rollback after successful nested file
mutations, both callback orderings after commit, queued retry delivery in reverse
request order, and provider completion races in both OVPA and EXTERNAL
collections. Existing related tests cover current permissions, failed retries,
idempotent append jobs, changing attachment sets, filing compensation, 30-day
recovery/purge, duplicate purge workers, and preservation of tracking originals.

Storage failures during compensation are logged and cannot be made transactional
with PostgreSQL. A caller that puts cleanup inside an already-aborted outer
transaction prevents the persisted-row safety check: that lookup failure is
logged, bytes are retained and the original IntegrityError is preserved (covered
by the final regression). Production decorators put cleanup outside their
atomic/locked mutation and root-owned view wrappers enclose the outer atomic
block, allowing compensation after rollback. Live cloud overwrite/delete behavior, real providers and
physical devices remain unverified. The root audit owns the full-suite run,
view-level outer transaction scopes and user messages. No commits were made by
this storage worker.
