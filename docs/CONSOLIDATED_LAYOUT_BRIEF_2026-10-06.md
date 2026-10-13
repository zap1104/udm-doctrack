# 1. Current state and evidence

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE. Repeat this rule in every response, revised prompt, and proposed implementation.**

UDM DocTrack is an existing Django/PostgreSQL/Bootstrap/HTMX document system for university offices. The user wants the overall layout and visual style improved, including desktop use, rather than another mobile-dashboard-only refinement. The previous implemented dashboard hierarchy is not an approved visual design.

The review evidence comprises the current source packets, all 17 saved screenshots, their stage index, and the externally supplied responses. The attachment attributed to Gemini contains exactly the same text as the earlier attachment attributed to Claude. The file comparison found identical text and hashes. Treat this as one unique proposal. It does not establish independent agreement or an additional review.

The source checks are documented in `docs/CLAUDE_LAYOUT_REVIEW_2026-10-06.md`. No new browser, screen-reader, timed-session or usability testing was performed during this consolidation. No application code changed.

Confirmed context to preserve:

- The application uses a shared sidebar/top bar and different overview, list, detail, form/review and reporting page families. The required stack and server-side authority remain unchanged.
- The current Dashboard has a prominent Needs attention strip, an 8/4 desktop module split, compact Tracking workload summaries, queues before the overview charts, expandable recent activity and a compact Repository summary. The hierarchy screenshots cover phones; earlier desktop screenshots show an earlier stage.
- Tracking workload, office Incoming/Outgoing, named queues, status-ring slices, overdue subsets, pending filing and filed Repository totals have distinct definitions. Similar example counts are not interchangeable. Snapshot numbers must not be treated as fixed or selected-office totals.
- Top Pending filing opens the Repository pending view. A Pending filing queue opens Tracking. Moving an item must preserve its actual destination and selected office.
- Current source already fixes narrow subjects and wrapping table buttons, provides receipt styling, preserves accessible field/error associations and tabs, and discloses older history beyond five entries. Preserve those changes rather than repeat old fixes.
- Repository already separates filed and pending views. The Dashboard Repository summary image is not evidence of the Repository page's current layout.
- Detail actions depend on state, permission and custody. Form grouping must retain individual endpoints and conditions. Filters retain repeatable selections and distinct viewed-office/originating-office semantics.
- Body text currently uses 15px and Windows/system fallbacks. Theme tokens, focus, reduced motion, chart data alternatives and deliberate paper surfaces remain foundations.
- Session defaults remain 15 idle minutes for administrators and 30 for other users, with a two-minute warning. Review warning clarity without changing the timeout or keepalive rules.

# 2. Proposed design work, not approved implementation

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**

Use the external proposal as a candidate, corrected by the source facts above. Produce a coherent design for the entire application before writing product changes. Do not present the direction or tasks as approved.

## Recommended candidate to compare visually

1. **Shared shell:** Retain recognizable navigation and university identity. Use a consistent page-heading, scope, action and content hierarchy, with restrained brand/status emphasis. Keep typography and panel treatment readable; do not shrink body text merely to increase density.
2. **Dashboard:** Compare a full-width operational queue near the top with a composition that gives compact urgent summaries equal discoverability. Keep office scope and read-only roles explicit. Put supporting overview information in balanced rows according to actual content; do not enforce 50/50 widths or identical heights solely for symmetry. Retain the Repository pending destination when relocating its shortcut.
3. **Lists and search:** Reduce the visual prominence and vertical footprint of filter groups while keeping selection state and essential filters understandable. Compare a compact toolbar against the existing links; preserve multi-select, Apply, reset, URL/history and pagination behavior. Retain the current subject widths and readable actions unless a new reproduction warrants adjustment.
4. **Detail screens:** Place a clearly identifiable, permission-aware action area near the current state, deadline and custody context. Keep supporting metadata, files and history easy to reach. Reuse existing disclosures, separate forms and server rules. Never show a custody assertion just because a user can view the record.
5. **Creation, upload and review:** Compare a bounded-width form with carefully grouped fields against a balanced two-column option. Keep critical instructions, consent, deadline choices and errors beside the relevant controls. Review pages must clearly distinguish editable suggestions, saved drafts/files, and the effect of sending or saving.
6. **Supporting screens:** Apply concrete versions of the same visual rules to Reports, Profile, Notifications and Administration. Address header/actions, filters, table/card density, errors and empty states. Include session-warning focus, wording and unsaved-work consequences. Preserve screen versus paper purposes.

## Do not automatically adopt

- New Action Console/Governance modes or an All I Can Act On filter.
- A universal 50/50 grid or artificial equal-height panels.
- A 14px body-text reduction, new web-font dependency or wholesale palette replacement.
- Blanket collapsed analytics, files or document information without evaluating discovery and extra steps.
- Broad deletion of old CSS layers before checking their theme, focus, screen and print dependencies.
- Repeated repairs for already-corrected issues, or merged counts with different query definitions.

## Next deliverable

Provide two comparable visual candidates, including annotated desktop and phone layouts for Dashboard, Tracking list, Tracking detail, Repository and a form/review flow. Include a concrete treatment for the remaining screen families. Use all 17 images as staged evidence; do not describe them as one simultaneous current version.

For every moved, collapsed or removed item, map its old/new location, initial visibility, access path, preserved data/office scope, destination and action authority. Identify genuine corrections separately from aesthetic alternatives and optional features. Name which findings need a current browser or NVDA check.

Evaluate page recognition, scope clarity, record identification, state/deadline/custody understanding, permitted action discovery, readable long content, keyboard flow, zoom, light/dark, meaningful empty/zero states and no-script/HTMX behavior. Existing regression checks remain required for later implementation, but do not establish visual preference.

The user chooses a visual direction before implementation. No design, changed workflow, security setting, commit, push or deployment is authorized by the external response's wording.

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**
