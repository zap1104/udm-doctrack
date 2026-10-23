# File uploads, versions and recovery

This guide covers Internal and External Tracking and both Repository collections. Original formats are preserved. DocTrack does not convert files to PDF, create signatures or verify the authenticity of an uploaded signature.

## Everyday use

- **Add file** starts a separate file at V1. Matching filenames never merge histories.
- **Upload new version** updates the selected file. Describe the change, for example, “Added the director’s signature.” A signed PDF may follow a DOCX in the same history.
- The current version and four older versions remain available. Uploading V6 retires V1; V7 retires V2. Retired content is permanently removed by maintenance, while its filename, checksum, version number and audit information remain.
- Identical content, stale submissions and failed uploads do not advance a version or retire another file.
- **Discard file** is available only on an unsent draft with no dispatch history. It requires a reason and confirmation. Returning an already dispatched record to Draft does not make its files discardable.
- A file can change only while Tracking permissions and custody rules permit it. Completed work must use the existing return-to-tracking action before another file update.

The current version has a **Vn · Current** badge. **Older versions** contains up to four available copies. **File audit history** contains the remaining metadata and policy receipts in pages of 25 entries. Unavailable content has no Preview or Download action.

Previews and downloads do not start work or change turnaround. DOCX previews are approximate; download the original when exact layout or signature inspection matters. Unsupported previews offer the original download.

## Saved uploads

With JavaScript, choosing a file saves it privately before the business form is submitted. Wait for **Saved**. A missing subject, invalid date or other validation error keeps valid neighboring files. Refreshing the form restores the saved file list, not the browser’s native file chooser.

Without JavaScript, files are saved on the first form submission. Validation redirects back to the same recoverable form. A refresh after that submission retains the saved files.

Separate tabs and separate inline forms have their own opaque saved-form identities. Saved previews return to the appropriate form. An uncertain upload/removal blocks submission until reconciliation or retry confirms its state. A file shown as removed must not silently become a business attachment.

Use **Saved upload forms** to resume or discard unfinished work. These files are private to the original user and office context, including against other administrators. Losing current account, office or custody permission can prevent resuming a form. Saved uploads never count as Tracking slips or Repository documents.

Default limits:

| Setting | Default | Meaning |
| --- | --- | --- |
| `UPLOAD_DRAFT_HOURS` | 24 | Lifetime after the latest successful staging or form save. Reading alone does not extend it. |
| `UPLOAD_DRAFT_MAX_OPEN` | 5 | Open saved forms per user. |
| `MAX_UPLOAD_MB` | 25 | Existing per-file upload limit. |
| `MAX_UPLOAD_FILES` | 10 | Existing per-form limit; version updates accept exactly one file. |
| `UPLOAD_DRAFT_MAX_BYTES` | 250 MiB by default | Total stored temporary bytes per user, including removed/consumed copies until cleanup confirms removal. |

Expiry and discard close a saved form and queue exact-object cleanup. They do not create a record, send a document, trigger OCR or begin processing time.

## Completion and Repository files

**Approve into Repository now** starts unchecked in both Tracking workflows. Omitted or unchecked means Completed - Pending Filing. The option is shown only to existing authorized approvers; a crafted checkbox grants no permission.

Completion commits first. If optional filing fails, the case remains awaiting filing with its files intact and a retry path. Successful filing creates one Repository document, one current file per logical Tracking file, and the retained history with its original version numbers and provenance.

Repository file contents are locked after the initial upload, scan or filing submission. Existing Repository files are also content-locked. Metadata correction and permitted extraction remain separate operations. Current file counts and indexing use current versions, not every historical copy.

Whole-document deletion review keeps the existing 30-day recovery process, including file history. Restoration cannot recover a version retired before deletion. Purging Repository copies preserves original Tracking attachments. Backups and exported copies have their own retention policies.

## Maintenance operations

Use the correct deployment environment and private storage configuration before running these commands. These examples describe operator actions; implementation verification did not apply them to the original database or storage.

```powershell
# Read-only reports, bounded to 100 candidates by default.
.venv/Scripts/python.exe manage.py maintain_file_uploads --limit 100
.venv/Scripts/python.exe manage.py reconcile_file_retention --limit 100

# Explicitly apply one reviewed batch.
.venv/Scripts/python.exe manage.py maintain_file_uploads --apply --limit 100
.venv/Scripts/python.exe manage.py reconcile_file_retention --apply --limit 100
```

`maintain_file_uploads` expires saved forms and processes durable cleanup intents. `reconcile_file_retention` finds eligible groups with more than five available versions; it does not run as a migration. Batch size must be 1 through 1,000. Both commands default to dry-run and report candidates, expired/retired items, removals, shared objects, blocked intents and failures without printing file contents or credentials.

Cleanup uses a recorded backend fingerprint and exact storage key, rechecks live/shared references under locks, and confirms removal before releasing temporary-byte quota. New binaries use unique keys. Failed deletes retain an intent with a lease, attempt count, error and retry time. Successful repeated jobs are harmless. Access retirement and physical removal are distinct states.

Unknown or no-longer-configured backends are **blocked**, not guessed. Blocked intents do not monopolize later cleanup batches. Administrators can inspect authorized cleanup state and retry after correcting configuration. A legacy version with no known backend requires explicit checksum-and-size verification:

```powershell
# Inspect one exact version first. Substitute a verified version ID.
.venv/Scripts/python.exe manage.py reconcile_file_retention --bind-legacy-kind tracking --bind-legacy-id 123
# Apply only after reviewing the verification result.
.venv/Scripts/python.exe manage.py reconcile_file_retention --bind-legacy-kind tracking --bind-legacy-id 123 --apply
```

Valid kinds are `tracking`, `external` and `repository`. Binding preserves an audit receipt. A mismatch or missing content does not authorize deletion. Existing shared cleanup receipts are linked without discarding evidence.

When background jobs are enabled, the existing schedule setup registers daily maintenance. Without a worker, an operator must run the bounded command regularly. Monitor retries, blocked intents and quota pressure; do not delete a storage prefix manually to clear them.

## Rollout and rollback

1. Back up the database and private storage using the existing deployment procedure.
2. Apply additive migrations in a test environment first. The new path migration affects future history writes; it does not move existing files.
3. Verify current uploads, previews, filing, signed downloads and restoration on that environment.
4. Review reconciliation dry-run output and backend bindings before choosing a pruning rollout.
5. Enable the maintenance schedule or operator runbook and inspect its first batches.

Schema rollout does not itself prune the original historical data. Disabling the enhancement or future maintenance cannot restore content already permanently retired. Preserve audit metadata when rolling back application code.

See the [verification report](verification/file-lifecycle-2026-10-23.md) for tested behavior, known unrelated failures and checks that remain unperformed. ISO/IEC 25010:2023 is used as a quality framework, not a certification or a guarantee that every possible defect is absent.
