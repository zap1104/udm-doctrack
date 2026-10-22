# DocTrack File Versions Implementation Plan

> **For agentic workers:** Use the approved in-chat specification, test-driven development and independent review. Execution has already been explicitly requested; do not ask for another approval.

**Goal:** Preserve immutable file versions during Tracking, freeze filed contents, and preview originals without PDF conversion.

**Architecture:** Existing Attachment and ExternalAttachment rows become version rows in separate logical groups. DocumentFile remains the current-file population and DocumentFileVersion stores frozen history. Shared preview and binary endpoints authorize the precise version through its parent.

**Tech Stack:** Existing Django/PostgreSQL/Bootstrap/HTMX/django-q2/private storage; self-hosted PDF.js, docx-preview and JSZip.

**Spec:** User-approved “DocTrack File Versions, Read-Only Repository Files and Previews” in this conversation, including original formats, no conversion, editable metadata, downloads and separate collections.

## Global constraints

- ONLY TRY TO FIX WHAT IS NEEDED AND DO NOT BREAK ANYTHING ELSE.
- Preserve current dirty UI and pending External custody work. Work in place on the current feature branch. No commit, push, deployment or migration of the original database.
- Use only disposable PostgreSQL test/browser data and temporary local storage. Simulate external OCR, email and remote storage.
- Existing IDs, paths, numbering, statuses and historical timestamps remain valid; no guessing version relationships from filenames.

## Review focus

- Repeated names represent independent initial files, never inferred history.
- Version upload and filing race under record/group locks; stale bases never overwrite.
- Older file text must not inflate current-file counts, page totals or default search.
- Repository content is locked even to admins and crafted legacy endpoints; metadata/extraction remain functional.
- Preview renderers cannot execute embedded content or fetch external resources; current permissions are checked on every bytes request.

## Tasks

### 1. Version models, mutation services and filing
- [x] Add failing regressions for numbered groups, original bytes, stale versions, immutable repository content and complete filed history.
- [x] Add logical groups, attachment version metadata, DocumentFileVersion and additive backfills. Every legacy row starts independently at Version 1.
- [x] Implement transactional version append with existing permissions/status effects, mandatory summary and duplicate-current rejection.
- [x] File one DocumentFile per group plus all immutable history snapshots; index/extract only the current roots. Handle storage rollback and purge of all snapshots.
- [x] Support one initial batch in ingest_upload, then prohibit add_file_to_document. Make file-bearing admin controls read-only.
- [x] Run focused PostgreSQL regressions and independently review the task.

### 2. Upload actions, current-file UI and history
- [x] Add failing endpoint/UI/count tests, then explicit version POST actions in both Tracking namespaces.
- [x] Keep Add file separate from Upload new version. Render current groups, collapsed histories and exact-version preview/download links.
- [x] Accept all direct repository initial files in the first submission; preserve scan and metadata review. Remove repository append controls and deny legacy POSTs.
- [x] Update logical file counts, print output, filing summaries and exports without changing document populations.

### 3. Private previews
- [x] Add failing tests for parent permissions, token purpose/user/version, inactive records, malformed inputs and blocked remote content.
- [x] Add shared exact-version preview/download/bytes routes. Keep legacy download endpoints unchanged.
- [x] Pin/self-host PDF.js, docx-preview, JSZip and license information. Disable PDF scripting and DOCX altChunks/external relationships.
- [x] Add sandboxed formatted DOCX, PDF page/text rendering, image zoom, bounded TXT/CSV/XLSX previews and honest unsupported fallbacks.
- [x] Verify keyboard/focus/no-JS download behavior and that previews cause no work/status/extraction mutations.

### 4. Combined verification and delivery
- [x] Run full PostgreSQL and JavaScript suites, background regressions, lint, templates, Django checks and isolated migration checks. Preserve the raw 49-failure broad result, the 48 exact pending External baseline matches, and the later focused parser/receipt-test corrections in the report; do not claim a clean final full-suite run.
- [x] Exercise real login/CSRF/browser workflows, both themes, six requested widths, keyboard, zoom and no-JS fallbacks.
- [x] Obtain independent final review, resolve confirmed findings and finish the report with the full-suite outcome, subsequent focused checks and independently reproduced pending External baseline failures.
