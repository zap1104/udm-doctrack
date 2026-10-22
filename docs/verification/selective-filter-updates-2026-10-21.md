# Selective filter updates and stable scrolling

Verified locally on 21–22 October 2026 on `codex/external-ui-alignment`, based on `bdae00dbaa5bd83cf713e6c0806bdda5744e87a7`. Changes remain uncommitted. Existing UI and External workflow work were preserved. No migration, push or deployment is part of this change.

## Applied policy

| Controls | Update behavior |
| --- | --- |
| Dashboard office, turnaround month and stage | Automatic |
| Reports office, year and month | Automatic |
| Tracking's separate Viewing as selector | Automatic |
| Notification type | Automatic |
| Advanced Search, including its Tracking office field | Search submission |
| Repository and External Repository filters | Search submission |
| External Tracking filters | Search submission |
| Audit, users, master data and deletion-review search forms | Search submission |
| Pills, pagination, page size and reset links | Explicit activation |

Enhanced GET forms default to submit-only. Dates, relevance sliders, checkboxes and multiselects never independently submit. Search remains visible; automatic forms hide only their Apply/Show fallback when enhancement is available. Both policies update main content through HTMX. Without JavaScript, ordinary GET submissions still work.

Only explicitly grouped Dashboard and Reports forms combine live selector values. Other forms submit their own values and existing applied hidden fields. Unsubmitted editable fields survive unrelated updates without entering those requests. An explicit pill selection/removal overrides a pending dropdown value for the parameter it changes, while unrelated drafts survive. Submitting Search adopts the submitted values; edits made while that request is running remain pending. Clear/reset and Search-mode changes deliberately discard the affected drafts. Audit panels clear their own drafts independently.

## Position, focus and recovery

The script captures the latest visible region offset immediately before a swap, after any scrolling during the request. It restores that region after content, disclosures and table scroll positions settle. If the anchor cannot be retained, it centers the affected region, or clamps the previous viewport position if that region no longer exists. Focus restoration uses `preventScroll`; wheel, touch, pointer, keyboard or focus movement during settling cancels the pending viewport restoration.

Equivalent pagination controls retain focus even when they have no ID and their destination changes. If the control disappears, the current-page indicator or results region receives focus. Fixed headers are considered when centering. No animated scrolling is introduced.

Normal and native HTMX history requests have separate cancellation guards. New navigation or filter changes supersede older history requests. HTTP, network and malformed-response failures retain previous results and offer visible Retry. History retry repeats the current entry without creating another entry. Expired-session redirects load the sign-in page with its proper URL and form destination. Routine feedback remains in the visually hidden polite live region.

## Fresh verification

The final `static/js/auto-filters.js` SHA-256 is `71ee70b21d52be4dea5855091ac11d0ea8827bb0c15d5cf547387c1941e84750`.

- Focused PostgreSQL filter/dashboard/search/External regression run: **385 passed, 1 skipped, 2 existing Django deprecation warnings**, 185.07 seconds. The skip is the existing MED-admin fixture with no incoming routed rows. Two obsolete markup selectors were updated without weakening their permission or carry-filter assertions.
- Separate template/accessibility/administration/External UI regression run: **124 passed**, with the same two existing deprecation warnings.
- Full JavaScript suite: **178 passed, 0 failed**.
- Real Chrome and HTMX fixture: **28 scenarios passed**, process exit 0. Coverage includes manual combined searches, selective automatic updates, repeated parameters, draft preservation and intentional clearing, explicit pill selection/removal overriding its own field, requests finishing out of order, HTTP/network/malformed failures and retries, latest viewport capture, reduced result height, focus/disclosures/table scroll, deliberate user scrolling, history navigation, session expiry and native no-JavaScript submission.
- The original policy tests reproduced six JavaScript failures and 50 rendered-template failures before implementation. Browser regressions reproduced the unwanted manual submissions and draft/scroll loss. Independent review then reproduced seven additional history/focus failures and two pill-selection/removal failures before their focused repairs; all nine now pass. Independent review of the final source found no remaining actionable defect.
- The legacy real-HTMX interaction script passed, including chart setup, print auditing, error visibility, repeated filters, history/session redirects and no-JavaScript fallback.
- Signed-in Django journeys verified exact repository results (three fixtures narrowed to the expected single document), combined dates/dropdowns sent only on Search, independent Tracking office updates preserving unfinished queries, Dashboard month/stage/focus/region position, and independent Audit-panel drafts. These use actual login, CSRF and backend responses.
- **36 live layout checks** passed for Dashboard, Search and Audit in both themes at 320, 390, 768, 900, 901 and 1366 pixels, with zero page overflow or browser errors. Full-page and control screenshots were inspected. Search also remained usable at 200% CSS zoom/reflow.
- Separate live JavaScript-disabled journeys passed, process exit 0: Dashboard retained its native Apply button; clicking repository Search submitted both dates, office and source and returned exactly the expected document. The final check captured a trusted click and native submit, followed by the actual filtered GET 200. An interim QA timeout was traced to automated scrolling and an animation-frame wait in the JavaScript-disabled test context; stabilizing the test-side button geometry resolved it without a production change.
- Ruff passed for the three changed Python test modules. All **109 templates** passed the template checker. Django system checks reported no issues. Scoped diff whitespace checks passed.

The PostgreSQL command was:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_filter_controls.py tests/test_dashboard_turnaround_filters.py tests/test_tracking_filters.py tests/test_repository_filters.py tests/test_filter_robustness.py tests/test_filter_agreement.py tests/test_workspace_filter_parity.py tests/test_administration_filters.py tests/test_external_views.py -q --reuse-db
```

JavaScript and browser commands:

```powershell
node --test tests/js/*.cjs
node scripts/check_selective_filters.cjs
node scripts/check_auto_filters.cjs
```

The browser scripts require Playwright, Chrome and a local pinned HTMX asset. See their headers and environment overrides. Failure-injection console messages in the legacy script are expected checks, not application errors.

Evidence is in `docs/verification/selective-filter-updates/`; local execution artifacts and private disposable-fixture configuration remain under ignored `.vercel/selective-filters/`.

## Verification limits

Testing used disposable PostgreSQL data and temporary local storage, not the original database or public demo. Signed-in browser journeys use actual Django login, CSRF protection and live backend responses; CDN assets are supplied from local copies of the pinned versions. Failure scenarios use an isolated HTTP fixture with real HTMX rather than modifying production endpoints.

The isolated QA server and its launcher were stopped after checking their process identities; port 8112 was verified to have no listener. QA evidence and disposable fixtures were retained.

Physical devices, screen readers, other browser engines and native browser 200% zoom have not been verified. The responsive check uses 200% CSS zoom/reflow. This is focused filter verification, not a fresh run of the entire systemwide suite or verification of the separate pending External custody refactor.
