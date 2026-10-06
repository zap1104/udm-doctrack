# Office layout implementation plan

> **For agentic workers:** Use Superpowers subagent-driven-development for independent template tasks, with integration and verification by the primary agent. Steps use checkbox syntax for tracking.

**Goal:** Make existing DocTrack screens calmer, clearer and more usable for all four roles on desktop and phone.

**Architecture:** Retain Django templates, Bootstrap, HTMX and existing services. Add a small screen-only shared styling layer and reposition existing content within each page family. Preserve queries, role checks, forms, endpoints, IDs, filters and accessibility behavior.

**Tech stack:** Django 5, PostgreSQL, Bootstrap 5, HTMX; Windows/PowerShell.

**Spec:** `docs/CONSOLIDATED_LAYOUT_BRIEF_2026-10-06.md`, with the user's subsequent explicit instruction to apply the source-checked insights. The current authorization permits implementation; prior proposal documents remain historical analysis.

## Global constraints

- ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.
- Preserve all pre-existing local edits on `codex/clear-deadline-controls`; work in the current checkout so the user's latest uncommitted fixes are included.
- Do not change database schema, backend queries, roles/permissions, scope, data, routing/current-batch rules, security, session durations, keepalive, downloads or extraction consent.
- Preserve separate POST forms, required attestations, explicit filter apply, query parameters, multi-select state, back/history, no-script fallbacks and existing accessible IDs/descriptions.
- Preserve at least 15px body text, existing semantic theme colors, focus and reduced-motion behavior. New presentation styling is screen-only; paper geometry and print audit remain unchanged.
- Avoid new modes, new filters, component libraries, external fonts and broad deletion of CSS. No commits, push, merge or deployment are requested.

## Review focus

- All-office oversight and selected-office links must retain distinct count semantics and authority.
- Long subjects, office names and file names must remain readable without page overflow at narrow widths and zoom.
- Read-only users must have a coherent layout without action controls being promoted outside their existing conditions.
- Repositioned forms and disclosures must keep IDs, keyboard reading order, errors and no-script/HTMX paths.
- Screen-only styling must leave dark/light tokens, forced colors, login light surfaces and paper outputs intact.

### Task 1: Shared visual foundation and list density (primary agent)

**Files:** `templates/base.html`, new `static/css/workspace.css`; existing Tracking list markup only if a class hook is needed.
**Interface:** Existing components keep all functional names and IDs. New CSS scopes to screen presentation and page-family class hooks; workers do not edit shared CSS.

- [x] Read effective current styling and baseline browser state; save touched-file baseline outside Git without including secrets. The temporary copy later became unavailable; final source/diff review and regression evidence are documented in the report.
- [x] Add a screen-only stylesheet after `doctrack.css` with restrained panel hierarchy, consistent spacing/type, readable table/form controls and responsive layouts.
- [x] Compact existing Tracking filter groups through layout rather than replacing query semantics or hiding active selections.
- [x] Inspect desktop/phone, light/dark, narrow-screen reflow and long content. Use existing contrast and theme regression checks. Actual 400% browser zoom remains a disclosed manual follow-up.

### Task 2: Dashboard composition (template worker)

**Files:** `templates/core/dashboard.html`, applicable existing dashboard layout tests.
**Interface:** Preserve `action-centre-queue`, all chart attributes, counts, selected office, module links, memo and chart/table alternatives. Root owns shared styles.

- [x] Recompose the existing queue into a full-width operational section near context, with compact urgent shortcuts retaining their distinct destinations.
- [x] Put overview/chart modules below the queue in content-appropriate rows, keeping Tracking and Repository titles and links explicit.
- [x] Preserve all current disclosures, analytics filters, chart data and paper behavior; do not force equal heights or auto-hide analytics.
- [x] Review template validity and any meaningful layout-contract tests; report exact changed assertions.

### Task 3: Detail and form composition (template worker)

**Files:** `templates/tracking/detail.html`, `create.html`, `review.html`.
**Interface:** Preserve current draft/deadline fixes, IDs, separate forms and every action condition. Root owns shared styles.

- [x] Move conditional receipt/active/completed action areas above history while keeping clear current-state/document context and existing form boundaries.
- [x] Give form/review pages consistent class hooks and a bounded, balanced input layout with critical help nearby.
- [x] Preserve existing history splits, saved attachments, draft actions and errors. No backend changes.
- [x] Check template structure and existing action/draft/accessibility tests through the primary agent's integrated run.

### Task 4: Repository, Search and supporting screens (template worker)

**Files:** Relevant existing `templates/documents`, `templates/search`, `templates/accounts/profile.html`, `templates/core/notifications.html`, `templates/administration`, `templates/reports` templates only.
**Interface:** Shared page-family hooks keep all existing controls, field names, routes, scope, conditions and print classes. Root owns shared styles.

- [x] Improve panel grouping and heading/actions across existing page families; retain filed/pending distinctions and upload save truthfulness.
- [x] Align form/search/report/account/admin composition, preserving visible errors and meaningful empty states.
- [x] Add only useful presentation hooks and concise context wording; avoid arbitrary changes where shared styling already suffices.
- [x] Report affected screens and dependencies; primary agent runs integrated regression tests.

### Task 5: Integration, independent review and final proof (primary agent + reviewer)

- [x] Inspect every worker diff against baseline and constraints; address reviewer findings before completion. Use Git/source review where the temporary baseline was unavailable.
- [x] Run template validation, Python/style checks, relevant JavaScript checks and existing Django suites for affected roles/screens/filters/security/session behavior.
- [x] Run broader suite after integration when justified by shared CSS and templates; diagnose actual failures rather than weaken assertions. Final result: 6,630 passed, 2 existing skips.
- [x] Verify live representative desktop/phone screens and roles; save screenshots with exact page/scope/theme and evidence limits.
- [x] Write change/verification report; deliver real screenshot proof and disclose manual screen-reader gaps. Leave all changes local.

## Decisions

- The user's latest go-ahead authorizes applying the scoped insights; no additional design-approval pause is needed.
- Reuse the current feature checkout because a new worktree would omit the user's uncommitted corrections and provide a stale baseline.
- Existing tests are the regression foundation. Do not add tests that simply mirror CSS; add a failing test only for new meaningful behavior/contracts.
- Browser evidence justified a native secondary-filter disclosure that opens for every applied filter, and a conditional phone link to permitted record actions. Neither introduces new permissions or query semantics.
- The suspected error-link focus issue passed fresh keyboard and browser checks after loading; no speculative JavaScript change was made.
- Full evidence and remaining manual checks are in `docs/UI_OFFICE_LAYOUT_2026-10-06.md`.
