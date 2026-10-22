# External recipient selection on the first page

Follow-up to `external-office-selectors-2026-10-20.md`, on local branch `codex/external-ui-alignment`. This is an External-only revision. Changes have not been pushed or deployed.

## Result

- The first External tracking creation page now includes required **From office** and **To office** selectors together. They share the existing internal/external option groups, appear side by side on larger screens and stack on phones.
- The validated recipient is remembered per draft in the current session and preselected on **Review and send**. Review still allows correcting the recipient before confirmation.
- Creation still produces an unsent draft: no routing step, receipt wait, recipient notification or permanent tracking number is created early. The existing send action performs the transition to Pending Receipt.
- Inactive, unavailable, foreign-managing-office and same-source/recipient choices are rejected. A recipient retired before review is cleared with a visible warning.
- Session loss leaves the draft unsent and permits choosing the recipient again. Existing callers that omit all recipient fields can still create legacy drafts for review; an explicitly submitted blank To field is rejected.
- Internal screens, models, services, manual External status actions and collection separation were preserved. No migration is required.

Production files for this follow-up: `apps/tracking/external_forms.py`, `apps/tracking/external_views.py`, and `templates/tracking/external/form.html`. The endpoint validator registry now keeps independent From/To validators, preserving their different scopes.

## Fresh verification

- Before implementation: new first-page regression file produced **10 expected failures and 1 pass** against the previous behavior.
- After implementation: focused selector/view checks **78 passed**.
- Final related PostgreSQL regression run: **188 passed**, with two existing Django deprecation warnings, in 39.17 seconds. Includes all External selector, first-page, UI contract, views, workflow, repository and notification tests, plus Internal tracking review tests.
- Real Chrome browser: **24 layout checks** across creation/review, light/dark themes, and widths 320, 390, 768, 900, 901 and 1366. No horizontal page overflow or JavaScript errors. Desktop light and phone dark screenshots were visually inspected.
- Real CSRF-protected submissions: internal-to-external creation with first-page recipient carryover, refresh, confirmation and manual External receipt action; JavaScript-disabled validation redisplay, changes to both offices, recipient carryover and external-to-internal confirmation. Keyboard traversal reaches To directly after From.
- Ruff passed for changed forms/views and the related UI/selector regression files. Template checker: **109 templates**, no problems. Django system check: no issues. Migration drift: no changes detected. Diff whitespace check: passed.
- Independent source review found no blocking defects in scope validation, draft creation, session intent, stale choices or sender protection.

The full system suite was not rerun for this narrow follow-up. The 7,601-pass result documented in the earlier selector report predates this revision and is historical evidence only. JavaScript source was not changed. Physical devices and screen readers were not tested.

Tests and browser actions used disposable PostgreSQL data and separate localhost-only storage/credentials. Original and public-demo data were not modified. Logs and screenshots are under ignored `.vercel/`: `external-first-page-red.log`, `external-first-page-green.log`, `external-first-page-regression.log`, and `external-uiqa/first-page-*`.
