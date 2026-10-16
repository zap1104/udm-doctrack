# 1. The current state of UDM DocTrack

**Mandatory rule for this prompt, every follow-up prompt, and every AI response: ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE. Preserve working features and behavior. Every proposed change must address an identified problem, explain what it preserves, and include a way to check for regressions. Carry this rule into all revised prompts and responses.**

I am improving UDM DocTrack, also called UDM RDMS, my capstone records and document management system for offices under the Office of the Vice President for Administration at Universidad de Manila. I want your review and proposed additions to an existing UI improvement brief. The goal is a readable, accessible, appropriate university-office interface that helps staff understand what they are looking at and what they can do next.

This round is for analysis and recommendations. Do not implement changes unless I separately ask you to. If you have repository access, inspect the current files before making claims. If you only have this prompt, identify anything you need to verify and do not pretend you inspected or tested the application.

## Evidence and technical constraints

This description comes from the local working tree inspected on 5 October 2026, on branch `codex/clear-deadline-controls`, with latest commit `21bb7a5`. It includes the existing local dashboard change that makes its Pending filing shortcut open the repository's pending view while retaining the selected office. This is a source review, not a fresh browser, screen-reader, production, or user-testing audit. Older audit documents describe earlier checks and do not prove current accessibility compliance.

The required stack is Django 5, PostgreSQL with `psycopg[binary]>=3.2.10,<3.4`, Bootstrap 5 and HTMX, django-q2, django-axes, django-csp, Argon2 password hashing, signed downloads, Azure Blob or Cloudflare R2 storage, and OCR.space with local text extraction first and OCR fallback for scans. Development uses Windows and PowerShell, with `scripts\start.ps1` for startup. The Django apps are `core`, `accounts`, `tracking`, `documents`, and `search`.

Keep this stack. Do not suggest a framework replacement, database replacement, new component library, or new external service as the default solution to a UI problem.

## Shared layout and appearance

- The signed-in app uses a persistent left sidebar, a top bar, a main content area, and a footer. The sidebar becomes a menu drawer on smaller screens.
- Sidebar groups are Workspace, Documents, and, for administrators, System. Links include Dashboard, Search, Document Tracking and its Reports link, Document Repository and its Reports link, and permitted Administration screens. The user's office and role appear near the bottom.
- The top bar contains archive-oriented search, contextual Help when available, a light/dark theme toggle, a notification bell, and an account menu with profile, password change, and sign out. Django Administration is restricted to superusers.
- The palette is institutional green, gold, and khaki. The UI uses bordered panels, colored status pills, office badges, filter pills, tables, and document cards. The actual shared font stack uses Aptos/Segoe UI/system fonts. Body text is 15px; several table headings, badges, captions, and supporting labels are smaller, often around 10–13px. These sizes need readability evaluation; their presence alone does not prove a failure.
- The authenticated app has light/dark semantic color tokens. Sign-in and paper-oriented memos/routing slips intentionally retain light surfaces. Theme choice persists and is applied before the page paints.
- Existing accessibility support includes a skip link, main landmark, focus styles, many labeled controls, required-field announcements, textual status labels, native disclosure sections, reduced-motion styling, and some live status announcements. These are foundations to preserve.
- Extended help is collected into a right-side Page guide. JavaScript moves `details.chart-note` and elements marked `data-help-title` into that panel and adds contextual help buttons. Without this enhancement, the original inline help remains. This behavior matters when assessing what is actually visible after page load.
- Wide data tables have their own horizontal scrolling area. Several use minimum widths around 660–720px. This is an existing table pattern that needs browser assessment, rather than automatic replacement.

## Roles and information boundaries

The current roles are System administrator (all offices), Office administrator, Office user, and Viewer (read-only). Do not assume an older Secretary role is still a separate current role.

System administrators can work across offices. Office administrators manage their own office. Office users perform permitted document work within their office. Viewers have read-only access to permitted records. Actual authority also depends on office, record state, custody, sharing, and server-side checks. A hidden button is not a security boundary.

The dashboard's selectable office scope belongs to system administrators; other users see their own-office scope. Counts and lists must continue to respect document visibility. Never broaden access to make a layout easier to build.

## Dashboard

The dashboard begins with a greeting and office identity. Its header offers Open Reports and Generate memo, plus an office selector for system administrators. New Tracking Slip, Upload to Repository, and Print dashboard buttons were intentionally removed from this page. Starting work is reached from the relevant module. Do not reintroduce those controls without a specific, justified request.

On large screens, Document Tracking occupies the wider left column and Document Repository the narrower right column. They stack with Tracking first on smaller screens.

The Tracking side contains:

1. Four metric cards: Tracking workload, Incoming, Outgoing, and Overdue. They link to corresponding filtered tracking lists. Incoming and Outgoing are disabled under All offices because direction requires an office's point of view. These counts overlap and must not be added together.
2. A Tracking donut panel with Status/Overdue views. It shows either an all-offices ring or separate Incoming/Outgoing rings. Linked legends provide counts and percentages; pending filing is represented separately in the status view.
3. Tracking queues with Incoming, Pending Receipt, Received, In Process, Pending filing, Overdue, and Outgoing options. Pending Receipt is the default. A small set of rows is shown with a link to the full list. Eligible rows support receipt confirmation and bulk receipt with a custody attestation.
4. Latest tracking activity, showing a small set of recently updated records.
5. Cumulative Created/Endorsements/Completed activity, with a chart and an available data table.
6. Turnaround controls and figures: month and stage choices, daily chart and table, stage statistics, on-time summary, and waiting information when applicable.

The Repository side contains a Pending filing shortcut, a repository-status donut, and the current calendar month's additions by owning office, with counts and shares.

Important distinctions: queues describe current work; the month selector changes turnaround figures, while repository additions remain about the current month. Endorsements count routing steps, while Created/Completed count documents. Do not subtract these cumulative series to invent a backlog. Receipt time, processing time, total lifetime, and waiting information have different meanings. A missing observation is not a recorded zero.

The memo action opens a modal with scope, timestamp, generated-by identity, selected turnaround month, and generated sections. Its separate print page has audit behavior. An ordinary browser print of the dashboard is not equivalent to this official memo.

An important current source finding: the entire dashboard overview is marked `data-help-title`, so the help enhancement moves its scope, updated time, permission note, period explanation, and glossary into the Page guide. These essential facts are therefore removed from the main dashboard when that enhancement runs.

## Document Tracking

Tracking has Tracking and Reports subtabs. Its main navigation offers All in Tracking, Incoming, and Outgoing. Its filters distinguish Viewing as, Originating office, Stage, Deadline, Sort, and Show. Show distinguishes All I can see, Office files, and Files created by me only. Several filters allow multiple selections and use immediate link navigation. Direction, origin, custody, and record ownership are different concepts.

Rows show tracking number, subject with supporting type/direction/origin information, current office, receiving office, updated time, workflow status, deadline information, and Open or Confirm Receipt. Eligible records have bulk-selection controls and an optional receipt note.

Current displayed stages include Draft, Pending Receipt, Received, In Process, Completed - Pending Filing, and Completed. Completed means approved into the repository. Overdue is a separate deadline condition, not a replacement workflow stage.

Creating a tracking slip is a two-step flow: Document and Routing Details, then Review. Fields include subject, type/classification, automatically derived originating office, receiving offices, requested action, instructions, priority, optional deadline date/time, and attachments. Review persists a draft; the final tracking number is issued on first send. Review offers Back to Edit, Send Document, Keep as Draft, and a section for changing destinations/deadline. Missing remembered destinations produce a warning and editable routing fields; the deadline-date helper falls back to the persisted draft when remembered deadline data is absent.

Tracking detail combines record information/files, timeline/activity, and authorized action panels such as Add remark, Forward or return, Complete, and Share access. Earlier files/history can be expanded through disclosures. Completion can require separate administrator filing approval. Eligible users can approve filing or return an unfiled completion to tracking with a reason.

Sending a document does not mean the destination confirmed receipt. Confirmed custody changes through receipt, and the server records the timestamp. Multi-recipient routing and the current routing batch matter. History remains append-only. The printable routing slip includes institutional identity, QR, routing information, movement history, and attachments.

## Document Repository and upload

Repository navigation includes All documents, Browse folders, and Reports. Users browse office folders and a location breadcrumb. Filed documents and Pending filing have distinct counts and views. Pending filing contains completed tracking records awaiting approval; it is not already-filed repository content.

Filed entries appear as document cards. Main filters include a text query, document type, year, and month; More filters reveals tags, origin, and retention. Applied-filter chips, clear-filter actions, empty-state guidance, and retention-review notices already exist. Retention notices request a review decision; documents are not automatically deleted.

Upload follows Choose File → Review Extracted Details. It already provides OCR language selection and explicit consent for external OCR. Suggested metadata is editable, including relevant title/type/office/date/tags and other details, with extracted text and confidence information. Do not describe these suggestions as a guaranteed correct interpretation or as a new AI integration.

Document detail supports download and permitted metadata editing, additional files, re-extraction, retention information, OCR status/text/privacy, origin, and suggestion history. OCR progress can update without replacing the whole review form.

Current copy says the user reviews everything before saving. The ingestion service actually creates the document/file before metadata review. This needs truthful wording; do not invent an unpublished-draft or extra approval workflow for direct uploads to make the existing sentence true.

## Search, reports, accounts, and administration

- Search has separate Document Repository and Document Tracking modes. Repository search uses metadata/extracted text, ranked cards, snippets, tags, match information, and relevance controls. Tracking search uses active-record filters and table results without repository relevance scores. Switching mode starts a fresh search because the filters differ. The top bar currently describes archive search.
- Tracking and Repository have separate report pages. They include scoped metrics, charts with data tables, relevant breakdowns, and Print Report. Tracking reports include turnaround and overdue accountability. Repository reports include additions, document types, retention, and upkeep information. Office scope, periods, and denominators must remain accurate in screen and print versions.
- Authentication uses a branded split layout with a light sign-in form, username/password, validation, lockout guidance/countdown, and conditional password recovery. There is no public registration. Password change, recovery, verified-email handling, and session-expiry warning/keepalive behavior already exist.
- Profile shows separate Profile Information and Notification Preferences forms, email-verification information, and read-only account details.
- Notifications have All/Unread choices, a type filter, grouped cards, pagination, mark-as-read actions, and Mark all as read. Read state belongs to each user. The bell polls approximately every 60 seconds, with hidden-page polling suppressed. Marking one row read updates that row; surrounding counts/filter results can remain unchanged until another refresh.
- Administration has role-scoped users and audit access. Global offices, document types, tags, metadata rules/fields, working schedule, and holidays belong to the appropriate system-administration authority. Lists use filters, tables, pagination, and edit/state actions.

## Specific findings to verify before fixing

These findings come from source inspection. Runtime consequences and usability severity must be reproduced where applicable.

1. Essential dashboard context moves into Help. Related wording still says Incoming is addressed to “your office” when a system administrator may have selected another office, and an overlap explanation still refers to “Total” instead of Tracking workload.
2. Tracking creation saves the full deadline datetime, but the review handoff remembers only the date and initializes no time. Sending recomputes the deadline, and a blank time defaults to end of day. An explicitly chosen time can therefore be lost unless re-entered. Review/detail summaries also omit the time that the list displays.
3. Profile has two separate POST forms, but its handler binds and saves both on every submission. Saving one can apply missing values to the other, including notification settings or optional personal details. Reproduce safely and correct only the submitted form.
4. The custom shared field template renders help/error blocks without the IDs that installed Django expects in generated `aria-describedby` references. Check the rendered DOM and provide matching associations.
5. The main search inputs have blank labels and are rendered directly with placeholders. They need clear accessible names. Some manually rendered metadata fields also lack their inline error output.
6. Tracking action tabs lack some explicit tab-to-panel attributes in source. Bootstrap supplies some behavior at runtime; verify the completed accessibility tree and keyboard interaction before prescribing a correction.
7. Some compact dashboard tables omit column headers. Notification filters indicate selection visually without equivalent current-state attributes. Repeated administration Edit actions need assessment for meaningful accessible names.
8. Back to Edit uses browser history rather than a guaranteed edit-existing-draft route. Assess state preservation and possible repeated-draft submission. Filter resets, auto-submitting scope selectors, and HTMX swaps also need focused keyboard/state checks.

Useful starting points: `templates/base.html`, `templates/partials/_sidebar.html`, `_topbar.html`, `_field.html`, `templates/core/dashboard.html`, `_action_centre_queue.html`, `templates/tracking/{list,create,review,detail}.html`, `templates/documents/{repository,upload,review,detail}.html`, `templates/search/search.html`, `templates/accounts/profile.html`, `templates/reports/reports.html`, `static/css/doctrack.css`, `static/js/doctrack.js`, `apps/tracking/{forms,views}.py`, `apps/accounts/{forms,views}.py`, and `apps/documents/services.py`. Inspect the repository's `AGENTS.md` and relevant existing tests as well.

# 2. The prompt to improve and fix what is needed

**Mandatory rule: ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE. Keep working behavior, permissions, data, and accessibility intact. Prefer the smallest complete correction that solves the actual problem. Repeat this rule in your response and preserve it in every revised prompt.**

Act as a careful UX reviewer, accessibility reviewer, and Django/Bootstrap maintainer. Review the current state above and propose improvements appropriate for daily university-office use. Think about staff with different technical experience, older users, keyboard users, screen-reader users, low vision, color-vision differences, motor limitations, and users who need extra time or simpler instructions.

Make the interface easier to understand without surrounding every task with more cards, warnings, explanations, and controls. Accessibility should be built into ordinary interaction, with additional preferences proposed only when they address a real need.

## A. Establish a clear information hierarchy

For each major screen, explain its main purpose and what the user should see first. Identify which information must stay visible, which can be expanded, and which belongs on another existing screen.

Keep page identity, relevant office scope, current status, deadlines, and the immediate next permitted action easy to find. Keep essential safety/privacy instructions and validation errors beside the task. Use short inline context for these facts; place extended definitions and calculations in accessible help or disclosures.

For the dashboard, assess putting actionable work ahead of detailed analytics, especially on smaller screens. Preserve existing figures and report access. Do not simply delete charts, restore intentionally removed shortcuts, add arbitrary new metrics, or change what the counts mean. Recommend reordering or disclosure only when you can explain the user problem and preserve discoverability.

Favor one clearly emphasized action per task section. Secondary actions can remain visible with quieter styling or use an accessible menu when that improves clarity. Keep high-impact actions clearly distinguishable. Opening a page, sending a document, confirming receipt, completing work, and approving filing must look and read like different operations.

## B. Make wording accurate and consistent

Propose exact replacement wording for unclear labels and messages. Use ordinary office language, short sentences, and clear action verbs. Explain unavoidable abbreviations once. MED must remain Maintenance and Engineering Division.

Preserve distinctions between originating office, current office, receiving office, viewing scope, document owner, and acting user. Keep Draft, sending, receipt, processing, completion awaiting filing, and repository filing distinct. State overdue separately. Do not rename backend states or alter lifecycle rules to simplify a label.

Make report scope and date periods explicit. Keep metric explanations consistent with the visible label and destination list. Show actual dates/times when they affect decisions, with an understandable local-time convention. Preserve the existing blank-time deadline behavior unless separately authorized; never call end of day “office closing time” unless that matches the implemented calculation.

Correct the upload saving claim and remove the tracking helper's backend-conversion wording. Match configurable login/password guidance to actual settings. Do not promise unsaved-draft recovery, real-time counts, automatic filing, or extraction accuracy that the system does not provide.

## C. Improve readability without making everything larger indiscriminately

Recommend a modest shared typography, spacing, heading, panel, and action pattern using the existing design system. Evaluate roughly 16px main reading text and readable secondary text as design starting points, not WCAG requirements. Reduce unnecessary tiny uppercase labels and excessive badge density when they hinder scanning.

Preserve useful institutional identity and color roles. Evaluate text contrast in both themes: generally at least 4.5:1 for ordinary text and 3:1 for qualifying large text. Test necessary control boundaries and meaningful graphics against applicable 3:1 non-text contrast requirements. Include selected, hover, focus, error, placeholder, and status states. [W3C text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), [W3C non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html).

Do not rely on color alone for status, urgency, selection, or charts. Keep useful text/shape alternatives. Avoid decorative animation, unnecessarily loud success messages, and repeated explanation panels. Keep inline links recognizable.

## D. Make accessibility requirements testable

Use WCAG 2.2 Level AA as the target. Identify applicable criteria and actual evidence for each finding. Automated scans or attractive screenshots alone do not establish compliance. [WCAG 2.2](https://www.w3.org/TR/WCAG22/).

- **Keyboard and focus:** Make all ordinary tasks operable without a mouse. Maintain a sensible focus order and clearly visible focus that sticky content does not obscure. Check the mobile drawer, account menu, disclosures, receipt controls, help panel, memo modal, and calculation dialog. Return focus appropriately when overlays close. Do not trap focus except within a correctly managed modal interaction. [W3C focus visibility and obstruction guidance](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html).
- **Names and structure:** Use semantic headings, landmarks, meaningful links, associated labels, and table headings. Give repeated row actions enough record context. Wire actual tabs to their panels and test their keyboard behavior; keep ordinary navigation links as links. Prefer native controls and correct Bootstrap patterns. [W3C page structure](https://www.w3.org/WAI/tutorials/page-structure/), [W3C tabs pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/).
- **Forms and errors:** Repair label/help/error associations and expose custom-field errors. Preserve valid entered values after failure. Where needed, provide a concise error summary linking to fields and manage focus. Explain what failed and how to recover, including upload, permission, network, and expired-session cases. Avoid announcing the same message repeatedly. [W3C form notifications](https://www.w3.org/WAI/tutorials/forms/notifications/).
- **Dynamic updates:** Assess HTMX focus after queue/notification replacement, result counts, selected filters, pending extraction, and failed requests. Use proportionate status announcements and meaningful busy states. Background polling should not repeatedly interrupt reading or silently discard input. Verify that a mark-as-read action leaves counts and the Unread view understandable. [W3C status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html).
- **Zoom and reflow:** Test 200% text enlargement and reflow at an effective 320 CSS-pixel viewport, including a 1280px viewport at 400% zoom. General page content should remain usable without page-wide horizontal scrolling. Where a genuine data table needs two-dimensional layout, preserve a discoverable, keyboard-usable local scrolling area. Do not hide essential columns or actions. [W3C reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html).
- **Text adaptation:** Ensure content survives user overrides of 1.5 line height, 2em paragraph spacing, .12em letter spacing, and .16em word spacing without losing content or actions. These are override-test values, not mandatory default styling. [W3C text spacing](https://www.w3.org/WAI/WCAG22/Understanding/text-spacing.html).
- **Targets and motor access:** Check applicable 24×24 CSS-pixel minimum target-size or spacing requirements and exceptions. Prefer comfortably larger important touch controls, around 44px where appropriate, as a design recommendation. Check small help icons, chip removals, page controls, row actions, and chart targets. Provide alternatives to drag-only or hover-only operations. [W3C target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html).
- **Authentication and time:** Preserve password-manager support and paste, avoid adding cognitive puzzles, and make login/recovery errors understandable. Assess session warnings and safe extension/recovery with the existing security model. Do not weaken lockout, verification, or timeout enforcement. [W3C accessible authentication](https://www.w3.org/WAI/WCAG22/Understanding/accessible-authentication-minimum.html).
- **User preferences and fallback:** Preserve reduced motion, browser zoom, light/dark choice, and HTML fallbacks. Test Windows high-contrast/forced-color behavior. Treat adjustable text/density or an extra contrast preference as optional proposals requiring evidence, rather than an accessibility overlay or a large settings panel.
- **Charts and print:** Preserve usable chart tables/legends, zero-versus-missing distinctions, and keyboard/touch access. Check printable reports, routing slips, and memos for legible text, headings, page breaks, correct scope, and audit behavior.

Assess auto-submitting selectors deliberately. An always-visible Apply action can be preferable when changing a choice unexpectedly navigates, interrupts keyboard selection, or loses focus. Do not remove automatic behavior across the application without demonstrating the problem and preserving filters, bookmarks, and fallbacks.

## E. Address functional mismatches narrowly

Prioritize safe reproduction of deadline-time loss and the profile form save mismatch. These are small correctness issues exposed through the UI, not permission to refactor the backend.

For deadlines, preserve the selected date/time through creation, the existing saved-draft review flow, sending, and display. Keep date-only/end-of-day behavior, existing validation, and the existing fallback to persisted draft values when remembered session data is absent. This does not request a new unsaved-form recovery feature.

For profile, ensure Save Changes edits the intended personal fields and Save Preferences edits notification preferences, with unrelated values retained in both cases. Preserve email-verification/recovery behavior.

Review Back to Edit and filter/reset/HTMX state transitions for data loss or unintended repeated submissions. Propose a change only after identifying the actual behavior. Do not replace an entire form during extraction polling and erase user edits.

## F. Preserve these boundaries in every proposal

Preserve server-side permissions and office scope; current-batch routing with `F("current_batch")`; custody attestation; server-recorded receipt times; multi-recipient routing; draft privacy and numbering; immutable history; completion-versus-filing authority; filtered count/list/report agreement; signed/authenticated downloads; upload validation; OCR consent and local-first extraction; retention review without automatic deletion; search mode and relevance semantics; safe pagination/filter state; print identity/audit behavior; existing theme tokens; CSP-compatible external JavaScript; HTMX restrictions; CSRF and POST-only state changes.

Do not introduce unrelated migrations, new integrations, permission changes, broader OCR transmission, workflow automation, dependency upgrades, or global CSS rewrites. Keep Django's UserManager inheritance, tuple unpacking for `Tag.get_or_create_by_name()`, and anchored gitignore patterns intact.

If a necessary fix touches an existing service, form, or view, explain the exact dependency and limit the change to that correction. A shared component fix can be appropriate, but verify every affected screen. Never weaken a meaningful test to make a cosmetic change pass.

Preserve pre-existing user edits. Follow the repository's branch policy, do not commit directly to main/develop, and do not push, merge, or deploy from this review request.

## G. Return a focused, actionable review

Your response must:

1. Repeat the mandatory preservation rule verbatim.
2. State what evidence you inspected and what remains unverified. Distinguish source-confirmed findings, reproduced defects, usability hypotheses, and optional ideas.
3. List the five highest-priority changes. For each, give the affected screen/user, current problem, evidence, exact proposed correction or replacement wording, what it preserves, risk, and a concrete acceptance check. Use practical urgency labels such as Task/data risk, Accessibility blocker, Clarity improvement, and Optional.
4. Describe a compact proposed hierarchy for the dashboard, tracking detail, repository, and review forms. Show what remains visible, what is disclosed, and the next action. Include short text wireframes if useful; do not design an entirely new application.
5. Suggest additional improvements only when they solve an unmet need. Keep these in a short deduplicated backlog, explain their tradeoffs, and identify which existing features should be retained.
6. Give a phased implementation order, minimal affected files/components, and acceptance checks. Start with correctness and essential context, then shared accessibility defects, then visual refinements supported by evidence.
7. If reviewing another AI's response, classify each proposed addition as Accept, Revise, Reject, or Needs verification, with reasons. Do not accept an idea merely because another model proposed it. Produce a consolidated revised prompt that retains these two parts and the mandatory rule, and does not silently treat proposals as implemented facts.

Verification must cover the affected tasks for all four roles, own-office/selected-office/all-offices views where permitted, empty and populated states, long names/subjects/file names, pending/received/completed/pending-filing records, exact-time/date-only/no-deadline cases, light/dark themes, common desktop/phone widths, keyboard navigation, zoom/text-spacing/forced-color modes, relevant HTMX/no-JavaScript paths, and printing. Include screen-reader testing on a Windows office workstation, preferably with NVDA, and focused existing regression tests appropriate to the change. Separate automated evidence from manual checks still needed. Do not claim tests passed unless they were run.

Judge success by whether an office user can identify the page and scope, find a permitted record, understand its current stage and deadline, perform the next permitted action, and recognize the result without unrelated information competing for attention.

**Final standing rule: ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE. Carry it into every response, revised prompt, and future implementation.**
