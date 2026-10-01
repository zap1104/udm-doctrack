# Branch verification — 1 October 2026

Checkpoint for `feature/core-reports-dashboard`: security hardening, the repository folder browser and pending-upload queue, corrected navigation highlighting, and Pending Receipt labels. The subsequently requested margin alignment and folder sidebar are not included in this checkpoint.

## Results

- Full PostgreSQL test run with coverage: **1,532 passed, 6 failed, 1 skipped**, **76.70% coverage** (CI requires 60%).
- All six failures were addressed. The final rerun of all affected test modules plus security and repository regressions finished with **281 passed, 1 skipped**, and no failures.
- The full suite was not repeated after those fixes. The fixes changed test setup/assertions and the mobile menu icon's CSS token usage; application Python code did not change after the full run.
- The skip is an existing office-scope scenario with no incoming records. Two existing Django 6 deprecation warnings remain.
- Dependency consistency, Ruff, all 77 template checks, Django system checks, production deployment checks, migration drift checks, static collection, and Git whitespace checks passed.
- All 21 end-to-end workflow checks passed. The smoke check requested 244 pages across four roles with no unexpected errors.
- Production dependency audit reported no known vulnerabilities. A limited credential-pattern scan of the files prepared for the push found no matches; `.env.example` is the only tracked environment file.

## Repairs found by the full run

- Folder-navigation sample documents now explicitly grant university-wide access. Private folders remain protected by separate permission regressions.
- The legacy-role migration test verifies sharing while the office holds the record and rejection after the record has moved on.
- Turnaround chart samples use a fixed mid-month clock and matching handover timestamps, so month-boundary dates do not invalidate multi-day expectations.
- Upload visibility assertions check the actual upload link instead of obsolete button wording, for both ordinary users and viewers.
- The mobile menu applies its theme text color to the button and draws its bars with `currentColor`, preserving contrast and the theme token convention.

## Scope and limitations

Checks used disposable PostgreSQL databases and temporary storage. They did not modify the normal records database. Local Python was 3.13.7; GitHub CI specifies Python 3.12. Deployment checks used synthetic configuration and do not verify live SMTP or cloud storage.

At preparation time there was no open pull request for this branch. The workflow runs on pull requests and pushes to `main` or `develop`, so this branch-only push does not itself trigger GitHub Actions. These are local results, not a claim of a completed GitHub CI run.

Other installations must install the updated requirements, apply the two security migrations, and collect static files. The earlier security audit's remaining deployment and security recommendations still apply.
