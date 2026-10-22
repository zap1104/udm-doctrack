# Files, versions and previews

DocTrack preserves uploaded file formats. It does not convert documents or create or verify signatures. Upload a copy signed outside DocTrack when it is ready.

## While a slip is in Tracking

- Use the existing attachment controls to **add a separate file**. Each starts at Version 1, even when another file has the same name.
- Open **Upload new version** beside the file you want to update. Select the revised file and explain what changed. Supported format changes, such as DOCX to a signed PDF, are allowed.
- The latest version stays visible. **Version history** contains earlier copies with their uploader, known office and timestamp, plus Preview and Download links.
- Identical current content is refused. If someone updated or filed the record first, reload it and check the current version before retrying. No previous version is overwritten.
- Existing custody and role restrictions apply. Receipt waits block work. Completed records awaiting filing must return to Tracking through the existing action before files can change. Filed records cannot accept more versions.

Internal and External Tracking keep separate records, permissions and identifiers. This feature does not change the pending External custody-control design.

## In the Repository

Filing copies the complete history and preserves the final original file for each logical group. File counts, page totals and text indexing use current files only. Older content remains in history.

Repository file contents are read-only for every role, including system administrators. Initial direct uploads and scans can include all supporting files in the first submission. After that submission, additional files, replacements and individual version deletion are blocked. Authorized metadata correction, extraction retries, previews and downloads remain available.

Whole-record deletion review, 30-day recovery and final purge continue to apply. Repository history follows its document. Purging repository copies does not remove original Tracking attachments or storage objects still referenced elsewhere.

## Preview support

| Format | Preview |
| --- | --- |
| PDF | PDF.js page navigation, zoom and selectable text, when present |
| DOCX | Approximate formatted text, tables and raster images in a scriptless sandbox |
| JPEG / PNG | Image with zoom |
| TXT / CSV | Escaped text or values, with truncation disclosed |
| XLSX | Bounded stored worksheet values; chartsheets are excluded; formulas are not executed or recalculated |
| Other accepted formats | Preview unavailable; original download remains available |

Previews require current permission to their parent record. Links expire and are bound to the exact version, user and request purpose. A stored link does not preserve revoked access. Preview failures do not change files, statuses, timers or extraction results. DOCX preview copies discard active content, external relationships and unsupported embedded objects while original downloads remain byte-for-byte originals.

Without JavaScript, version history and authorized downloads still work. Text and spreadsheet previews render on the server. PDF, image and formatted DOCX previews need JavaScript. Returning from a preview opens and focuses its exact version entry.

Preview libraries and licenses are self-hosted under `static/vendor/file-preview`; the manifest records pinned versions and asset hashes. No new API keys are required.

## Enabling the schema changes

Two additive migrations initialize every existing Tracking attachment as its own Version 1 and every existing repository file with a Version 1 history snapshot. IDs, download URLs, storage paths and known dates remain unchanged. Matching filenames are never merged, and missing historical office information stays blank.

The implementation request excludes migrating the original database. Before enabling this feature in that database, back up PostgreSQL and private storage, review the migration plan, then apply migrations through the normal release process. Existing repository files become content-locked. Run `collectstatic` so the pinned preview assets are available in deployed environments.

See `docs/verification/file-versions-2026-10-22.md` for the source revision, fresh verification and limitations.
