# External-only From/To office selectors

Scope: approved follow-up to the External UI alignment, on local branch `codex/external-ui-alignment`. Changes remain uncommitted and have not been pushed or deployed.

Later revision: recipient selection now starts on the first creation page. See [the first-page recipient verification](external-first-page-recipient-2026-10-20.md). The implementation and test results below describe the earlier selector change.

## Change

- External draft creation has one required **From office** selector, with **Internal offices** and **External UDM offices** option groups.
- External draft review and subsequent endorsements show **From office** as the recorded current custodian in a read-only, unsubmitted field, beside one required **To office** selector with the same option groups.
- Internal creation/routing forms, repository controls, models, migrations, tracking services, numbering, manual status actions, timing and permissions were not changed by this follow-up.
- Existing scoped office validators still check selections. The form converts valid grouped choices to the existing service arguments; the combined token never reaches a service.
- Valid older submissions using the former field names still work. Those legacy fields are not rendered, avoiding stale hidden values when users correct validation errors or work without JavaScript.
- Invalid, repeated, inactive, foreign-office and conflicting values are rejected. A forged read-only From value cannot change the actual sender.

Production files changed in this follow-up: `apps/tracking/external_forms.py` and `templates/tracking/external/form.html`. Earlier UI/branding edits were preserved. Regression coverage was added in `tests/test_external_endpoint_selectors.py`; the earlier UI contract test was updated for the intentionally combined recipient control.

## Verification

- Before implementation, all 18 initial selector tests failed against the old paired fields.
- Final External selectors, views, workflow, repository, notifications and UI contract run: **142 passed**, with two existing Django deprecation warnings.
- Lint: passed. Template checker: **109 templates**, no problems. Django system check: no issues. Migration drift: no changes detected.
- Independent source review found no concrete defects in endpoint validation, compatibility, sender protection or permission enforcement.
- Live browser: **36 layout checks** across creation, draft review and later endorsement, light/dark themes, and widths 320, 390, 768, 900, 901, 1366. No page overflow or browser JavaScript errors.
- Real CSRF-protected browser submissions passed for internal-to-external routing and external-to-internal routing. A separate JavaScript-disabled journey passed validation redisplay, source changes and keyboard submission. Sending still produces Pending Receipt and preserves manual external receipt actions.
- Full PostgreSQL Django suite against the final production code: **7,601 passed, 4 skipped, 2 existing Django deprecation warnings**, in 1,123.86 seconds. The skips were two background-only cases, one role/filter fixture without routed rows, and the optional existing systemwide Chromium harness. The separate real-browser journeys above were run for this External change.

All request-driven checks use disposable PostgreSQL data. The browser uses a separate localhost-only server, fresh disposable credentials and separate temporary storage. The working and public-demo databases were not modified.

Logs are locally available under ignored `.vercel/`: `external-endpoints-red.log`, `external-endpoints-focused.log`, `external-endpoints-full-tests.log`, and `external-uiqa/endpoints-browser.log`. Browser screenshots and the structured report are under `.vercel/external-uiqa/endpoints-*`.

Physical devices and screen readers were not used. Browser-size checks do not establish assistive-technology conformance. Live cloud services were not contacted.
