# Admin All offices Reports view

## Behavior

- Only a System Admin viewing All offices gets the combined chart, including the default Reports scope and `?office=all`.
- A single full-width, 48-pixel stacked bar shows Draft, Pending Receipt, Received, and In Process using the existing status palette. The current total is inside the chart, replacing the Incoming/Outgoing headline cards for this scope.
- Every document is counted once, including records sent to multiple recipients or forwarded through several offices. The four stages reconcile with the current total.
- Both completion stages stay excluded. Repository reports, historical analytics, selected-office Reports, and ordinary users' own-office views retain their existing behavior.
- Stage links open the exact matching records through Tracking's existing filters. No schema, routing, permission, or document-data change is needed.
- The combined chart reuses the existing grouped counts and total; the Reports query budgets remain unchanged.

## Checks

- New and existing direction regressions: **15 passed**. Tests first demonstrated the missing combined chart, then passed with the implementation.
- Broader Reports, permission, destination-count, query-budget, and dashboard agreement regressions: **242 passed**.
- An independent source review found no actionable issues.
- Ruff checks and all **90 templates** passed.
- Full regression suite: **6,783 passed, 2 skipped, 2 existing Django 6 deprecation warnings** in 474.63 seconds. The skips cover an office-admin fixture without incoming rows and a background-job check exercised by CI. Full output is in `pytest-full.txt`.
- Django system checks passed, and the migration check reported no changes.

## Browser layout evidence

The normal browser automation runtime was unavailable. A read-only render of the actual Reports view and local System Admin data was inspected with an isolated headless Chrome profile. This preview did not sign in, change an existing browser session, or write user/document records. It verifies layout and current styling; interactive destination behavior is covered by the Django client tests.

- Current local data: **44 documents**, consisting of Draft 0, Pending Receipt 13, Received 11, and In Process 20. Visible rounded percentages are 0%, 30%, 25%, and 45%; the bar uses exact counts for segment proportions.
- Desktop light and dark previews were visually inspected in `desktop-light.png` and `desktop-dark.png`.
- Exact 390-pixel light/dark previews and a 320-pixel light preview were checked inside same-width frames because the headless browser has a 500-pixel minimum outer window. The mobile screenshots are labeled as previews.
- Browser measurements found no horizontal overflow at 320 or 390 pixels. Every stage link measured at least 44 pixels tall, with no overflowing contents. The bar remained 48 pixels tall. Details are in `layout-checks.json`.
- Print CSS preserves segment colors and keeps the combined chart together; no PDF export was performed.

The unrelated existing edit to `config/settings.py` was preserved. No commit or push was performed for this update.
