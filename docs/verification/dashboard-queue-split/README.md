# Dashboard queue split verification, 6 October 2026

ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.

The dashboard now separates active tracking queues (left, two thirds on wide
screens) from completed records pending filing (right, one third). Smaller
screens stack active work first. Recent activity stays full width below them.
Each preview stops at five rows and links to its matching full Tracking list.
Records-staff document identity combines tracking number and subject in one
cell so the active table fits its narrower panel.

The pending preview uses the existing visible_to and office_queue predicates.
It sits outside receipt forms and the HTMX target. Open record links retain
the existing record-page permissions and filing workflow. No shared tracking
query rules, database schema, or main Tracking-page layout changed.
Old dashboard pending-upload bookmarks remain usable. The pre-existing
repository Pending Filing shortcut and chart hover behavior are preserved.

Verification:

- 369 dashboard, queue, filter-agreement and template-accessibility tests passed;
  one existing empty-fixture case skipped. Two existing Django deprecation warnings.
- 146 JavaScript tests passed, including the existing chart hover tests.
- 87 templates compiled; targeted Ruff and diff checks passed.
- Fresh independent review found no critical, important or minor issues.
- Browser: 1280px desktop active table fits without internal overflow; 320px and
  390px layouts stack without page overflow. Wider desktop checked at 1910px.
  Records-staff tables retain a labelled, keyboard-focusable scroll region on
  phones. Both themes checked. HTMX queue changes and Back preserve the filing
  panel. A selected SUP office showed nine pending records and opened a full
  Tracking list with the same nine records. Office administrator, office user
  and viewer layouts were checked alongside the system administrator.

Evidence: desktop.jpg, desktop-dark.jpg, phone-active.jpg and phone-filing.jpg.
These are real local-browser captures. Automated template checks and keyboard
inspection do not constitute a screen-reader certification.

Changes remain local; no commit, push or deployment was requested.
