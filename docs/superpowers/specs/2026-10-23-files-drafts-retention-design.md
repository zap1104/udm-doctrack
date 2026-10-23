# DocTrack: durable uploads, draft removal and five-version file history

Date: 23 October 2026. Status: written design approved by the user on 23 October 2026. The five-version binary-retention policy is confirmed. The detailed implementation plan is at `docs/superpowers/plans/2026-10-23-files-drafts-retention.md`; original-data migration/pruning, pushing and deployment remain separate operations.

**ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.**

## 1. Intended outcome and boundaries

Make files understandable at a glance and prevent repeated file selection after a form error or refresh. Apply the same behavior to Internal and External Tracking and their separate Repository collections.

Confirmed requirements:

- Unsent draft attachments can be discarded by an authorized editor.
- Each logical file retains its current file and four older file versions. Older contents are permanently removed, with audit entries retained. This is binary retention, not merely hiding old versions.
- Successfully uploaded files survive missing fields, validation failures and refreshes.
- Current version, available older versions, preview and download actions are visually clear with little explanatory text.
- Tracking actions receive consistent colors and recognizable labels/icons.
- Approve into the Document Repository right away defaults to unchecked.
- Define and verify quality requirements aligned with ISO/IEC 25010.

Preserve permissions, collection separation, numbering, office scope, routing, timers, current-version indexing, original file formats and existing UI work. Repository file contents remain read-only. Keep the existing whole-document deletion review and 30-day recovery flow. No PDF conversion, new provider integration, paid service or live OCR is required.

Implementation and verification will remain local. Use disposable PostgreSQL data and temporary storage. Do not migrate the original database, prune its files, push or deploy during this work. Leave unrelated local configuration edits and the pending External custody refactor untouched.

## 2. Current source-confirmed behavior

| Source | Current behavior | Required adjustment |
| --- | --- | --- |
| `apps/tracking/views.py`, `RecordCreateView.post` and `_edit_draft` | Whole-form validation precedes attachment persistence. Re-rendering invalid forms loses the browser file selection. | Save valid files privately before publishing the requested mutation; recover their references on the next GET. |
| `apps/documents/views.py`, `UploadView.post`; `external_views.ExternalUploadView.post` | Invalid initial upload forms return without saving files. Once ingested, Repository metadata review already has persisted files. | Add recovery for the initial form without creating incomplete Repository documents. Preserve the existing metadata-review behavior. |
| `apps/tracking/file_versions.py`, `_new_version` | The record/group are serialized, stale current-version submissions and identical uploads are rejected, and versions grow monotonically. | Keep these checks and retire content beyond the newest five only after a new version commits. |
| `apps/documents/file_versions.py`, `copy_tracking_history` | Filing expects a complete sequence starting at Version 1 and copies all historical binaries. | Recognize audited retired versions and copy only retained binaries, preserving original version numbers and audit metadata. |
| `templates/partials/_file_versions.html`; `tracking.version_views` | Current and historical files share a text-heavy list; all historical rows are prefetched. | Compact current-file cards and a small retained-history disclosure; fetch audit-only history separately. |
| `apps/tracking/forms.py`, `CompleteForm.archive_now` | The BooleanField currently has `initial=True`. | Set the default to false; unchecked and omitted values must not file the record. |
| `apps/tracking/views.py`, `CompleteRecordView` | Completion is recorded first; immediate filing requires existing approval authority. Filing failure leaves the record awaiting filing. | Preserve this recovery behavior. |
| `apps/tracking/external_views.py`, `ExternalActionView`; `ExternalCompleteForm` | External completion and filing are separate actions. | Offer an equivalent unchecked option only to an actor who already has filing authority, keeping revision checks. |
| Tracking timeline partials and `apps/core/colors.py` | Status colors are centralized, but activity styling groups many events into generic or muted entries. | Add a separate presentation mapping for activity categories without changing status calculation. |
| `apps/core/storage.py`; `apps/documents/tasks.py`, `purge_deleted_documents` | Upload rollback compensates new objects. Repository purge deduplicates keys and protects Tracking/shared objects. | Extend reference checks to retained versions and temporary uploads; add durable cleanup intent for retirement and expiry. |

These are source findings. New browser, storage-provider and accessibility verification has not yet been performed for this change.

## 3. Selected approach and alternatives

Use a shared, private upload-draft service and a common file-card component, with workflow-specific authorization and finalization adapters.

Browser-only file caching would depend on local quotas and leave document data on shared computers; it would also provide a weaker no-JavaScript path. Creating business records as soon as a file is selected would change draft/document populations and could expose incomplete entries. Private upload drafts keep unfinished files separate from numbered records, Repository totals and OCR jobs.

The shared service owns temporary file persistence, manifests, expiry and consumption. Existing Tracking/Repository services continue owning business validation and permissions. The retention service owns audit-only retirement and exact-object cleanup. UI components consume these services rather than implementing their own file authority.

## 4. Durable upload drafts

### Identity and lifecycle

Introduce an upload-draft manifest and staged-file rows inside the existing apps. A manifest has an opaque ID, owner, owner-office snapshot, workflow/collection, optional target and file-group binding, an expiry time, revision, allowlisted form state and lifecycle state. A staged file records a server-generated private key, validated original name/type, size and checksum.

- A manifest belongs to one user and one form instance. Different tabs have different IDs.
- Create manifests through authorized, CSRF-protected writes. The first successful enhanced upload updates the current form URL with its opaque manifest ID without navigating. Subsequent GETs, refreshes and Back/Forward navigation can recover that same form instance while preserving validated target/office context.
- Normal states are open, consumed, discarded and expired. A file is shown as saved only after its bytes and database entry have committed.
- The default lifetime is 24 hours from the latest successful explicit save or upload. Read-only GETs do not extend it.
- Do not store file bytes in browser local storage, session cookies or form-hidden fields. Hidden/URL identifiers refer only to server-controlled objects; they never accept arbitrary paths.
- Recheck active account, role, original office context and target action permissions before writing, reading, discarding or consuming. A transfer, suspension or revoked target permission cannot preserve edit access through an old manifest.
- An administrator does not gain access to another user's unsubmitted upload draft through a guessed identifier.

### User flow

With JavaScript, selecting files begins private upload and shows progress, then a concise Saved state with Preview and Remove. Submission waits for required uploads to finish. Failed uploads remain visibly unsaved with retry/removal actions.

Without JavaScript, the ordinary multipart submission saves individually valid files before checking unrelated fields. Validation failures use Post/Redirect/Get and reconstruct the form, field errors and saved-file cards from the private manifest. A subsequent refresh or corrected submission reuses these files. Client filenames are not used to repopulate a native file input or infer file identity.

Persist only an allowlist of normal form fields, not CSRF tokens, passwords or arbitrary POST data. Preserve repeated multi-select values, explicit unchecked choices and per-request office context. Refreshing the form must not restore a previously checked filing option over the current unchecked value.

Scope includes Internal/External creation, existing-draft editing, supporting-file uploads, endorsement/remark forms carrying attachments, new-version forms and both initial Repository upload forms. Existing Repository metadata review continues referencing the already persisted document. Target-bound actions still require receipt/custody permissions; an upload draft is not an editing bypass.

### Finalization and limits

- Existing file validation, MIME checks and per-file size limits apply before staging. Valid neighboring files remain recoverable if another file is invalid.
- Count staged and newly submitted files together against the existing per-form file limit. A version form accepts one new file.
- Proposed bounded defaults: five open manifests per user; total staged bytes per user no more than `MAX_UPLOAD_FILES * MAX_UPLOAD_MB`; current defaults therefore allow 250 MB. Configuration must validate positive bounds.
- Use immutable storage keys and checksums. Finalization may transfer the existing private object's ownership/reference into the permanent attachment rather than duplicating large bytes. Do not depend on an object-store rename being atomic.
- Lock and revalidate actor, target record/document, manifest and group in one documented order. Manifest consumption and the target mutation commit together. A stale or replayed submission cannot create duplicate files or business records.
- Retain staged files if validation, copying or the business mutation fails. Expiry/discard must serialize with consumption. Cleanup must never delete a consumed object's permanent reference.
- Staging never runs OCR, allocates tracking numbers, updates work status/timers, sends workflow notices or changes Dashboard/Repository counts. Existing extraction runs only after successful finalization under its current consent and actor checks.
- Reuse existing q2/management-command infrastructure for bounded expiry cleanup, with dry-run and retry support. With no worker, saved files remain private and can be cleaned through the command. Do not configure a blanket storage-prefix lifecycle rule that could delete a subsequently claimed file.

## 5. Draft-file discard

Provide Remove on staged files and Discard on a persisted unsent Tracking draft's logical file card. Show the filename in a compact inline confirmation with Cancel.

On persisted drafts, check the same record-editing authority used by the draft form and confirm that no dispatch has occurred. Lock the record and file group, then mark the group discarded and record who acted, when, which versions were affected and why. Discard removes the whole draft file group, not an arbitrary version inside active Tracking. The confirmation explicitly identifies the group and the number of draft versions being discarded.

After first endorsement, this control is unavailable and crafted requests are rejected. Active Tracking supports Add file and New version under existing rules. Filed Repository contents expose no user-driven addition, replacement or individual-version deletion. Discard must not manufacture a work update or rewrite an earlier routing/activity entry.

## 6. Five-version binary retention

### Policy and audit metadata

The limit is per logical file, not per document and not five attachments in total. With Versions 1–6 uploaded successfully, V6 is current, V2–V5 are available history, and V1 is audit-only. Uploading V7 retires V2. Numbers, attachment IDs, known timestamps and checksums are never reset or reused.

Retain immutable metadata for retired versions: group/parent identity, number, filename, type, size, checksum, original uploader/office snapshots, upload time, change summary, retirement time, reason and policy actor. Missing historical provenance stays unknown. Audit metadata is not counted as an available file version.

Use explicit retirement state on version records and a durable storage-cleanup intent. Do not infer that every missing file was intentionally retired. Download/preview authorization checks the parent first; an authorized request for retired content receives a clear unavailable response. Neither an old signed URL nor a direct storage-path parameter restores access.

### Ordering, cleanup and filing

- Validate and save a new version using existing record-before-group locking, expected-version and External revision checks.
- Only after its transaction commits may old binaries be removed. If Version 6 fails validation or storage, Versions 1–5 remain intact and available.
- Retire old content access transactionally and save cleanup intent with the same commit. Physical deletion is idempotent and retryable. A storage failure leaves a visible administrative cleanup issue, not a false assertion that deletion completed.
- Delete only exact unshared keys. Recheck current roots, retained Tracking/External versions, Repository versions, and staged/claimed references. Distinguish storage backend/container identity so configuration changes cannot redirect a cleanup job to another object store.
- Known previews, chunks and derived extraction data tied to retired versions must not remain downloadable/searchable as an alternative copy. Preserve current-version indexes and invalidate affected source evidence when necessary; do not erase unrelated documents containing similar text.
- Adapt filing validation to recognize explicitly retired audit entries. Preserve the current version number, copy at most five retained binaries per logical file, and retain audit-only history without attempting to open removed files. Unexplained gaps or corrupt retained content still fail filing safely.
- Preserve existing one-current-root-per-logical-file counts. History never inflates file counts, totals, reports, search or extraction inputs.
- Exclude discarded groups from normal lists, file-count annotations, print views, exports and filing. Ordinary model managers still expose audit rows to authorized jobs/history code; presentation filtering must not make cleanup or audit data disappear globally.
- Repository content locking still applies. Automated retirement beyond this explicit policy is a controlled storage operation, not a general file-edit/delete permission.

### Existing data and recovery boundary

Additive schema migrations initialize existing versions as retained. They do not delete bytes or move existing files. A separate dry-run reconciliation reports groups above five, candidate versions, shared objects and inconsistencies. Applying that report enforces the policy on eligible existing groups, including completed Tracking records and active Repository records, after implementation is authorized and rollout is explicitly chosen; it will not run against the original data during development.

Do not shorten the existing 30-day recovery window for a whole soft-deleted Repository record. Exclude such records from historical backfill pruning while recovery is open; re-evaluate retention after restore or use the established whole-record purge at expiry. Restoring a document cannot recover a version already retired before that deletion. Backups and copies exported outside DocTrack continue following their own policies.

## 7. File cards and accessible action presentation

Default card content:

```text
[PDF]  Signed request.pdf                 [V6 · Current]
       245 KB · 23 Oct
       [Preview] [Download] [New version]
       Older versions (4) ▸
```

New version appears only where authorized. A draft card additionally offers Discard. Saved temporary uploads show Saved and Remove; uploading and failed states are distinct. A Repository card has Preview/Download and history only.

An expanded history shows compact newest-first rows with version number, format, short change summary and actions. Keep extended uploader/checksum/audit details behind a separate disclosure. Retired versions have no download action; their metadata remains in a separately paginated audit view. An older preview prominently identifies its version and the current version number.

Do not generate thumbnails or load document binaries just to render the list. Fetch retained versions and counts efficiently; do not prefetch unlimited audit-only history. Long filenames wrap, action rows reflow on phones and focus order follows visual order. Keep preview behavior, approximate DOCX notices, signed links and format fallbacks from the existing viewer.

### Tracking action mapping

Use shared presentation tokens, with labels/icons in addition to color. Keep official status colors and all status/timer calculations unchanged.

| Activity category | Presentation |
| --- | --- |
| Draft created | Gray, document icon |
| Endorsed / awaiting receipt | Amber, outward arrow |
| Receipt confirmed | Blue, receipt/check icon |
| Remark / work update | Existing process pink, message icon |
| File attached / new version | Violet, file-plus/version icon |
| Returned / reopened | Orange, return arrow |
| Completed, awaiting filing | Existing pending-filing teal, completion icon |
| Filed in Repository | Existing completed green, archive icon |
| Draft discarded / content retired | Red accent, removal icon; routine retention details remain in audit disclosure |
| Access changed | Indigo, shield/key icon |
| Opened / printed / other historical events | Neutral, event-specific icon and label |

Share the mapping between Internal and External timelines; unknown events fall back safely to a neutral labeled item. Preserve manual External provenance and actual/recorded timestamps. Historical messages remain unchanged. Print must remain understandable in grayscale.

## 8. Completion and filing option

Set Internal `CompleteForm.archive_now` to false initially. Missing/unchecked values complete the work into Pending Filing. Show the optional filing checkbox only to actors who already qualify for approval.

Offer the equivalent unchecked option on External completion using its existing administrative authority and revision checks. Staff completion remains available where already permitted; a forged checkbox must never grant filing authority. Recheck rights at filing time.

Preserve the established recovery boundary: completion succeeds first; checked authorized filing is then attempted. Successful filing creates exactly one Repository record. If filing fails, keep the case completed and awaiting filing, retain its files and show a short error with a retry path. Repeated completion/submission cannot create duplicate Repository entries.

## 9. Quality requirements and verification

Use ISO/IEC 25010:2023 as the product-quality framework. The following are DocTrack acceptance measures mapped to its characteristics, not claims that the standard prescribes five versions or certifies the application. If the capstone evaluation uses the 2011 rubric, document the edition and provide a separate mapping rather than silently mixing editions.

| Characteristic | Project acceptance evidence |
| --- | --- |
| Functional suitability | Exact retained-version IDs after V6/V7, unchanged current bytes/checksums, correct one-file counts, working preview/download, unchecked filing behavior in both workflows. |
| Performance efficiency | Enforced upload quotas; bounded cleanup batches; no binary reads for list rendering; retained-history queries remain bounded as audit history grows. Record fixture size/hardware and measured times. |
| Compatibility | Ordinary GET/POST and no-JavaScript recovery; existing signed URLs for retained versions; local-storage simulations plus mocked Azure/R2 contracts; distinguish live-provider checks. |
| Interaction capability | Clear current-version badge and history disclosure, concise errors/progress, keyboard controls/focus, readable labels and both themes. Target WCAG 2.2 AA checks for color, contrast, errors, reflow and status messages. |
| Reliability | Missing title and refresh preserve valid uploads; simultaneous upload/discard/expiry/filing cases; rollback and retry assertions for database and storage failures. |
| Security | Own-user staging, current role/office/custody checks, forged and cross-collection identifiers, signed preview/download scope, CSRF, upload validation and private/no-store responses. |
| Maintainability | Shared staging, retention and presentation services with explicit adapters; regression tests; additive migrations and documented recovery commands. |
| Flexibility | Configured private storage backends, bounded configurable staging lifetime/quotas, responsive layouts and unchanged accepted original formats. |
| Safety | Save before retiring; protect current/shared content; clear removal confirmation; no accidental immediate filing; audit-only retirement is identifiable. Evaluate actual application risks without claiming a safety-critical certification. |

User understanding requires human evidence as well as automated checks. Prepare a short defense checklist asking representative users to identify the current version, open an older version, recover a missing-title submission and complete without filing. Record observed completion times/errors and participant context; do not report those tasks as passed unless people actually perform them.

### Required regression groups

1. Retain V1–V5; V6 retires exactly V1; V7 retires exactly V2; group/file counts remain unchanged. Identical, stale and failed uploads retire nothing. Parallel same-version uploads have one success and one conflict.
2. Filing retained V3–V7 preserves those numbers and succeeds with audit-only V1/V2. Corrupt retained files and unexplained history gaps fail without a partial Repository document or orphaned copies.
3. Missing title/subject, invalid offices/dates and one bad neighboring file preserve valid staged files. Refresh, no-JavaScript POST recovery, multiple tabs and repeated submissions have saved-state assertions.
4. Expiry/discard races with finalization; quota races; storage write/delete failures and retries; consumed objects and shared keys survive unrelated cleanup.
5. Draft discard before/after dispatch, stale role/custody, viewers, office admins, system admins, transferred/suspended users, forged IDs, direct URLs and signed-link replay. No Repository file-edit bypass.
6. Both completion forms start unchecked. Omitted, false, checked-authorized and forged/stale-permission submissions produce the correct status, authority checks and exactly-once filing result.
7. Current-version OCR/indexing remains correct; no staging/discard operation changes active work timers, record numbering or collection-specific totals.
8. Existing deletion recovery, restoration, expiry purge and Tracking-original/shared-object preservation remain intact across retained and retired metadata.
9. Real browser journeys with login/CSRF at320,390,768,900,901,1366 widths, both themes, keyboard, refresh, delayed/failed upload and no-JavaScript forms. Distinguish CSS/native zoom, screen readers and physical devices; list unperformed checks explicitly.
10. Run relevant/full PostgreSQL and background regressions, JavaScript tests, lint, template and Django checks, migration drift and disposable migration checks. Record existing unrelated failures rather than hiding them. Obtain one independent final review under the chosen execution workflow.

## 10. Rollout and sources

Order implementation so shared upload persistence and retention invariants are verified before wiring the compact UI and completion controls. Run backfill/pruning commands in dry-run mode first. Keep physical cleanup failures observable and retryable through existing administration/job infrastructure. Rollback may disable staging enhancement or future policy application, but cannot restore bytes already permanently retired; audit metadata must remain intact.

No original database/storage cleanup, push or deployment is part of implementation approval. Those are separate operations after a verified rollout report.

Primary quality references:

- [ISO/IEC 25010:2023, product quality model](https://www.iso.org/standard/78176.html).
- [ISO/IEC 25010:2011, previous edition and replacement information](https://www.iso.org/standard/35733.html).
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/), particularly1.4.1,1.4.3,1.4.10,1.4.11,2.1.1,2.4.7,2.4.11,2.5.8,3.3.1,3.3.3 and4.1.3.

Written-spec approval is recorded. Next gate: review of the detailed implementation plan under the previously selected Native execution approach. No product code has been changed for this design.
