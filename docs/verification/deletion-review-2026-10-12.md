# Deletion review verification, 12 October 2026

The later approved 30-day recovery and permanent cleanup policy supersedes the original soft-removal-only behavior below. See [Thirty-day deletion recovery verification](deletion-purge-2026-10-12.md) for the subsequent changes and checks.

## Implemented behavior

Document Repository now provides an administrator-only **Deletion review** screen. Its filtered queues show documents due today or earlier, due tomorrow through day 90, without a review date, and recoverable Deleted documents. Queue totals use the same office and search scope as their lists.

Administrators can keep and reschedule a document, remove a due document after recording a reason and explicitly confirming approval, or restore a removed document with a reason and future review date. Each decision and its document change are committed together. There is no automatic removal or permanent purge.

Removal excludes the document from normal repository access, repository counts and charts, search, downloads, document notifications, and future training exports. Stored files, metadata, prior decisions, audit records, and completed Tracking history remain. Restoration returns the original repository document identity and permissions.

Only the owning office's active administrator or a system administrator may decide. Existing restricted-access rules still apply. Services reload and lock the account before the document, recheck current permissions and ownership, and reject changed or repeated submissions.

## Bugs and usability gaps corrected

- Clearing a review date no longer silently recreates it during later saves.
- Explicit dates and old metadata forms that omit the date retain their intended behavior. First classification still computes the initial schedule.
- Initial schedules handle leap days and reject dates beyond the supported range without a server error.
- Due and upcoming dates share the same inclusive boundaries across repository filters, reports, and review queues.
- Selecting an already selected queue preserves that queue instead of clearing its status.
- Removed documents cannot be accessed through detail, metadata, attachment, extraction, or ordinary search paths.
- Extraction jobs recheck active status and permissions before writing results, including removal while a provider is processing a file.
- The decision menu renders the available Keep, Remove, or Restore actions. Its widget choices are assigned together rather than appended after synchronization.
- New borders use the existing light/dark theme tokens.
- Every validation failure offers a reload link, including stale decisions that have become unavailable.
- Conditional form sections, keyboard navigation, error focus, and layouts are checked in both themes and mobile/desktop sizes.

## Verification

- First full backend run: **6,906 passed, 2 skipped, 1 failed**. The failure identified an undefined border token in the new styles; it was corrected.
- Latest focused backend regression run: **122 passed**, covering the review workflow, date handling, access boundaries, concurrent decisions, extraction safety, count/search agreement, rendered choices, and theme checks.
- Initial browser run identified the empty decision menu, plus fixture transport issues. The actual menu defect was fixed and captured in a regression test; the fixture transport was corrected without weakening its assertions.
- Final browser check: **21/21 scenarios passed**, with no console errors, JavaScript exceptions, unhandled requests, or missing assets. This includes real HTMX filter/history behavior, deliberate decision submissions, validation recovery, and native form validation with JavaScript disabled. Reviewed desktop and mobile screenshots in both themes; mobile captures include the ordinary top navigation.
- Full backend suite after the fixes: **6,910 passed, 2 skipped, 2 warnings** in 543.92 seconds. The skips are the office-direction fixture with no routed rows and worker scheduling in the normal, background-disabled run. The two warnings are existing Django CheckConstraint deprecations.
- Background-enabled processing, maintenance, queued permission, and deletion-safety regressions: **47 passed**. This includes the worker scheduling check skipped in the normal suite.
- Existing JavaScript test suite: **172 passed**.
- Ruff checks passed for application code and the new tests/fixture renderer. All **94 templates** passed template checks.
- Django system checks passed. Migration drift check reported **No changes detected**. Static collection succeeded.
- Production-shaped configuration checks passed using the repository CI's dummy S3/SMTP configuration in the command process only. Earlier attempts using development settings correctly rejected DEBUG/local storage. This does not verify real cloud credentials or deployed services.

## Database and scope

Applied the two additive migrations to the local development database: the deletion-review history table and updated field labels. No existing review dates or repository documents were removed by the migration. Browser fixtures and their decisions use a temporary office inside a transaction that is rolled back; backend tests use the separate test database.

Existing `retention_until` / `retention_years` field names and `retention=due|soon|unscheduled` links remain compatible. Screen wording uses Deletion review. Unrelated local work and settings were preserved. No commit, push, or deployment was performed for this request.

Real OCR/cloud-storage integrations and manual screen-reader testing were not performed. See [workflow documentation](../DELETION_REVIEW.md), [backend log](deletion-review-full-suite-final.txt), and [browser report](deletion-review/browser-report.json).
