# Security and logic audit — 3 November 2026

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**

Status: completed within the local audited scope. No unresolved reproduced defects remain in the inspected repairs; the verification limits below still apply.

Branch: `codex/security-logic-audit-20261103`. Starting revision: `8fc45b08c7e38fd74e2a28ddc52825f73c84bf17`.

Verified repair revision: `d0675de56dab20832a2b4e6cceae3e501be78206`. Added 58 regression cases and extended the existing browser journeys. [Machine-readable results](security-logic-audit-2026-11-03/verification-summary.json), [test-ID reconciliation](security-logic-audit-2026-11-03/test-id-coverage.json), [independent review](security-logic-audit-2026-11-03/independent-review.md).

## Scope and isolation

This audit inspected authentication, email OTP, account administration, capabilities, record sharing, both Tracking workflows, both Repository collections, file versions, previews/downloads, extraction, deletion recovery, filters, counts, reports, exports and background tasks. Repairs address reproduced defects. They do not change the approved roles, office boundaries, custody policy, numbering, timers or repository file locks.

All request-driven mutations and failure/concurrency tests used a newly initialized, loopback-only PostgreSQL 18 cluster on port 8877. Every audit database starts with `test_doctrack_security_20261103`; workers have separate databases and temporary media. Email, OCR and cloud storage were simulated. The original database and uploaded files were neither migrated nor modified. Unrelated local configuration and historical artifacts were excluded from changes. There is no push or deployment in this audit.

## Confirmed defects and repairs

| Defect and trigger | Focused repair | Regression evidence |
| --- | --- | --- |
| A personal-profile POST loaded before an assisted email recovery could save its stale, read-only email and restore the old sign-in factor. | When mandatory OTP is enabled, personal-profile saves exclude the email field. The dedicated email-security flow remains authoritative. | `test_security_logic_regressions.py`: stale profile versus replaced email. |
| The normal user screen considered an active system-admin role sufficient as a replacement, even without staff access, a usable password or a verified email. Another unready administrator could also suspend or demote the only ready administrator. | Share one readiness predicate across account-administration entry points. Refuse removal of the last ready administrator; preserve explicit enrollment-mode behavior. | `test_security_logic_regressions.py`, `test_admin_self_lockout.py`. |
| Native Django user administration allowed self-deactivation/deletion or loss of administrator access, and could remove the last ready administrator through edits, bulk deletion or disabling a password. | Guard edit, password-disable, individual-delete and bulk-delete paths. Authorized changes remain available when a ready replacement exists. | `test_raw_user_admin_availability.py`: real requests, invalid replacement cases and concurrent step-down. |
| Native account forms could load a target before acquiring its row lock, allowing a later full-instance save to overwrite concurrent security changes. | Lock the target before constructing account/password forms, inside the site's existing account-wide serialization. | `test_raw_user_admin_availability.py`: independent database connections prove the target is locked before form construction. |
| A system administrator blocked from extraction could still edit derived OCR state through native document administration. | Make derived extraction fields read-only for the actual collection. Keep authorized metadata editing and content-field exclusion intact. | `test_admin_extraction_policy.py`: both collections, crafted POSTs and add forms. |
| External Repository browse/search cards could expose OCR text through the snippet fallback after the final document refetch, despite a file-content block. | Redact OCR text on the final page instances before rendering. Metadata, pagination and counts remain available. | `test_external_repository_content_policy.py`: browse, search, collection independence and metadata-save preservation. |
| External version-history actions were built using OVPA capability context, producing incorrect or missing links for differently restricted users. | Carry the External area into history-link generation for External Tracking and External Repository. | `test_file_history_capability_context.py`: independent restrictions and signed actions. |
| The original Repository Download URL did not apply immutable-version retirement and storage-identity checks used by version-specific downloads. | Return a private 410 response for unavailable, mismatched or retired current content before opening storage. Existing valid signed URLs continue to work. | `test_security_logic_regressions.py`: both collections and storage drift. |
| A receiving office could be deactivated after selection but before routing; internal routing trusted the stale office instance. | Reload and lock active internal destinations until the movement commits. Lock recipients in primary-key order while preserving their selected routing order. | `test_routing_endpoint_validation.py`: stale selection and real concurrent deactivation attempts in both workflows. |
| Correcting an External dispatch time could place it after its existing deadline, creating impossible chronology. | Validate dispatch against the existing deadline before persisting any correction, revision or history event. | `test_routing_endpoint_validation.py`: state/history remain unchanged after rejection. |
| Installed `pypdf` 6.21.2 had seven known malformed-PDF resource-exhaustion/infinite-loop advisories; the requirement allowed that version. | Raise the supported minimum to 6.21.4 and update the local environment. No format conversion or extraction-policy changes. | Installed-environment audit reproduced the advisories before the update; fresh post-update audit is clean. [Maintainer advisory](https://github.com/py-pdf/pypdf/security/advisories/GHSA-5849-834g-hvw5), [page-tree advisory](https://github.com/py-pdf/pypdf/security/advisories/GHSA-cggf-277g-3q54). |
| Demo setup applied other offices' Repository tags as a Records staff user; the permission error aborted sample creation but the command still reported that the demo was ready. | Use the existing authorized seed administrator for tag changes while preserving the original uploader. Return a command error for sample failures, retaining the separately committed accounts and master data. | `test_seed_demo_authorization.py`: both archive phases, an injected failure and complete sample creation. Four new cases failed before repair; all 12 new/related seed-account tests passed after repair. |

Each application repair above had a failing regression before implementation and a passing focused run afterward. The dependency repair used the installed-package advisory scan as its failing check. Preliminary source concerns that were already protected by middleware or established policy were withdrawn rather than used to justify unnecessary changes.

## Coverage map

| Area | Checked behavior and evidence families |
| --- | --- |
| Identity and sessions | Password/OTP gates, resend/expiry/replay/rate limits, account changes, recovery, lockouts, old sessions and direct native-admin entry: `test_email_otp*`, `test_account_password_races.py`, `test_login_lockout.py`, `test_raw_user_admin_availability.py`. |
| Role, office and capability boundaries | Four independent areas, own-office administration, content versus metadata access, crafted URLs/POSTs, revoked grants and permission revisions: `test_all_role_security.py`, `test_office_*`, `test_capability_*`, `test_systemwide_access.py` and the new content/admin regressions. |
| Tracking and routing | Draft/dispatch/receipt/work/completion/filing, parallel recipients, current batches, returns, External manual provenance/corrections, current authority, stale revisions and office deactivation: Tracking/External workflow, timing, status and routing regression modules. |
| Lists and aggregates | Exact IDs as well as totals; selected/all offices, collection separation, current direction, stage, overdue, date/type/tag/source/search filters, charts, drilldowns and exports: filter/agreement/matrix/oracle modules plus standalone `audit_counts.py` and `audit_filters.py` on mixed fixtures. |
| Files and Repository | Current/history authorization, checksums, five-version retirement, locked filed contents, upload drafts, previews, signed URLs, deletion/restore/purge and original Tracking-file preservation: version, preview, upload-draft, deletion and maintenance modules. |
| Extraction and failures | Consent/current permission rechecks, stale jobs, storage identities, cleanup/rollback, failed queue submissions and extraction retry ordering: processing, queued-extraction, source-unit, intelligence, file-maintenance and storage regressions; separate background-enabled run. |
| Browser | Actual Django responses, CSRF, OTP, saved state, scoped content, filter requests, workflows and responsive layouts. Two opt-in journey families are executed separately; the default suite intentionally skips them. |
| Operator setup | Isolated fresh migrations, drift/system/deployment-shaped checks, complete demo generation, workflow self-check, page smoke and route/command inventory. The inventory itself is not execution coverage for every command. |

## Verification ledger

These are fresh outcomes from this audit. Focused groups overlap; their totals must not be added together. The environment was Windows, Python 3.13.7, Django 5.2.17, PostgreSQL 18 and Node 24.22.1.

| Area | Fresh evidence | Result |
| --- | --- | --- |
| Full PostgreSQL suite with mandatory OTP | Four isolated databases; exact test-ID inventory and outcome reconciliation | 8,251 passed, 7 skipped; all workers exited 0 |
| Supplemental final demo regressions | Four cases added after full-suite collection; XML outcomes reconciled against a fresh complete collection | 4 passed; no missing or extra IDs across all 8,262 current test IDs |
| Background-enabled extraction, deletion, maintenance, capabilities and routing | Dedicated database; simulated services | 77 passed |
| JavaScript interactions/security | `node --test tests/js/*.cjs` | 194 passed |
| OTP/security browser, CSRF and saved state | Four roles; 50 assertions; native-admin safeguards and External OCR redaction; persisted-state assertions | 1 journey passed; zero browser errors |
| Broader real browser workflows | Real OTP/CSRF; filters, internal/External routing and filing, downloads, TXT preview/version label, history/reset/retry/session expiry, Dashboard scrolling, keyboard and JavaScript-disabled behavior | Core: 451 checks and 181 live filter swaps; Tail: 35 checks and 14 swaps. Both phases passed separately with no unexpected browser/HTTP errors. One deliberate stale-request cancellation produced the expected `ERR_ABORTED`. |
| OVPA count/chart/export reconciliation | 100 internal Tracking records and 128 OVPA Repository documents, with 2 External Repository fixtures testing collection isolation; 26 office/role scopes | 21,518 checks, zero mismatches; all four integrity counters zero. External view reconciliation is additionally covered by the full-suite matrices and browser workflows. |
| Filter validation | Same mixed disposable population; standalone audit for system administrator, office administrator, staff and viewer | 627 checks; zero failures |
| Isolated migrations, Django checks, workflow self-check and pages | Fresh command database, complete asserted demo generation and temporary media | All migrations/drift/system checks passed; 21 workflow checks and 256 page requests across four roles passed |
| Deployment-shaped configuration | Fake SMTP/S3 configuration, mandatory OTP and production security settings | `check --deploy --fail-level WARNING` passed; no live service verification |
| OTP readiness command | 15 active verified disposable accounts and a ready system administrator; simulated delivery configuration | Passed; actual inbox delivery remains unproved |
| Lint, templates and static assets | Ruff; 131 templates; isolated static collection | Passed; 442 static files collected |
| Dependency checks | Resolved runtime requirements and actual installed environment | Both scans report no known vulnerabilities after parser update |
| Pinned browser libraries | OSV exact-version queries for PDF.js, docx-preview, JSZip, Bootstrap and HTMX; independent SHA-256 checks | No advisories returned; all 290 preview assets match the manifest |
| Independent final review | Read-only review of combined repairs, seed-command addendum and regression design | No outstanding important findings |

The three browser journeys total 536 checks and 195 real filter updates. Light/dark themes were exercised at widths 320, 390, 768, 900, 901 and 1366, including CSS layout zoom at 200%. Representative Tracking, External Repository and permission-table screenshots were inspected. Long tables scroll within their containers at narrow widths.

Detailed results: [full suite](security-logic-audit-2026-11-03/full-suite.json), [counts](security-logic-audit-2026-11-03/counts.json), [filters](security-logic-audit-2026-11-03/filters.json), [workflow browser](security-logic-audit-2026-11-03/workflow-browser.json), [route/command inventory](security-logic-audit-2026-11-03/surface-inventory.json). Complete local logs, XML, per-ID outcomes and screenshots remain under `.vercel/security-logic-20261103/`; no credentials or OTP values are included in the committed evidence.

The full suite collected before the final four demo regressions were added. Those four ran separately against the final command code. A fresh complete collection reconciles exactly to the full run plus their passing XML results: 8,262 IDs, with no omitted or extra test IDs. This does not turn skipped cases into executed cases.

The seven default-suite skips comprise three background-only cases, the OTP browser journey, two broader browser phases and one conditional filter case with no applicable routed rows for `med_admin`/Incoming in its fixture. The three background cases passed in the background-enabled run; browser outcomes are recorded separately above. The conditional empty-fixture case remains a declared gap rather than a claimed pass.

The test environment initially had a stopped PostgreSQL process; those connection-error runs were stopped and retained as infrastructure failures. Another partial run was deliberately stopped before upgrading the PDF parser. Only complete fresh runs under the patched dependency count toward final verification. Browser setup/harness corrections included the installed Playwright path, same-URL POST synchronization, OTP support in the older journey, stdin cleanup and a clean disposable database after an interrupted runner. A Windows Python thread-dump crash was rerun with that diagnostic timer disabled while retaining the journey's subprocess watchdog. These failed setup/harness attempts are not counted as passing product checks.

## Limits

Passing tests cannot establish that every possible defect is absent. This is a local audit, not a penetration test of the deployed website or a certification. Real inbox delivery, OCR.space, Azure/R2, production hosting configuration, backups/disaster recovery and a live multi-worker deployment were not exercised. Live environment values and provider credentials were not audited. Browser checks use desktop Chromium emulation and CSS layout zoom; physical devices, browser/OS magnification, other browser engines and screen readers remain unverified. The browser preview journey covered TXT; PDF/DOCX/image/spreadsheet preview security relies on the automated backend/JavaScript tests rather than an exhaustive browser inspection of every format. A route/command inventory records available surfaces, not proof that every management command was executed. Existing Django 6 deprecation warnings were retained because the required stack is Django 5; they are not failing checks.

No original-data migration, historical rewrite, deployment, main-branch merge or push is included.
