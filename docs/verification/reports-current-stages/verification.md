# Reports: current work and direction stages

## Scope

The Tracking Reports headline now has three cards: Total, Incoming, and Outgoing. Total excludes both Completed and Completed - Pending Filing, while retaining drafts and other current records the user is allowed to see. The separate Pending filing workflow remains available.

The "What [office] received and sent" panel and its aggregate query were removed for every role. Historical turnaround and repository reporting keep their existing datasets.

Documents by stage now has Incoming and Outgoing sections. Each has Pending Receipt, Received, and In Process counts. Its stacked bar represents that direction's full total, with segment sizes proportional to the actual counts. Colors use the existing status variables: pending receipt gold/orange, received blue, in process mauve/pink. No new statuses or colors were introduced.

This update is limited to Reports. In the System Admin's All offices scope, Total remains available; Incoming and Outgoing require choosing an office. A transfer is incoming to one office and outgoing from another, so inventing a disjoint university-wide direction split would misstate the records.

## Accuracy and accessibility

- Counts begin with `TrackingRecord.objects.visible_to(user)` and retain the existing office filter. Direction uses routing steps in the current batch only.
- Total and direction links use Tracking's existing multiple-status filters, excluding both completion stages. Each stage link also includes its direction and office scope.
- A controlled fixture has 10 incoming records (2 pending, 3 received, 5 in process), 7 outgoing records (4 pending, 1 received, 2 in process), two completed records, a draft, and a record passed on in an earlier hop. Counts and destination IDs are checked for system admin, office admin, staff, and viewer.
- Draft visibility remains author-specific for office users and office admins. Drafts do not appear in either direction bar.
- Empty directions show three zero counts and an empty track. All offices shows an unavailable direction value rather than a misleading zero.
- Every stage has a named, keyboard-accessible link with a count and percentage; decorative segments are hidden from assistive technology. Mobile links have a 44-pixel minimum height.
- Print CSS retains the status palette and keeps each direction group together. This was checked in source; an exported PDF was not generated.

## Live browser evidence

The existing MED office-user session showed 71 lifetime tracking records, including 59 completed or awaiting filing. The corrected report shows **12 current records, 7 incoming and 5 outgoing**.

- Incoming: 0 Pending Receipt, 3 Received, 4 In Process.
- Outgoing: 2 Pending Receipt, 0 Received, 3 In Process.
- The Incoming / Received link opens exactly **3 tracking records**, all marked Incoming and Received.
- Light and dark modes were inspected on desktop and a 390-pixel mobile viewport. A 320-pixel check found no horizontal overflow or overflowing stage links.
- Screenshots: `desktop-light.png`, `desktop-dark.png`, `mobile-light.png`, `mobile-dark.png`.
- The original dark theme and normal viewport were restored. The Reports page was left open.

## Checks

- Updated Reports, destination-count, scope, and layout regressions: **157 passed**.
- The earlier broader focused run identified obsolete panel, completion-stage, and headline-link expectations; those tests now follow the requested design while retaining accuracy checks.
- Ruff: passed. Template parser: **90 templates**, passed. Django checks: no issues. Migration check: no changes. No schema or data migration is needed.
- Full suite: **6,774 passed, 2 skipped, 2 warnings** in 476.21 seconds. The skips cover an office-admin fixture without incoming rows and a background-job check exercised by CI. The warnings are existing Django 6 constraint deprecations. Full output is recorded in `pytest-full.txt`.

The unrelated local edit to `config/settings.py` was preserved.
