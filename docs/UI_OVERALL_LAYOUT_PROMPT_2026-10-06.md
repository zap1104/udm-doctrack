# 1. The current state

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE. Repeat this rule in every response, revised prompt, and proposed implementation.**

I am improving UDM DocTrack, also called UDM RDMS, my university capstone records and document management system for offices under OVPA at Universidad de Manila. Staff need to find permitted records, understand their stage and deadline, and perform the next permitted action without competing information overwhelming the screen.

My feedback concerns **the overall application layout and visual style**, not only the dashboard or a mobile layout. I disliked the latest dashboard revision, and I want a consistent university-office design across the system. Do not assume that revision is the right design simply because it was implemented or passed functional checks. Review it critically. Preserving working behavior does not mean preserving every current visual decision.

This is an **analysis and design round only**. Do not change application code. I will compare proposals from Claude and Gemini before choosing a design.

## Evidence and boundaries

The attached source packets are snapshots of my local working files on **6 October 2026**, including uncommitted changes. Each section names its original repository path. They are selected files, not the full application. Read relevant source before making implementation-specific claims. Request an exact missing file when a finding depends on it. Do not invent code, dependencies, screenshots, user research, or test results.

The packet includes **all 17 previously saved screenshots**, covering desktop dashboard views, Tracking lists/rows/detail, Tracking form/deadline entry, Repository, phone layouts, and light/dark examples. They come from different review stages. Read SCREENSHOT_INDEX.md: the original review images predate the UI corrections; the first correction images predate the dashboard hierarchy pass; the hierarchy images show that later pass; the older turnaround image has an unconfirmed capture date. Use current source for current structure and screenshots for the appearance at their recorded stage. Do not combine old and newer dashboard layouts into one claimed current screen. Screens without screenshot coverage still need source-based layout review; mark visual observations about them as unverified. Distinguish source-confirmed findings, screenshot observations, hypotheses, and checks requiring a browser or screen reader.

Required stack: Django 5, PostgreSQL, Bootstrap 5 with HTMX, django-q2, django-axes, django-csp, Argon2, signed downloads, Azure Blob or Cloudflare R2, and OCR.space with local-first text extraction. Windows/PowerShell development. Do not propose replacing the stack or adding a component library to solve this review.

## Shared shell and appearance

- Desktop uses a left sidebar, top bar, main content area, and footer. The sidebar becomes a drawer on small screens.
- Navigation groups are Workspace, Documents, and permitted System controls. Dashboard, Search, Document Tracking, Document Repository, and their Reports links are distinct. Office and role appear in the sidebar.
- The top bar contains archive search, contextual Help, a light/dark toggle, notifications, and an account menu.
- The current shared CSS has multiple layers and later overrides. It specifies a dark green sidebar, institutional green/gold accents, bordered panels, status pills, office badges, tables, and several button treatments. Later font declarations request DM Sans for body text and Space Grotesk for headings, with Windows/system fallbacks. The declaration alone does not prove those fonts are loaded; check effective styles before judging typography.
- Later CSS also changes panel spacing, controls, and borders. Do not describe the appearance from an early rule while ignoring later selectors and media queries.
- Existing support includes a skip link, landmarks, visible focus treatments, textual statuses, labeled controls, native disclosures, reduced-motion rules, and chart data tables. Light/dark semantic tokens and pre-paint theme initialization exist.
- JavaScript moves extended inline explanations into the Page guide and adds contextual help controls. Source order is not always the final visible layout. Essential scope, task instructions, errors, and consequences must remain understandable without opening Help.

## Current dashboard layout

1. Header: Workspace eyebrow, **Dashboard** title, signed-in office, and actions **Open Reports** and **Generate memo**. System administrators can choose **All offices** or one office with an explicit **Apply** button. Other users have their own-office scope.
2. A context panel states the viewing scope, update time, permitted visibility, and period rules. Queues are current; month filters affect turnaround only; repository additions use the current month.
3. **Needs attention** spans above the module columns. Two prominent linked cards show **Overdue** and **Pending filing**. Overdue opens the existing filtered Tracking list. Pending filing opens the Repository pending view, preserving the selected office.
4. Large screens use a wider Document Tracking column and narrower Document Repository column, nominally Bootstrap 8/4 at the xl breakpoint. Smaller screens stack Tracking first. Evaluate the effective CSS and whether this creates uneven heights or a poor reading path.
5. Tracking starts with smaller workload summaries. Incoming/Outgoing appear for an office scope; they are omitted from the summary under All offices. These counts overlap. **Tracking workload includes completed records awaiting filing**, so do not rename it Active without checking the definition.
6. **Tracking queues** precede charts. Queue links include Incoming, Pending Receipt, Received, In Process, Pending filing, Overdue, and Outgoing. Directional choices need an office. Pending Receipt is the default. The selected queue shows up to five records and a link to its full Tracking list. Eligible rows support receipt confirmation and bulk receipt with required custody attestation. HTMX swaps the queue and preserves URL/history; ordinary links work without JavaScript.
7. **Latest tracking activity** is expandable and collapsed after scripts load. It remains available without scripts and opens through existing print behavior.
8. **Tracking overview** retains Status/Overdue views, an all-office donut or office-specific Incoming/Outgoing donuts, linked legends, counts, and percentages. Pending filing has a short explanation/link rather than a second large banner. Preserve distinctions between a status total, an overdue subset, and a different queue.
9. Tracking also contains cumulative Created/Endorsements/Completed activity, an alternative data table, and monthly turnaround with month/stage filters and calculation context.
10. Repository uses a compact filed-document total and linked source rows instead of its former donut. It retains current-month additions by office and the related report link.

Last captured all-office examples were workload **84**, overdue **32**, pending filing **40**, and repository **256**. These are snapshot values, not hardcoded requirements or necessarily today's totals. They are not four parts of one total.

The roles are System administrator, Office administrator, Office user, and Viewer. Viewing a queue does not automatically mean the viewer can act on every record. All-office summaries must not imply personal assignment. Tracking completion and repository filing are different stages.

## Current layout and flows beyond the dashboard

**Tracking list:** Page title and permitted new-slip action; queue/status choices; office scope where authorized; query/filter controls; counts and pagination; a dense table of tracking numbers, subjects, offices, stage/deadline information, and Open actions. Rows have readable subject widths and named scrolling regions. Essential list information must remain available on desktop and narrow screens. Filters have explicit apply actions. Review whether tabs, chips, filters, badges, and row actions create too many levels of visual emphasis.

**Tracking creation and review:** New Tracking Slip / Edit Tracking Draft uses a step indicator, grouped Document details, Routing details, and Attachments, with a Before You Continue panel. The form supports receiving-office selection and deadline date/time choices. Review Document presents the saved draft before sending. Back to Edit returns to the same draft. Recent corrections preserve the saved deadline/time through review and editing; do not repeat the older time-loss finding as an unresolved fact without checking current source. Evaluate form column balance, instructions, required/optional distinctions, errors, and placement of Review/Send/Back actions.

**Tracking detail:** The subject is the page heading. Document information, files, routing timeline, activity log, permitted receipt confirmation, and action tabs are separate areas. Current action panels include remarks, routing, completion, and access, subject to server permissions and record state. Completion/approval/filing controls depend on authority. Do not imply every role has all actions. Assess whether the present state, deadline, custody, and next permitted action are easy to distinguish from history and metadata. Keep receipt attestation and irreversible-action consequences visible.

**Repository:** Office Folders groups only records available to the account. Within a folder, breadcrumb/location context and Filed documents/Pending filing choices remain distinct. A pending preview can appear alongside filed content. Main filters cover search, type, year, and month; More filters includes tags, origin, and retention. Filed entries appear as cards with metadata and permitted actions. Evaluate whether folder tiles, counts, pending panels, filters, cards, and tags have a coherent hierarchy. Pending filing represents Tracking records awaiting repository filing, not already-filed documents.

**Upload, extracted-details review, and repository detail:** Upload or Scan a Document leads to Review Extracted Document Details. Instructions now correctly state that the uploaded file is already saved before metadata review. Review combines extracted text and editable suggested metadata; suggestions require user review. External OCR requires consent. The save action is Save reviewed details. Detail includes document facts, files/downloads, permitted edits, extraction/retention information, and related history. Preserve user input while extraction status updates. These screens require source-based layout review because the included screenshots do not show their complete current layouts.

**Search:** Repository and Tracking are separate modes with persistent mode-appropriate input labels. Repository results use ranked cards, snippets, metadata/tags, and relevance controls; Tracking uses its own filters and table results. Switching modes does not mean their filter sets or scoring are interchangeable. Review the composition of mode selection, query, filters, result counts, result metadata, and actions.

**Reports and paper outputs:** Tracking and Repository reports have scoped metrics, charts/tables, periods, and print actions. Dashboards, report pages, memos, and routing slips serve different purposes. Keep scope, denominator, period, calculation context, and print identity/audit behavior accurate. Proposed screen styling must not accidentally alter paper layouts.

**Accounts, notifications, and administration:** Sign-in uses a branded split layout with a light form. Profile Information and Notification Preferences are separate forms with separate save handlers; the old cross-form save defect has been corrected locally. Notifications support All/Unread/type filters, grouped entries, pagination, and mark-read actions. Administration uses role-scoped lists/forms for permitted users, offices, types, tags, metadata configuration, schedules/holidays, and audit access. Review common page headings, action positions, filters, tables, field groups, errors, and empty states across these families. Do not assume the packet contains screenshots of all of them.

**Automatic logout:** Existing defaults are 15 idle minutes for administrators and 30 for other users, with a two-minute warning, Stay signed in, and Sign out now. Activity refreshes the session before the warning opens. Recent UI edits did not change that logic. Review the warning's clarity, keyboard/focus behavior, and unsaved-work explanation as part of shared UI. Preserve the configured security timeout; do not silently disable, lengthen, or replace it. Timed/multi-tab/offline behavior still requires runtime verification.

The earlier UI corrections also added field help/error associations, linked error-summary focus, enhanced office-picker descriptions, tab/panel relationships, notification current-state attributes, and more specific accessible action names. Preserve these improvements and assess their actual rendering rather than recommending them as missing features by default.

# 2. The prompt to improve the overall layout and UI

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**

Review the **overall application layout and visual style** for daily university-office use: shared shell/navigation, Dashboard, Tracking list/create/review/detail, Repository/folders/upload/review/detail, Search, Reports, Accounts, Notifications, and Administration. Give the system one coherent visual language, while keeping each task recognizable. This is a visual and interaction design review within the existing architecture, not authorization to rebuild the backend. Review every supplied screenshot, including desktop views, and use the source packets for screens with no image evidence.

## Diagnose before proposing

Identify what makes the overall application feel visually unsatisfactory or inconsistent. Evaluate the dashboard alongside document lists, detail screens, forms, and the shared shell. Consider reading order, proportions, column balance, panel nesting, repeated borders, colored surfaces, heading weight, small labels, spacing, button prominence, and chart/table density. Explain the specific problem and its effect on staff. Do not assume that more cards, bigger numbers, more whitespace, or hiding more content will fix it.

Assess whether the new Needs attention strip belongs in its current position, whether the 8/4 module layout is effective, whether the compact repository looks disconnected, and whether the queue plus its chips competes with nearby headings and summaries. These are questions, not predetermined defects.

## Compare two concrete design directions

Give two coherent alternatives:

- **A: Refine the existing application layouts.** Keep most task and layout relationships while improving alignment, proportions, typography, surfaces, and action emphasis consistently across screens.
- **B: Recompose the major page families.** Propose a consistent composition for overview, list, detail, and form/review pages using their existing capabilities. Explain where each existing item goes and how users reach it. Avoid forcing charts, lists, detail records, and forms into one identical layout.

For each direction, provide annotated desktop wireframes for Dashboard, Tracking list, Tracking detail, Repository, and a creation/review form. Include narrow-screen adaptations and explain how the same rules apply to Search, Reports, Profile, Notifications, and Administration. Label real sections, essential context, actions, and secondary information. Show the first screenful and content order below it. Desktop staff work is a primary use case; do not return only a mobile dashboard proposal. Explain tradeoffs for all-office oversight versus an office's daily work. Recommend one direction with reasons. Avoid generic SaaS mockups, decorative gradients, unnecessary icons, and invented features.

Preserve university identity, but do not assume every panel must be green or gold. Propose a restrained role for brand, selection, status, and urgency colors. Keep status meaning separate from branding. Specify a small practical type scale, spacing scale, table row treatment, panel treatment, and button hierarchy. Identify existing tokens/components to reuse and the screens a shared adjustment would affect. Do not invent contrast results from color names.

## Accessibility and precision

Treat accessibility as part of the layout. Proposals must keep essential information readable, controls identifiable, keyboard focus visible and unobscured, and selected/disabled/error states understandable beyond color. Check headings, label relationships, long subjects and office names, zoom, text spacing, responsive reflow, scrollable tables, touch targets, reduced motion, and forced colors. Preserve chart alternatives and logical reading order. State which points require NVDA/browser verification; do not claim full WCAG conformance.

Progressive disclosure must be deliberate. For each item you move or collapse, state the initial visibility, how it is discovered, and any extra step imposed. Do not hide deadlines, office scope, required attestation, important errors, or consequences of an action in Help. Do not automatically add tabs, accordions, preferences, or notifications.

Shorten wording only when meaning survives. Keep workload versus active work, completed versus filed, incoming versus outgoing, current queue counts versus monthly figures, and access versus authority explicit. If a clearer label requires different data, flag that dependency rather than silently changing the calculation.

## Preserve working contracts

Keep permissions, visibility, office scope, current-batch routing with `F("current_batch")`, data, numbering, draft privacy, receipt/custody requirements, immutable history, count/list/report agreement, existing destinations, filters, pagination, HTMX/history, no-JavaScript fallbacks, downloads, validation, extraction consent, CSRF, and POST-only state changes. Keep theme persistence and intentional paper layouts, memo identity, and print audit behavior.

Do not reintroduce dashboard New Tracking Slip, Upload to Repository, or Print dashboard controls that were intentionally removed. Do not propose unrelated backend refactoring, migrations, integrations, dependency upgrades, global CSS replacement, deletion, or permission changes. Do not weaken meaningful tests to accommodate a visual proposal.

## Required response

1. Repeat the mandatory rule verbatim and list inspected evidence and gaps.
2. Give up to eight prioritized visual/layout findings across the application, citing a filename and template/function/selector or an identified screenshot. Distinguish observation from hypothesis.
3. Present two overall design alternatives and annotated desktop/phone wireframes for the specified page families, with one recommendation. Explain desktop reading order, sidebar/topbar treatment, content width, page actions, forms, tables, cards, and responsive behavior.
4. Give a content map: current item, proposed location, initial visibility, preserved action/data, and rationale. Include meaningful zero and empty states.
5. Describe the proposed visual rules with concrete values and examples. Explain shared-component impact and how to limit it.
6. List the smallest affected files/components and acceptance checks. Cover role/scope, unchanged counts and destination filters, keyboard/NVDA, desktop/phone, zoom, long content, light/dark, HTMX/no-script, and relevant paper outputs. These are checks to run, not claimed results.
7. Keep necessary corrections separate from optional additions. Recommend a phased order: shared visual rules, representative desktop/phone layouts, then screen-family refinements. A broad review does not authorize implementing every suggestion at once. When reviewing another AI's answer, classify ideas as **Accept, Revise, Reject, or Needs verification**, deduplicate overlap, and identify conflicts. Agreement between models is not evidence.

Finish with a revised two-part prompt that keeps confirmed current-state facts separate from proposed changes. Label every proposal as unimplemented and unapproved until I choose it. Do not quietly turn another AI's suggestion into an existing feature or approved requirement.

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**
