# UDM DocTrack testing plan

**Purpose:** define how the team checks that tracking, records management, search, permissions, and deployment changes work before they reach users.

**Scope:** Django application (`apps/`), templates and browser JavaScript, PostgreSQL data behavior, file extraction/storage integrations, and production configuration. This plan complements the automated test suite; it does not claim that CI replaces staff acceptance or a production restore drill.

## 1. Quality goals

Release checks should provide evidence that:

1. Document custody is accurate: sending a record does not count as receipt, each receipt is attributed, and routing history is append-only.
2. Users see and change only records allowed by their role and office scope, including through forged identifiers, filters, downloads, exports, and bulk actions.
3. A document and its original file remain consistent through upload, metadata review, completion, archive, search, download, and recovery.
4. Dashboard, report, list, and export figures agree for the same permission scope and filters.
5. Errors, empty states, responsive layouts, keyboard controls, and security settings remain usable and safe.

## 2. Test levels and when to run them

| Level | What it checks | Run when |
| --- | --- | --- |
| Unit and service tests | Business time, deadlines, numbering, permissions, extraction rules, ranking, and calculations | Every change; full suite in CI |
| Django request and integration tests | Views, forms, database constraints, access boundaries, history, uploads, reports, and downloads | Every change affecting app behavior |
| Browser JavaScript tests | Chart interactions and browser security behavior | Every change to tested JavaScript; CI runs both Node test files |
| Static and configuration checks | Ruff, template structure/URL references, Django checks, migrations, and deploy checks | Every pull request |
| Workflow and page smoke checks | End-to-end create/route/receive/forward/complete/archive/search flow and role-based page rendering | CI and before a demo/release |
| Manual browser and accessibility review | Real navigation, responsive behavior, visual clarity, keyboard operation, and representative files | UI, browser, or release-critical changes |
| User acceptance | Office staff complete realistic tasks and confirm terminology and workflow | Before handover or defence sign-off |
| Operational drills | Health checks, background worker, backup/restore, and storage consistency | Before production handover and after material infrastructure changes |

## 3. Environment and test data

- Use the project virtual environment and a dedicated PostgreSQL database. Pytest creates its own PostgreSQL test database; never point a test run at production.
- Keep external OCR, email, object storage, and background workers isolated or mocked in ordinary tests. Run provider-specific checks only with approved test credentials and non-sensitive files.
- Use synthetic records and files. Never copy personal, confidential, or real university documents into fixtures, logs, screenshots, or CI artifacts.
- Cover system administrator, office administrator, regular user, and viewer roles; include at least two offices so cross-office leakage is testable. Shared and routed records should be represented separately.
- Set deterministic dates, time zones, office schedules, holidays, deadlines, and OCR responses in tests. Include empty data, boundary dates, malformed input, and large-but-bounded datasets where relevant.
- CI currently uses PostgreSQL 16, Python 3.12, and Node 22. Local environments can differ; investigate version-specific failures rather than weakening assertions without evidence.

## 4. Functional coverage map

| Area | Core scenarios and expected result | Existing coverage examples |
| --- | --- | --- |
| Accounts and sessions | Valid/invalid login, lockout, password rules, timeout, role changes; unauthenticated requests redirect or deny access | `test_login_lockout.py`, `test_password_security.py`, `test_session_timeout.py`, `test_viewer_role.py` |
| Authorization and office boundaries | Each role can only view, mutate, administer, export, and download within permitted scope; forged office/record IDs do not widen access | `test_all_role_security.py`, `test_office_admin_boundaries.py`, `test_office_scoped_administration.py`, `test_permissions.py` |
| Tracking lifecycle | Create unique number, route, explicit receipt, remark/forward, complete, archive; one event per action and prior events remain unchanged | `test_tracking_lifecycle.py`, `test_history_is_append_only.py`, `test_tracking_numbers.py`, `test_pending_filing.py` |
| Deadlines and business time | SLA precedence, zero/no deadline, schedule, lunch, weekends, holidays, due/overdue boundaries and explanations | `test_routing_sla.py`, `test_business_time.py`, `test_holidays.py`, `test_deadline_state.py` |
| Documents and extraction | Supported and rejected uploads, size/content limits, extraction failure/retry, external OCR opt-out, reviewed metadata, retention dates, file downloads | `test_extraction_limits.py`, `test_queued_extraction_security.py`, `test_processing_regressions.py`, `test_text_limits.py` |
| Search and repository | Relevance ordering/reasons, typo behavior, threshold, filters, permissions, click-through access, repository reconciliation | `test_search.py`, `test_search_rules.py`, `test_unified_search.py`, `test_repository_reconciliation.py` |
| Dashboards and reports | Filter agreement, role-scoped drilldowns, counts, turnaround calculations, empty/future periods, exports and query budgets | `test_dashboard_reports_agreement.py`, `test_metric_drilldowns.py`, `test_turnaround_service.py`, `test_export_durations.py` |
| Interface and browser behavior | Theme/status contrast, page size, narrow viewport, chart pointer/touch/keyboard behavior, safe browser content | `test_dark_mode.py`, `test_phone_width.py`, `tests/js/test_turnaround_chart.cjs`, `tests/js/test_browser_security.cjs` |
| Administration and audit | Master data changes are scoped; audit entries identify actor/action; record access is logged; append-only expectations hold | `test_office_scoped_administration.py`, `test_record_access_log.py`, `test_security_audit.py` |

The examples point to current test modules, not an exhaustive one-test-per-requirement mapping. When a defect escapes, add a focused regression test in the relevant module or a clearly named new module.

## 5. Standard pull request checks

From the repository root, using PowerShell on Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest
node --test tests/js/test_turnaround_chart.cjs tests/js/test_browser_security.cjs
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe scripts/check_templates.py
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

Run with project dependencies from `requirements-dev.txt`. If local Node or Ruff is unavailable, record that gap and rely on CI; do not report the check as passed. For changes to extraction, maintenance, or queued jobs, also run the CI background-processing regression command with `ENABLE_BACKGROUND_TASKS=True`.

For a production-like change, reproduce CI's deployment configuration and run `manage.py check --deploy --fail-level ERROR`. Run `collectstatic` when static assets or production manifest behavior is affected.

## 6. Workflow and release verification

After migrations and demo data are available in a dedicated verification database:

```powershell
.\.venv\Scripts\python.exe manage.py selfcheck
.\.venv\Scripts\python.exe scripts/smoke_pages.py
```

`selfcheck` exercises the primary workflow and rolls back its test transaction. `smoke_pages.py` requests pages as each role and also rolls back. Neither replaces the focused tests or manual checks. Do not run these against production.

Before a release or defence, manually complete one synthetic record from creation through route, receipt, forwarding, completion, archive, and search. Confirm each handoff in the timeline, then test the printable routing slip and download. Repeat key read/write checks as a regular user, office administrator, viewer, and system administrator.

For UI changes, inspect at narrow phone width and desktop width, in light and dark themes where supported. Navigate with keyboard only, verify focus visibility and labels, and check empty/error/loading states. Use the accessibility audit and relevant feature verification reports as references; note devices and browsers not exercised.

For extraction changes, test a text PDF, scanned PDF, supported office file, unsupported file, oversized file, and a document with external OCR disabled. Verify pending/success/failure/retry states and confirm the original file remains downloadable. Do not submit confidential records to an external OCR provider.

## 7. Security and data integrity scenarios

For permission-sensitive changes, test both allowed and denied cases using separate clients/users. Attempt direct URL access and forged POST values for another office's record; test list, detail, change, receipt, archive, export, and download paths as applicable. A denial must not disclose document title, metadata, file contents, or whether a protected record exists beyond the intended response.

For state changes, verify failed/invalid submissions do not create partial routing, receipt, audit, or file state. Confirm bulk actions affect only explicitly selected eligible records and produce individual custody/history events. Confirm history and audit behavior after later edits, retries, and repeated submissions.

For file handling, exercise invalid extensions and content, malformed files, size boundaries, missing storage objects, and expiring/signed download links. Never include uploaded bytes or extracted text in ordinary logs or notifications.

## 8. Acceptance criteria and defect handling

A pull request is ready when relevant automated checks pass, migrations are committed and current, new behavior has regression coverage, and the reviewer can identify any manual/environment checks that remain. A release candidate is ready when CI passes, the workflow and role-based smoke checks pass, release-relevant manual checks are recorded, and there are no unresolved release-blocking defects.

Classify defects by impact:

- **Blocker:** unauthorized disclosure/change, corrupted or lost record/history/file, or core workflow unavailable. Stop release.
- **Major:** a core role or workflow is unusable, or reported figures materially mislead users. Stop release until fixed or explicitly resolved by the release owner.
- **Moderate/minor:** workaround exists or impact is limited; record owner, workaround, and target fix before handover.

For each failure, record build/commit, environment, role, setup data, exact steps, expected and actual result, and sanitized evidence. Add a regression check for fixed defects. Do not attach real document contents or secrets.

## 9. Limits and operational follow-up

Automated tests cannot establish usability with office staff, real provider reliability, production storage durability, or backup recoverability. Before operational handover, perform staff acceptance with at least two offices and run a restore drill that checks the database and matching file storage together. Record actual browsers/devices, external services, and deployment settings exercised; describe untested areas as unverified rather than passed.

See also [CI workflow](../.github/workflows/ci.yml), [operations runbook](OPERATIONS.md), [accessibility audit](ACCESSIBILITY_AUDIT.md), [team checklist](TEAM_CHECKLIST.md), and [turnaround graph verification](TURNAROUND_GRAPH_TESTING.md).
