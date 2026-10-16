# Main release verification, 14 October 2026

This release includes automatic filters, office-scoped counts and direction/stage navigation, recipient-specific routing stages and receipt displays, endorsement terminology, and administrator-approved deletion review with 30-day recovery.

## Release package

- Application code, required migrations, maintenance commands, templates, static assets, regression tests, and Markdown documentation are included.
- Machine-specific development host settings, generated screenshots, rendered review packets, raw JSON, and local test logs remain outside the commit.
- Independent backend and packaging reviews found no release-blocking defects. These reviews supplement the automated checks; they do not replace them.

## Fresh verification

- Ruff: passed.
- Template validation: 96 templates passed.
- Django system checks and deployment checks: no issues.
- Migration drift: no changes detected.
- Static-file collection: passed.
- JavaScript interaction tests: 172 passed.
- Dependency audit: no known vulnerabilities found.
- Full PostgreSQL regression suite: **7,137 passed, 3 skipped**, in 809.80 seconds.
- Background-enabled processing and deletion cleanup regressions: **46 passed**, including both schedule checks skipped in the ordinary run.
- The remaining skip covers an empty office/direction fixture. Populated direction-filter cases are covered elsewhere in the suite.

Existing Django constraint deprecation warnings remain. The much slower local coverage-profiled run was stopped without claiming a full result; GitHub CI enforces application coverage.

Local raw logs are retained alongside this report and excluded from the release. GitHub CI repeats the release checks, including deletion cleanup with background jobs enabled.

## Operations

Deployments must apply migrations. With `ENABLE_BACKGROUND_TASKS=True`, run `python manage.py ensure_schedules` after migrations and keep `python manage.py qcluster` running. This registers the deletion cleanup schedule and existing maintenance schedules. See [Deletion review](../DELETION_REVIEW.md) and [Operations](../OPERATIONS.md).
