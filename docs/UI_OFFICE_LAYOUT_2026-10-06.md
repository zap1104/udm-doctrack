# DocTrack office layout refinement

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**

Implemented locally on 6 October 2026. No commit, push, merge or deployment was performed. Existing local corrections were retained.

## Result

The workspace now gives current work a clearer place on the page. Urgent shortcuts are compact, the dashboard queue spans the available width, and overview figures and historical trends follow it. The university green navigation and meaningful status colors remain, with quieter white panels and a neutral canvas in light mode. Dark mode keeps the existing semantic color roles.

The new screen-only styling is in `static/css/workspace.css`, loaded after the existing stylesheet by `templates/base.html`. It sets consistent spacing and headings, readable form controls, visible control boundaries, bounded forms and responsive layouts. Body text remains at least 15px; phone form controls use 16px text and a minimum height of 44px.

## Necessary changes

| Area | Change | Behavior retained |
| --- | --- | --- |
| Dashboard | Current queue and recent activity first; supporting summaries and full-width trends below; compact urgency links and phone header controls. | Counts, office scope, queue links, analytics periods, chart/table alternatives and memo generation. |
| Tracking list | Secondary filter groups sit inside a native “Filter records” disclosure. Applied filters open it and display “Filters applied”; office scope stays outside. | Query parameters, multi-select stages, ordering, ownership, pagination and explicit office Apply. Both deadline filter values remain visible when applied. |
| Tracking detail | Existing permitted actions precede history. Document information and files remain clear context. Phones gain a conditional “Go to document actions” link. | Every permission condition, separate POST form, confirmation, custody statement, deadline, draft, routing and filing operation. Viewers receive no action shortcut or action forms. |
| Tracking create/review | Bounded forms, clearer support panels and confirmation beside the reviewed information. Receiving-office correction wording matches the actual layout. | Field names, validation, saved attachments, session/draft handling and final send behavior. |
| Repository | More consistent cards; document metadata/files before extracted text; editable review metadata before OCR supporting text. | Filed/pending distinctions, save/upload behavior, signed downloads and extraction consent. |
| Search, profile, notifications, reports and administration | Shared spacing and headings, useful panel grouping, readable filters and consistent form errors. | Search scopes, account forms, report periods, role boundaries, admin permissions and separate submissions. |

The only print-specific adjustment restores history before action help when printing the ordinary tracking detail page. The new workspace stylesheet does not load for print. Routing slip and memo paper geometry were not redesigned.

## Preservation checks

- No schema, backend query, role, authority, routing/current-batch, security or session logic was changed for this layout refinement.
- Administrator idle logout remains **15 minutes**. Staff and viewer idle logout remains **30 minutes**. The existing two-minute warning and activity keepalive remain.
- Live system administrators retain the all-office selector. Office administrators, staff and viewers remain in their authorized office view. A viewer detail page had zero action forms and no document-action shortcut.
- Existing field-error summary focus and links were verified after the page finished loading. Enter on the receiving-office error link reaches the visible office filter, and Enter on the subject error link reaches the subject input. No speculative JavaScript fix was needed.
- Browser checks used existing demo accounts and read-only record views. Empty form submissions exercised validation without creating or routing records. No upload, receipt, filing or administrative mutation was submitted.

## Verification

| Check | Final result |
| --- | --- |
| Full Django suite | **6,630 passed, 2 skipped**, 524.53 seconds. |
| JavaScript regression tests | **135 passed, 0 failed**. |
| Template validation | **86 templates**, no problems. |
| Ruff | All checks passed. |
| Git whitespace check | Passed; existing line-ending conversion warnings only. |
| Browser inspection | Desktop and narrow-screen checks across the principal screen families and all four roles, including light/dark examples. |

The two skips are the existing MED-admin fixture without incoming rows and the background-enabled CI case. The full suite reports two existing Django 6 deprecation warnings for `CheckConstraint.check`.

Seven new filter-disclosure cases first failed and then passed. They cover default closure, each applied filter opening the disclosure, the office scope outside it, and the selected stage state. Existing dashboard layout assertions were updated for the new composition without removing count/data assertions. One existing receiving-office validation wording assertion now matches the relocated review editor.

The [browser evidence](verification/office-layout/browser-checks.json) contains 40 rendered-page samples and 9,089 text checks across visits. The sampler found no sampled foreground/background contrast concern and no page-wide horizontal overflow. Narrow-screen checks included 320px and 390px layouts; table regions retain their own intentional scrolling. These counts include repeated visits and are not a count of unique elements or a complete accessibility audit.

The review used W3C guidance for [text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), [reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html) and [target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html). Text sampling, template tests and narrow viewports do not establish WCAG conformance. Manual NVDA/screen-reader checks, actual 400% browser zoom, forced-colors mode and printed output inspection remain unverified. The pre-existing Bootstrap action tabs and review routing-editor collapse also retain their existing JavaScript dependency.

## Screenshots

The [screenshot folder](verification/office-layout/screenshots/) contains 27 real browser captures from this verification session. Default desktop captures are 1910px wide; phone examples are 390px or 320px wide. They show the local demo data rather than invented mockups.

| Screenshots | Coverage |
| --- | --- |
| [01](verification/office-layout/screenshots/01-dashboard-desktop-light.jpg), [12](verification/office-layout/screenshots/12-dashboard-phone-light.jpg) | Final system-administrator dashboard, desktop and phone, light mode. |
| [10](verification/office-layout/screenshots/10-dashboard-desktop-dark.jpg), [11](verification/office-layout/screenshots/11-dashboard-phone-dark.jpg) | Desktop and phone dark-mode dashboard. |
| [02](verification/office-layout/screenshots/02-tracking-list-desktop-light.jpg), [13](verification/office-layout/screenshots/13-tracking-list-phone-light.jpg), [14](verification/office-layout/screenshots/14-tracking-filters-phone-applied.jpg) | Tracking list and expanded applied filter on phone. |
| [03](verification/office-layout/screenshots/03-tracking-detail-desktop-light.jpg), [15](verification/office-layout/screenshots/15-tracking-detail-phone-320.jpg), [27](verification/office-layout/screenshots/27-permitted-actions-phone-320.jpg) | Final tracking detail on desktop and at 320px, plus the settled phone action shortcut destination. Keyboard focus reaches the action container below the sticky top bar. |
| [04](verification/office-layout/screenshots/04-repository-desktop-light.jpg), [16](verification/office-layout/screenshots/16-repository-phone-320.jpg) | Repository on desktop and at 320px. |
| [05](verification/office-layout/screenshots/05-search-desktop-light.jpg), [06](verification/office-layout/screenshots/06-profile-desktop-light.jpg), [07](verification/office-layout/screenshots/07-notifications-desktop-light.jpg) | Search results, profile and notifications. |
| [08](verification/office-layout/screenshots/08-administration-desktop-light.jpg), [09](verification/office-layout/screenshots/09-reports-desktop-light.jpg) | Administration and tracking reports. |
| [17](verification/office-layout/screenshots/17-tracking-form-phone-errors.jpg), [26](verification/office-layout/screenshots/26-error-link-office-focus-phone.jpg) | Phone validation errors and keyboard focus on the receiving-office control. |
| [18](verification/office-layout/screenshots/18-office-admin-dashboard-phone.jpg), [19](verification/office-layout/screenshots/19-office-admin-actions-phone.jpg), [20](verification/office-layout/screenshots/20-office-admin-actions-desktop.jpg) | Office-administrator dashboard and permitted actions. Capture 19 was taken during the shortcut's smooth scroll; capture 20 shows the settled action layout. |
| [21](verification/office-layout/screenshots/21-staff-dashboard-phone.jpg), [22](verification/office-layout/screenshots/22-staff-form-desktop.jpg) | Staff dashboard and new-tracking form. |
| [23](verification/office-layout/screenshots/23-viewer-dashboard-phone.jpg), [24](verification/office-layout/screenshots/24-viewer-detail-phone.jpg), [25](verification/office-layout/screenshots/25-viewer-detail-desktop.jpg) | Read-only viewer dashboard and detail on phone and desktop. |

## Recommendations for the next review

1. Have an office staff member complete one normal receive/action/file task and identify wording or placement that still slows them down.
2. Perform an NVDA and keyboard-only pass through the office picker, dashboard queues, error links, action tabs and idle warning before claiming full accessibility coverage.
3. Review actual print/PDF output, 400% browser zoom and forced colors. Make a correction only when a reproducible issue is found.
4. Ask Claude/Gemini to critique these actual captures and source sections. Require filename/section evidence, separate confirmed problems from browser concerns, and retain the mandatory preservation rule above.

The temporary baseline copy used at the start was unavailable in later tool sessions. Final review therefore used current source, Git diffs, worker reviews, live browser checks and the full regression suite. It does not claim an exact comparison to that temporary copy.
