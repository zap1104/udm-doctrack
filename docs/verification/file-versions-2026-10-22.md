# File versions, repository immutability and previews

Implementation is local on `codex/external-ui-alignment`, based on `bdae00dbaa5bd83cf713e6c0806bdda5744e87a7`, with the pre-existing dirty UI/filter/External work preserved. No commit, push, deployment or original-database migration was performed. All request-driven tests, migrations and browser actions used disposable PostgreSQL databases and temporary local storage.

The final working-tree source snapshot is `.vercel/file-versions/final-source-manifest.json`: 785 source/test/template/script/static files, SHA-256 `ca426bde03c6cf6ce5554f0d217a15d65146c1df38f5dbcbd5790ec73b505e09`. It includes preserved pre-existing changes and excludes documentation and private verification files. This identifies the dirty checkout without inventing a new commit. The earlier full-run snapshot is retained as `full-run-source-manifest.json`, SHA-256 `e4e9777528aaca8afdc254169863011095c42593654e96e5f74f51a4a47c50a2`; the three-file delta and subsequent focused checks are explained below.

## Delivered behavior

- Internal and External attachments belong to independent logical file groups. Existing attachment IDs and legacy download routes remain valid. Each old attachment is backfilled as its own Version 1; matching names are not merged.
- New versions require a summary, fresh permissions, an active record and the expected current version. External requests also check record revision. Earlier bytes and provenance remain immutable, and identical current content is rejected.
- Filing copies complete histories into immutable repository snapshots and creates one current `DocumentFile` per logical group. Checksums and source provenance are preserved. A missing/corrupt source or storage/database failure rolls back filing and removes only new storage copies.
- History copying streams one version at a time instead of retaining the entire history in memory. Current roots alone contribute to indexing, file/page totals and extraction.
- Repository content additions/replacements/version deletion are denied through the public service, legacy endpoint and Django administration, including system administrators. Direct ingest accepts all initial files in one atomic batch. Metadata correction and extraction retries remain available.
- Whole-document deletion recovery and purge include history, deduplicate shared keys and preserve original Tracking storage.
- Private exact-version previews and downloads check current parent access and purpose/user/kind/version-bound signatures. Downloads retain audit entries. Previews do not create work events or change Tracking status/timers.
- PDF page/zoom/text rendering, sandboxed approximate DOCX text/tables/images, PNG/JPEG zoom, escaped bounded TXT/CSV and stored XLSX worksheet values are available. Unsupported/malformed previews retain original downloads. No conversion or signature creation/verification was added.

## Defects found and repaired during implementation

- PostgreSQL deadlock between an uploading actor's `FOR UPDATE` lock and deferred history foreign-key checks during concurrent filing. `active_writer` now uses `FOR NO KEY UPDATE`, retaining serialization of account permission/office/suspension changes while allowing foreign-key key-share checks. A deterministic transaction regression failed before this repair and passes after it.
- Unbounded accumulation of all historical payload bytes while filing. Copies now stream per version; rollback/checksum tests cover failed copies.
- Empty and chart-only/malformed XLSX handling; chartsheets are not offered as cell worksheets. Long cell truncation is disclosed.
- Malformed workbook XML raised lxml's `XMLSyntaxError`, which the stdlib-only parser catch missed. Two actual preview requests reproduced this failure. The preview boundary now handles both parser families through `SyntaxError`; fallback, exact original downloads and unchanged status are verified.
- Historical versions incorrectly listed as independent current files in draft edit/review and printing/counting paths.
- Incomplete display of uploader/office/time provenance, and visible labels for hidden concurrency fields.
- Empty-list validation in the shared multiple-file field, exposed by the new required initial batch upload. Missing files now produce a field error.
- New download routes initially omitted the existing download audit event.
- Excessive declared raster dimensions and mislabeled embedded images bypassing nominal dimension parsing. Preview-only signature and pixel checks reject these inputs without modifying originals.
- Production static collection failed on omitted source maps referenced by vendored scripts. Dangling map comments are removed, all PDF CSS icons are packaged, and a production-manifest collection/render regression passes.

## Verification evidence

Detailed local logs and isolated settings are under `.vercel/file-versions/` and are intentionally untracked. Logs contain test/demo data, not production credentials.

| Verification | Fresh recorded result |
| --- | --- |
| Full PostgreSQL suite, before the final parser guard and receipt-test observer adaptation | **7,728 passed, 49 failed, 4 skipped, 2 warnings** in 29m19s (`full-verified.log`); classification below |
| Version services, filing, rollback, migration roundtrip and adjacent archive/purge checks | 66 passed, 1 background-only skip before the dedicated background run |
| Final lock, concurrent account updates, raw administration, versions/previews and all-role security/access checks | 303 passed, 2 existing deprecation warnings (`actor-lock-green.log`) |
| Background-enabled extraction, schedules, purge, security maintenance and file versions | 79 passed, 2 existing deprecation warnings (`background-final.log`) |
| Final preview/security and production-static checks after the malformed-workbook guard | 22 passed, 2 existing deprecation warnings (`workbook-parser-green.log`); preceding RED run reproduced both XML failures |
| Final receipt/version concurrency and administration checks after adapting the SQL observer | 15 passed, 2 existing deprecation warnings (`receipt-observer-green.log`) |
| Production static manifest collection and preview rendering | 1 passed (`static-production-green.log`); 437 assets post-processed |
| JavaScript suite | 178 passed (`final-js.log`) |
| Real Chromium login/CSRF journeys | 12 completed check groups; no runtime JavaScript errors or external document-resource requests (`browser-results.json`) |
| Ruff, template and whitespace checks | Passed; 112 templates checked; final three-file Ruff recheck also passed |
| Django and simulated deployment configuration checks | No issues |
| Migration drift | No changes detected; two additive feature migrations were exercised only in isolated databases |
| Workflow self-check | All 20 passed after completing the isolated third-office fixture (`selfcheck-complete.log`) |
| Page smoke run | 220 pages across four roles; no unexpected page error (`smoke-pages.log`) |

The completed broad run used the isolated `final_settings` database:

```powershell
$env:PYTHONPATH = "$PWD\.vercel\file-versions;$PWD"
.venv\Scripts\python.exe -m pytest -q --reuse-db --ds=final_settings --tb=short --durations=5 -p no:faulthandler
```

Its 48 pending External failure node IDs exactly match the independently reconstructed baseline (`full-baseline-comparison.json`). The remaining failure was `test_bulk_and_individual_receipts_by_same_user_do_not_deadlock`: its test observer recognized only literal `FOR UPDATE`, so it never signalled that the actor's new `FOR NO KEY UPDATE` lock had been acquired. The observer now recognizes both lock modes, retaining the original synchronization and one-receipt assertions. Production receipt code was not changed for this test adaptation; the receipt test and related version races pass in the subsequent 15-test run.

After the broad run started, an additional malformed-workbook probe exposed the parser error described above. The final source delta is only `apps/core/file_previews.py` (the parser exception guard), `tests/test_file_previews.py` (two new regressions), and `tests/test_receipt_concurrency.py` (the lock observer). These changes received focused re-verification and independent review. The full suite was not repeated after that three-file delta; its raw result is preserved, not described as a green final-source full-suite run.

An earlier full-run attempt exited with a Windows Python 3.13.7 access violation while emitting timeout diagnostics. It is not completed verification. The completed command above disabled the diagnostic plugin without an application change. Its four skips were two background-only cases (covered in the separate background-enabled run), one filter-agreement case without routed `med_admin` incoming fixtures, and the opt-in systemwide live-browser test. Feature-specific live browser journeys were executed separately; they do not certify every unrelated browser workflow.

## Browser coverage

The live journey uses actual Django authentication, CSRF-protected forms and current database responses. Static Bootstrap/HTMX assets are local test copies; viewer libraries are the pinned self-hosted assets. Checks cover PDF page/zoom controls, formatted historical DOCX text/tables/embedded image, image and text/CSV/XLSX previews, worksheet selection, unsupported-format download, actual new-version POSTs in both Tracking namespaces, repository read-only contents, exact-version return focus and JavaScript-disabled history/downloads.

Light and dark themes were checked at 320, 390, 768, 900, 901 and 1366 pixels, including horizontal overflow, keyboard use and 200% CSS layout zoom. Twelve screenshots are retained locally. The approximate Word paper area intentionally preserves a white document surface in either theme.

The isolated browser-test server on port 8114 was stopped after verification; that port was confirmed no longer listening.

## Baseline and limits

The starting tracked checkout was reconstructed from HEAD plus the captured pre-feature dirty patch, with the original pending External tests copied unchanged. A settings assertion confirmed that the probe imported the reconstructed checkout, not the new implementation. Its isolated run reproduced **48 failures in the separate pending External custody refactor**, plus one pre-existing search-test DOM slicing error; 12 tests passed. The stale search assertion was corrected without changing search behavior. The three pending External test files remain unchanged and their workflow is explicitly outside this feature plan.

Physical devices, assistive screen readers, operating-system/browser-chrome magnification and physical printing are unverified. Raster limits bound declared static dimensions; arbitrary decoder behavior and animation-frame memory are not certified. OCR/email/remote storage were simulated; no live R2/Azure/OCR.space delivery is claimed. No whole-system absence-of-bugs claim is made.

Independent reports are `.vercel/file-versions/independent-review.md` and `final-lock-review.md`, including follow-ups for the final parser and receipt-test corrections. Reviewers found and rechecked the repaired issues; their reports distinguish static/in-memory review from author-run database/browser tests. No critical or important file-version issue remained identified by those reviews. The full suite remains non-green because of the separate pending External refactor, and this report is not a release certification.

See [usage and enablement notes](../file-versions.md). Applying the additive migrations to the original database and releasing these changes remain separate actions.
