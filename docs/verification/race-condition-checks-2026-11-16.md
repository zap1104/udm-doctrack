# DocTrack race condition checks

Verified November 15–16, 2026 on local changes based on `1f143b3`. PostgreSQL tests used separate disposable databases and local test files. Concurrency regressions used independent database connections or controlled ordering at the relevant read and write boundaries. Email and OCR were simulated.

| Area | Reproduced interleaving | Repair |
| --- | --- | --- |
| Tracking receipt | A receipt paused while the slip was confirmed, forwarded, and returned to the same office. The old request could confirm the new batch. | Check the batch that the request loaded against the batch held under the record lock. Apply the same check to individual and bulk receipts. |
| Repository filing | Approval paused while the record was reopened and completed again. The older approval could file the replacement completion. | Compare the completion being approved with the completion on the locked record before writing the repository copy. |
| Source-unit extraction | A queued append or retry passed the initial generation check, then ran after a newer retry finished. | Recheck the expected generation while holding the document lock. Leave newer text, status, and extraction records intact. |
| Repository search | A reindex loaded old metadata, an edit committed, then the earlier rebuild restored the old search title. | Reload current metadata under a lock and write both search vectors in one transaction. |
| Django administration | An extraction worker committed after a metadata form loaded. The form's save restored the previous extraction state. | Lock the document when the edit POST loads its record. The metadata edit and the worker can then finish without either operation losing its update. |
| Sign-in | Completing an OTP challenge issued before a subsequent password lockout cleared the new lock. An overlapping cancellation could also mark a consumed code as cancelled. | Permit the already-started sign-in and preserve the active deadline. Apply cancellation only while the challenge remains pending. |
| Record sharing | A grant held a record and waited for its recipient's account while that recipient held their account and waited to revoke a share on the same record. PostgreSQL aborted one request. | In Repository and Tracking, lock a user recipient before the record. Authorize against the locked current record and recipient. |

| Regression group | Outcome |
| --- | --- |
| Routing, filing, receipt concurrency, rollback and attachment history | 55 passed. An earlier receipt and routing group passed 79. |
| Source-unit and legacy extraction, OCR consent, and search publication | 58 passed. |
| Repository admin, files, permissions and deletion | 59 passed. |
| Reindex, repository and unified search, and master-data changes | 82 passed. |
| Workflow, upload and version races, deletion and recovery, and External workflows | 170 passed in a newly created test database. A first run in a reused database failed fixture setup on duplicate office codes. |
| Sign-in, lockout, OTP, access restrictions and sharing | 233 passed. The dedicated share run also passed all 11 cases. |
| Final combined race regressions | 41 passed with zero failures, errors or skips. |
| JavaScript | 194 passed, including delayed filter responses and overlapping upload actions. |
| Project checks | Ruff, 131 templates, Django checks, migration drift and Git whitespace checks passed. The changes require no schema migration. |

The Tracking and Django admin checks cover overlapping operations after a request loads its record. Browser forms opened before a later change still have no version check on submission. The regression groups overlap and are targeted follow-up runs.
