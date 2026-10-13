# Endorsement terminology, 10 October 2026

The application uses **endorsed** for past actions and **endorsement/endorsements** for routing activity counts. Dashboard headings, legends, table headers, tooltips, accessibility descriptions, Reports, Tracking help, and turnaround explanations use the same vocabulary. Existing Endorse actions remain in place.

Chart series identifiers, their CSS color selector, tooltip color fallback, receipt sample nouns, office-flow context keys, audit scripts, existing test expectations, and project documentation were updated consistently. Routing-step and document counts keep their original meanings. No model field, migration, URL, permission rule, stored record, or routing calculation was changed. Existing local filter work and unrelated settings were preserved. Archived review packets and previous verification captures retain their historical contents.

## Verification

- A source scan found no old terminology, including spaced or underscored variants, in application code, templates, styles, scripts, or tests.
- **1,141 affected Python tests passed**, with two existing Django deprecation warnings. Coverage includes role permissions, chart rendering, Reports and Dashboard agreement, turnaround samples, current routing batches, and office-flow shares. See `endorsement-pytest.txt`.
- **169 JavaScript tests passed**, including chart hover and keyboard behavior. See `endorsement-js.txt`.
- Browser checks used the actual `_columns.html` template, analytics series metadata, application stylesheet, Bootstrap 5.3.3, and chart hover script. At 1440px and 390px in both light and dark modes, the endorsement bar kept its official pending color; hover showed the correct label and count; keyboard navigation announced the new label; zero-value items retained their color fallback. These were focused component checks, not a manual screen-reader audit.
- An AST comparison against the starting commit confirmed that both modified backend modules retain executable structure apart from renamed identifiers and text.
- Ruff passed, all **91 templates** passed validation, Django system checks found no issues, static collection succeeded, and whitespace checks passed.

The changes remain local on `codex/automatic-filter-updates`; no commit or push was requested.
