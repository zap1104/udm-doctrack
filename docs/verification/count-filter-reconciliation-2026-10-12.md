# Count and filter reconciliation, 12 October 2026

Scope: Dashboard, Tracking, Reports, Repository, both Search modes, CSV exports, notification counts and administrative summaries. Preserve existing office permissions, routing, data and deletion recovery behavior. No release was requested for this audit.

## Corrections

- Tracking Search now preserves the selected office perspective, direction, repeated stages, repeated originating offices, owner and deadline condition when submitting a query. System administrators can choose their perspective on that page.
- One office-ID/code resolver now serves Dashboard, Reports, Tracking and Search. Codes are case-insensitive. Oversized numeric values are rejected safely. Invalid administrator perspectives explain the fallback; Tracking/Search disable direction when falling back to All offices.
- Tracking forms and filter links consume the same normalized state. A valid originating office is retained when another submitted office is invalid, with a warning about the rejected value. Repeated selections are deduplicated.
- Legacy `status=OVERDUE` and `scope=overdue` bookmarks become the canonical deadline filter. All deadlines and On time can now replace them, and changing offices preserves their meaning.
- Repository tag counts and the deletion-review count link explicitly select their category. Clicking an already-selected count no longer clears that filter.
- Visible filed documents owned by retired offices remain reachable through Repository folders and Search. This does not reactivate the office or offer it as a routing destination. Office-administrator and document visibility restrictions still apply.
- Reports' stale-receipt diagnostic requires an actual unconfirmed step from an older batch. An unsent draft is not a stale receipt.
- Tracking CSV direction uses the same office perspective as the table. Across all offices, unavailable direction is shown as a dash instead of incorrectly calling every record Passed on. The population filter remains distinct from the direction perspective.
- The report section counting receipt events is labelled **Confirmed receipts by office**. Multiple confirmed receipts of one document are events, not multiple documents.

## Counting definitions preserved

| Term | What it counts |
| --- | --- |
| Current work in Tracking Reports | Distinct visible records excluding Completed and Completed - Pending Filing. Drafts remain in the stated overall current-work total. |
| Tracking workspace | Visible records still in Tracking, including drafts and work awaiting filing; filed Completed records are excluded. |
| Incoming / Outgoing | Direction relative to one office in the current routing batch. All-offices views have no direction. |
| Stage | The document's workflow status. A partially received multi-office batch can have a global received/in-process stage while another recipient still owes a receipt. |
| Overdue | A deadline condition across unfinished stages, not an additional stage to add to a status total. |
| Pending filing | Completed tracking work awaiting approval into the Repository. |
| Repository total | Distinct active, permission-visible filed documents; removed documents are excluded. |
| Monthly repository chart | Filed documents in each displayed month plus explicit Earlier/Later-date buckets, reconciling to the total repository population. |
| Endorsements / confirmed receipts | Routing events. A document can contribute more than once, so these totals are not document totals. |
| `office` in Tracking / Reports / Dashboard | An administrator's office perspective, subject to role restrictions. |
| `offices` in Tracking / Search | Originating-office content filter, potentially several offices, within visible records. |
| `office` in Repository / repository Search | Owning-office content filter within documents the account may see. |

Incoming and Outgoing are not a universal partition of all visible work: drafts and records an office previously passed on may belong to neither current direction. Search relevance thresholds and explicit result/export caps also have their own displayed denominators. These distinctions must remain explicit rather than forcing unrelated totals to match.

## Verification

- Baseline live audit: **21,228 checks across 26 role/office scopes, zero mismatches**. This established ordinary-flow agreement; synthetic regressions then reproduced the URL and interaction defects above.
- Added 64 backend regression cases for office codes, invalid/unauthorized perspectives, preserved filters, legacy deadlines, exact repository result sets, retired owners, stale receipts and CSV direction/record agreement. Confirmed failing cases before implementation.
- Real Django-rendered Search pages tested with local HTMX/Bootstrap assets at **1366 px and 390 px**: Search submission, repeated filters, office change, keyboard focus, Back/Forward, no full-page reload, no JavaScript errors or page overflow. Temporary fixture records and sessions were rolled back.
- Shared automatic-filter browser harness passed error/retry handling, permission failures, superseded requests, history, session-expiry navigation, reset, print, no-JavaScript fallback and hidden routine feedback.
- **172 JavaScript tests passed.** Python lint passed. Django system checks reported no issues; migration check reported no changes. Whitespace check passed.
- Enhanced final live audit: **21,757 checks across 26 role/office scopes, zero mismatches**. All four data-integrity counters were zero: pending stage without a current unreceived step; completed work without a completion timestamp; completion before creation; received/in-process work without a first-receipt timestamp.
- Full backend run: **6,992 passed, 6 failed, 3 skipped**. The six failures were expectations tied to the intentional changes: three old receipt headings, one incoming-without-an-office fixture, one assertion expecting form-level rather than shared validation feedback, and one legacy-overdue URL spelling. Updated those checks without removing their record/permission assertions. All six then passed together with the 64 new cases: **70 passed** in the final focused run. The entire backend suite was not repeated after those test-only corrections.
- Separate background-enabled verification: **3 passed**, covering the two scheduling checks skipped by the normal configuration plus the clarified invalid Repository-office message. Background support was enabled only in that test process. The remaining full-suite skip is an office-direction fixture with no incoming records. The two warnings are existing Django CheckConstraint deprecations.

Evidence is stored alongside this report in `count-filter-*.txt`, `count-export-direction-red.txt`, `count-stale-receipts-red.txt`, `counts-2026-10-12-*.json`, and desktop/mobile screenshots. The enhanced live audit also compares exact CSV record sets and on-screen/exported directions. Its URL helper preserves repeated parameters.

Limits: Browser checks use actual server-rendered fixtures and the real frontend code with local response fixtures, not a deployed production session. No external storage, email, or scheduled deletion job was exercised. The live audit rolls back sessions/logs and does not modify document contents or routing history. Test evidence cannot establish that no undiscovered defect exists.
