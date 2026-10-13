# Pending filing count correction

The HR dashboard showed 3 on its Pending filing shortcut while the Tracking workload and completed preview showed 10. The shortcut used the Repository's originating-office folder, counting only work raised by HR. Tracking's pending-filing queue also includes completed records the office handled from other offices.

The shortcut now uses the same permission-scoped Tracking queue and link as the completed preview. Its caption says "Completed in Tracking · not yet filed". The separate originating-office query and count were removed from the dashboard.

Browser verification on the existing local data:

- HR Pending filing shortcut: **10**.
- HR workload: **16 currently tracking + 10 pending filing = 26 documents**.
- Clicking the shortcut opens `/tracking/?scope=pending-upload&office=5`, listing **10 tracking records**, all completed and pending filing.
- Returned the browser to the HR dashboard. Screenshot: `hr-pending-card.png`.

The regression fixture contains 3 records raised by HR, 7 additional completed records handled by HR, and 1 unrelated record. The original shortcut failed with `3 != 10`; the corrected shortcut must show 10 and open exactly the ten expected records. Existing role and office-scope tests also check that its count matches the full list and workload breakdown.

Repository folder behavior, filing permissions, records, routing, and user data were not changed.

Final verification, 6 October 2026:

- Full Python suite: **6,767 passed, 2 skipped**, in 487.55 seconds. The skips are the empty office-admin incoming fixture and the security-maintenance check exercised by background-enabled CI. Two existing Django 6 deprecation warnings remain.
- Dashboard query pins: **53** for all offices, **55** for a selected office. Both passed; sharing the pending-filing count removes one duplicate read.
- Changed Python files passed Ruff; all **90 templates** parsed; Django system checks passed; the migration check found no schema changes; the diff whitespace check passed.
