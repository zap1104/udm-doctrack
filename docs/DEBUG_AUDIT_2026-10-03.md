# Debugging and release review, 3 October 2026

Scope: the Django apps, current pending changes, page and report rendering, role boundaries, document processing, account management and the GitHub release workflow. This pass fixes defects in existing behavior. The earlier security audit and its infrastructure limitations remain in [SECURITY_AUDIT_2026-10-03.md](SECURITY_AUDIT_2026-10-03.md).

## Confirmed oversights corrected

| Problem | Result after the fix | Evidence |
| --- | --- | --- |
| Partial extraction replaced a document's page total with only that job's pages. Synchronous added files did not update the total; synchronous filing did not aggregate attachment pages. | Totals use all attachment page counts. Unprocessed attachments contribute zero known pages rather than an invented count. | `Document.file_page_count`, document services and extraction tasks; `tests/test_processing_regressions.py`. |
| An append job merged its output with text loaded before another job finished. | The merge reads the current document inside its final transaction, preserving another completed job's text. Actor and document locks follow the same order as mutation services. | `apps/documents/tasks.py`; concurrent-update regression. |
| Re-extraction read only the primary file and used cached permission checks in the synchronous view. | A guarded service rechecks the active actor/current ownership and processes every attachment in both runtime modes. | `re_extract_document`, `ReExtractView`; full-document, queued and demotion tests. |
| Missing or failed retry input could erase the last usable search text or present success while part of the document had not been read. | Failed/skipped replacement jobs preserve existing text and record the incomplete state. The synchronous page warns about failures. An explicit empty file selection does not process unrelated attachments. | Processing and queued-extraction regressions. |
| Filing silently ignored an unreadable attachment and marked the tracking record filed. | Filing stops with a clear message, rolls back the repository row/status change, and leaves the tracking record pending filing for a retry. Opened source files close reliably. | `archive_tracking_record`; missing-attachment rollback regression. |
| An inactive system administrator counted as a valid replacement for the last active system administrator. Simultaneous self-demotions could both pass the same count. | Only active replacements count. Account-management writes take a PostgreSQL transaction advisory lock before actor/target row locks. Simultaneous requests leave one active system administrator and a validation message for the other. | Account form/mixins/views; `tests/test_admin_self_lockout.py`. |
| Expired request-limit counters had a cleanup function but no registered worker schedule. | The existing maintenance command registers daily cleanup, without duplicates; active limits remain intact. | `ensure_schedules`, `tests/test_security_maintenance.py`, operations documentation. |
| Local nested worktrees could be picked up by Git or project linting. | The local `.claude` directory is ignored and excluded from lint. Its existing files remain untouched. | `.gitignore`, `pyproject.toml`. |

The first five processing regressions failed against the previous implementation. The new guarded retry service is additionally tested after a mid-request demotion. Database transactions and after-commit queueing follow [Django's transaction guidance](https://docs.djangoproject.com/en/5.2/topics/db/transactions/) and [locking behavior](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update).

## Verification

- Processing/authorization checks after the initial fixes: **213 passed**.
- Background-enabled processing, queued permissions and maintenance: **26 passed**. CI now runs this configuration separately.
- Account lockout, concurrency and office boundaries: **197 passed**.
- Browser security and chart interaction checks: **131 passed**.
- Lint, 87 templates, Django checks, migration completeness, static collection and CI-style deployment configuration checks passed.
- Database workflow checks: **21 passed**; page smoke checks: **256 requests across four roles**, with no unexpected errors. Temporary database changes were rolled back.
- Dependency audit found no known vulnerabilities in the declared production requirements.

Release verification resumed on **5 October 2026**. Fresh local checks passed **290 Python regressions**, with one schedule-registration case intentionally deferred to the separate background-enabled run, which passed **26 tests**. All **131 browser/chart tests** passed too. Lint, all 87 templates, Django checks, migration completeness, static collection and the production-requirements vulnerability audit passed again. The earlier final local full-suite attempt was interrupted at 13% and is **not** counted as a passing run. Main is gated on the complete GitHub pull-request CI run, including the full Python suite, coverage threshold, background processing, database workflows, page rendering and dependency audit.

Local release evidence is saved in `C:/Users/USER/.codex/visualizations/2026/09/28/01a0e731-ab88-7a12-b339-04f3266711b3/`, with `release-` and `resume-release-` log names. CI runs the full suite with background tasks disabled; the separate background-enabled run verifies worker app registration and schedules against PostgreSQL too. The pull request and its GitHub Actions run provide the final release evidence.

## Release and remaining limits

The release includes the already reviewed office-admin boundaries, turnaround explanations, schedule controls and related regressions currently pending in the checkout. Office administrators still have no global-office view. No real account permissions, passwords or existing document consent choices were changed by this audit. Test/workflow database changes are isolated or rolled back.

Apply the included migrations during deployment. In environments using workers, run `python manage.py ensure_schedules` after migrations and keep the django-q2 worker running; registering a schedule does not start a worker. Local development directories and screenshots are not release files.

The previous audit's remaining upload-scanning/MFA/infrastructure gaps are not solved by these bug fixes. A database rollback also does not automatically delete object-storage files saved before a later failure; storage reconciliation/cleanup remains an operational improvement. Previously stored page totals/text can be refreshed by an authorized office editor; this release does not reprocess or rewrite the university's existing records automatically.

No code review or test suite guarantees absence of all defects. Physical-device and additional browser-engine verification, production bucket/IAM/TLS checks and backup recovery require their actual environments.
