# File lifecycle verification, 23–24 October 2026

Status: approved file-lifecycle scope implemented and verified locally. The full project suite still has the 45 pre-existing, out-of-scope External custody/context failures listed below.

Implementation revision: `c519ac50238cccd74ef6884d266cd52866ce204d`, on `feature/documents-intelligence`. The approved-plan base is `07f0f2b`. Full-suite revisions and later focused verification are identified below. Nothing was pushed or deployed, and no migration or pruning command was applied to the original database/storage.

## Implemented behavior

- Private recoverable uploads support ordinary multipart forms and progressive uploads in both collections. Valid files survive validation errors and refreshes. Staging does not create a business record, start OCR or change tracking time.
- Each logical file retains its current binary and four older binaries. Older version metadata and policy receipts remain visible through bounded audit pages. Unsent draft files can be explicitly discarded; dispatched and Repository files cannot use that action.
- Filing copies only retained bytes, preserves original version numbers, checksums and provenance, and keeps one current Repository file per logical file. Repository contents remain locked while existing metadata and extraction permissions remain available.
- Current-version cards, collapsed older copies and labeled activity colors clarify the UI. Both completion checkboxes start unchecked and retain existing approval authority.
- Durable exact-key cleanup handles expiry, consumption and retirement with shared-reference checks, backend identity checks, bounded claims, retries and observable failures. Legacy identity binding remains an explicit verified operator action.

No PDF conversion, signature verification, new provider/API or pending External custody refactor was introduced.

## Reproduced defects and repairs

| Trigger | Repair and evidence |
| --- | --- |
| Native POST from a private saved-form page sent `Origin: null` in Chromium and failed CSRF. | Private HTML uses `same-origin` referrer policy; binaries retain `no-referrer`. CSRF checks remain active. Watched browser 403 plus header regression; 25 related tests passed. |
| New activity icons referenced an icon font that the application does not load. | Local inline SVGs with visible labels; no new external icon dependency. Watched failing renderer assertion; 40 related tests passed. |
| A parallel receipt was not counted as partial in combined/sender/unconfirmed-office report views. | Count both received and unreceived current-batch facts without changing an office’s own stage. Legacy Inbox/Sent also exclude completed populations. 36 related tests passed. |
| Reusing a filename after physical cleanup could reuse a key with an old completed cleanup receipt. | New Tracking, External and Repository binaries use UUID key components. Existing paths remain unchanged; history upload path has an additive field-state migration. Four failing key regressions reproduced the cause. |
| Filing opened a known source-backend mismatch through the currently configured storage. | Reject before opening source bytes. Both collections’ failing regressions now pass. |
| Old blocked cleanup intents filled every bounded oldest-first batch. | Automatic claims exclude blocked intents until explicit operator recovery; dry-run reports them separately. A blocked-prefix regression verifies later actionable cleanup. |
| Failed endorsement validation dropped the legitimate Forward/Return choice. | Recover only the declared routing values for that workflow; other workflow actions remain excluded. Native and enhanced-state checks cover recovery. |
| An upload committed, both responses were lost, and the user clicked Remove. | The queue keeps uncertainty visible, resolves the opaque identity and confirms removal before submission. Two combined-failure JavaScript regressions reproduced the defect. |
| A detail page had two saved forms, then one failed validation or opened a preview. | Carry only freshly authorized same-record form slots through POST recovery and preview Back links. Cross-user slots remain inaccessible. Separate watched PRG and preview regressions failed before repair. |
| A provider refused a later staged file, or the final manifest update failed. | Compensation now encloses the complete staging transaction. Supported provider write failures are handled per file; database/programming failures still roll back the batch and compensate new blobs. Two watched regressions reproduced the defect. |
| New recovery gates rejected existing office-code/all perspectives, office-less system-admin draft edits or confirmed participants in parallel routing. | Preserve existing actor/custody predicates and URL contracts, rechecking actual authority inside transactions. The selected office remains a perspective, never impersonation. |
| An active record showed the completed/awaiting-approval panel. | The panel now uses the actual postcompletion approval predicate, while the completion checkbox uses the precompletion eligibility predicate. Four failing display/form regressions preceded repair; 97 related tests passed. |
| A saved version preview returned to a standalone form instead of its inline form, and the generic disclosure initializer hid recoverable saved/error forms. | Return to the authorized record and its independent form slots. Respect the server’s explicit keep-open state. The preview follow-up passed 93 related tests; the complete JavaScript suite includes the disclosure regression. |
| Smoke verification encountered deliberately retired attachments. | Expect private 410 only for an unavailable attachment; retained signed downloads remain required to work. Existing checks still assert no lost business data or false success messages. |

The final independent source review inspected lifecycle models/services, permissions, HTTP/JavaScript recovery, storage reference lifetime, filing/purge, migrations, UI and regression assertions. It identified no Critical findings. Its Important findings were reproduced and repaired in one implementation pass. The reviewer ran no tests; execution results below are separate evidence. Later browser-discovered filing-panel, preview-return and disclosure fixes have targeted execution evidence rather than a second independent source review.

## Verification environment

- Windows 11, Python 3.13.7, PostgreSQL, desktop Chromium through Playwright.
- Intel Core i7-1185G7 at 3 GHz, 8 logical CPUs, approximately 16.89 GB RAM.
- Disposable databases `test_doctrack_file_retention_20261023_s1` through `s4`, a separate background-test database, and `doctrack_file_retention_browser_20261023` with private temporary local storage.
- The final browser run uses a separate temporary PostgreSQL 18.4 cluster bound to `127.0.0.1:8775`. Its settings explicitly pin host, port and test identity. Reset helpers validate the exact database, local host, ownership marker and media root before mutation. Earlier operations/full-suite evidence retains its original environment.
- OCR, email, ML and external providers disabled or simulated. The deployment check uses placeholder durable-storage/SMTP settings without contacting those services.
- Original credentials/configuration and unrelated user edits were excluded from task commits.

The full-suite inventory is partitioned by exact collected test ID. The shard plugin fails closed on missing, extra or duplicate IDs. Earlier interrupted and pre-repair runs are not described as final verification.

## Fresh results

| Gate | Result |
| --- | --- |
| Focused final recovery/preview/permission compatibility | 115 passed, 52.76 seconds. |
| Focused background-enabled storage compensation regressions | 48 passed, 32.50 seconds. |
| Broader final background-enabled file/upload, presentation, completion, schedules and deletion regressions | 262 passed, zero failures/errors/skips, 101.48 seconds. |
| Final visual filing-panel, office-perspective, completion and race regressions | 97 passed, 37.71 seconds. |
| Saved version preview Back navigation follow-up at `2c909b9` | 93 related tests passed, 75.61 seconds; both neighboring saved forms remain available. |
| Complete JavaScript suite after the disclosure fix | 194 passed, zero failed. Final handoff refresh: 1.795 seconds. |
| Independent base comparison of pending External tests | Base `07f0f2b`: 48 failed, 12 passed, 17.85 seconds. Exact-ID comparison used unchanged tests and identical conftest. |
| Django check / placeholder deployment check | Passed; no reported issues. Provider connectivity was not tested. |
| Migration drift | No changes detected. |
| Actual disposable migration / second pass | Additive migrations applied only to disposable data. Later two verification passes both reported no pending migrations. |
| Templates | 126 checked, no problems. |
| Workflow selfcheck | 19 passed; third-office isolation scenario skipped because that fixture has two offices. Broader role/isolation tests are separate. |
| Whole-repository lint | `ruff check apps tests scripts`: passed. |
| Full PostgreSQL suite at `35e74a2` | **8,105 tests: 8,055 passed, 45 failed, 5 skipped, zero errors.** All expected IDs ran exactly once, with no missing, extra or duplicate IDs. All 45 failures match the verified baseline failure set. Three baseline failures now pass. |
| Real browser journeys and responsive screenshots at `c519ac5` | 24 main journey checks plus 3 supplemental state/navigation checks passed; zero failures or uncaught browser errors. 85 screenshots: 72 page/theme/viewport states, 12 file-card crops and one CSS-zoom view. |
| Retirement-aware page smoke at `2c909b9` | 228 Django Client requests across four roles, no raised page errors, 50.458 seconds. This checks expected refusals as well as allowed responses; it is not a browser run or a claim that every response is HTTP 200. |

The full PostgreSQL run precedes the narrow preview-return fix at `2c909b9` and disclosure fix at `c519ac5`. Those later changes are covered by the 93-test related run, complete 194-test JavaScript run and final live browser journeys. The earlier full run is not presented as a fresh full-suite run of those later commits. Background and JavaScript logs record outcomes without their own Git hash; their implementation order is recorded here rather than inventing revision metadata.

The 45 remaining failures are in the explicitly excluded pending External custody/context refactor: 10 creation-context, 23 custody-control and 12 custody-repository cases. The [exact failure and skip ledger](file-lifecycle-known-external-failures.json) records their IDs. Matching IDs establish pre-existing failing triggers, not proof of identical internal causes. Three full-run skips are schedule/security checks exercised successfully in the background-enabled run. The other two are an opt-in browser audit and an empty MED incoming fixture. Separate live browser verification is recorded below.

Raw XML/logs and private test manifests remain in the ignored `.vercel/file-retention-audit` directory. Publicly committed evidence contains no login secrets or reusable private file links.

### Real browser evidence

The [sanitized browser results](file-lifecycle-browser-results.json) record actual login, CSRF-protected submissions and saved backend assertions, rather than success messages alone:

- Missing subject, partial-invalid batch, refresh, corrected submission, separate tabs and independent inline forms preserve the correct private files.
- A real committed upload with lost upload/read responses, Remove, blocked submission and Retry cannot silently restore a removed attachment.
- V6 and V7 leave exactly V3–V7 available after the second update; V1–V2 remain audit-only. A previously signed retired link returns private HTTP 410.
- Staged and historical previews return to the correct form/history item and restore focus. Browser Back/Forward retains the saved validation form.
- Explicit draft discard works before dispatch. A crafted discard of an active record is refused with a readable state error and preserves its current file.
- Authorized External completion with the checkbox selected files once. Unchecked Internal completion remains Pending Filing until explicit approval. Viewers have no file-mutation controls.
- JavaScript-disabled multipart submission, validation, refresh and finalization reuse saved bytes. A real role revocation refuses both the saved form and its previously signed preview with HTTP 403.
- Stored state in both collections has one logical Repository file, seven history rows, five available versions, the correct current bytes and matching source/copy checksums. Maintenance removed 14 exact queued objects, preserved all 25 retained objects’ checksums, and removed zero objects on repetition.

Creation, validation errors, active Tracking, empty-file Tracking, External Tracking and Repository were checked at **320, 390, 768, 900, 901 and 1366 pixels in both themes**. Automated document-width assertions passed for all 72 states; the complete screenshot matrix and representative full-size file cards were visually inspected. Long filenames wrap without document overflow. Keyboard submission/focus, readable validation errors, loading/retry state and print output were checked. The zoom check uses 200% CSS layout zoom; native browser/OS zoom was not performed.

Representative screenshots: [phone file card](file-lifecycle/screenshots/tracking-file-light-320.jpg), [dark file history](file-lifecycle/screenshots/tracking-file-dark-1366.jpg), [validation error](file-lifecycle/screenshots/create-error-light-390.jpg), [empty files](file-lifecycle/screenshots/tracking-empty-dark-390.jpg), [Repository desktop](file-lifecycle/screenshots/repository-light-1366.jpg). The remaining screenshots use the same naming scheme in that directory.

### Commands and revision boundaries

The following were executed, with full logs retained privately. They require the isolated settings and generated disposable fixtures, not the original deployment configuration:

```powershell
# Four disjoint, fail-closed full-suite shards, each with its own database/media.
# N = 1, 2, 3, 4; all IDs are reconciled after the four runs.
.venv/Scripts/python.exe -m pytest tests -p file_retention_sharding_plugin --file-retention-shard=N --ds=file_retention_shard_N -o pythonpath=.vercel/file-retention-audit -q --tb=short -ra --junitxml=post-visual-shard-N.xml

node --test tests/js/*.cjs
.venv/Scripts/ruff.exe check apps tests scripts
.venv/Scripts/python.exe scripts/check_templates.py
.venv/Scripts/python.exe manage.py check --settings=file_retention_browser_settings
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=file_retention_browser_settings
.venv/Scripts/python.exe manage.py check --deploy --fail-level ERROR --settings=file_retention_deployment_check_settings

# Private manifest contains only freshly generated disposable test credentials.
node scripts/check_file_retention.cjs .vercel/file-retention-audit/browser-manifest.json
```

The browser harness requires Playwright/Chromium, pinned local vendor assets and its ignored fixture/assertion helpers. It refuses non-loopback origins or the wrong database name. The additional empty/error/Back–Forward run used `browser-state-visuals.cjs` in the ignored audit directory, with the same browser helper functions. The tracked harness now includes empty/error screenshots directly; the final recorded run obtained those 24 views separately. Full-suite results are tied to `35e74a2`; the later 93-test preview follow-up, 194-test JavaScript run and final browser evidence explicitly cover the subsequent changes. Final Django, placeholder deployment, migration-drift, lint and 126-template checks were refreshed at `c519ac5`.

## Counts, performance and cleanup evidence

A rolled-back fixture contained **1,000 actual version metadata rows**, five retained copies and complete unique version IDs. No binary `open` or `exists` operation was allowed during list/history measurements.

- File-card metadata: **2 queries**, five displayed versions, **10.306–13.297 ms** across five samples.
- First audit-history page: **2 queries**, 25 entries, **8.438–267.199 ms** across five samples. The first cold sample was 267.199 ms.
- Walking the entire history returned all **1,000 unique IDs exactly once across 40 pages**.
- Record file-row builder: 2 queries, 27.13 ms in this fixture. These are metadata/build measurements, not end-to-end page latency guarantees.

Four full PostgreSQL test shards were running on the same host during the benchmark. Fixture data was rolled back. Quota and cleanup bounds are asserted separately: 24-hour default staging lifetime, five open forms, default 250 MiB temporary-byte quota, five retained versions and maintenance batches of 1–1,000.

On disposable storage, dry-run reported three candidates with no database/file changes. Apply removed three exact queued objects; all 12 retained file rows kept identical content checksums. A repeated apply changed nothing. Storage failure, acknowledgement failure, expiry/finalization, cleanup/alias, upload/filing and duplicate-worker cases have dedicated PostgreSQL regressions. This is not live Azure/R2 service verification.

## ISO/IEC 25010:2023 evidence map

The [2023 product quality model](https://www.iso.org/standard/78176.html) is the acceptance framework. Five versions and the project’s limits are DocTrack policy, not requirements prescribed by ISO. This report does not claim certification.

| Characteristic | Evidence / practical limit |
| --- | --- |
| Functional suitability | Exact version IDs/checksums, logical-file counts, filing/reopening, preview/download, both completion defaults and regression tests. |
| Performance efficiency | Bounded lists/audit pages, measured 1,000-row fixture, upload quotas and cleanup batch limits. No production-load claim. |
| Compatibility | Existing formats and signed URLs, multipart/no-JavaScript recovery, local private storage and mocked provider errors. Live provider behavior unverified. |
| Interaction capability | Current/older labels, visible action names, progress/errors, keyboard/focus and responsive/theme checks. Physical-device and screen-reader evaluation remain unperformed. |
| Reliability | Refresh recovery, idempotent replay, stale-version conflicts, storage/transaction compensation and durable cleanup retries. |
| Security | Fresh actor/office/custody checks, own-user staging, CSRF, signed version/purpose links, private/no-store responses, MIME/resource limits and Repository content locking. |
| Maintainability | Shared services with explicit workflow adapters, additive migrations, regressions, independent review and the operations guide. |
| Flexibility | Configurable lifetime/quotas, supported private storage backends, original file formats and responsive layouts. |
| Safety | Confirmed draft discard, save-before-retire, shared/current-content protection, clear unavailable history, unchecked immediate filing and preserved audit metadata. |

If the defense uses [ISO/IEC 25010:2011](https://www.iso.org/standard/35733.html), label that edition separately. Its Usability category corresponds broadly to the 2023 Interaction capability evidence here, and Portability to Flexibility evidence; the 2023 Safety category is additional. Do not present the editions as identical. Accessibility checks target relevant [WCAG 2.2](https://www.w3.org/TR/WCAG22/) criteria; automated evidence alone does not establish full AA conformance.

## Representative-user defense tasks

These tasks are prepared, **not human-tested results**. Record participant role/context, task time, wrong turns, assistance and completion outcome.

1. Identify the current version and explain where the four older copies are.
2. Open an older version, identify its version number, then return to its history row.
3. Submit a file with a missing subject, refresh, correct the subject and continue without selecting the file again.
4. Complete work with immediate filing unchecked and explain which authorized action files it later.
5. Distinguish an endorsement, a receipt and a work update without relying only on color.

## Implementation decisions and deferred findings

These are the plan rulings, in the order made. No minor source-review findings remain deferred; the blocked-intent dry-run counter was also corrected.

| Decision | Reason / cost if wrong |
| --- | --- |
| Continue in the approved feature branch and use native PowerShell equivalents of the bash-only ledger helpers. | Preserves the existing approved workspace. The risk is mixing unrelated edits; exact-path commits excluded the user’s environment edit and unrelated untracked work. |
| Test the actual expiry/finalization race in Task 8 when the maintenance worker exists. | Exercises the real lock/cleanup interaction. The risk was leaving this gate unfinished; PostgreSQL race and background regressions subsequently covered it. |
| Allow a system administrator to select an authorized creation office before the first manifest is bound; do not rebind an existing manifest. | Preserves the existing creation flow without treating viewing context as custody. An incorrect binding would risk cross-office authority; fresh actor/context and crafted-request tests check the boundary. |
| Add the nullable cleanup receipt `superseded_by` link in additive migration `core0017`. | Legacy binding can encounter an existing exact-key intent; linked receipts preserve both pieces of evidence. Incorrect linking could hide cleanup state; collision/reference regressions cover it. |
| Replace the interrupted single-process full run with four exact-ID shards in separate databases/media. | Completes coverage under local resource limits. A partition error could omit tests; inventory reconciliation proved all final 8,105 IDs appeared exactly once. |

The final browser shell lost its earlier inherited local connection environment. The final gate therefore used a new, dedicated loopback PostgreSQL cluster with explicit settings, without changing the original database or cloud configuration. Original-data rollout and the pending External custody refactor remain separate work.

## Remaining limits and rollout

Physical devices, screen readers, native browser/OS zoom, representative human tasks, live Azure/R2/OCR/email connectivity, production load and original-data rollout were not performed. CSS zoom must be labeled as such. The deployment-placeholder check is configuration validation, not deployment readiness.

Pending External custody refactor failures are outside this feature’s approved scope. Exact final IDs and their base comparison are retained in the linked failure ledger. No claim is made that the entire application is universally bug-free.

Use [the operations guide](../file-lifecycle.md) for dry-run/apply, retries, legacy binding, rollback and the 30-day whole-document recovery boundary. Original historical pruning requires a separate reviewed rollout decision.
