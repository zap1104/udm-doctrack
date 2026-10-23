# Durable uploads and five-version file history: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Native execution is the previously selected approach. Use one independent final review, not an implementation agent for every task. Steps use checkbox syntax for tracking.

**Goal:** Recover valid uploads after form errors/refresh, permit unsent draft-file removal, retain five available versions with older audit metadata, and make file actions and completion clear.

**Architecture:** Private upload manifests hold unfinished files independently of business records. Explicit workflow adapters consume them atomically through existing mutation services. A shared lifecycle service records retirement/discard decisions and retryable cleanup intents; retained file history remains immutable. Shared file cards and timeline presentation consume that state without changing workflow calculations.

**Tech Stack:** Existing Django 5/PostgreSQL, Bootstrap 5/HTMX, django-q2, private Azure/R2-compatible storage, signed previews and downloads. Windows PowerShell. No new library, API key, conversion service or integration.

**Spec:** `docs/superpowers/specs/2026-10-23-files-drafts-retention-design.md`, written-spec approval received 23 October 2026. This implementation plan is a new artifact awaiting review; no product code has been changed for it.

## Global constraints

- **ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**
- Keep original formats. No PDF conversion, Word automation or signature-verification claims.
- Five available binaries means current plus four older versions **per logical file**. Retain immutable audit metadata; never renumber or reuse attachment IDs/version numbers.
- Preserve OVPA/External separation, existing office authority, numbering, routing, status rules, timers, signed links for retained files and Repository content locking. Do not implement the separate pending External custody refactor.
- Only current versions enter normal file counts and extraction/indexing. Staging is private and never allocates a Tracking number, changes work status, creates a Repository record or runs OCR.
- Staged uploads expire 24 hours after the latest successful explicit save/upload; GET does not extend them. Default five open manifests/user; default staged-byte quota is `MAX_UPLOAD_FILES * MAX_UPLOAD_MB * 1024 * 1024` (currently 250 MiB). All bounds must be positive.
- Preserve valid neighboring uploads when another file or ordinary field is invalid. Count staged plus new files against the existing per-submission limit; a new-version submission accepts exactly one file.
- Keep whole-Repository deletion recovery at 30 days. No pruning during the recovery window; retirement before whole-record deletion is not reversible by restore.
- Additive migrations only. Do not migrate, prune, backfill or exercise destructive requests against the original database/storage. Use disposable PostgreSQL and temporary files; simulate OCR, email and storage failures.
- Stay local on the current feature branch, preserving unrelated edits/untracked work. No push or deployment. Never stage `.env.example`, `.env`, audit credentials or ignored evidence.
- ISO/IEC 25010:2023 is an acceptance framework, not a certification claim. Target applicable WCAG 2.2 AA checks; distinguish automated, browser, human and unperformed checks.

## Review focus

1. A slow upload finishes after the user removes it, changes office, or submits: no resurrection, unauthorized attachment or duplicate business record (Tasks 2–5).
2. Storage deletion succeeds but a worker crashes before acknowledgement: retries must remain safe, must not delete a new current/shared reference, and must report the real state (Tasks 6–8).
3. A retired legacy version has no trustworthy backend identity or an unexplained missing blob: do not guess that it was intentionally removed or delete from a newly configured backend (Tasks 1, 6–8).
4. A record is routed, completed, filed, or the user is transferred while a form is open: saved uploads cannot bypass fresh authority, receipt, version or revision checks (Tasks 2–4, 6, 10).
5. Refresh restores an old checked checkbox, repeated choices or the wrong tab's files: recover only the exact manifest/form state and keep immediate filing unchecked when omitted (Tasks 3–5, 10–11).

## Workspace and evidence

Use the current feature branch in place, consistent with the previous approved local workflow. At execution, verify `git branch --show-current`, `git rev-parse HEAD`, and `git status --short --untracked-files=no`. Do not reset, stash or clean unrelated changes. If the branch changed to main/develop, create a task branch before product edits.

Use this plan's ignored workspace at `.superpowers/sdd/2026-10-23-files-drafts-retention/`, resolved through the skill's workspace helper. Its ledger records baseline revision, task completion, RED/GREEN commands, commits, rulings, final review and limitations. Use ignored `.vercel/file-retention-audit/` for disposable settings, XML, browser fixtures, screenshots and logs. No secrets in tracked reports.

Before implementation, copy the existing isolated audit-settings pattern with new explicit database names: non-test sentinel `doctrack_file_retention_unused_20261023`, pytest `test_doctrack_file_retention_20261023`, background pytest `test_doctrack_file_retention_bg_20261023`, browser `doctrack_file_retention_browser_20261023`. Use separate temp media roots, locmem email, `OCR_BACKEND=none`, blank provider keys. Verify database names before every migration/fixture command. Do not print connection strings or credentials.

PowerShell command aliases below are documentation shorthand, not shell aliases:

```powershell
# R: replace <tests> and <run> for each RED/GREEN gate; never run two suites against this same TEST database concurrently.
& .venv/Scripts/python.exe -m pytest <tests> --ds=file_retention_settings -o pythonpath=.vercel/file-retention-audit -q --junitxml=.vercel/file-retention-audit/<run>.xml
# B: background gate uses distinct TEST database/media and ENABLE_BACKGROUND_TASKS=True.
& .venv/Scripts/python.exe -m pytest <tests> --ds=file_retention_background_settings -o pythonpath=.vercel/file-retention-audit -q --junitxml=.vercel/file-retention-audit/<run>.xml
```

Run the existing version, preview, receipt and deletion regressions before changing their behavior. Prior verification is historical: the last full run had 7,876 passes, 49 failures, four skips; one sampling-fixture failure was corrected in a narrower rerun. Forty-eight failures remain in the three untracked pending External custody/context modules. Record a fresh baseline and compare IDs; do not hide those modules or describe a partial run as a green full suite. This plan does not authorize their separate refactor.

## Planned file boundaries

| Responsibility | Files |
| --- | --- |
| Shared persistent manifests, lifecycle receipts and cleanup intents | New `apps/core/upload_models.py`, `apps/core/file_lifecycle_models.py`; import model classes from `apps/core/models.py`; additive core/tracking/documents migrations |
| Stage/restore/consume plus workflow authorization | New `apps/core/upload_drafts.py`, `apps/core/upload_context.py`, `apps/core/upload_views.py`, `apps/core/upload_forms.py`; adapters in new `apps/tracking/upload_drafts.py`, `apps/documents/upload_drafts.py` |
| Retirement, discard, storage identity/reference checks and cleanup | New `apps/core/file_lifecycle.py`, `apps/core/storage_references.py`, `apps/core/file_maintenance.py`; small hooks in existing file-version/storage/purge code |
| Shared browser behavior and rendering | New `static/js/upload-drafts.js`, `templates/partials/_upload_draft.html`, `templates/partials/_file_audit.html`, `templates/core/upload_draft.html`; existing file cards/form templates and `static/css/doctrack.css` |
| Action presentation | Existing `apps/core/colors.py`, template-tag module, Internal/External timeline partials; new External activity partial |
| Tests, real browser and operations | New focused `tests/test_upload_*.py`, `tests/test_file_retention_*.py`, `tests/test_file_discard.py`, JS/browser tests; `docs/file-lifecycle.md`, final verification report |

### Task 1: Add explicit file-lifecycle and upload-manifest state

**Files:** Create the two model modules above and `tests/test_file_retention_models.py`; modify `apps/core/models.py`, `apps/core/checks.py`, `config/settings.py`, `apps/tracking/file_version_models.py`, `apps/tracking/models.py`, `apps/tracking/external_models.py`, `apps/documents/file_versions.py`. Add migrations after current leaves (core 0014, tracking 0018, documents 0028; recheck graph before assigning numbers).

**Interfaces produced:** `UploadDraft`, `StagedUpload`, `FileLifecycleEvent`, `StorageCleanupIntent`; `available_content` on version rows; `discarded_at`/`discarded_by`/reason on logical groups. Add lifecycle state without relaxing existing immutable provenance guards.

- [ ] Write `test_manifest_target_constraints`, `test_retirement_preserves_original_metadata`, `test_positive_upload_limits`, and `test_additive_migration_preserves_existing_keys_and_ids`. Assert mutually exclusive Internal/External target/group FKs, matching workflow/collection, positive revision/size/version constraints, exact existing file names/checksums and unknown historical provenance remaining unknown.
- [ ] Run R on this module. Expected RED: absent lifecycle/manifests/constraints, not a broken test fixture.
- [ ] Implement manifest UUID identity; owner and office snapshot; workflow, collection, explicit target/group FKs; open/consumed/discarded/expired state; revision, expiry, allowlisted multi-value form-state JSON and serialized result reference. Add staged-file UUID, private immutable key, type/size/checksum, storage identity and lifecycle fields. Add append-only lifecycle receipts and unique deduplicated cleanup intents with status, lease/retry/error, exact key and nonsecret backend identity. Events retain version metadata and actor/office snapshots after content retirement. Use protected/SET_NULL links or snapshots so purge does not erase the minimal audit receipt.
- [ ] Add retained/retired metadata to versions and group-discard metadata, all retained/undiscarded by default. Repository audit-only snapshots may have an empty file field when created from an already retired source. Existing rows keep their paths. New stored rows capture a nonsecret backend identity; unknown legacy identities remain explicitly unknown, never invented by a migration.
- [ ] Run R plus existing immutable-file/admin and migration tests; check migration drift. Expected GREEN: constraints enforce valid state; existing rows and download identifiers are unchanged. Commit exact task paths: `feat(files): add upload drafts and lifecycle receipts`.

### Task 2: Build private staging, quotas and current-authorization checks

**Files:** Create `apps/core/upload_context.py`, `apps/core/upload_drafts.py`, `tests/test_upload_drafts.py`, `tests/test_upload_draft_security.py`; modify `apps/core/storage.py` only for shared compensation/identity helpers needed here.

**Interfaces produced:** `resolve_upload_context(user, workflow, *, target_id=None, group_id=None, query=None) -> UploadContext`; `open_draft(*, user, context, form_nonce) -> UploadDraft`; `stage_files(draft_id, files, *, user, expected_revision, client_upload_ids) -> StageResult`; `save_form_state(draft_id, values, *, user, expected_revision) -> UploadDraft`; `discard_staged_file(draft_id, file_id, *, user, expected_revision) -> UploadDraft`; `read_draft(draft_id, *, user, context) -> UploadDraft`. `UploadContext` is a frozen value containing workflow, collection, actor-office ID, validated creation-office ID, target kind/ID and group ID. `StageResult` contains accepted IDs, per-file errors and current revision, never storage paths. A client upload UUID is unique per manifest and identifies a retry; it does not infer identity from the name or checksum.

- [ ] Write tests for five-manifest/250-MiB bounds, per-form limits including already staged files, partial-valid batches, independent tabs, unchanged GET expiry, explicit-save expiry extension, stale revisions, forged IDs, viewers/suspension/transfer, stale target custody and both collections. Assert no business rows, OCR/provider calls, numbering, work activity or notifications are created by staging.
- [ ] Run R on both modules. Expected RED: private stage/authorization/limits missing.
- [ ] Implement explicit context adapters for existing Internal and External authority. Creation contexts use this branch's current office rules; reject invalid/repeated/unauthorized context without broadening. Bind drafts to the actor's original office even if a system admin selected another permitted creation office. A draft UUID grants no authority, including to another administrator. Initial GET issues only a signed, expiring, actor/context-bound form nonce; first authorized POST creates or finds its unique manifest. This keeps GET read-only while serializing double initial submissions. Repeated client-upload UUIDs return the original staged identity, reject mismatched content, and cannot resurrect a removed file. No-JavaScript multipart submissions derive per-file identities from the signed form instance and input ordinal.
- [ ] Serialize per-user quota changes with the current actor row. Global lock order: actor, target parent if any, manifest, group if any, staged/version rows, then exact storage-key guards. Use bounded streams/temporary files, existing MIME validation/checksums, unique keys and storage compensation. Validate each input separately so a bad neighbor does not destroy valid saved files; checksum/name equality never implicitly merges files. Server responses say Saved only after commit.
- [ ] Run R and existing storage/security regressions. Expected GREEN: only the owner with fresh workflow authority can access or mutate staged bytes; failed writes leave no falsely saved row. Commit `feat(files): stage private uploads with bounded recovery`.

### Task 3: Consume manifests atomically through explicit workflow adapters

**Files:** Create `apps/tracking/upload_drafts.py`, `apps/documents/upload_drafts.py`, `tests/test_upload_finalization.py`, `tests/test_upload_draft_races.py`; modify shared staging and the minimum existing service hooks needed for finalization.

**Interfaces produced:** `finalize_upload(draft_id, *, user, context, cleaned_data, expected_revision, expected_version=None) -> UploadResult`. Dispatch only from a server-side registry. `UploadResult` stores workflow, parent kind/ID, redirect and resulting revision/version. Workflow keys: `tracking-create`, `tracking-edit`, `tracking-remark`, `tracking-route`, `tracking-version`, `external-create`, `external-attachment`, `external-version`, `repository-upload`, `external-repository-upload`.

- [ ] Add tests for complete rollback on a later-file failure, no-file submissions where currently valid, idempotent replay of a consumed manifest, rejected reuse with changed context/payload, exact collection/office resolution and same-starting-version conflicts. Add barrier-based PostgreSQL tests for consume versus expiry/remove, two simultaneous consumers and permission changes; assert exact IDs, files and single business outcome.
- [ ] Run R for these modules. Expected RED: recovery consumption and replay are unsupported.
- [ ] Stream staged files through existing `attach_files`, External `add_attachment`, version services and `ingest_upload`; copy to new immutable permanent keys. Deliberately do not promote/rename staged objects, reducing cleanup/reference races. Preserve staged source bytes until target mutation and manifest consumption commit together; schedule their cleanup only after successful consumption. Wrap the outer transaction in existing storage compensation. Do not hold actor locks while calling an external provider.
- [ ] Preserve both extraction modes: if Repository ingestion needs an adapter-specific deferred-extraction path, add an optional keyword defaulting to existing behavior. Persist files/consumption first, then run/queue existing actor-bound extraction after commit, including the legacy no-q2/no-source-units path. Processing failures must retain a successful upload with a clear retry state, not turn consumption into another document on resubmit. Never run extraction for staging itself. Save suggestions through the existing review flow.
- [ ] Run R plus version service/race tests, `test_processing_regressions.py` and `test_queued_extraction_security.py` under their appropriate settings. Expected GREEN: replay gives the same result, one new version wins, failures keep valid stages retryable without leaked permanent copies. Commit `feat(files): finalize recovered uploads exactly once`.

### Task 4: Wire recoverable ordinary forms and private preview routes

**Files:** Create `apps/core/upload_forms.py`, `apps/core/upload_views.py`, `templates/core/upload_draft.html`, `templates/partials/_upload_draft.html`, `tests/test_upload_draft_views.py`; modify core URLs/file access/file views, Internal/External forms/views, version view, both Repository upload views and affected form templates.

**Interfaces produced:** `/uploads/drafts/` CSRF POST creates a context-bound manifest; `/uploads/drafts/<uuid>/` GET/POST reads/saves allowlisted form state; `/uploads/drafts/<uuid>/files/` POST stages files; `/uploads/drafts/<uuid>/files/<uuid>/remove/` POST removes one staged file. Add owner-only signed `upload` preview/content/download resources, retaining existing `file-version:{purpose}:{kind}:{pk}:{user}` binding and parent-first authorization. The form query key is `upload_draft`, never a path or arbitrary return URL.

- [ ] Test missing subject/title, invalid office/date, bad neighboring file, exact repeated type/tag/recipient values, explicit unchecked fields, stale new-version form and transfer between POST/GET. Verify CSRF failures, untrusted redirects, double/repeated manifest parameters and cross-user/resource/purpose tokens are refused. Assert refresh GET reuses the saved file and never replays a mutation. Double first submissions with the same signed form nonce must share one manifest and one final outcome.
- [ ] Run R. Expected RED: invalid multipart forms lose file selection and no private stage resource exists.
- [ ] Persist valid files before validating unrelated business fields. A no-JavaScript invalid submission uses PRG to its exact form/anchor with `upload_draft`; rebuild bound errors from allowlisted saved fields and saved-file references. The adapter's `state_fields` is derived only from its existing declared normal form fields, excluding file/password widgets, CSRF, form nonce and action/target/ownership override parameters; collection/office/target context is stored and validated separately. Preserve repeated values via `QueryDict.lists()`. Existing remark/route detail forms recover their own prefixed state without overwriting independent controls. Never persist uncontrolled POST data or browser fake paths. Treat absent checkboxes as false when saving state.
- [ ] Add optional attachments to External creation (currently it has none), creating its draft plus staged attachments atomically through the existing External services and current revision. Keep the existing review-before-send and current office restrictions. All other actions use the explicit adapter list; record-saving/topbar/filter behavior is unchanged.
- [ ] Extend the shared safe preview path to staged files with an owner-authorized private return page, safe fixed return target and no-store/nosniff headers. Add explicit `files/preview/upload/<uuid:pk>/`, content and download UUID routes; preserve every existing integer-version URL. Expired/discarded/consumed stage links do not expose content; existing retained-version links still work. No provider calls or work events on preview.
- [ ] Run R plus `test_file_previews.py`, `test_file_preview_production.py` and relevant create/route/search tests. Expected GREEN: ordinary multipart forms recover saved files with JavaScript disabled, and unauthorised stage reads are denied. Commit `feat(files): recover upload forms after errors and refresh`.

### Task 5: Add progressive upload enhancement without surprise submission

**Files:** Create `static/js/upload-drafts.js`, `tests/js/test_upload_drafts.cjs`; modify the shared upload partial, affected script blocks and safe form-error integration.

**Interfaces consumed:** Task 4 routes and data attributes `data-upload-draft`, `data-upload-workflow`, `data-upload-file-list`, `data-upload-status`. Use the server-returned revision and IDs; never infer ID from filename.

- [ ] Add JS tests for choose → uploading → saved, failed/partial upload retry, serialized stage requests, remove during an in-flight upload, stale response, aborted request and submit waiting for pending uploads. Verify URL replacement preserves unrelated validated query/hash and tab isolation. No data in localStorage/sessionStorage or file input fake rehydration.
- [ ] Run Node's test runner on this file. Expected RED: no progressive recovery controller exists.
- [ ] Implement CSRF-protected upload on selection, concise progress/status/error messages, explicit retry/remove and disabled/waiting final submission until uploads settle. If upload enhancement fails, leave the ordinary multipart submission usable and do not clear unsaved file inputs. Clear an input only after its saved IDs are acknowledged. Serialize revision-bearing writes; reconcile an ambiguous network failure with current server state instead of blindly duplicating the upload. Prevent a late response restoring a removed item.
- [ ] Update only this form's URL using `history.replaceState` when a manifest is saved. Preserve the existing selective-filter policy and history/scroll handlers; no result-search submission on file selection or ordinary field change. Announce status through one polite region, errors through clear linked feedback. Use delegated/once-only initialization after HTMX swaps.
- [ ] Run this file and all `tests/js/*.cjs`. Expected GREEN: uploads recover without duplicate requests or automatic business submission; no-JS baseline still works. Commit `feat(ui): save selected uploads with clear progress`.

### Task 6: Retire excess versions and discard only unsent draft groups

**Files:** Create `apps/core/file_lifecycle.py`, `tests/test_file_retention_services.py`, `tests/test_file_discard.py`; modify Internal/External version services, group state and file access/legacy attachment download views. Add explicit discard POST routes to both Tracking namespaces.

**Interfaces produced:** `retire_excess_versions(group, *, policy_actor=None, limit=5) -> list[FileLifecycleEvent]`; `discard_draft_group(record, group_id, *, user, reason, expected_version, expected_revision=None) -> FileLifecycleEvent`. Services require the caller's actor/parent/manifest/group lock order; External retains its existing record revision contract.

- [ ] Add parameterized Internal/External V1–V7 tests: V6 retains exactly IDs V2–V6; V7 retains V3–V7; identical, failed and stale uploads retire nothing. Assert exact checksums, monotonic numbers and preserved metadata. Test no dispatch versus first dispatch, reopened records, forged IDs, viewers/staff/admin boundaries and stale permissions. Add actual two-thread version/discard/dispatch conflicts.
- [ ] Run R. Expected RED: versions are unlimited and draft-file discard is missing.
- [ ] On successful new-version creation, mark only available excess versions retired in the same transaction; record reason/time/provenance and cleanup intents. Do not physically delete bytes inside that transaction or on rollback. Unknown legacy storage identity is a blocked cleanup issue requiring verified reconciliation, not permission to target today's backend. Current versions can never be retired as excess. Model/queryset/admin guards prevent users rewriting retirement/provenance outside the lifecycle service.
- [ ] Discard the entire selected logical group only when status is Draft **and no dispatch has ever occurred**; require a reason and explicit confirmation. Retain all original version rows and lifecycle audit; invalidate staged target manifests as needed. Do not manufacture work activity, movement timestamps or status changes. After first dispatch, including a later return/reopen, refuse crafted discard requests.
- [ ] Parent-authorize before disclosing retirement/discard. Authorised retired-content requests receive a clear private 410 response; unrelated users get existing refusal behavior. Guard all legacy and version-specific preview/content/download paths. Generating history links must not mint usable links for unavailable content.
- [ ] Run R plus existing version races/admin and preview/security tests. Expected GREEN: at most five available versions after a committed update; audit-only entries cannot be downloaded via old URLs; no failed upload removes content. Commit `feat(files): retain five versions and audit draft discard`.

### Task 7: Make filing, counts and evidence understand audit-only history

**Files:** Modify `apps/documents/file_versions.py`, both filing services, Tracking/Repository row builders, `apps/documents/views.py`, shared file-count annotations and print/export consumers found through `rg`; create `tests/test_file_retention_filing.py`, `tests/test_file_retention_counts.py`. Inspect `apps/core/analytics.py`, External analytics and search consumers before changing any query; change only actual file-population consumers.

**Interfaces produced:** `available_groups(record)` in the lifecycle module and `file_history_rows(parent, *, user, cursor=None)` in new `apps/core/file_history.py`, with current/retained/audit populations kept explicit. Normal rows fetch at most five retained versions per group; audit history uses a separately authorized cursor, 25 entries/page. Add `FileHistoryView` and named history routes for Internal/External groups and Repository roots; resolve parent visibility before listing metadata and never accept arbitrary model/path names.

- [ ] Test V1/V2 retired plus V3–V7 retained filing, retained original-byte/checksum/provenance parity, no attempt to open retired content, corrupt retained blob, unexplained missing row and group discard. Verify one Repository root/logical file, identical mixed-collection document IDs/counts before/after history additions, no obsolete text indexed, no body reads during list rendering and bounded SQL with 1,000 audit entries.
- [ ] Run R. Expected RED: `copy_tracking_history` tries to read retired binaries and normal row builders include discarded/unbounded history.
- [ ] Adapt copying to validate a continuous **metadata** sequence, allow explicit retirement, copy only retained bytes and create audit-only Repository snapshots for retired sources. Preserve source version numbers, current-root matching key, original IDs on Tracking, original byte hashes and timestamps. Missing metadata or unexplained missing retained files still fail safely. Skip discarded groups. Existing filing transactions compensate every newly copied key on failure.
- [ ] Keep counts, printing, filing summaries, default downloads and exports based on non-discarded logical files/current roots. Retired metadata never inflates files/documents or enters extraction. Inspect source-run/chunk and legacy extracted-text references before retirement: revoke any alternate delivery path tied to a retired source; preserve current evidence and unrelated documents. Do not change document filters, totals or collection managers globally.
- [ ] Run R plus current file-version, extraction, search, report/count and filing tests. Expected GREEN: both collection filings reconcile exactly; no partial Repository record/copies on failure; list work remains bounded independently of audit depth. Commit `fix(files): file retained history without reviving retired content`.

### Task 8: Complete retryable cleanup and dry-run maintenance

**Files:** Create `apps/core/storage_references.py`, `apps/core/file_maintenance.py`, `apps/core/management/commands/maintain_file_uploads.py`, `apps/core/management/commands/reconcile_file_retention.py`, `tests/test_file_retention_cleanup.py`, `tests/test_upload_expiry.py`; modify schedules, read-only core administration, storage helpers and Repository purge shared-reference checks.

**Interfaces produced:** `storage_identity(storage) -> str` (nonsecret stable backend/container/endpoint fingerprint); `referenced_storage_key(identity, key, *, excluding=None) -> bool`; `locked_storage_key(identity, key)`; `maintain_file_uploads(*, now=None, limit=100, dry_run=True) -> dict`; `reconcile_file_retention(*, limit=100, dry_run=True) -> dict`. Every call is bounded and reports candidates/retired/removed/shared/blocked/failed separately.

- [ ] Add failures and PostgreSQL concurrency tests: successful delete then acknowledgement failure, delete raises/exists stays true, reversed/repeated jobs, expired draft versus consumption, cleanup versus filing/shared-key reference, config identity change, unknown legacy identity, soft-deleted Repository at day29/day30, restoration and whole-record purge. Assert exact storage keys and untouched Tracking originals, current roots and staged/consumed references.
- [ ] Run R and B where q2 is required. Expected RED: cleanup/expiry/reconciliation intents are not processed.
- [ ] Implement short leased claims with retry/backoff, bounded candidates, post-commit deletion, idempotent missing-key handling and observable last error. All application paths reusing an existing key and cleanup take the same PostgreSQL key guard; new copies use unique immutable keys. Before removal, recheck live roots, retained versions, relevant discarded/retired intent owners and staged references under the parent/key synchronization. Never delete on a backend fingerprint mismatch or by prefix alone.
- [ ] Keep cleanup metadata after crashes; distinguish access retired from physical removal completed. Durable intents handle successful-consume temporary copies, explicit removal, expiry and version retirement. Quotas release only under the manifest lock and cannot be evaded by simultaneous writes. Preserve no-worker operation through the management command; add maintenance to existing `ensure_schedules` only when q2 is enabled.
- [ ] Default both commands to dry-run; require explicit `--apply`, bounded `--limit`, summary output without paths/contents/secrets, and tests that dry-run causes zero SQL/storage/provider mutation. Legacy identity binding requires explicit operator action plus checksum/size verification and a receipt; never infer intent from missing bytes. Existing over-limit reconciliation is never invoked on original data during development. Exclude recovery-window Repository records; restore schedules re-evaluation, not deletion of unrecoverable historical facts.
- [ ] Expose read-only failed/blocked cleanup state and retry through existing administrator permission boundaries. Do not expose other users' private upload manifests. Update whole-document purge to account for shared stage/history keys without weakening its 30-day boundary.
- [ ] Run R, B, deletion suites and migration checks. Expected GREEN: retries converge, shared content survives, and dry-run is inert. Commit `feat(files): reconcile expiry and retirement with safe retries`.

### Task 9: Render compact file cards and consistent activity colors

**Files:** Modify `_file_versions.html`, new upload/audit partials, `tracking.version_views`, Repository file-row builder, `apps/core/colors.py`, template tags, `doctrack.css`, Internal/External timelines, create/review/print templates; create `tests/test_file_cards.py`, `tests/test_activity_presentation.py`.

**Interfaces produced:** File rows expose current item, `V<number> · Current`, format/size/date, up to four available older versions, available/audit counts, server-authorized actions and audit-page URL. `activity_presentation(event) -> {token, icon, label}` handles both workflows with neutral fallback. A separate External activity partial preserves actual/recorded time and manual provenance.

- [ ] Test retained versus retired cards, unavailable history actions, no Repository mutation control even for a system admin, unsent-only discard, long filenames, stable focus IDs and paginated audit state. Assert event-category mapping for every stored enum plus unknown/legacy events, unchanged official status classes and print labels.
- [ ] Run R. Expected RED: old verbose layout lacks concise current badges/discard/audit state and category presentation.
- [ ] Render format badge, wrapped filename, current-version badge, one metadata line and clear Preview/Download/New version actions. Put four older versions in an initially collapsed disclosure; put unavailable audit entries behind a separate page/disclosure. Show a brief confirmation with filename and affected count for draft discard. Preserve signed links, approximate DOCX notices and exact-version Back focus behavior; no thumbnail/binary reads on lists.
- [ ] Add semantic activity tokens: draft gray; endorsement amber; receipt blue; work existing pink; attachment/version violet; return/reopen orange; pending filing existing teal; filed existing green; discard/retirement red accent; access indigo; other neutral. Icons are decorative beside explicit labels; color alone conveys nothing. Do not change stored messages, activity classification used by status/timing, or old history. Respect both themes, print/grayscale, reduced motion and existing focus styling.
- [ ] Run R, templates and all JS tests. Expected GREEN: file meaning and actions are clear at 320px without changing the workflow or exposing disabled mutations. Commit `feat(ui): clarify file versions and tracking actions`.

### Task 10: Default completion to Pending Filing in both workflows

**Files:** Modify `apps/tracking/forms.py`, `apps/tracking/external_forms.py`, `apps/tracking/views.py`, `apps/tracking/external_views.py` and completion templates; create `tests/test_completion_filing_defaults.py`.

**Interfaces:** Internal `archive_now` remains the posted field. Add equivalent `archive_now` to External completion only for currently authorized filing actors; form construction receives `user` and `record`. External completion uses the existing expected revision; optional filing uses the **new revision after completion**, not the submitted stale one.

- [ ] Write both-workflow tests for initially unchecked, omitted, false, checked authorized, forged checked by staff/viewer, revoked permission, duplicate POST and storage/queue failure. Assert exactly one completed case and at most one Repository document; completed-but-unfiled files stay intact and retryable.
- [ ] Run R. Expected RED: Internal starts checked; External has no equivalent authorized option.
- [ ] Set default false, show concise wording `Approve into Repository now` only to existing approvers, and preserve staff completion rules. Server-side rights determine behavior regardless of submitted checkbox. Complete first, then attempt optional authorized filing with fresh rights/state. Filing failure stays Pending Filing and shows a short error with the existing approval retry path. No user is granted approval via form fields.
- [ ] Run R plus both completion/filing workflows and storage-failure tests. Expected GREEN: omitted/unchecked always stops at Pending Filing; checked legitimate filing succeeds once; failure never undoes completion. Commit `fix(tracking): make immediate filing an explicit choice`.

### Task 11: Verify full flows, visual quality and final combined changes

**Files:** Create `scripts/check_file_retention.cjs`, `docs/file-lifecycle.md`, `docs/verification/file-lifecycle-2026-10-23.md`; extend existing version browser tests for new selectors while preserving their assertions. Use disposable fixture scripts/settings only in ignored evidence directories.

- [ ] Add/run real browser journeys, starting with failing assertions before UI repairs if defects are found. Use actual login, CSRF and backend storage-state assertions. Cover missing title then refresh, partial-invalid batch, two independent tabs, upload retry/remove, late response, pending submission, stale permission, staged and older previews, V6/V7 retention, unsent discard/refused active discard, Internal/External filing and no-JavaScript PRG. Verify saved files persist, exact versions appear and counts reconcile, not merely success banners.
- [ ] Start the isolated server through `scripts/start.ps1 -PreviewOnly -Port 8774` after checking that port is free. Verify its settings and exact disposable database; do not interrupt unrelated user servers/tunnels. Scope requests to loopback and block providers. Inspect screenshots at 320,390,768,900,901,1366 in light/dark; check long names, empty/error/history states, keyboard/focus, 200% zoom, loading/error announcements, print and Back/Forward. Distinguish actual native zoom from CSS zoom; record which was used.
- [ ] Run all new modules with R and background-sensitive cases with B; record RED/GREEN and final XML. Run the full project PostgreSQL suite with exact failing/skipped node IDs. Compare known unrelated pending External failures to the baseline; investigate every new failure. Run the full JavaScript suite with Node's `--test tests/js/*.cjs`; PowerShell can expand the file list explicitly through `Get-ChildItem` if needed.
- [ ] Run `.venv/Scripts/ruff.exe check apps tests scripts` and `.venv/Scripts/python.exe scripts/check_templates.py`. Run Django `check`, `check --deploy --fail-level ERROR` using safe placeholder production settings, `makemigrations --check --dry-run`, and actual additive migrations twice against the disposable browser database. Pass the isolated settings/pythonpath explicitly to every Django command. Run existing `selfcheck` and `scripts/smoke_pages.py` under isolated settings; distinguish Django-client smoke from real browser verification. Run maintenance dry-run and explicit apply only against disposable files. Verify no network/provider calls and no original files changed.
- [ ] Record measurements with fixture/hardware/process context: file-list query count/time with 1,000 audit rows, staged-size/quota limits, cleanup batch bound and upload/preview behavior. Prepare representative-user tasks (identify current version, open an older version, recover missing title, complete without filing); leave human results unverified unless people actually do them. Map evidence to all nine 2023 quality characteristics; provide a separate 2011 mapping only if the defense uses that rubric. Do not claim certification or a universally bug-free system.
- [ ] Obtain one fresh independent final review of the combined implementation against spec and this plan, including lock order, storage reference lifetime, private drafts, obsolete source evidence and compatibility. Use the current permitted model/tool configuration. Reproduce important findings with RED regressions, fix narrowly and rerun related checks. Document minor deferred findings and business decisions rather than silently expanding scope.
- [ ] Update operations docs with stage expiry, quotas, five-version permanence, dry-run/apply/retry, legacy identity blocking, no-worker maintenance, recovery boundary and rollout/rollback limitations. Tie the report to the final implementation SHA and exact fresh commands/outcomes; prior logs remain historical. Inspect `git diff --check` and task-only diff/status without printing `.env.example`. Commit `docs(files): record lifecycle verification and recovery operations`.

## Completion and handoff

All tasks require watched failing regressions followed by passing focused checks; the final report must explicitly retain unrelated known failures and unperformed external/device/screen-reader/human checks. Application changes remain local. Do not run historical pruning, original database migrations, push or deployment as a side effect of finishing this plan.

Self-review: spec sections 4–6 map to Tasks 1–8; file cards/activity to Task 9; completion to Task 10; quality/rollout to Task 11. Shared interfaces and lock order are defined once and consumed by adapters; physical cleanup is deferred until reference checks exist. No new provider, pending custody refactor or general Repository file-edit permission is introduced.
