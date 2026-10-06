# Dashboard hierarchy corrections

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**

This follow-up addresses the supplied review's main finding: several dashboard figures had similar visual weight, even though they answered different questions. The changes are confined to dashboard presentation and the two calculations that became unused after removing their displayed content.

## Applied changes

- The page title is now **Dashboard**. The signed-in office and selected viewing scope remain visible.
- A **Needs attention** section leads with **Overdue** and **Pending filing**. Their links retain the selected office and existing destinations.
- **Tracking workload** is a smaller secondary summary. Incoming and Outgoing appear only when there is an office to give those directions meaning. An actual zero remains visible for a selected office.
- The operational queue precedes the tracking charts. Its existing filters, receipt controls, custody attestation, table labels and HTMX behavior are retained.
- **Latest tracking activity** uses the existing keyboard-accessible disclosure control. It is collapsed after scripts load, available without scripts, and reopened by the existing print behavior. Its heading remains available to screen readers when expanded.
- Pending filing has one prominent summary. The large repeated chart bar was replaced with a short explanatory link. The queue chip retains its count because it identifies a selectable queue.
- The repository donut is replaced by a compact total and two linked source rows, including counts and percentages. These use the same data and URLs as the former chart.
- Explanations on summary cards are shorter. Office scope, update time, permitted visibility and period definitions remain visible; detailed help and Reports remain available.
- Header actions take less vertical space. New styles use the existing semantic theme colors and are scoped to the dashboard.
- The unused greeting and incoming movement-today calculation were removed from dashboard context. The latter no longer performs a query for content that is not displayed. Count, routing, visibility and permission definitions are unchanged.

## Suggestions applied critically

The review suggested labelling the workload "Active". This was not used: the existing total also includes completed records awaiting filing. Calling all of it active would misstate the data.

The review's proposed "documents need your attention" headline could imply personal assignment. The dashboard can cover all offices or records an office previously handled. The page instead names the queues and keeps its viewing scope explicit.

The office-specific Incoming and Outgoing tracking charts remain separate. They describe different directions and cannot safely be merged into a single total. The repository chart was the redundant large chart, so that is the one replaced.

No default viewing scope, permissions, status definitions, receipt rules, routing, user data, file behavior, theme preference or memo content was changed. The existing pending-filing link to the Repository was preserved.

## Source

- `templates/core/dashboard.html`: priority summaries, conditional direction summaries, queue order, recent activity disclosure and repository source rows.
- `templates/core/_dashboard_help.html`: terminology aligned with Tracking workload.
- `static/css/doctrack.css`: dashboard hierarchy, compact secondary metrics and header layout.
- `apps/core/views.py`, `DashboardView.get_context_data`: removes only the two unused context calculations.
- Existing dashboard, chart, filter and context tests were updated for the intended layout. URL checks now decode HTML entities before following links, matching browser behavior. Existing count and permission assertions remain.

## Verification

- Three checks failed against the old presentation, then passed after the changes.
- The broad dashboard, report, filter, chart, accessibility and theme run passed 605 cases, with one existing skip. Five failures identified old presentation assumptions, HTML URL decoding in tests, and newly unused context values.
- All nine corrective checks passed, including those five cases, the exact dashboard context contract, office link/count agreement and the new panel order.
- Final context, queue and accessibility checks: 92 passed. The two exact query-budget checks then passed with the office budget reduced from 54 to 53, reflecting removal of the unused counter. Reports and all-office budgets stayed unchanged.
- Python lint passed, 86 templates passed validation, and whitespace checks passed.
- Browser checks confirmed the all-office and HR layouts, scoped links, HTMX queue switching, keyboard opening and closing of recent activity, and no missing description targets or duplicate IDs.
- Light and dark mobile layouts fit at 320 and 390 pixels. Proof is saved under `docs/verification/dashboard-hierarchy/screenshots/`: `02-dashboard-phone-light.jpg`, `03-dashboard-phone-dark.jpg` and `04-repository-phone-light.jpg`. Wide-screen DOM/layout checks were completed; wide screenshot capture timed out in the browser tool.

The existing skip is for a demo office with no routed incoming rows. The Django deprecation warnings predate this change. This work does not establish complete WCAG conformance; manual screen-reader review and an office-user walkthrough remain useful before release.

Changes are local. No commit, push or deployment was performed.
