# Independent final integration review

Reviewed on 18 October 2026 against baseline
`bb84ac863937a27dc871e115db694b2e84a78f73` on
`codex/systemwide-bug-audit`. The review covers the working-tree product repairs,
their new regressions, diagnostic changes, live-browser harness and saved
verification evidence. It does not substitute for the coordinator's final
revision and collected-test reconciliation.

## Product findings

The initial integration review identified no unresolved, reproduced product
defect in the inspected combined changes. A subsequent requested extraction
probe confirmed a sibling-job failure disclosure defect. See
`sibling-failure-report.md` and `sibling-failure-red.txt`: two intended assertion
failures. The coordinator then assigned its repair to this reviewer, so that
new repair is not independently approved by this document. Its focused/related
verification initially recorded 87 passing regressions, including 13 sibling
boundary cases. A further requested finalization-failure probe reproduced and
repaired persistent RUNNING after index/suggestion-write errors. The latest
combined related run records 95 passing cases, including 15 sibling and six
finalization/synchronous-boundary cases after an independent clean-cancellation
follow-up. See `extraction-finalization-report.md` and the sibling report. The
coordinator owns independent review and the final full suite for these
subsequent implementation changes.

- Notification visibility now checks the referenced internal Tracking record
  and repository document for every role. Office restriction and External
  visibility are retained.
- Internal attachment and repository downloads require expiring signatures
  bound to file kind, file ID and current user. Existing fresh authorization
  remains in the download views. All inspected UI links issue the corresponding
  signature, including deletion review; private responses disallow caching.
- New-file compensation surrounds database transactions in the correct order.
  A rollback removes new objects, while the exact persisted row/name check
  protects files after an unrelated post-commit callback fails. Existing files
  are not registered for compensation.
- Queue submission failures preserve committed work and expose a retryable
  extraction state. Full retries and queued/direct append jobs capture and
  recheck extraction generations before publishing file metrics, document text,
  suggestions and the search index. Fresh actor checks remain in place.
- Added-file and retry messages distinguish saved files, background work and
  failed extraction. The synchronous detailed failure-message path is retained.
- The additive generation migration defaults existing documents to zero. No
  record conversion, numbering or historical timestamp migration is introduced.
- Diagnostic changes address collection isolation, inactive tag choices,
  optional chart links, displayed draft identifiers, retired-office search and
  pagination beyond the bounded Show All page size. These are diagnostic repairs,
  not evidence that previously correct production filtering needed replacement.

## Evidence inspected directly

The reviewer read the focused production diff, new storage/generation/access
regressions, isolated settings, signed smoke checks, browser harness and evidence
files. A fresh `git diff --check` during this review exited zero. The reviewer
did not rerun the full suite or contact live services.

- `full-suite/worker-0-direct.txt`: 3,761 passed, 2 skipped, 2 warnings.
- `full-suite/worker-1-direct.txt`: 3,761 passed, 2 skipped, 2 warnings.
  The two worker lists predate the additional signed-smoke regression. Their
  union must be reconciled with the final collection and separately executed
  cases before reporting a final suite-wide total.
- `filters-final.txt` and `filters-report.md`: 113 additional filter/oracle
  tests; independently constructed fixture IDs supplement ordinary response
  parity checks.
- `background-final.txt`: 69 passed, including the background-only scenarios
  skipped by the ordinary worker configuration.
- `isolated-command-smoke.txt`: 21 workflow checks and 231 page requests across
  four roles, with deliberately refused operations counted as correct outcomes.
- `deployment-shaped-check.txt`: production-shaped configuration check passed
  with no provider or database network calls during that check. This does not
  verify production credentials, infrastructure or live storage behavior.
- `lint-final.txt`, `templates-final.txt`, `dependencies-final.txt` and the
  dependency vulnerability evidence record their specific successful checks.

## Browser evidence correction and final gate

The saved 207-check browser result is evidence for its earlier harness version.
The current harness additionally exercises Tracking pills, history/reset,
pagination, delayed responses, transport failure/retry, session expiry, print
invocation and CSS zoom. These additions require their own completed run.

This review found one further verification weakness: registering theme init
scripts repeatedly did not prove the requested theme was actually active.
The coordinator replaced this with a direct saved theme selection and an
assertion of the rendered `data-bs-theme` after each responsive navigation.
That is a test-harness correction, not a reproduced application defect.
The current harness, including that correction, must pass before its expanded
browser coverage is claimed. The coordinator owns the final run and evidence.

The earlier review's two diagnostic findings are repaired in source: smoke
downloads supply signatures, and the live download assertion requires exact
expected bytes rather than accepting an empty body. The signed-smoke regression
and related successful run support the former; the current browser run must
support the latter.

## Limits that must remain explicit

- Passing tests support the exercised contracts; they cannot establish that
  every possible bug is absent. No new business decision is required by this
  review.
- Inventory entries marked mapped/notproved remain discovery targets. A test
  file reference is not evidence that every route, role or button was exercised.
  The audit must not claim an exhaustive live journey through every raw Django
  administration action or every available UI control.
- Browser response snapshots compare displayed record-link IDs and headings
  with a normal backend response. They do not independently establish numeric
  totals. Independent expected-ID/filter tests provide separate count evidence.
- The wide responsive loop exercises Tracking and External Repository at six
  widths in both requested themes. It does not establish visual correctness of
  every page at every width. Keyboard checks establish focus movement, not a
  complete accessibility audit or screen-reader usability.
- CSS 200% zoom, print invocation/CSS and Chromium viewport emulation are
  simulated checks. Browser/OS zoom, physical printing, physical devices and
  assistive technologies remain unverified unless additional evidence is added.
- Storage, OCR and email boundaries are simulated as authorized. Published
  dependency vulnerability checks do not detect every security defect.
- Existing business data is outside the mutation tests. The new migration was
  checked in isolated PostgreSQL; deployment and applying it to a business
  database are separate operations.

Local audit delivery additionally requires independent acceptance and final
combined verification of the subsequently repaired sibling and finalization
extraction defects, source-tied final collection reconciliation and completion
of the current browser run.
This review authorizes no push or deployment.
