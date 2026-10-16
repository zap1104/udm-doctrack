# Thirty-day deletion recovery verification, 12 October 2026

This extends the [Deletion review verification](deletion-review-2026-10-12.md) with the user's approved policy: recoverable for 30 days after removal, followed by permanent repository cleanup.

## Implemented policy

- Approved removal records an exact timestamp. Recovery lasts 30 full days from that timestamp; the screen shows its deadline and time zone.
- Authorized restoration before the deadline preserves document identity, files, metadata and permissions, cancels cleanup, and sets a future review date. A later removal starts a new recovery period.
- Restoration is rejected at the deadline, even if the worker has not yet cleaned up the expired document. Expired screens show **Awaiting permanent deletion** without a restoration form.
- Permanent cleanup removes the repository document, its unshared repository file objects, related metadata and classifications, detailed deletion-review rows, and document notifications. Tag usage counts are updated.
- Completed Tracking records and their separately stored original attachments remain. Existing append-only audit history remains; an additional minimal disposal receipt excludes document titles/content and remains visible only within the appropriate administrative scope.
- Active documents are never automatically removed because a review date arrives. Existing permission, approval, reason and stale-submission checks remain required.

## Cleanup safeguards

Cleanup locks and rechecks every expired inactive document. Concurrent workers cannot dispose of the same row twice. It only deletes the exact file keys attached to the repository document, and preserves a key referenced by another repository document or original Tracking attachment.

A mandatory audit receipt is written before storage deletion. Storage failures keep the expired row available for retry while recovery remains closed. Exact object deletion is checked before database removal. Failed attempts rotate behind untouched candidates so a damaged oldest batch cannot indefinitely block other expired documents. Retrying partial cleanup is idempotent.

Document notifications are deleted explicitly before their document relationship could become null and expose them again. Queued extraction exits safely when its document has been permanently removed, including disappearance during a provider operation. Django's maintenance admin cannot delete repository documents, edit inactive documents/files/metadata, or reindex inactive documents.

Migration `0009_document_deleted_at` uses the latest known removal decision for existing deleted documents. Legacy inactive rows without a reliable decision receive a fresh recovery period. Migration `0010_document_purge_attempted_at` adds fair retry ordering. Both additive migrations have been applied locally.

## Verification

- Full backend suite before the final maintenance-admin/form guards: **6,929 passed, 3 skipped, 2 warnings** in 572.18 seconds. Worker scheduling/maintenance skipped in this background-disabled run are covered by the enabled checks below; the remaining skip is the office-direction fixture with no routed rows. The warnings are existing CheckConstraint deprecations. The final guards have dedicated tests and real-route checks below.
- Background-enabled deletion, processing, maintenance and all-role permission regression: **266 passed**, with two existing CheckConstraint deprecation warnings.
- Final background-enabled cleanup and scheduling checks after retry rotation/concurrent-worker coverage: **26 passed**, with the same two warnings.
- New maintenance-admin safeguards: **4 failed before the fix, then 4 passed**; the warning is the existing model CheckConstraint deprecation.
- Protecting inactive admin documents exposed a document-type form `KeyError` when editable fields were omitted. The real admin GET and a new focused test reproduced it. The shared form now handles omitted fields; all **5** maintenance-admin tests passed afterward.
- Latest background-enabled combined deletion workflow, cleanup, safety, rendered controls, maintenance-admin and existing document-type regressions after all application changes: **102 passed**, with the same two existing warnings.
- Actual maintenance-admin requests using a temporary true system superuser: active-document deletion **403**, expired-document metadata/file edits **403**, expired-document inspection **200** with no editing controls and no file-inline add/change/delete permissions. Complete document and related-row snapshots stayed unchanged.
- Browser checks: **25/25 passed**, with zero console errors, page exceptions, unhandled requests, or missing assets. Both themes and desktop/mobile sizes cover filtering/history, explicit submissions, validation recovery, restoration deadlines, expired restoration denial and JavaScript-disabled behavior. Screenshots were inspected.
- Fixture renderer captured actual Django responses using only temporary rows in a rollback transaction, with no file bytes, provider calls or mail.
- Ruff checks passed for application code, new tests and the fixture renderer. Django system checks passed; migration drift reported no changes. Templates were validated.

The real provider's cloud credentials and bucket versioning were not exercised. Permanent cleanup tests use actual temporary file bytes in a separate PostgreSQL test database. No real local repository document or file was purged, and no changes were committed or pushed.

## Operation

The daily `deleted-document-purging` schedule calls `apps.documents.tasks.purge_deleted_documents`. With background tasks enabled, register it using `python manage.py ensure_schedules` and run the existing `qcluster` worker. `python manage.py purge_deleted_documents` provides the same guarded cleanup directly, with an optional positive `--limit`.

The current local setting is **ENABLE_BACKGROUND_TASKS=False**. Automatic cleanup is therefore not running locally. The policy, job and command are implemented; worker activation and schedule registration remain operational setup. Existing backups and object-store version history follow their separate policies. See [DELETION_REVIEW.md](../DELETION_REVIEW.md) and [OPERATIONS.md](../OPERATIONS.md).
