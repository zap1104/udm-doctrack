"""Immutable file history, checked against PostgreSQL and isolated storage."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import File
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.utils import timezone

from apps.documents.models import Document, DocumentFile, DocumentFileVersion
from apps.documents.services import add_file_to_document, archive_tracking_record, ingest_upload
from apps.tracking.models import (
    Attachment,
    ExternalAttachment,
    ExternalOffice,
    ExternalTrackingRecord,
    RecordActivity,
    Status,
    TrackingRecord,
)
from apps.tracking.services import attach_files, complete_record, confirm_receipt, create_draft_record, route_record

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def local_storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = True


def upload(payload=b"First version", name="same.txt"):
    return SimpleUploadedFile(name, payload, content_type="text/plain")


@pytest.fixture
def record(users):
    return create_draft_record(user=users["med"], subject="Version test", instructions="Review")


def test_independent_initial_groups_and_immutable_bytes(record, users):
    first, other = attach_files(record, [upload(), upload(b"Another file")], user=users["med"])
    assert hasattr(first, "file_group_id"), "Attachment needs an independent logical file group"
    assert first.file_group_id != other.file_group_id
    assert first.version_number == 1
    assert first.file_group.latest_version.pk == first.pk
    assert first.uploader_office_snapshot == users["med"].office.name
    first.original_name = "overwritten.txt"
    with pytest.raises(ValidationError):
        first.save()


def test_version_upload_stale_same_bytes_and_history(record, users):
    first = attach_files(record, [upload()], user=users["med"])[0]
    assert hasattr(first, "file_group_id"), "Attachment needs version metadata"
    from apps.tracking.file_versions import upload_attachment_version
    second = upload_attachment_version(record, first.file_group_id, upload(b"Second"), user=users["med"],
        expected_version=1, change_summary="Corrected wording")
    assert second.version_number == 2
    assert first.file_group.latest_version.pk == second.pk
    assert second.checksum == hashlib.sha256(b"Second").hexdigest()
    with first.file.open("rb") as stream:
        assert stream.read() == b"First version"
    for base, content, summary in [(1, b"Third", "Stale"), (2, b"Second", "Identical"), (2, b"Third", "")]:
        with pytest.raises(ValidationError):
            upload_attachment_version(record, first.file_group_id, upload(content), user=users["med"],
                expected_version=base, change_summary=summary)
    assert first.file_group.versions.count() == 2


def test_direct_ingest_snapshots_and_closed_append(users):
    document, _ = ingest_upload(user=users["med"], uploaded_file=upload())
    file = document.files.get()
    assert hasattr(file, "versions"), "Repository needs immutable snapshots"
    assert file.versions.get().file.name == file.file.name
    assert file.version_number == 1
    with pytest.raises(PermissionDenied):
        add_file_to_document(document, upload(b"Append"), user=users["admin"])


def test_initial_batch_is_atomic(users):
    with pytest.raises(ValidationError):
        ingest_upload(user=users["med"], uploaded_files=[upload(), upload(b"bad", "bad.exe")])
    assert not Document.objects.exists()
    assert not DocumentFile.objects.exists()


def version(record, first, user, payload=b"Second version", **extra):
    from apps.tracking.file_versions import upload_attachment_version
    return upload_attachment_version(record, first.file_group_id, upload(payload), user=user,
        expected_version=extra.pop("expected_version", first.version_number),
        change_summary=extra.pop("change_summary", "Corrected wording"), **extra)


def stored(root):
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def history(record, users):
    first = attach_files(record, [upload(b"Obsolete text")], user=users["med"])[0]
    second = version(record, first, users["med"], b"Current searchable text")
    return first, second


def finish(record, users, offices):
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    complete_record(record, user=users["sup"])


def test_queryset_fk_alias_cannot_rewrite_uploader(record, users):
    first = attach_files(record, [upload()], user=users["med"])[0]
    with pytest.raises(ValidationError):
        Attachment.objects.filter(pk=first.pk).update(uploaded_by=users["sup"])


@pytest.mark.parametrize("operation", ["save", "bulk_upsert"])
def test_constructed_existing_pk_cannot_bypass_file_immutability(record, users, tmp_path, operation):
    first = attach_files(record, [upload()], user=users["med"])[0]
    before = stored(tmp_path)
    replacement = Attachment(pk=first.pk, record=record, file_group=first.file_group,
        file=upload(b"Replacement"), original_name="replacement.txt", uploaded_by=users["med"], created_at=first.created_at)
    with pytest.raises(ValidationError):
        if operation == "save":
            replacement.save()
        else:
            Attachment.objects.bulk_create([replacement], update_conflicts=True, unique_fields=["pk"], update_fields=["original_name"])
    assert stored(tmp_path) == before


def test_filing_rejects_corrupt_history(record, history, users, offices, tmp_path):
    first, _ = history
    with first.file.storage.open(first.file.name, "wb") as stream:
        stream.write(b"Corrupt history bytes")
    finish(record, users, offices)
    before = stored(tmp_path)
    with pytest.raises(ValidationError, match="history|checksum|content"):
        archive_tracking_record(record, user=users["med_admin"])
    assert not Document.objects.exists()
    assert stored(tmp_path) == before


def test_filing_streams_and_copies_each_version_before_reading_the_next(record, users, offices):
    first = attach_files(record, [upload(b"First" * 40000)], user=users["med"])[0]
    second = version(record, first, users["med"], b"Second" * 40000)
    version(record, second, users["med"], b"Third" * 40000)
    finish(record, users, offices)
    storage = first.file.storage
    source_names = set(record.attachments.values_list("file", flat=True))
    open_original, save_original = storage.open, storage.save
    reads, events = [], []

    class ObservedFile(File):
        def read(self, size=-1):
            reads.append(size)
            return self.file.read(size)

    def observe_open(name, mode="rb"):
        stream = open_original(name, mode)
        if name in source_names:
            events.append(("source", name))
            return ObservedFile(stream, name=name)
        return stream

    def observe_save(name, content, *args, **kwargs):
        events.append(("copy", name))
        return save_original(name, content, *args, **kwargs)

    with patch.object(storage, "open", side_effect=observe_open), patch.object(storage, "save", side_effect=observe_save):
        document = archive_tracking_record(record, user=users["med_admin"])
    assert reads and all(0 < size <= 64 * 1024 for size in reads), "History reads must use bounded chunks"
    source_indices = [index for index, event in enumerate(events) if event[0] == "source"]
    copy_indices = [index for index, event in enumerate(events) if event[0] == "copy"]
    assert len(source_indices) == len(copy_indices) == 3
    assert all(copy < next_source for copy, next_source in zip(copy_indices[:-1], source_indices[1:], strict=True)), \
        "Copy each opened version before opening another; do not retain a group's history bytes"
    assert document.files.get().versions.count() == 3


def test_internal_filing_keeps_only_current_text_and_all_source_provenance(record, history, users, offices, settings):
    first, second = history
    finish(record, users, offices)
    settings.ENABLE_BACKGROUND_TASKS = False
    document = archive_tracking_record(record, user=users["med_admin"])
    assert document.files.count() == 1
    root = document.files.get()
    assert root.version_number == 2
    assert root.uploaded_by_id == users["med"].pk
    assert "Current searchable text" in document.ocr_text
    assert "Obsolete text" not in document.ocr_text
    assert document.file_page_count == root.page_count
    snapshots = list(root.versions.all())
    assert [row.version_number for row in snapshots] == [1, 2]
    assert snapshots[-1].file.name == root.file.name
    for snapshot, source in zip(snapshots, [first, second], strict=True):
        assert snapshot.checksum == source.checksum
        assert snapshot.uploaded_at == source.created_at
        assert snapshot.uploaded_by_id == source.uploaded_by_id
        assert snapshot.uploader_office_snapshot == source.uploader_office_snapshot
        assert snapshot.file.name != source.file.name
        with snapshot.file.open("rb") as stream:
            assert hashlib.sha256(stream.read()).hexdigest() == source.checksum


def test_failed_internal_ocr_is_retained(record, history, users, offices, settings):
    from apps.documents.extraction import ExtractionResult
    finish(record, users, offices)
    settings.ENABLE_BACKGROUND_TASKS = False
    with patch("apps.documents.services.extract_document_text", return_value=ExtractionResult(status="FAILED", notes=["OCR unavailable"])):
        document = archive_tracking_record(record, user=users["med_admin"])
    assert document.ocr_status == "FAILED"
    assert "OCR unavailable" in document.ocr_notes
    assert document.files.get().versions.count() == 2


@pytest.mark.parametrize("failure", ["missing_history", "storage_write", "ocr_exception"])
def test_failed_filing_cleans_all_new_copies(record, history, users, offices, tmp_path, settings, failure):
    finish(record, users, offices)
    if failure == "missing_history":
        history[0].file.storage.delete(history[0].file.name)
        context = patch("apps.documents.services.logger.warning")
    elif failure == "storage_write":
        context = patch.object(DocumentFileVersion, "save", side_effect=OSError("storage write failed"))
    else:
        settings.ENABLE_BACKGROUND_TASKS = False
        context = patch("apps.documents.services.extract_document_text", side_effect=RuntimeError("OCR crashed"))
    before = stored(tmp_path)
    with context, pytest.raises((ValidationError, OSError, RuntimeError)):
        archive_tracking_record(record, user=users["med_admin"])
    assert stored(tmp_path) == before
    assert not Document.objects.exists()
    record.refresh_from_db()
    assert record.status == Status.COMPLETED_PENDING_UPLOAD


@pytest.mark.parametrize("role", ["viewer", "sup"])
def test_version_denies_readonly_and_foreign_office(record, history, users, role):
    with pytest.raises(PermissionDenied):
        version(record, history[-1], users[role])
    assert record.attachments.count() == 2


@pytest.mark.parametrize("state", [Status.PENDING_RECEIPT, Status.COMPLETED_PENDING_UPLOAD, Status.COMPLETED])
def test_version_rechecks_fresh_status(record, history, users, state):
    TrackingRecord.objects.filter(pk=record.pk).update(status=state)
    with pytest.raises((PermissionDenied, ValidationError)):
        version(record, history[-1], users["med"])
    assert record.attachments.count() == 2


def test_version_rechecks_fresh_actor(record, history, users):
    type(users["med"]).objects.filter(pk=users["med"].pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        version(record, history[-1], users["med"])


def test_foreign_group_and_older_content_and_type_change(record, history, users):
    other = create_draft_record(user=users["med"], subject="Another", instructions="Review")
    foreign = attach_files(other, [upload()], user=users["med"])[0]
    with pytest.raises(ValidationError):
        version(record, foreign, users["med"])
    from apps.tracking.file_versions import upload_attachment_version
    third = upload_attachment_version(record, history[-1].file_group_id, upload(b"Obsolete text", "restored.csv"),
        user=users["med"], expected_version=2, change_summary="Restored original")
    assert third.version_number == 3
    assert third.original_name == "restored.csv"
    assert third.checksum == history[0].checksum


def test_new_version_logs_one_work_update_and_only_current_batch(record, users, offices):
    first = attach_files(record, [upload()], user=users["med"])[0]
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    baseline = record.activities.filter(event=RecordActivity.Event.ATTACHMENT).count()
    second = version(record, first, users["sup"])
    assert second.routing_step.batch == record.current_batch
    assert record.status == Status.IN_PROCESS
    assert record.activities.filter(event=RecordActivity.Event.ATTACHMENT).count() == baseline + 1
    assert record.activities.latest("pk").work_office_id == offices["SUP"].pk


def test_version_storage_rollback_leaves_no_gap(record, history, users, tmp_path):
    original_save = Attachment.save
    def fail(instance, *args, **kwargs):
        original_save(instance, *args, **kwargs)
        raise RuntimeError("database failed after storage")
    before = stored(tmp_path)
    with patch.object(Attachment, "save", fail), pytest.raises(RuntimeError):
        version(record, history[-1], users["med"])
    assert stored(tmp_path) == before
    assert version(record, history[-1], users["med"]).version_number == 3


def test_initial_batch_content_and_derived_updates(users):
    document, _ = ingest_upload(user=users["med"], uploaded_files=[upload(), upload(b"Second")])
    assert document.files.count() == 2
    assert DocumentFileVersion.objects.count() == 2
    root = document.files.first()
    root.page_count, root.extracted_chars = 7, 80
    root.save(update_fields=["page_count", "extracted_chars"])
    root.refresh_from_db()
    assert root.page_count == 7
    for model, row in [(DocumentFile, root), (DocumentFileVersion, root.versions.get())]:
        with pytest.raises(ValidationError):
            model.objects.filter(pk=row.pk).update(original_name="wrong.txt")


def test_purge_visits_history_deduplicates_and_preserves_shared_tracking_key(record, history, users, offices):
    from apps.documents.tasks import purge_deleted_documents
    finish(record, users, offices)
    document = archive_tracking_record(record, user=users["med_admin"])
    root = document.files.get()
    other, _ = ingest_upload(user=users["med"], uploaded_file=upload(b"Shared"))
    shared_key = root.versions.get(version_number=1).file.name
    DocumentFile.objects.create(document=other, original_name="shared.txt", file=shared_key)
    # A second deleted document points directly at an original Tracking key.
    tracking_shared, _ = ingest_upload(user=users["med"], uploaded_file=upload(b"Tracking shared"))
    DocumentFile.objects.create(document=tracking_shared, original_name="tracking.txt", file=history[0].file.name)
    expired = timezone.now() - timedelta(days=31)
    Document.objects.filter(pk__in=[document.pk, tracking_shared.pk]).update(is_active=False, deleted_at=expired)
    storage = root.file.storage
    with patch.object(storage, "delete", wraps=storage.delete) as remove:
        result = purge_deleted_documents()
    assert result == {"purged": 2, "failed": 0}
    deleted_names = [call.args[0] for call in remove.call_args_list]
    assert deleted_names.count(root.file.name) == 1
    assert shared_key not in deleted_names
    assert storage.exists(shared_key)
    assert all(source.file.storage.exists(source.file.name) for source in history)
    assert not DocumentFileVersion.objects.filter(document_file__document_id=document.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_double_submission_has_one_winner(record, history, users):
    gate = Barrier(2)
    def submit():
        close_old_connections()
        try:
            current = TrackingRecord.objects.get(pk=record.pk)
            gate.wait(timeout=10)
            try:
                return version(current, history[-1], users["med"]).version_number
            except ValidationError:
                return "stale"
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(map(str, results)) == ["3", "stale"]
    assert record.attachments.count() == 3


@pytest.fixture
def external_history(users, offices):
    from apps.tracking.external_services import add_attachment, create_record
    from apps.tracking.file_versions import upload_external_attachment_version
    counterpart = ExternalOffice.objects.create(name="Outside Office", managing_office=offices["MED"])
    external = create_record(user=users["med_admin"], title="Outside", from_external_office=counterpart)
    # Start at confirmed internal custody, with a counterpart retained by its history.
    from apps.tracking.models import ExternalRoutingStep
    ExternalTrackingRecord.objects.filter(pk=external.pk).update(current_external_office=None,
        current_office=offices["MED"], status=Status.RECEIVED)
    ExternalRoutingStep.objects.create(record=external, sequence=1, from_external_office=counterpart,
        to_office=offices["MED"], from_name_snapshot=counterpart.name, to_name_snapshot=offices["MED"].name,
        sent_at=timezone.now(), received_at=timezone.now())
    external.refresh_from_db()
    first = add_attachment(external, user=users["med_admin"], expected_revision=external.revision, uploaded_file=upload(b"External old"))
    stale_revision = external.revision
    second = upload_external_attachment_version(external, first.file_group_id, upload(b"External current"), user=users["med_admin"],
        expected_revision=external.revision, expected_version=1, change_summary="Corrected outside copy")
    return external, first, second, stale_revision


def test_external_versions_revision_and_filing(users, external_history):
    from apps.documents.external_services import archive_external_record
    from apps.tracking.file_versions import upload_external_attachment_version
    external, first, second, stale_revision = external_history
    assert second.version_number == 2
    assert external.status == Status.IN_PROCESS
    assert external.activities.filter(event="ATTACHMENT").count() == 2
    with pytest.raises(ValidationError):
        upload_external_attachment_version(external, first.file_group_id, upload(b"Stale outside"), user=users["med_admin"],
            expected_revision=stale_revision, expected_version=2, change_summary="Stale revision")
    ExternalTrackingRecord.objects.filter(pk=external.pk).update(status=Status.COMPLETED_PENDING_UPLOAD, completed_at=timezone.now())
    external.refresh_from_db()
    document = archive_external_record(external, user=users["med_admin"], expected_revision=external.revision)
    assert document.collection == "EXTERNAL"
    assert document.files.count() == 1
    assert document.files.get().versions.count() == 2
    assert document.files.get().version_number == 2
    assert ExternalAttachment.objects.filter(record=external).count() == 2


@pytest.mark.parametrize("failure", ["missing_history", "snapshot_insert"])
def test_external_filing_rolls_back_history_and_copies(users, external_history, tmp_path, failure):
    from apps.documents.external_services import archive_external_record
    external, first, _second, _revision = external_history
    ExternalTrackingRecord.objects.filter(pk=external.pk).update(status=Status.COMPLETED_PENDING_UPLOAD, completed_at=timezone.now())
    external.refresh_from_db()
    if failure == "missing_history":
        first.file.storage.delete(first.file.name)
        context = patch("apps.documents.external_services.logger.exception")
    else:
        context = patch.object(DocumentFileVersion, "save", side_effect=OSError("storage unavailable"))
    before = stored(tmp_path)
    with context, pytest.raises((ValidationError, OSError)):
        archive_external_record(external, user=users["med_admin"], expected_revision=external.revision)
    assert stored(tmp_path) == before
    assert not Document.objects.exists()
    external.refresh_from_db()
    assert external.status == Status.COMPLETED_PENDING_UPLOAD


@pytest.mark.parametrize("state", [Status.PENDING_RECEIPT, Status.COMPLETED_PENDING_UPLOAD, Status.COMPLETED])
def test_external_versions_recheck_fresh_pending_and_completed_state(users, external_history, state):
    from apps.tracking.file_versions import upload_external_attachment_version
    external, first, _second, _revision = external_history
    ExternalTrackingRecord.objects.filter(pk=external.pk).update(status=state)
    if state == Status.PENDING_RECEIPT:
        external.routing_steps.update(received_at=None)
    with pytest.raises((PermissionDenied, ValidationError)):
        upload_external_attachment_version(external, first.file_group_id, upload(b"Denied"), user=users["med_admin"],
            expected_revision=external.revision, expected_version=2, change_summary="Should fail")
    assert external.attachments.count() == 2


@pytest.mark.django_db(transaction=True)
def test_legacy_migration_preserves_independent_ids_paths_and_unknown_offices(users, offices):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    old_targets = [("documents", "0013_document_extraction_failed_generation"), ("tracking", "0017_external_activity_custody_anchor")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()
    executor.migrate(old_targets)
    try:
        old = executor.loader.project_state(old_targets).apps
        legacy_record = old.get_model("tracking", "TrackingRecord").objects.create(
            tracking_number="LEGACY-VERSIONS", subject="Legacy", originating_office_id=offices["MED"].pk,
            current_office_id=offices["MED"].pk, created_by_id=users["med"].pk)
        AttachmentModel = old.get_model("tracking", "Attachment")
        rows = [AttachmentModel.objects.create(record_id=legacy_record.pk, original_name="same.txt",
            file=f"tracking/legacy/{number}.txt", checksum=str(number) * 64, uploaded_by_id=users["med"].pk) for number in [1, 2]]
        legacy_document = old.get_model("documents", "Document").objects.create(title="Legacy filed", year=2026,
            office_id=offices["MED"].pk, source="DTS", tracking_record_id=legacy_record.pk, uploaded_by_id=users["admin"].pk)
        legacy_root = old.get_model("documents", "DocumentFile").objects.create(document_id=legacy_document.pk,
            original_name="same.txt", file="documents/legacy/copy.txt", checksum=rows[0].checksum, uploaded_by_id=users["admin"].pk)
        row_ids, root_id = [row.pk for row in rows], legacy_root.pk
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)
    restored = list(Attachment.objects.filter(pk__in=row_ids).order_by("pk"))
    assert len({row.file_group_id for row in restored}) == 2
    assert all(row.version_number == 1 and row.uploader_office_snapshot == "" for row in restored)
    assert [row.file.name for row in restored] == ["tracking/legacy/1.txt", "tracking/legacy/2.txt"]
    root = DocumentFile.objects.get(pk=root_id)
    snapshot = root.versions.get()
    assert snapshot.file.name == root.file.name == "documents/legacy/copy.txt"
    assert snapshot.uploaded_by_id == users["med"].pk
    assert snapshot.uploaded_at == restored[0].created_at
    assert snapshot.uploader_office_snapshot == ""
