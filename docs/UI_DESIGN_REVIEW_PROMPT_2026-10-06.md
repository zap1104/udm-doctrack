# 1. The current state

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE. Repeat this rule in every response, revised prompt, and proposed implementation.**

I am improving UDM DocTrack, also called UDM RDMS, my university capstone records and document management system for offices under OVPA at Universidad de Manila. Staff need to find permitted records, understand their stage and deadline, and perform the next permitted action without competing information overwhelming the screen.

My latest feedback is specifically about **the overall layout and visual style**. I disliked the latest dashboard revision. Do not assume that revision is the right design simply because it was implemented or passed functional checks. Review it critically. Preserving working behavior does not mean preserving every current visual decision.

This is an **analysis and design round only**. Do not change application code. I will compare proposals from Claude and Gemini before choosing a design.

## Evidence and boundaries

The attached source packets are snapshots of my local working files on **6 October 2026**, including uncommitted changes. Each section names its original repository path. They are selected files, not the full application. Read relevant source before making implementation-specific claims. Request an exact missing file when a finding depends on it. Do not invent code, dependencies, screenshots, user research, or test results.

The included screenshots were captured during the dashboard pass on **5 October 2026**. They show phone layouts and a repository excerpt, not the complete desktop screen or every role. Use source for current structure and screenshot pixels for the captured appearance. Distinguish source-confirmed findings, screenshot observations, hypotheses, and checks requiring a browser or screen reader.

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

# 2. The prompt to improve the UI

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**

Review the **dashboard and shared shell**, focusing on overall composition and visual style for daily university-office use. Treat other screens as compatibility checks for any shared style proposal, not permission to redesign the whole system.

## Diagnose before proposing

Identify what actually makes this screen feel visually unsatisfactory. Consider reading order, proportions, column balance, panel nesting, repeated borders, colored surfaces, heading weight, small labels, spacing, button prominence, and chart/table density. Explain the specific problem and its effect on staff. Do not assume that more cards, bigger numbers, more whitespace, or hiding more content will fix it.

Assess whether the new Needs attention strip belongs in its current position, whether the 8/4 module layout is effective, whether the compact repository looks disconnected, and whether the queue plus its chips competes with nearby headings and summaries. These are questions, not predetermined defects.

## Compare two concrete design directions

Give two coherent alternatives:

- **A: Refine the current composition.** Keep most layout relationships while improving alignment, proportions, typography, surfaces, and action emphasis.
- **B: Recompose the dashboard.** Change the arrangement of the same capabilities where that clearly improves the task flow. Explain where each existing item goes and how users reach it.

For each, provide an annotated desktop wireframe and narrow-screen wireframe. Label real sections, essential context, actions, and secondary information. Include the first screenful and the order below it. Explain tradeoffs for all-office oversight versus an office's daily work. Recommend one direction with reasons. Avoid generic SaaS mockups, decorative gradients, unnecessary icons, and invented features.

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
2. Give up to five prioritized visual/layout findings, citing a filename and template/function/selector or an identified screenshot. Distinguish observation from hypothesis.
3. Present the two alternatives and annotated desktop/phone wireframes, with one recommendation.
4. Give a content map: current item, proposed location, initial visibility, preserved action/data, and rationale. Include meaningful zero and empty states.
5. Describe the proposed visual rules with concrete values and examples. Explain shared-component impact and how to limit it.
6. List the smallest affected files/components and acceptance checks. Cover role/scope, unchanged counts and destination filters, keyboard/NVDA, desktop/phone, zoom, long content, light/dark, HTMX/no-script, and relevant paper outputs. These are checks to run, not claimed results.
7. Keep necessary corrections separate from optional additions. When reviewing another AI's answer, classify ideas as **Accept, Revise, Reject, or Needs verification**, deduplicate overlap, and identify conflicts. Agreement between models is not evidence.

Finish with a revised two-part prompt that keeps confirmed current-state facts separate from proposed changes. Label every proposal as unimplemented and unapproved until I choose it. Do not quietly turn another AI's suggestion into an existing feature or approved requirement.

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**
