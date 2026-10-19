# Reproducing this local audit

Run from the repository root in PowerShell, with the project's existing Python
environment and a running local PostgreSQL server. Keep audit databases separate
from all business databases. Never deploy `config.audit_settings`.

## Complete Python suite

```powershell
.\.venv\Scripts\python.exe scripts\run_systemwide_tests.py --workers 2
```

The runner forces isolated settings and generates `test_doctrack_audit_full_N`
database names. It prepares/migrates and **flushes only those disposable worker
databases**, sequentially, before starting the workers. Previously saved test
fixtures in those databases will be discarded. Normal business databases and
external caches are not targets. Each worker has separate temporary files and
coverage output. The script checks worker results and combines statement coverage.

Four workers are supported; on this machine the browser must wait until the
heavy suite finishes to avoid local PostgreSQL connection timeouts. An interrupted
or setup-error run is not a test pass. Keep its output separate from a later run.

## Focused background regressions

```powershell
$env:DJANGO_SETTINGS_MODULE = 'config.audit_settings'
$env:DOCTRACK_AUDIT_SLOT = 'background_manual'
$env:ENABLE_BACKGROUND_TASKS = 'True'
.\.venv\Scripts\python.exe -m pytest tests/test_processing_regressions.py tests/test_security_maintenance.py tests/test_queued_extraction_security.py tests/test_deletion_purge.py tests/test_systemwide_storage_jobs.py tests/test_systemwide_append_generation.py tests/test_systemwide_outer_storage.py tests/test_systemwide_sibling_failure.py tests/test_systemwide_extraction_finalization.py --reuse-db -q
```

Do not run two processes with the same audit slot. File/OCR/email/queue failures
in these tests use controlled local substitutes, not real provider accounts.

## Live browser

This opt-in test requires Node, the `playwright` Node package and an installed
Chromium executable. These were already available in the local runtime used for
this audit; no package was installed or upgraded. The normal Python suite skips
the browser case unless explicitly enabled.

```powershell
$env:DJANGO_SETTINGS_MODULE = 'config.audit_settings'
$env:DOCTRACK_AUDIT_SLOT = 'browser_manual'
$env:ENABLE_BACKGROUND_TASKS = 'False'
$env:RUN_LIVE_BROWSER_AUDIT = '1'
# Set AUDIT_NODE to node.exe if it is not on PATH.
# Set NODE_PATH if playwright is installed outside normal Node resolution.
# Set PLAYWRIGHT_CHROMIUM_EXECUTABLE to your installed Chromium executable.
.\.venv\Scripts\python.exe -m pytest tests/test_systemwide_live_browser.py --reuse-db -q
```

The harness's local defaults refer to this audit machine. Set the three runtime
variables above as appropriate on another machine. Cached, version-matched
Bootstrap and HTMX vendor assets must exist in `AUDIT_VENDOR_DIR` (defaults to
the Windows temporary directory). The exact asset names are declared in
`scripts/check_systemwide_live.cjs`. HTML, sessions, CSRF, filters, saved actions
and downloads still come from the actual isolated Django server.

The browser report records cancelled requests from the deliberate stale-response
test separately from unexpected HTTP or JavaScript errors. Removing the session
cookie simulates lost authentication; it is not a real 15-minute clock wait.

## Inventory and static checks

```powershell
.\.venv\Scripts\python.exe scripts\audit_inventory.py
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe scripts\check_templates.py
node --test tests/js/*.cjs
```

Inventory generation blocks database connections. Its candidate-test mappings
remain discovery evidence, not a claim that every listed route/role/action passed.
Raw JUnit, coverage, worker logs and browser JSON are machine-local evidence;
the committed report and summaries describe the actual accepted runs and limits.
