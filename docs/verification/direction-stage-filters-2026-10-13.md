# Direction and stage filters: 13 October 2026

Dashboard, Document Tracking, and Tracking Search now show separate Incoming and Outgoing groups. Each contains All, Pending Receipt, Received, and In Process. The groups share one template and one definition of the stages; Reports already uses the same direction/stage combinations.

Selecting a stage replaces direction and stage together. For example, Outgoing → Received uses `scope=outgoing&status=RECEIVED`. It does not use the older inbound custody scope `scope=received`. Tracking/Search retain the canonical office, search words, repeated originating-office filters, owner, deadline, sort, and page length. Changing selection clears pagination. Additional multi-stage filters remain available under More filters.

Dashboard counts are distinct current-route documents, and each child's link opens the exact set counted. Completed work and completed work awaiting filing remain outside these direction-stage groups. Current routing uses the existing current-batch predicate and permission-scoped querysets. Only a system administrator can choose another office; all-office views require choosing an office before using direction filters.

Receipt reminders and overdue work are separate. The dashboard's default reminder uses Awaiting receipt, including partially confirmed routes. The subsequent lifecycle correction in `status-lifecycle-2026-10-13.md` supersedes the earlier shared-stage behavior: incoming stages follow each recipient's own receipt/work; outgoing and all-offices document summaries remain Pending Receipt until every current recipient confirms. Saved queue URLs remain valid.

The two groups stack on mobile, preserve the existing light/dark palette and focus styling, identify direction and stage in accessible link names, and expose the selected link with `aria-current`. Dashboard accessible names also include their counts. Stable control IDs let the existing page-update code restore keyboard focus after selection. Native links continue working without JavaScript; the existing HTMX enhancement updates data in place. Applied global stage filters stay visible when an administrator changes to all offices.

## Verification

- Initial regression failed for the missing grouped-filter context before implementation: `direction-stage-red.txt`.
- Final focused backend/template regression: **427 passed, 1 skipped**, 2 existing Django deprecation warnings. The skip is a fixture with no incoming routed rows. See `direction-stage-final.txt`.
- After final focus and wording polish, **47 direction-stage/accessibility tests passed**: `direction-stage-keyboard-regression.txt`.
- All **172 JavaScript tests passed**: `direction-stage-js.txt`.
- Lint across apps, tests, and scripts passed; Django system checks and `git diff --check` passed.
- Renderer validated **39 genuine Django page states**, including dashboard partials, using temporary records/offices/users/sessions rolled back afterwards.
- Browser verification passed **12 enhanced workspace configurations** (three pages × desktop/mobile × light/dark), **4 disabled-office/Reports configurations**, and **3 JavaScript-disabled flows**. It checked all six stage links and both parent links, exact records/counts, active selection, preserved filters, disclosure state, keyboard focus, Back/Forward, and retained page state without a full reload. No console errors or horizontal overflow were found.
- Independent source review found no blocking query or permission issues. Its no-office consistency finding was corrected on Dashboard.
- A dedicated keyboard regression reproduced focus moving to the page body on all three workspaces, then passed after adding stable IDs. Tab follows both groups, Enter selects the correct records without reloading, and focus returns to the activated control. See `direction-stage-keyboard-red.txt` and `direction-stage-keyboard-green.txt`. The general browser run is recorded in `direction-stage-browser.txt`.

This is a focused regression set for the changed navigation; the entire application test suite was not rerun for this change. Browser accessibility checks cover semantic names and keyboard behavior; a manual screen-reader session was not performed.

## Screenshots

- [Dashboard, desktop/light](direction-filters/dashboard-1366-light-groups.png)
- [Dashboard, mobile/dark](direction-filters/dashboard-390-dark-groups.png)
- [Tracking, mobile/dark](direction-filters/tracking-390-dark-groups.png)
- [Search, mobile/light](direction-filters/search-390-light-groups.png)

All pre-existing workspace changes were preserved. No commit or push was made for this request.
