# HTTPS diagnostic follow-up

Source under review: `c355774` on `codex/systemwide-bug-audit`.

The first GitHub CI run (`39186784541`) completed 7,574 collected cases with 7,568 passed, two failures, four documented skips, and two existing warnings. The two failures came from `FilterAudit.run()` creating its own ordinary Django `Client`, then requesting plain HTTP while CI enabled `SECURE_SSL_REDIRECT`. The resulting 301 had no template context, so `listed_ids()` could not inspect its page. The count diagnostic already requests HTTPS; the shared pytest client also defaults to HTTPS.

The local audit settings inherit the HTTPS redirect value from the existing environment. That environment disabled the redirect, so the earlier local full suite did not expose this diagnostic-client mismatch. The repair preserves application HTTPS settings and all filtering behavior.

Regression: both existing failing tests now explicitly enable `SECURE_SSL_REDIRECT=True`, independent of the local environment. Before the helper repair, run those two tests in `config.audit_settings` using the disposable slot `https_fix`; then request HTTPS in `FilterAudit.get()` and run the complete oracle module plus the filter matrix. No business database or real storage, OCR, email, or queue providers are used.

## Verification

- RED before the helper repair: **2 failed**, two existing Django 6 deprecation warnings, **104.22 seconds**. Both failed at `listed_ids()` because the HTTP redirect had no rendered template context. Artifacts: `ci-https-red.txt` and `ci-https-red.xml`.
- Minimal repair: `FilterAudit.get()` calls `self.client.get(path, params or {}, secure=True)`. This matches `Audit.get()` and the application's HTTPS test client.
- GREEN complete oracle module and filter matrix: **137 passed**, zero failures/errors/skips, two existing Django 6 deprecation warnings, **175.15 seconds**, exit code 0. JUnit confirms **8 oracle cases and 129 filter-matrix cases**. Artifacts: `ci-https-green.txt` and `ci-https-green.xml`.
- Ruff for the two changed Python files: **all checks passed**, exit code 0. Artifact: `ci-https-lint.txt`.

No application code, database schema, HTTPS setting, permissions, filters, or query behavior changed. This follow-up does not claim a fresh local full-suite result; the next GitHub CI run will verify the complete suite.

## Reproduction

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.audit_settings"
$env:DOCTRACK_AUDIT_SLOT = "https_fix"
$env:ENABLE_BACKGROUND_TASKS = "False"
$env:RUN_LIVE_BROWSER_AUDIT = "0"
.\.venv\Scripts\python.exe -m pytest tests/test_systemwide_audit_oracles.py tests/test_systemwide_filter_matrix.py -q --reuse-db --tb=short --no-cov --junitxml=docs/verification/systemwide-audit-2026-10-17/ci-https-green.xml
.\.venv\Scripts\python.exe -m ruff check scripts/audit_filters.py tests/test_systemwide_audit_oracles.py
```

For RED, select only the two named tests above before applying the `secure=True` helper repair. Both regressions enable SSL redirection directly, so their failure/success does not depend on the developer's environment.
