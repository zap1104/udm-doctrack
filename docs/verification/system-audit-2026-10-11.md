# System audit, 11 October 2026

## Confirmed issues fixed

1. **Expired sessions during automatic filtering or browser history navigation.** The sign-in page also contains `#main-content`, so the filter handler accepted it as workspace content and left the authenticated shell and wrong form destination in place. `static/js/auto-filters.js` now checks the final same-origin response URL before swapping. A native history-XHR load guard handles HTMX 1.9.12 history restoration, which bypasses the normal swap event and cannot be cancelled at its later history-load event. Real browser regressions reproduced the failures before the fix and verify filter, Back, and Forward navigation, full shell replacement, correct login POST destination, and no transient insertion.

2. **False office warnings from Repository Reports.** `ReportsView._workspace_context` used Tracking's `office=all` value for repository links. Repository expects the office parameter to be omitted for all available offices. The generated workspace and document-type links now use the destination's accepted parameters. Explicit office IDs and Tracking's all-office scope remain intact. Tests follow the links and compare destination counts with report figures.

3. **Sharing form accepted two recipients and raised a server error.** `GrantAccessForm.clean` now rejects simultaneous office and user selections, matching the service's existing single-recipient rule. `GrantAccessView` displays the actual validation message. Tests cover rejection without creating grants for ordinary users, office administrators, and system administrators, and successful office-only and user-only shares.

4. **Bulk and individual receipts could deadlock.** Bulk receipt locked records before its nested receipt call locked the account; individual mutations used the reverse order. Bulk receipt now locks/revalidates the account first, then locks records, matching the shared mutation guard. A deterministic PostgreSQL test reproduced `DeadlockDetected` before the change and verifies that concurrent requests now complete with exactly one receipt event.

Changes preserve record data, role boundaries, routing semantics, status meanings, the earlier automatic-filter and terminology work, and unrelated local settings. No model or migration changes were needed. Changes remain local on `codex/automatic-filter-updates`; no commit or push was requested.

## Verification results

| Check | Result | Evidence |
| --- | --- | --- |
| Full baseline suite, before these audit corrections | 6,839 passed, 2 skipped; 81.83% application coverage | `system-audit-pytest.txt` |
| Final combined regression for changed backend behavior and related role/workflow rules | 314 passed | `system-audit-fixes-pytest-final.txt` |
| Background-enabled processing, maintenance, and queued extraction | 26 passed, including the maintenance path skipped by the normal configuration | `system-audit-background.txt` |
| Final JavaScript suite | 172 passed | `system-audit-js-final.txt` |
| Real HTMX browser interaction and expired-session scenarios | Passed | `system-audit-browser.txt` |
| Live dashboard, report, repository, filter, and search reconciliation | 21,224 checks across 26 role/office scopes; zero mismatches; all four recorded integrity counters zero | `system-audit-counts.json` |
| Live page smoke checks | 256 pages across four roles; no unexpected errors | `system-audit-pages.txt` |
| End-to-end document workflow | 21 checks passed | `system-audit-selfcheck.txt` |
| Turnaround calculations and timestamp edge cases | 44 checks passed | `system-audit-turnaround.txt` |
| Installed dependency vulnerability audit | No known vulnerabilities reported | `system-audit-dependencies.json` |
| Ruff, all 91 templates, Django system checks, deployment-shaped checks, migration drift, static collection, whitespace checks | Passed | Tool results from this audit |

The initial combined regression exposed a test-fixture mistake: another employee's private draft is correctly hidden from an office administrator. The fixture now routes the document before testing sharing validation. Production draft visibility was preserved. The final combined run passed.

The two Django `CheckConstraint.check` deprecation warnings predate this audit. The remaining full-suite skip is an office-admin fixture with no incoming rows for that specific scope. The full baseline suite was not repeated after the narrow corrections; the final combined backend suite, JavaScript suite, and browser checks cover the changed behavior.

Live audit helpers roll back their temporary records, sessions, and request logs. Browser auth regressions use a local HTTP fixture with the real pinned HTMX and application scripts; they are not a live Django browser login or manual screen-reader audit. External OCR, cloud storage delivery, and production deployment were not exercised.
