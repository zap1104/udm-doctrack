# Site appearance review

Reviewed 1 October 2026. Changes are local and have not been pushed.

## Shared appearance

- Page headers, content margins, card padding, section headings and action rows now follow shared styles.
- Administration uses the same underline tabs as Tracking and Repository. Sidebar navigation styling is scoped to the sidebar so it cannot alter Bootstrap action tabs.
- Primary, secondary and state-coloured buttons use consistent sizing, wrapping and pressed colours. Small row actions remain compact on desktop; phone actions have a 44-pixel minimum height.
- On phones, search has a separate row in the top bar. Navigation, Help, theme, notifications and account controls no longer compete with the search field at 320 pixels.
- Multi-action forms wrap consistently, with the primary action first on phones. Tracking review facts stack vertically on small screens.
- Profile starts with its page heading. Uploads share the same file panel and checklist style as Tracking.
- Password recovery shares sign-in branding and layout. Password change and recovery fields now use the existing Bootstrap form mixin while retaining Django's original password validation.
- Repository lists and folder browsing retain their separate tabs and shared outer margins from the preceding repository update.

Shared styles use the existing light/dark theme tokens. Printable routing slips and memos retain their paper layout. Django's separate raw administration console retains Django's own interface.

## Verification

The final browser pass checked 37 page variations at 320, 390, 768 and 1440 pixels (148 page views). Visible cards, headings, fields, tabs, buttons and top-bar controls stayed inside the viewport. Wide record tables retain their own horizontal scrolling. Page headers use the same left margin within each screen size.

Covered page families:

- Dashboard; Tracking list, creation, draft review and record detail.
- Repository document list, folders, pending uploads, upload, detail, metadata edit and review.
- Both Reports pages; both Search modes; Notifications.
- Profile and password change.
- Administration overview; users and offices, including create/edit screens; document types, tags, metadata rules, metadata fields and holidays; audit log.

An additional browser pass covered password recovery request, confirmation, completion, valid and expired reset forms, lockout and 403/404/500 layouts at 320, 390 and 1440 pixels. Exceptional forms and error pages were exposed only through temporary preview routes, never application routes. Light/dark screenshots were inspected for representative list, detail, account and upload layouts. Document action tabs were clicked to confirm that selection and panel content switch together.

Checks completed:

- All 79 HTML templates passed the repository template checker.
- Ruff passed for the changed Python files.
- Django system check reported no issues.
- The page smoke check requested 244 pages across system administrator, office administrator, office user and viewer roles with no unexpected page errors.
- Focused rendering checks passed for password change/recovery, valid/invalid email verification, memo printing and routing slips. The final account form widgets were also checked after styling their Django forms.
- Whitespace checks passed.

All page checks used a disposable database containing synthetic demonstration records. Email stayed in memory. Passwords were not changed, documents were not routed or uploaded, and verification writes from the page smoke checks were rolled back.

Pytest was not run for this local appearance update, following the request to run it when pushing. These browser checks do not claim testing on physical Android/iOS devices or every browser engine.

## Evidence

Screenshots and the final per-page layout measurements are saved in the Codex visualization output folder under `site-ui`: `administration-desktop.jpg`, `administration-dark.jpg`, `upload-mobile-dark.jpg`, `account-mobile-dark.jpg`, and `layout-checks.json`.
