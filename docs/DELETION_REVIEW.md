# Deletion review

Open **Document Repository → Deletion review** as an office administrator or system administrator. The queue separates documents due today or earlier, documents due in the next 90 days, documents without a review date, and Deleted documents. Search and office selection apply to each queue and its count. Deleted documents show their recovery deadline or **Awaiting permanent deletion** when the recovery period has ended.

## Decisions

- **Keep document and set next review date:** enter a date after today and explain why the document remains needed.
- **Remove from repository:** available only when the review date is due. Enter the reason and applicable approval reference, then explicitly confirm approval. The document moves to Deleted documents for a 30-day recovery period.
- **Restore to repository:** before the recovery deadline, open a document in Deleted documents, record a reason, and choose its next review date. The same document identity, files, metadata, and access permissions become available again. Restoration closes the current deletion cycle; a later approved removal starts a new 30-day period.

Removal immediately excludes the document from the repository, repository reports and dashboard totals, search, repository downloads, document notifications, and future training exports. Its repository files, metadata, and review history remain recoverable until 30 days after the recorded removal time. The page displays the exact recovery deadline with its time zone. Restoration must finish before that deadline, even if permanent cleanup has not run yet.

After the deadline, automatic cleanup permanently removes the repository document, its repository files, associated metadata, detailed review history, and document notifications. Completed Tracking history and its original attachments remain as separate records; a minimal audit entry records the disposal. Existing audit history also remains, including earlier document-title summaries. This feature does not alter the university's backup retention or object-store version history.

The application never removes an active document just because its review date has arrived. Permanent deletion follows an administrator's approved removal and its 30-day recovery period. If file storage is unavailable or cleanup fails, the expired document remains hidden and marked **Awaiting permanent deletion**; the next cleanup run retries it. An expired document cannot be restored while cleanup is pending.

## Automatic cleanup

After migrations, run `python manage.py ensure_schedules` with background tasks enabled to register **deleted-document-purging**, which calls `apps.documents.tasks.purge_deleted_documents`. Keep `python manage.py qcluster` running to process scheduled cleanup. Operators can run `python manage.py purge_deleted_documents` to process eligible documents directly; it uses the same deadline and cleanup safeguards. See [OPERATIONS.md](OPERATIONS.md) for deployment and worker maintenance.

## Dates and permissions

The initial schedule uses the main document type's review period, starting from the document date or 31 December of its document year when no date is known. A period of zero leaves the review unscheduled. Initial classification of an uploaded document still calculates this schedule. Metadata editors can set a custom date or clear an existing date; unrelated saves do not silently recreate cleared dates.

Due includes today. Upcoming covers tomorrow through day 90, inclusive. Documents scheduled beyond 90 days remain in the regular repository and can be reviewed from their detail screen.

Only an active owning-office administrator or system administrator can record review decisions. Office administrators cannot review another office's records, including shared records. Existing restricted-access rules still apply. Other authorized readers can see a document's review date and history but cannot record these decisions.

Django's maintenance admin cannot delete repository documents or edit removed documents. Removal and restoration use this review workflow so recoverable files and metadata remain protected.

Each decision records the reviewer, time, reason, and previous/next dates in the same transaction as the change. Review forms expire after two hours and reject changed or repeated decisions. The server checks current account permissions and document ownership again while holding the relevant locks.

Existing database field names and `retention=due`, `retention=soon`, and `retention=unscheduled` links remain compatible. The screen labels use **Deletion review**.
