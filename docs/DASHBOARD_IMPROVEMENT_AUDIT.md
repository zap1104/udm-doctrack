# Dashboard improvement audit — 30 September 2026

Both supplied improvement packages have the same SHA-256 hash and identical contents.
This implementation uses their verified counting and readability recommendations.

## Existing definitions checked

| Area | Code evidence | Finding |
| --- | --- | --- |
| Access and office scope | `apps/core/views.py`, `DashboardMemoMixin._scoped`; `apps/tracking/services.py`, `scope_office`, `office_queue`, `apply_scope` | Visibility is applied before office filters. Tracking analytics include records involving the selected office; they are not solely work performed by that office. Existing selector rules are preserved. |
| Total and queues | `apps/tracking/services.py`, `active_for`, `apply_scope`; `apps/core/views.py`, `DashboardView.get_context_data` | Total uses the live Tracking list. Incoming and Outgoing use the current batch. Outgoing includes acknowledged documents until completion. Cards overlap. |
| Repository identity | `apps/documents/models.py`, `Document.tracking_record`, `Document.office`, `Document.source` | The tracking link is one-to-one and owning office is required. Duplicate linked repository records are already prevented by the schema. Repository additions use `Document.created_at`, not tracking completion or the date printed on a document. |
| Incorrect repository additions | `apps/core/analytics.py`, `uploads_by_office` | Previously summed repository rows and completed Tracking rows. Now counts distinct active repository IDs in a bounded local calendar month and partitions those rows by source. Unfiled completions contribute zero. |
| Turnaround | `apps/core/analytics.py`, `_turnaround_samples`, `turnaround`, `turnaround_by_day`, `turnaround_by_month` | One shared collector feeds daily/monthly/report/memo calculations. Receipt is bucketed by receipt date; document durations by completion date. Monthly averages already use raw samples. Zero office time remains valid; invalid ordering and missing required endpoints are now excluded and explained per stage. |
| Chart observations | `apps/core/views.py`, `DashboardView._trend_points`; `templates/core/_turnaround_chart.html` | Solid lines connect recorded averages at their actual dates. Missing dates have no dots and do not enter calculations. An initial gap/dash change made sparse daily data unreadable and was reverted after user feedback. Tooltips retain descending value order and include elapsed time. |
| Calendar | `config/settings.py`, `TIME_ZONE`; `apps/core/business_time.py`; `apps/core/models.py`, `Holiday` | Default timezone is Asia/Manila. Working hours, lunch and working weekdays are configurable; administrator-maintained holidays currently represent whole-day closures. |
| Deadlines | `apps/tracking/models.py`, `due_at`, `overdue_q`; analytics collector | Deadlines are timestamps. On-time calculations use the stored current deadline; there is no new claim that it is an immutable deadline-at-completion snapshot. |
| Themes | `static/css/doctrack.css`, `static/js/theme-init.js` | Existing semantic colour tokens are preserved. No chart library or frontend dependency added. |

## Changes delivered

- Actual repository filings counted once, including scans and historical uploads. Bounded month excludes dates from later months and inactive documents.
- Office shares use largest-remainder percentage rounding; displayed shares total 100 while bar widths retain the actual fraction.
- The Tracking panel appears above Action Centre, as requested. Tracking and Repository remain separate.
- Turnaround stages use their corresponding yellow, pink and green theme colors. Completed Pending Upload remains included in Total Documents but is excluded from pie slices. A compact tinted row below the chart displays its count and explains that inclusion. Direction rings retain their unfinished-transfer definitions.
- A shared right-side Page guide holds scope details, refresh time, period differences and counting explanations. The topbar Help button lists topics; compact panel info buttons open the matching topic. Every `chart-note` explanation uses the same guide, including Reports. Essential numbers, filters, units, data tables and exception notices remain inline. If JavaScript or Bootstrap is unavailable, the original explanations remain readable inline.
- Help controls share one SVG icon, consistent dimensions, theme colors and focus treatment. Panel headers reserve space for the info button so titles and filters cannot collide with it; touch controls retain a 44-pixel target.
- Action Centre's full-list link sits in its header and updates with HTMX queue changes. Recent document updates show relative update times and their exact timestamp on hover.
- Removed the claim that cumulative Created minus Completed is the current queue; removed the office winner banner.
- Monthly summaries remain visible. Tooltips add elapsed time, lines connect observed values, and missing/invalid timestamps are explained.
- Dashboard, report and memo responses (including dashboard HTMX responses) receive private, no-store cache headers.
- Regression coverage added/updated. Run pytest and the full CI checks before pushing, per the user's testing preference.

## Proposals requiring a separate policy or architecture decision

The pasted text includes placeholders and conflicting scope recommendations. It is not an approved UDM calendar or service charter. No national holiday dates, statutory processing deadlines, new office schedules, or retention/disposal rules were inferred from it.

- Effective-dated per-office schedules, partial suspensions and calendar source/version records need agreed institutional rules, migration design and administration UI. Current whole-day calendar behavior is unchanged.
- Persisted turnaround facts and nightly reconciliation would introduce event hooks, backfill and invalidation responsibilities. The existing shared collector already prevents calculation drift; no second store was added without a demonstrated performance need.
- Historical deadline snapshots and reopened-document completion attribution require an agreed reporting policy before changing existing reports.
- Requested rejection/auditing of invalid office selectors differs from the current fallback behavior. Existing visibility restrictions are retained; this pass does not change navigation error semantics.
- First-visit onboarding, issue submission and a full data-quality work queue are further product features. This pass supplies a reading guide and per-stage exclusion counts, not those additional workflows.

These limits are deliberate: completed Tracking work is not silently reclassified as repository content, and office activity is not presented as staff productivity or legal compliance.
