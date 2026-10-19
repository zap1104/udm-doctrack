# Final live browser verification

## Evidence status

The final recorded run on 18 October 2026 passed: **1 pytest test passed, 2 existing Django deprecation warnings, 290.93 seconds**. Independently reconciling the saved JSON gives **306 recorded checks, 187 live filter swaps, and 321 navigations**. The JUnit result has zero errors, failures, or skips; its timestamp is `2026-10-18T11:41:28.758515+08:00`. These are the saved run results, not a claim that the test was repeated on 19 October.

The captured source revision is **`cd4316aaca993b0218c9252d8683477739368a3f`**, after the extraction-generation repairs and migrations 0012/0013 were committed. The final browser run therefore supersedes the earlier 235.53-second harness-stabilization result. Backend source remained frozen for this run. A subsequent one-line Reports wording correction and its two tests are verified separately; this browser journey does not visit Reports and does not verify that wording change.

Evidence files:

- `live-browser-source.txt`: source revision captured for this run.
- `live-browser-final.txt`: pytest result and warnings.
- `live-browser-final.xml`: JUnit result.
- `live-browser-output.txt`: browser summary.
- `live-browser.json`: individual checks, URLs, navigation timing, and expected request cancellation.

## What ran

The test used the real Django server, PostgreSQL audit database, authentication, CSRF checks, backend views, actions, and private-file download. Only static vendor assets were intercepted and served from matching local cached files. HTML responses and business actions were not mocked. Test records and files belonged to isolated, disposable audit storage.

| Area | Verified behavior |
| --- | --- |
| Authentication | Five real logins: MED staff, SUP staff, MED office administrator, system administrator, and MED viewer |
| Internal workflow | Browser creation, endorsement to SUP, SUP receipt, work remark, completion, administrator filing; Python assertions confirm the completed record and exactly one OVPA repository document |
| External workflow | Managing-admin manual dispatch, MED receipt, work update, completion, managing-admin filing, signed private download returning the exact expected bytes; Python assertions confirm exactly one External repository document |
| Filters | Staff, office administrator, system administrator, and viewer exercised the controls offered on internal Tracking, Repository, repository Search, External Tracking, and External Repository |
| Tracking navigation | Direction, status, originating-office, overdue, sort, and ownership link families |
| In-place refresh | 187 changes preserved a browser marker proving no full-page reload and matched record-link IDs and headings from a normal backend response |
| Navigation | Back/Forward restored selected filters and backend result IDs; Reset, the real second page, and page size 50 matched backend results |
| Response ordering | A real first backend response was held until a newer query won; the previous request was aborted and did not replace current results |
| Failure and retry | Injected HTTP 503 preserved prior results; Retry fetched and displayed the real backend results |
| Read-only role | Direct requests to three write-entry routes were refused for the viewer |
| Responsive layout | 96 combinations: four roles, light/dark themes, widths 320/390/768/900/901/1366, on internal Tracking and External Repository; asserted the actual selected theme, page-level overflow bounds, and reachable keyboard focus |
| Zoom and print | Tracking at 200% CSS zoom retained layout/focus for four roles; routing-slip print invocation and print CSS passed for those roles |
| Lost authentication | Removing the real session cookie before a filter request led to a usable full sign-in page |

All 96 responsive combinations are unique and cover exactly four roles, two themes, six widths, and two routes. All 321 saved navigations reached a complete document with no pending assets.

No browser JavaScript errors or unexpected HTTP error responses were recorded. The one `net::ERR_ABORTED` entry is the deliberately superseded old query in the response-ordering check. The injected 503 is separately labelled and expected.

## Harness corrections

Three test assumptions were corrected during the earlier harness-stabilization work and were retained for this final run. None required a product change:

1. A range control at its minimum needs ArrowRight to change its value; ArrowLeft correctly makes no request.
2. Direction groups reuse the visible word "All". The harness now targets the exact observed link URL rather than a partial text match that selected "All in Tracking".
3. Shared pagination renders Previous/Next links and a page-position label. The harness now clicks the actual `rel=next` control instead of looking for a numeric "2" link.

## Verification limits

These are Chromium automation and layout assertions. They do not establish physical-device or screen-reader behavior, a human visual review of every page, native browser/OS magnification, physical printer output, or the function of every button in the inventory. Responsive checks cover the two named workspaces; the filter journeys cover five workspaces.

The session-cookie check simulates loss of authentication. It does not advance the clock through an actual stored-session expiry boundary. The 200% check uses CSS zoom rather than browser chrome zoom.

Filter browser checks establish parity with normal backend responses for displayed IDs/headings; they are not an independent data oracle. The independent result-ID/count matrix supplies that separate evidence. Positive controls do not cover every possible filter combination or rejected parameter.

Real cloud storage, email, OCR services, and deployment networking remain unverified, as requested. Storage and service behavior in this test remained local or simulated.

## Reproduction

Use `config.audit_settings`, `DOCTRACK_AUDIT_SLOT=browser`, `ENABLE_BACKGROUND_TASKS=False`, and `RUN_LIVE_BROWSER_AUDIT=1`. Set `AUDIT_NODE` and `NODE_PATH` to the installed bundled Node runtime and its modules. Run `tests/test_systemwide_live_browser.py` with pytest, reuse of that isolated database, and the JUnit output path above. The harness uses the installed Chromium executable and local pinned Bootstrap/HTMX cache paths documented in its source. Never point this test at business data.
