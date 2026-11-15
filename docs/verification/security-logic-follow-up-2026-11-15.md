# DocTrack security and logic follow-up

Reviewed November 14–15, 2026. GitHub `main` and its successful CI run were verified at `1f143b30d1adb66a8b9841554282818b1d4acb99`. The repairs below were verified locally on top of that revision. [Baseline CI run](https://github.com/zap1104/udm-doctrack/actions/runs/43184292718)

| Area | Reproduced issue | Repair |
| --- | --- | --- |
| Password lockouts | Failures recorded on different devices could make the lock expire before five minutes. Old history could also retain the wrong deadline during a new lockout. | Start a single deadline at the threshold. Use it for both enforcement and countdown. Keep it stable across devices and concurrent failures. Full username resets still unlock immediately, and clearing recorded stages preserves an active lock. |
| Sign-in inputs | An oversized username reached Axes and could return HTTP 500 in DocTrack and Django admin setup mode. | Validate the account username limit before authentication in both forms. |
| Bulk receipt | Dashboard and Tracking dropped office and filter choices after submission. | Return to the submitting queue with its supported parameters, including repeated values. The destination is a fixed application route. |
| Office Files | A parallel receipt could hide the slip from an office that had already confirmed receipt in the same routing batch. | Match every confirmed holder in the current batch and retain the existing draft and pre-receipt behavior. Tracking and Search use the same scope. |
| Office previews and extraction | Damaged packages could return HTTP 500. A package could also expand past its declared size before the normal ZIP reader detected a problem. | Check entry boundaries, output sizes and checksums before automatic parsing. Malformed or unsupported packages use the existing unavailable or failed states. Authorized original downloads still return the original bytes. |
| Workbook indexing | Legacy extraction could omit cells when worksheet dimension metadata was stale while still reporting completion. | Read the actual worksheet extent and retain the existing row, column, cell and text limits. |
| Queued external OCR | A legacy retry could send bytes after consent changed. A result or provider error returned before publication could also be stored after consent was withdrawn. | Check current consent, generation and authority before each guarded dispatch. Recheck consent under the final document lock. Keep usable native text and discard unpublished provider output and notes. |

## Verification

| Check | Result |
| --- | --- |
| Account security | 240 passed, including cross-device expiry, concurrent failures, admin history clearing, username reset, OTP and native admin. |
| Workflow and filters | 317 passed, including Tracking and Search custody, report and repository reconciliation, bulk receipt, concurrent receipt, and External custody. |
| Files, permissions and processing | 180 passed, including Office archive regressions, authenticated signed preview routes, original-byte preservation, legacy and source-unit extraction, and storage tests. |
| Background processing | 52 passed with background work enabled, including OCR withdrawal, queue authorization, schedules and deletion recovery. |
| Local queue worker restart | One opt-in passed. Two workers processed five jobs. The interrupted job recovered, duplicate jobs completed, and revoked or superseded requests left prior text intact. |
| JavaScript | 194 passed on Node 24.22.3. |
| Chromium | One live journey passed with 35 assertions, 28 navigations and 14 filter requests. No JavaScript or unexpected HTTP errors were recorded. |
| Project checks | Ruff, 131 templates, Django checks, simulated deployment checks and migration drift passed. No migration is required for the follow-up. |

Verification ran on disposable PostgreSQL and isolated files. Email, external OCR and cloud storage were simulated. GitHub CI evidence applies to the released baseline. The local follow-up used the targeted suites listed here and did not rerun the entire historical test matrix.

Automatic Office parsing supports stored and DEFLATE ZIP entries. Original downloads remain available for older files whose previews cannot be rendered. The archive check covers entry expansion. This pass did not load-test central-directory or workbook parser memory. A password accepted before a later lockout can still finish its existing sign-in flow, including a pending OTP challenge. A new password attempt after the lock must wait.
