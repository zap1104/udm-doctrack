# UDM DocTrack Operations Runbook

This runbook is for the person responsible for keeping the Document Tracking and Management System available after deployment. It assumes PostgreSQL 14 or newer and a storage backend configured through `STORAGE_BACKEND`. The database and the document files are one recovery set: restoring only one of them produces an archive whose records and files no longer agree.

## Production checks before handover

Set a random `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False`, a specific `DJANGO_ALLOWED_HOSTS` value, `ENABLE_CSP=True`, `SECURE_SSL_REDIRECT=True`, and a durable `STORAGE_BACKEND` such as `s3` or `azure`. Run `python manage.py check --deploy --fail-level ERROR`; the deployment must not be handed over while it reports an error. Set `ENABLE_BACKGROUND_TASKS=True` and run `python manage.py qcluster` as a separate worker process. Point the platform probe at `/healthz/`. The default probe checks the database, cache table, migrations, and storage. `/healthz/?deep=1` additionally checks for a recent successful django-q2 task.

## What to back up

Back up **both** the PostgreSQL database and the media/object store. PostgreSQL contains tracking history, audit rows, document metadata, search columns, notification state, and references to files. The object store contains the file bytes. Back up the Django secret separately in the university secret manager; without the same secret, signed links and sessions issued before restoration cannot be verified.

A daily database backup and a daily object-store versioned backup are the minimum for a small office. Keep daily copies for 30 days, monthly copies for 12 months, and one quarterly restore-tested copy for the retention period required by the university. Store at least one copy outside the production account or region. Backups contain personal data and must have the same access controls as production.

## PostgreSQL backup commands

For a local installation:

```bash
pg_dump --format=custom --file=udm-doctrack-$(date +%Y%m%d-%H%M).dump \
  --host=127.0.0.1 --port=5432 --username=udm --dbname=udm_doctrack
```

For Render, copy the internal or external PostgreSQL connection string from the Render dashboard and pass it as `DATABASE_URL`; do not put it in shell history if the workstation is shared:

```bash
read -s DATABASE_URL
pg_dump --format=custom --file=udm-doctrack-$(date +%Y%m%d-%H%M).dump "$DATABASE_URL"
unset DATABASE_URL
```

After the command, record the dump size, SHA-256 checksum, timestamp, database identifier, and the storage location. A successful exit code is necessary but not sufficient; the restore drill below is the proof that the file can be used.

## Object-store backup

For Cloudflare R2 or another S3-compatible backend, enable bucket versioning and replicate the `documents/` prefix to a separate protected bucket or account. Preserve object metadata, especially the original content type and content disposition. For Azure Blob, enable blob versioning or a point-in-time restore policy and copy the container to a separate storage account. If local disk is used for development, copy the entire `MEDIA_ROOT` directory, not only its newest files.

A database dump without the matching object-store snapshot is incomplete. Record the object-store snapshot identifier next to the PostgreSQL dump identifier.

## Restore drill on a fresh machine

The following drill should be performed at least quarterly and after any major storage migration. The expected outputs are deliberately written down so an operator can distinguish a normal warning from a failed restore.

First install Python, PostgreSQL, Git, and the project dependencies. Create an empty database owned by the application role, then restore the custom-format dump:

```bash
createdb --host=127.0.0.1 --username=udm restored_doctrack
pg_restore --clean --if-exists --no-owner \
  --host=127.0.0.1 --port=5432 --username=udm \
  --dbname=restored_doctrack udm-doctrack-YYYYMMDD-HHMM.dump
```

Expected result: `pg_restore` exits with status 0. Warnings about ownership are acceptable only when `--no-owner` is used deliberately; missing-table or invalid-SQL errors are not.

Copy or mount the matching object-store snapshot and set the restored environment to use it. Set the same `DJANGO_SECRET_KEY` if the restored system must accept previously issued links, then run migrations and initialize extensions:

```bash
export DATABASE_URL='postgres://udm:password@127.0.0.1:5432/restored_doctrack'
python manage.py migrate --noinput
python manage.py init_db
python manage.py check --deploy --fail-level ERROR
```

Expected result: migrations report no unapplied changes, `init_db` confirms the `pg_trgm` and `unaccent` extensions, and the deploy check reports no errors for a production-like environment.

Rebuild the search index and verify the health endpoint:

```bash
python manage.py reindex_documents
curl --fail http://127.0.0.1:8000/healthz/
```

Expected result: the reindex command reports the number of documents processed; the health response has `"status": "ok"`, with database, cache, migrations, and storage checks all true. If the response is `503`, do not route users to the restored instance.

Start the web process and worker, then run the two workflow checks:

```bash
python manage.py qcluster
python manage.py selfcheck
python scripts/smoke_pages.py
```

Expected result: `selfcheck` completes and rolls back its fixture transaction; `smoke_pages.py` reports successful responses for the permitted roles. Finally open one restored record, download its primary file, inspect its routing history, and confirm that the file checksum matches the backup inventory.

## Failure cases

If a table is dropped, stop writes, restore the most recent database dump to a separate database, and compare the affected records before swapping the application connection. Do not restore directly over the only copy.

If the object-storage bucket is lost, restore the database and object snapshot as a pair. Missing files must remain visible as a storage error rather than being silently replaced. After the object restore, run the download smoke check on representative PDF, Office, image, and text files.

If only the search index is corrupted, do not restore older metadata. Run `python manage.py reindex_documents` against the current database. The command rebuilds the denormalised search columns and stored PostgreSQL vector from the current records.

If the django-q2 worker is down, existing records remain available but new uploads with `ENABLE_BACKGROUND_TASKS=True` stay in the pending extraction state. Start the worker and inspect the queue. Authorized users can use the document's **Run text extraction again** action after the worker recovers; do not bypass the pending state by editing OCR fields directly.

## Retention and privacy

`DocumentType.retention_years` and `Document.retention_until` describe when a record becomes due for human disposition. Migration backfills missing dates only where the document type and year or date are sufficient. The system must never silently delete, hide, or deactivate a document because a date passed. Records personnel should approve disposition, record the reason, and preserve the disposition register.

Before enabling an OCR provider, have the university privacy officer approve the provider and its data-processing terms. Each document stores whether external OCR is allowed. Sensitive uploads can remain local-only, and records archived from tracking default to external OCR disabled. Temporary provider failures retry a bounded number of times; document-visible notes report retries or permanent failure without including extracted content in email.

Bulk receipt requires an explicit selection and custody confirmation. Every selected record receives its own append-only routing event and audit entry. Search click telemetry stores the user, query-log identifier, document identifier, rank, and timestamp; it does not copy document content. Restrict reports and database access accordingly because search terms themselves can still reveal work context.

The archive contains personal data and is operated by a Philippine university. Apply the principles of the Data Privacy Act of 2012: limit access to the people and offices that need it, protect backups and object storage, avoid putting document content in email, keep audit logs, and follow the university's approved retention and disposal policy. Consult the university privacy officer for the institution's definitive retention schedule and breach-response procedure.


## Notification maintenance

Notifications are a user-interface convenience, not the audit trail. `AuditLog` remains append-only and is never removed by notification maintenance. When background tasks are enabled, run `python manage.py ensure_schedules` once after migrations to register the daily `notification-pruning` django-q2 schedule. The task resolves informational notifications older than `NOTIFICATION_INFO_RESOLVE_DAYS` (default 30 days) and deletes only notifications already resolved for longer than `NOTIFICATION_RETENTION_DAYS` (default 90 days). It never deletes routing records, documents, files, activities, receipts, or audit entries.

If the scheduled worker is unavailable, notification rows remain available and the application continues to function. Start `python manage.py qcluster`, then inspect the django-q2 schedule and worker logs. Adjust the two retention settings only after the records owner and privacy officer agree that the shorter or longer UI-history window is appropriate.
