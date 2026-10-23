"""Explicit lifecycle state must preserve the original immutable file history."""
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core import models as core
from apps.tracking.models import Attachment, ExternalTrackingRecord
from apps.tracking.services import attach_files, create_draft_record

pytestmark = pytest.mark.django_db


def draft_model():
    assert hasattr(core, "UploadDraft"), "Private uploads need a persistent manifest"
    return core.UploadDraft


def test_manifest_target_constraints(users, offices):
    model = draft_model()
    record = create_draft_record(user=users["med"], subject="Internal", instructions="Review")
    external = ExternalTrackingRecord.objects.create(managing_office=offices["MED"], current_office=offices["MED"],
        title="Outside", created_by=users["med_admin"], tracking_number="DRAFT-test-manifest")
    values = dict(owner=users["med"], owner_office_id_snapshot=offices["MED"].pk,
                  workflow="tracking-edit", collection="OVPA", expires_at=timezone.now() + timedelta(hours=24))
    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.create(**values, tracking_record=record, external_record=external)
    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.create(**{**values, "collection": "EXTERNAL"}, tracking_record=record)
    manifest = model.objects.create(**values, tracking_record=record)
    assert manifest.revision == 0 and manifest.state == "open"
    assert manifest.form_state == {} and manifest.result == {}


def test_retirement_preserves_original_metadata(users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    record = create_draft_record(user=users["med"], subject="History", instructions="Review")
    version = attach_files(record, [SimpleUploadedFile("original.txt", b"Original content", "text/plain")],
                           user=users["med"])[0]
    assert hasattr(version, "available_content"), "Versions must distinguish retained from audit-only content"
    assert version.available_content
    assert version.retired_at is None
    assert len(version.storage_identity) == 64
    original = (version.pk, version.file.name, version.checksum, version.created_at)
    version.original_name = "rewritten.txt"
    with pytest.raises(ValidationError):
        version.save()
    version.refresh_from_db()
    version.retired_at = timezone.now()
    with pytest.raises(ValidationError):
        version.save()
    with pytest.raises(ValidationError):
        Attachment.objects.filter(pk=version.pk).update(retired_at=timezone.now())
    version.refresh_from_db()
    assert (version.pk, version.file.name, version.checksum, version.created_at) == original
    assert version.retired_at is None


@pytest.mark.parametrize("name", ["UPLOAD_DRAFT_HOURS", "UPLOAD_DRAFT_MAX_OPEN", "UPLOAD_DRAFT_MAX_BYTES"])
def test_positive_upload_limits(settings, name):
    from django.core.checks import run_checks

    setattr(settings, name, 0)
    assert any(issue.id == "doctrack.E020" for issue in run_checks()), "Unsafe upload-draft limits must fail checks"


def test_lifecycle_receipt_is_append_only(users):
    assert hasattr(core, "FileLifecycleEvent"), "Retirement needs durable immutable audit receipts"
    event = core.FileLifecycleEvent.objects.create(resource_kind="tracking", parent_id=1, version_id=2,
        action="retired", reason="Five-version policy", metadata={"version_number": 1, "filename": "proof.txt"},
        actor_id_snapshot=users["med"].pk)
    event.reason = "rewrite"
    with pytest.raises(ValidationError):
        event.save()
    with pytest.raises(ValidationError):
        type(event).objects.filter(pk=event.pk).update(reason="rewrite")
    with pytest.raises(ValidationError):
        event.delete()


def test_cleanup_intents_deduplicate_exact_objects():
    assert hasattr(core, "StorageCleanupIntent"), "Cleanup must survive worker interruptions"
    model = core.StorageCleanupIntent
    values = dict(storage_identity="a" * 64, storage_key="private/file.txt", reason="retired")
    first = model.objects.create(**values)
    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.create(**values)
    assert model.objects.get(pk=first.pk).state == "pending"


@pytest.mark.django_db(transaction=True)
def test_additive_migration_preserves_existing_keys_and_ids(users):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    from apps.documents.models import DocumentFileVersion

    record = create_draft_record(user=users["med"], subject="Legacy", instructions="Review")
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    previous = [("core", "0014_topic_review_controls"),
                ("tracking", "0018_externalattachmentgroup_and_more"), ("documents", "0028_tagreview_sampling")]
    try:
        executor.migrate(previous)
        historical = executor.loader.project_state(previous).apps
        group = historical.get_model("tracking", "AttachmentGroup").objects.create(record_id=record.pk)
        row = historical.get_model("tracking", "Attachment").objects.create(record_id=record.pk, file_group_id=group.pk,
            file="tracking/legacy/original.txt", original_name="original.txt", checksum="a" * 64,
            uploaded_by_id=users["med"].pk, uploader_office_snapshot="Known historical office")
        document = historical.get_model("documents", "Document").objects.create(title="Legacy repository",
            office_id=users["med"].office_id, uploaded_by_id=users["med"].pk, year=2026)
        root = historical.get_model("documents", "DocumentFile").objects.create(document_id=document.pk,
            file="documents/legacy/original.txt", original_name="original.txt")
        snapshot = historical.get_model("documents", "DocumentFileVersion").objects.create(document_file_id=root.pk,
            file=root.file.name, original_name=root.original_name, checksum="b" * 64)
        source_id, snapshot_id, timestamp = row.pk, snapshot.pk, row.created_at
    finally:
        MigrationExecutor(connection).migrate(latest)
    source = Attachment.objects.get(pk=source_id)
    saved = DocumentFileVersion.objects.get(pk=snapshot_id)
    assert (source.file.name, source.checksum, source.created_at) == ("tracking/legacy/original.txt", "a" * 64, timestamp)
    assert source.uploader_office_snapshot == "Known historical office"
    assert source.storage_identity == saved.storage_identity == ""
    assert source.retired_at is saved.retired_at is None
    assert saved.file.name == "documents/legacy/original.txt" and saved.checksum == "b" * 64
