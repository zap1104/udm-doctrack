"""Durable exact-key cleanup must be safe to retry and inert by default."""
import importlib
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core.models import StorageCleanupIntent
from apps.documents.models import Document, DocumentFile
from tests.test_file_retention_services import history

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def maintenance():
    assert importlib.util.find_spec("apps.core.file_maintenance"), "Cleanup intents need a retryable worker"
    return importlib.import_module("apps.core.file_maintenance")


def test_blocked_prefix_cannot_starve_new_actionable_cleanup(users):
    worker = maintenance()
    for number in range(3):
        StorageCleanupIntent.objects.create(storage_identity="", storage_key=f"unknown/{number}",
            state="blocked", reason="Unknown legacy storage")
    _, _, versions = history(users, False, 6)
    assert worker.maintain_file_uploads(limit=2)["blocked"] == 3
    assert worker.maintain_file_uploads(limit=2, dry_run=False)["removed"] == 1
    assert not versions[0].file.storage.exists(versions[0].file.name)
    assert StorageCleanupIntent.objects.filter(state="blocked", attempts=0).count() == 3


def test_dry_run_is_inert_then_cleanup_removes_only_retired_content(users):
    worker = maintenance()
    _, _, versions = history(users, False, 6)
    with CaptureQueriesContext(connection) as queries:
        result = worker.maintain_file_uploads()
    assert result["candidates"] >= 1
    assert not any(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for query in queries)
    assert versions[0].file.storage.exists(versions[0].file.name)
    assert worker.maintain_file_uploads(dry_run=False)["removed"] == 1
    assert not versions[0].file.storage.exists(versions[0].file.name)
    assert all(row.file.storage.exists(row.file.name) for row in versions[1:])
    assert StorageCleanupIntent.objects.get().state == "complete"
    assert worker.maintain_file_uploads(dry_run=False)["removed"] == 0


@pytest.mark.parametrize("failure", ["raise", "exists", "acknowledge"])
def test_storage_and_acknowledgement_failures_retry_without_losing_intent(users, failure):
    worker = maintenance()
    _, _, versions = history(users, False, 6)
    storage = versions[0].file.storage
    original_save = StorageCleanupIntent.save
    def fail_ack(instance, *args, **kwargs):
        if instance.state == "complete":
            raise OSError("SQL acknowledgement failed")
        return original_save(instance, *args, **kwargs)
    mocked = patch.object(StorageCleanupIntent, "save", fail_ack) if failure == "acknowledge" else patch.object(storage, "delete", side_effect=OSError("Storage failed") if failure == "raise" else None)
    with mocked:
        assert worker.maintain_file_uploads(dry_run=False)["failed"] == 1
    intent = StorageCleanupIntent.objects.get()
    assert intent.state == "retry" and intent.last_error and intent.attempts == 1
    assert worker.maintain_file_uploads(now=timezone.now() + timedelta(hours=2), dry_run=False)["removed"] == 1
    assert StorageCleanupIntent.objects.get().state == "complete"


def test_shared_repository_root_blocks_retirement_cleanup(users):
    worker = maintenance()
    _, actor, versions = history(users, False, 6)
    old = versions[0]
    document = Document.objects.create(title="Shared object", year=2026, office=actor.office)
    DocumentFile.objects.create(document=document, file=old.file.name, original_name=old.original_name,
        checksum=old.checksum, size=old.size)
    result = worker.maintain_file_uploads(dry_run=False)
    assert result["shared"] == 1 and old.file.storage.exists(old.file.name)
    assert StorageCleanupIntent.objects.get().state != "complete"


def test_backend_identity_change_never_deletes_from_new_backend(users, settings, tmp_path):
    worker = maintenance()
    _, _, versions = history(users, False, 6)
    original_root = tmp_path
    settings.MEDIA_ROOT = tmp_path / "different-backend"
    assert worker.maintain_file_uploads(dry_run=False)["blocked"] == 1
    assert (original_root / versions[0].file.name).exists()


def test_cleanup_cannot_delete_content_inside_an_uncommitted_caller_transaction(users):
    from django.core.exceptions import ValidationError
    worker = maintenance()
    _, _, versions = history(users, False, 6)
    with transaction.atomic(), pytest.raises(ValidationError, match="commit|transaction"):
        worker.maintain_file_uploads(dry_run=False)
    assert versions[0].file.storage.exists(versions[0].file.name)


def test_zero_byte_file_is_not_staged(users):
    from uuid import uuid4

    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.core.upload_context import form_nonce_for, resolve_upload_context
    from apps.core.upload_drafts import open_draft, stage_files
    context = resolve_upload_context(users["med"], "tracking-create")
    draft = open_draft(user=users["med"], context=context, form_nonce=form_nonce_for(users["med"], context))
    result = stage_files(draft.pk, [SimpleUploadedFile("empty.txt", b"", "text/plain")], user=users["med"],
        expected_revision=0, client_upload_ids=[uuid4()])
    assert result.errors and not result.accepted_ids and not draft.files.exists()


def test_two_cleanup_workers_remove_one_exact_object(users):
    from tests.test_upload_draft_races import parallel
    worker = maintenance()
    _, _, versions = history(users, False, 6)
    results = parallel(lambda: worker.maintain_file_uploads(dry_run=False), lambda: worker.maintain_file_uploads(dry_run=False))
    assert all(isinstance(row, dict) for row in results)
    assert sum(row["removed"] for row in results) == 1
    assert not versions[0].file.storage.exists(versions[0].file.name)


def test_alias_creation_racing_cleanup_never_creates_a_missing_reference(users):
    from tests.test_upload_draft_races import parallel
    worker = maintenance()
    _, actor, versions = history(users, False, 6)
    old = versions[0]
    document = Document.objects.create(title="Alias race", year=2026, office=actor.office)
    results = parallel(lambda: worker.maintain_file_uploads(dry_run=False), lambda: DocumentFile.objects.create(
        document=document, file=old.file.name, original_name=old.original_name, checksum=old.checksum, size=old.size))
    assert len(results) == 2
    if document.files.exists():
        assert old.file.storage.exists(old.file.name)
    else:
        assert not old.file.storage.exists(old.file.name)


def test_reconciliation_dry_run_and_unknown_legacy_binding_are_explicit(users):
    from apps.core.file_lifecycle_models import lifecycle_write
    worker = maintenance()
    record, _, versions = history(users, False, 7)
    with lifecycle_write():
        record.attachments.update(retired_at=None, retirement_reason="")
        type(versions[0]).objects.filter(pk=versions[0].pk).update(storage_identity="")
    before = list(record.attachments.values())
    assert worker.reconcile_file_retention()["candidates"] == 1
    assert list(record.attachments.values()) == before
    assert worker.reconcile_file_retention(dry_run=False)["retired"] == 2
    assert hasattr(worker, "bind_legacy_identity")
    assert worker.bind_legacy_identity("tracking", versions[0].pk)["bound"] == 0
    assert worker.bind_legacy_identity("tracking", versions[0].pk, dry_run=False)["bound"] == 1
