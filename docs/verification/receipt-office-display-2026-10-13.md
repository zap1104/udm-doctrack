# Receipt office display clarity

The former Current office/Receiving office pair mixed a single cached custody summary with every destination of the latest endorsement. A confirmed recipient therefore appeared in both columns, while parallel confirmed recipients could disappear from the first column.

Tracking, tracking Search and document detail now display:

- **Received by:** all offices that confirmed receipt of the displayed endorsement.
- **Awaiting receipt:** only its unconfirmed recipients while work remains active.

The two sets are disjoint. If MED confirmed and PROC has not, MED appears only under Received by and PROC only under Awaiting receipt. If both confirmed, both appear under Received by and the other column says All receipts confirmed. Drafts say Not sent yet; completed work has no false receipt task. Detail labels the receiving person Latest receipt by rather than suggesting that one person represents every parallel recipient.

Dashboard action rows also use Awaiting receipt without falling back to confirmed destinations. Recent activity retains all destination metadata under Endorsed to. Tracking/Search retain a three-office badge cap, Dashboard its four-office cap, and detail lists all offices.

The grouped display helper loads receipt and badge fields in one query. Detail reuses its loaded timeline rows. It matches each already-loaded record's current batch so a concurrent endorsement does not blank the receipt cells. Actual routing/inbox queries, lifecycle transitions, filters, permissions, stored custody fields, exports and history remain unchanged. No migrations or live document updates were needed.

## Verification

- Initial receipt-display regressions: 25 failures reproduced before the fix.
- Page-loading snapshot regression failed before the grouped-query correction.
- Final receipt, wording and snapshot suite: **58 passed** (`receipt-office-display-final-focused.txt`).
- Broad related regression: **423 passed**, with one updated-test import error, corrected afterward (`receipt-office-display-regression.txt`).
- Final helper/count regression: **116 passed**, with one test-only substring assertion false positive, corrected and covered by the final 58-test run (`receipt-office-display-final.txt`). The assertion now checks the exact empty-state text. Test counts overlap and are not additive.
- Dashboard/Reports query budgets passed: 51 queries for the all-office dashboard, 55 for selected-office dashboard, and 43 for Reports. No per-row badge lookups were added.
- Genuine Django response browser verification: **19 filter/workspace configurations** and **16 detail/print configurations**, across desktop/mobile and both themes. Receipt cells were checked for overlap and readable empty states; browser errors and page overflow checks passed. See `receipt-office-display-browser.txt` and `receipt-office-display-detail-browser.txt`.
- Live read-only check: **34 documents** with MED as the cached latest office; zero offices displayed as both received and awaiting receipt (`receipt-office-display-live.txt`).
- Django system checks and static analysis passed. Existing constraint deprecation warnings remain. Manual screen-reader testing was not performed.

Changes remain local; no commit or push was requested or performed.
