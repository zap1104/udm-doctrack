# Defense-readiness audit and focused repairs

Authorized by the user's full logic/test audit and push request on 24 October 2026.
Starting revision: `1bebf45`. Branch: `codex/defense-readiness-audit`.

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**

## Execution

1. Inventory current routes, features and every collected test. Record baseline failures and distinguish missing approved behavior from defective test assumptions.
2. Reproduce and repair confirmed workflow/authorization/count/filter defects. The previously approved External custody-based control and shared-directory plan remains the contract for those cases; preserve registration/numbering/history and existing filed ownership. Do not weaken office or collection boundaries to make tests pass.
3. Verify all supported roles, internal/external routing and returns, receipt waits, work/completion/filing, private file versions and recovery, deletion/restore, timing, notifications, searches, filters, counts and exports. Use independent result-ID/count checks alongside feature tests.
4. Run the full PostgreSQL inventory with exact-ID reconciliation in isolated databases; run background-enabled regressions, JavaScript, lint/templates, Django/deployment configuration, migration, static and dependency checks. Investigate every failure rather than describing a baseline defect as passing.
5. Use seeded disposable data for operational selfcheck, page smoke, count/filter audits and real browser journeys with actual login/CSRF. Check both themes, required widths, keyboard/focus, filter updates, previews/downloads, session behavior and stored state.
6. Obtain one independent review of the combined repairs, address confirmed findings and rerun affected/final checks. Produce a revision-bound defense report and practical presentation checklist with explicit limits.
7. Commit focused changes on the audit branch and push to main after verification, as expressly requested. Verify the remote commit.

## Safety and evidence

- Every request-driven test/mutation uses disposable PostgreSQL on loopback port 8775 and temporary local storage. Never run migrations, seeding, pruning, request tests or provider calls against original data.
- Storage/OCR/email providers remain mocked or disabled; real integration availability is a separate presentation setup check.
- Preserve the unrelated local environment edit and untracked historical work. Add only reviewed task paths to commits.
- Use watched failing regressions before repairs. Keep raw logs/private fixture credentials in the ignored `.vercel/defense-audit-20261024` directory; publish only sanitized evidence.
- Do not claim universal freedom from bugs, production readiness, ISO certification, screen-reader conformance or physical-device verification without the corresponding evidence.

## Initial findings

- 8,120 tests collected at the starting revision, including 60 existing untracked External custody/context regression cases.
- The 60 External cases reproduce 45 failures and 15 passes. They cover previously approved custody responsibility, shared directory choices, selected-office context and filing authority; these are investigated in this audit rather than excluded by default.
- Initial lint, 124-template checks and all 194 JavaScript tests pass.
