# Email OTP and permission management verification

Implementation commit: `d0f8800432de58633a2c7d5197f1e5a28b877f2d`.
Base: `b81799a1a64755f44c87cbbceee6c6ea4d3f73c4`.
Branch used for implementation: `codex/email-otp-permissions`.

The following report-only commit leaves the implementation unchanged. The user subsequently authorized publishing to main. No original-database migration, email enrollment or manual deployment was performed.

## Results and dates

| Verification | Actual result | Date |
|---|---|---|
| Full PostgreSQL suite, mandatory OTP enabled | **8,197 passed, 7 skipped, zero failures**; all 8,204 unique collected IDs accounted for across four separate test databases | 2026-11-01 |
| Background-enabled extraction, security, deletion, suggestions and permission regressions, django-q2 loaded | **70 passed**, zero skips/failures | 2026-11-01 |
| Final OTP, permission and file-maintenance regressions with background support | **62 passed**, zero skips/failures | 2026-11-02 |
| JavaScript suite | **194 passed**, zero failures; rerun before publishing | 2026-11-02 |
| Real Chromium OTP/admin journeys | **43 checks**, zero browser runtime errors; one opt-in browser test passed | 2026-11-01 |
| Lint and template checks | Passed; **131 templates checked**, rerun before publishing | 2026-11-02 |
| Deployment-shaped Django check | No issues; fake mail/storage configuration, no external requests | 2026-11-02 |
| Fresh isolated migrations, Django check and migration drift | Passed; additive accounts 0009/0010 and tracking 0020 applied; no drift | 2026-11-01 |
| Workflow self-check | **21 checks passed** | 2026-11-01 |
| Authorized/refused page smoke checks | **215 requests across four roles**, no page errors | 2026-11-01 |

[Full suite summary](full-summary.json) and [browser results](browser-results.json) preserve the machine totals. Detailed local execution logs remain under `.vercel/email-otp-20261101/`; they are not production logs. Prior intermediate failing runs were diagnostic and are not counted as final verification.

The full-suite skips were three worker-only schedule checks, two opt-in general system-browser cases, one opt-in OTP browser case and one intentional empty-office routing fixture. The separate background run covered the security/deletion schedule checks; the final file-maintenance run covered the third schedule check. The separate OTP browser run covered its skipped opt-in case. The two general system-browser cases and empty-fixture case were not claimed as passing.

## Checked behavior

- Real username/password plus email-code sign-in for staff, viewers, office administrators and system administrators; anonymous pending session before OTP; normal login proof and raw Django admin access only after OTP.
- Old sessions, unverified addresses, expired/exhausted/replayed codes, stale account data and direct raw-admin password requests fail closed. Single-use consumption is tested with concurrent PostgreSQL transactions.
- Resend timing, shared account/IP failed-code limits, per-account send budget, replacement-code digits, delayed superseded delivery, delivery exceptions, enrollment invitations, email replacement and audited administrator/operator recovery.
- Restrictive policies retain existing role, office, collection, custody, receipt-wait and repository-file-lock rules. Policy revisions reject stale overwrites; signed historical/current and legacy file paths, metadata-only search and saved uploads honor restrictions.
- Read sharing retains inherited access and does not grant custody. Both generic and legacy sharing recheck and lock active recipients, including transferred-user cases.
- Existing counts, filters, exports, routing, numbering, timing, file-version, deletion and storage-failure regressions run with mandatory OTP enabled. Existing post-login tests use an explicitly named verified-session helper; real authentication and bypass tests use actual password/code flows.
- CI prepares verified disposable fixture accounts only for trusted page diagnostics. It does not disable mandatory OTP for the full suite or enroll production accounts.

## Independent review and focused repairs

A separate read-only reviewer inspected the working implementation against the approved plan. Important findings were repaired and the reviewer confirmed no outstanding important findings from that review. Focused regressions cover:

- Raw Django-admin routing inline bypasses, actual collection checks and immutable collection/source links.
- Extracted/model evidence rendered through invalid metadata-review POSTs despite blocked content access.
- Stale administrator identity during raw-admin writes and verification invitations.
- Stale or concurrently changed sharing recipients, including the legacy internal sharing service.
- Metadata-edit restrictions accidentally blocking separately permitted initial uploads and extraction.

Additional implementation regressions fixed equivalent role values producing different OTP fingerprints, selected action panels losing their restored state, small-screen permission-table overflow, and a resent code repeating the preceding digits. The fixed per-request permission snapshot adds one measured read to dashboard/report query budgets; mutation checks remain fresh inside transactions.

## Browser scope and limits

The browser journey used real Django responses, login, CSRF tokens, persisted restrictions and simulated email delivery. It covered widths **320, 390, 768, 900, 901 and 1366**, light/dark themes, basic keyboard focus, CSS 200% zoom and sign-in without JavaScript. Screenshots were captured at all twelve width/theme combinations; examples: [320px light](permissions-light-320.png) and [1366px dark](permissions-dark-1366.png).

Physical-device, screen-reader and native browser-zoom verification were not performed. General system-browser journeys beyond the dedicated OTP/admin journey were not rerun as part of this feature. Permission combinations are not exhaustively combinatorial. Passing tests do not prove the absence of every possible defect.

Email, storage and OCR failure scenarios were simulated. **Actual email delivery, real mailbox ownership, provider configuration and production activation remain unverified.** A temporary test cluster stopped overnight; the final targeted run used a recreated disposable database after restarting only that cluster. No original data or files were involved.

## Before using mandatory OTP with real accounts

Follow [setup and recovery instructions](../../EMAIL_OTP_AND_PERMISSIONS.md). Apply the reviewed migrations to the intended database yourself, enroll every active account, run `check_otp_readiness`, verify actual email-code delivery and keep `EMAIL_OTP_REQUIRED=True` for rollout. Private temporary enrollment mode is rejected by deployment checks. There is no OTP bypass or automatic verification of real addresses.
