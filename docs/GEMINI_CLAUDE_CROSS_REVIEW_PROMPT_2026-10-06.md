# 1. Confirmed current state and review context

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE. Repeat this rule in every response, revised prompt, and proposed implementation.**

I am reviewing the overall layout and visual style of UDM DocTrack for daily university-office use. This includes the shared shell, Dashboard, Tracking, Repository, Search, Reports, Accounts, Notifications and Administration. The previous implemented dashboard was not visually satisfactory to me. I am comparing designs, not authorizing implementation.

Use the original overall-layout brief and its current source packets, all 17 screenshot artifacts and SCREENSHOT_INDEX.md. Claude's complete response is another model's proposal, not an authority or an approved design. Its ending incorrectly calls Direction A and implementation tasks approved. **No design or implementation tasks have been approved.**

The source-check corrections below are limited to the current files. There has been no new live-browser, timed logout, screen-reader or usability test in this cross-review. Do not treat proposed wireframes or older screenshot dimensions as measured current behavior.

- Current Dashboard order is context, Needs attention, then module columns. The Tracking column contains compact workload summaries, the operational queue/recent activity, and then the Tracking overview charts. The queue is already before the donuts. See `templates/core/dashboard.html`, `_action_centre_queue.html`.
- Latest complete desktop hierarchy screenshots are unavailable. Earlier desktop images and newer phone images describe different stages. The short right column is a plausible layout concern, but Claude's precise latest desktop heights are unverified.
- Current CSS already gives Tracking subject cells a 280px minimum, dashboard queue subject cells 240px, and table buttons no wrapping. See `static/css/doctrack.css`, approximately lines 3669–3674. Investigate residual defects before proposing the same repair again or reducing an existing minimum.
- Existing `.btn-receipt` styles already distinguish receipt controls. Existing Tracking detail logic uses `TIMELINE_VISIBLE = 5` and older/recent splits for routing steps, activity and attachments. Their disclosures should be preserved.
- Queue counts and status-ring slices are related but not necessarily identical. See `DashboardView._action_centre`, `_tracking_rings`, `_stage_slices`, and `tracking_services.office_queue`/`apply_scope`. Incoming/Outgoing depend on one office; workload can include previously handled records and completed records awaiting filing. Preserve denominator and scope distinctions.
- Top Pending filing opens Repository `?view=pending`. A dashboard Pending filing queue opens Tracking. Moving the top figure into queue navigation must not silently lose its Repository destination.
- Repository already distinguishes Filed documents from Pending filing with views, counts and explanatory text. The pending preview may be visually excessive, but separation is not a missing feature. The hierarchy `04-repository-phone-light.jpg` depicts the Repository module on the Dashboard, not the Repository page.
- Tracking detail actions are conditional by record state, custody and authority. Reordering an area must preserve permission conditions, form boundaries, endpoint/POST semantics, field IDs and descriptions. Do not render every action together or assert custody from visibility alone.
- Current body text is 15px with Windows/system fallbacks. Reducing it to Claude's suggested 14px is not automatically a readability improvement. The reviewed template/CSS files name DM Sans/Space Grotesk but contain no corresponding font loading; effective installed fonts need runtime verification.
- Current source already contains Export CSV and Keep as Draft. Keep as Draft navigates away from a saved draft; do not imply a new save operation. The 84/32/40/256 figures were one all-office snapshot. Do not reuse them as selected-office facts.
- Existing session defaults remain 15 idle minutes for administrators, 30 for other users, with a two-minute warning and Stay signed in/Sign out now. Review the warning UI while preserving configured security behavior.

The required Django/PostgreSQL/Bootstrap/HTMX stack, user roles, visibility, office scope, current-batch routing, custody, record history, validation, extraction consent, signed downloads, CSRF, URL/filter state, theme behavior and paper-output identity/auditing remain boundaries. Every design change below is unimplemented and unapproved.

# 2. Review and improve the proposals

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**

Read Claude's full response critically. If you have already produced an independent review, compare it as well. Do not agree merely because both models mention the same idea. If source evidence for one of the corrections above is absent from your attachments, request the exact file; do not pretend to have inspected it.

1. Classify distinct proposals as **Accept for design exploration, Revise, Reject, Already addressed, or Needs verification**. Give brief evidence and reasons. These classifications do not authorize implementation.
2. Correct the source/screenshot mix-ups and stale current-state descriptions. Name the actual page and screenshot stage supporting each finding. Replace unverified pixel claims with layout hypotheses and the measurement required.
3. Compare two coherent overall visual directions. Direction A may retain a full-width work queue with restrained supporting summaries; the alternative should offer a meaningfully different composition without gratuitous new modes or features. Do not default to 50/50 columns simply for symmetry. Explain usable width, reading order, workload, chart wrapping, and populated/empty states.
4. Supply comparable annotated desktop and phone wireframes for Dashboard, Tracking list, Tracking detail, Repository, and creation/review forms. State how the same system applies to Search, Reports, upload/extraction review, Profile, Notifications, Administration, and the session warning. Treat screens with no screenshots as requiring visual verification.
5. Explicitly map every existing item moved, collapsed or removed: source location, proposed location, initial visibility, discovery/action path, data scope, and preserved destination. Keep overdue/deadline/custody consequences discoverable. Compare all-office oversight, an office's work and Viewer without implying identical actions or totals.
6. Preserve repeated `status` and `offices` selections, viewed `office` scope, search, owner, sort, overdue, page size, reset, bookmarks and back/forward behavior when redesigning filters. State whether changes need new behavior; do not hide that inside a cosmetic task.
7. Suggest a readable type/spacing/control scale, restrained palette and action hierarchy using existing tokens first. Explain why each value improves current rendering. Identify dark/light, focus, status, selected, disabled and print dependencies before changing or deleting CSS. Do not declare colors contrast-compliant without measured foreground/background pairs and relevant states.
8. Keep necessary corrections separate from aesthetic alternatives and optional features. Provide a small phased design-validation order and focused acceptance checks, not an implementation order presented as approved. Preserve existing accessibility improvements and security boundaries.

Return a corrected, consolidated **two-part analysis prompt**: current state first, proposed design work second. Label every proposal unapproved. Do not generate an implementation instruction, edit application code, or claim tests passed.

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**
