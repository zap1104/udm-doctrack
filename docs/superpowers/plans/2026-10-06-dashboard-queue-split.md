# Dashboard queue split

Approved scope: split the dashboard's operational queues into active tracking
on the left (two thirds) and completed records awaiting filing on the right
(one third). Stack them on smaller screens. Keep the main Tracking page intact.

Mandatory rule: ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.

Constraints: retain visible_to and office_queue boundaries, current-batch routing,
receipt attestation, CSRF, HTMX navigation, existing analytics and repository
shortcut semantics. Existing unrelated working-tree edits are outside this task.
No commit, push, migration or production release.

## Task 1: Separate operational data and navigation

Write tests first for disjoint active/pending previews, matching full-list counts,
office isolation across roles, five-row preview, empty state, and legacy pending
dashboard URLs. Run them and observe missing-context failures. Then remove only
the pending-filing chip from the active panel and build a separate preview from
the existing pending-upload queue. Keep old dashboard pending links usable by
showing the normal active queue alongside the new filing panel.

Expected: new tests fail before implementation and pass afterward; existing
active queue counts and navigation stay correct. No shared queue-service change.

## Task 2: Present and verify the responsive split

Add a compact, labelled pending panel outside the receipt form. Use Open record
links so existing detail-page permissions govern filing. Keep recent activity
full width below both panels. Use existing theme tokens and responsive grid.
Adjust old layout tests to the approved split. Verify relevant dashboard,
filter-agreement and accessibility tests, template compilation, lint and JS.
Inspect desktop, small screens, dark theme, office roles and HTMX in browser;
save real desktop and phone screenshots.

Expected: two readable panels on wide screens, one column without page overflow
on phones; count and View all links agree; no duplicate IDs or filing mutation.

## Review focus

Fresh final review: office visibility, legacy links, form boundaries, HTMX
swaps/history, empty panels, responsive records-staff tables, and preservation
of pre-existing dirty files. Review only the delta for this task.
