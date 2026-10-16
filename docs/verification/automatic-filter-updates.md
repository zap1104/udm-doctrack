# Automatic filter updates, 10 October 2026

Dropdown filters now request updated page content through the existing GET views. The main workspace refreshes without reloading the browser document, sidebar, theme controls, or session warning. No backend query, permission, routing, data model, or record-saving behavior was changed.

## Coverage

- Dashboard office, turnaround month, and turnaround stage.
- Tracking office scope; existing same-page filter pills and pagination are enhanced where the filter form is available.
- Tracking and Repository Reports office scope, plus Tracking Reports annual year and turnaround month.
- Repository and Pending Filing document type, year, month, tags, origin, and retention.
- Advanced Search dropdowns, date fields, relevance threshold, and show-all checkbox.
- Notifications type, and administration office/role/status/action/event filters.
- Same-page reset and pagination links retain their normal URLs and can update in place.

Search buttons remain for typed queries. Saving, routing, filing, receipts, completion, uploads, and other POST actions retain explicit submission. Pure filter Apply/Show buttons remain available only when JavaScript or HTMX is unavailable. No inline event handlers or executable response scripts were introduced.

## Interaction safeguards

- Filter choices are combined after a short debounce. Visible choices across independent Dashboard/Audit forms override stale hidden carry fields.
- New choices immediately cancel previous requests, including the gap before the next request starts. Old responses cannot replace the latest selection.
- Repeated parameters, page sizes, and existing server-side filter rules are preserved.
- A persistent polite status announces loading, completion, or failure. Routine feedback is visually hidden and occupies no layout space. Only failed updates, including permission errors, display the message and Retry. Failed/incomplete responses leave previous results visible.
- Dropdown focus and expanded disclosures survive content updates. Initial-page autofocus is removed from in-place responses to prevent Search from stealing focus.
- Back/Forward retrieves fresh main content in place. Legacy HTMX body-cache entries are discarded because their shape is incompatible with main-content restoration. This also preserves Search helpers and contextual help.
- Chart measurements, help topics, Search readouts/autocomplete, printing, and confirmation/double-submit guards continue working on replaced content.

## Verification

- New rendered-control expectations failed before the automatic filter wiring existed. The period-picker cases also reproduced missing enhancement before their correction.
- Real HTMX 1.9.12 headless-browser regressions reproduced and then verified fixes for overlapping requests, malformed responses, autofocus, history-restored Search controls, and legacy history caches.
- Full final PostgreSQL suite: **6,839 passed, 2 skipped, 2 existing Django deprecation warnings**, 768.79 seconds. See `auto-filters-pytest-final.txt`. The initial integrated run's three failures were obsolete exact-markup expectations for Apply buttons; they now check the native fallback contract.
- JavaScript suite: **167 passed**, including new automatic-filter event and parameter tests. See `auto-filters-js.txt`.
- **30 browser checks** covering 15 real rendered filter cases at 1440px/light and 390px/dark. Checks included in-place updates, selected query values, hidden Apply buttons, retained focus/theme, fresh Back navigation, and available help topics. See `auto-filters-browser.txt`.
- `scripts/check_auto_filters.cjs` passed the interaction checks for repeated statuses, rapid selections across forms, focus, disclosures, chart measurements, print audit references, history, retry, reset, and no-JavaScript fallback. Its 500/abort console messages are deliberately injected recovery scenarios, not application errors. See `auto-filters-interactions.txt`.
- Ruff passed, Django system checks reported no issues, all **91 templates** passed, static collection succeeded, and diff whitespace checks passed.

The real-page browser checks replayed actual server-rendered responses captured in a rolled-back transaction. They did not alter user records and are not a live signed-in end-to-end or manual screen-reader audit. Unrelated local settings were preserved. These changes are local on `codex/automatic-filter-updates`; no push was requested for this task.

Follow-up feedback visibility check: **169 JavaScript tests passed** (see `filter-feedback-js.txt`). The real HTMX browser check with Bootstrap 5.3.3 and the current application stylesheet verified that initial, loading, and successful messages are visually clipped, remain in the polite live region, and occupy no layout space. Injected 500 and 403 responses visibly displayed Retry without replacing previous results; recovery hid the message again. The full backend suite above was not repeated for this visibility-only change.
