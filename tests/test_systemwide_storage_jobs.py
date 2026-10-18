"""Regression evidence for storage, failed queues and overlapping extraction."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, close_old_connections, transaction

from apps.core.storage import cleanup_new_files
from apps.documents.extraction import ExtractionResult
from apps.documents.models import Document, DocumentFile, OcrStatus
from apps.documents.services import add_file_to_document, ingest_upload, re_extract_document
from apps.documents.tasks import extract_document_task
from apps.tracking import external_services
from apps.tracking.models import Attachment, ExternalAttachment
from apps.tracking.services import attach_files, create_draft_record


def upload(name="note.txt"):
    return SimpleUploadedFile(name, b"Searchable attachment contents for the storage audit.")


def stored_files(root):
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def document(users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    return Document.objects.create(title="Stored document", year=2026, office=users["med"].office,
                                   uploaded_by=users["med"], ocr_text="Last usable text", ocr_status=OcrStatus.DONE)


@pytest.mark.django_db
@pytest.mark.parametrize("operation", ["upload", "addfile", "tracking", "external"])
@pytest.mark.parametrize("failure", ["row_save", "later_write"])
def test_database_failure_removes_only_new_storage_bytes(users, document, tmp_path, operation, failure):
    original = DocumentFile.objects.create(document=document, file=upload("original.txt"), original_name="original.txt")
    before = stored_files(tmp_path)
    if operation == "upload":
        model = DocumentFile
        later = "apps.documents.services.log_action"
        def action():
            return ingest_upload(user=users["med"], uploaded_file=upload())
    elif operation == "addfile":
        model = DocumentFile
        later = "apps.documents.models.Document.rebuild_index"
        def action():
            return add_file_to_document(document, upload(), user=users["med"])
    elif operation == "tracking":
        record = create_draft_record(user=users["med"], subject="Files", instructions="Review")
        model = Attachment
        later = "apps.tracking.services.add_activity"
        def action():
            return attach_files(record, [upload(), upload("second.txt")], user=users["med"])
    else:
        record = external_services.create_record(user=users["med_admin"], title="Files")
        model = ExternalAttachment
        later = "apps.tracking.external_services._event"
        def action():
            return external_services.add_attachment(record, user=users["med_admin"],
                                                    expected_revision=record.revision, uploaded_file=upload())
    if failure == "row_save":
        original_save = model.save

        def save_then_fail(instance, *args, **kwargs):
            original_save(instance, *args, **kwargs)
            raise RuntimeError("simulated database write failure")

        context = patch.object(model, "save", save_then_fail)
    else:
        context = patch(later, side_effect=RuntimeError("simulated database write failure"))
    with context, pytest.raises(RuntimeError, match="simulated database"):
        action()
    assert stored_files(tmp_path) == before
    assert original.file.storage.exists(original.file.name)
    assert document.files.count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("operation", ["upload", "addfile", "retry"])
def test_queue_failure_keeps_committed_work_and_exposes_retry(users, document, settings, operation):
    settings.ENABLE_BACKGROUND_TASKS = True
    if operation == "retry":
        DocumentFile.objects.create(document=document, file=upload(), original_name="note.txt")
    with patch("django_q.tasks.async_task", side_effect=ConnectionError("queue unavailable")):
        if operation == "upload":
            document, _ = ingest_upload(user=users["med"], uploaded_file=upload())
        elif operation == "addfile":
            add_file_to_document(document, upload(), user=users["med"])
        else:
            re_extract_document(document, user=users["med"])
    document.refresh_from_db()
    assert document.files.count() == 1
    assert document.files.first().file.storage.exists(document.files.first().file.name)
    assert document.ocr_status == OcrStatus.FAILED
    assert "retry" in document.ocr_notes.lower()
    if operation != "upload":
        assert document.ocr_text == "Last usable text"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("collection", ["OVPA", "EXTERNAL"])
def test_older_full_retry_cannot_overwrite_newer_completed_text(users, document, collection):
    Document.objects.filter(pk=document.pk).update(collection=collection)
    DocumentFile.objects.create(document=document, file=upload(), original_name="note.txt")
    started, finish_old = Event(), Event()
    actor_id = users["med_admin" if collection == "EXTERNAL" else "med"].pk

    def extract(*args, **kwargs):
        if not started.is_set():
            started.set()
            assert finish_old.wait(30), "newer extraction never released older worker"
            return ExtractionResult(text="Older provider result", pages=2, status="DONE")
        return ExtractionResult(text="Newer provider result", pages=7, status="DONE")

    def run():
        close_old_connections()
        try:
            return extract_document_task(document.pk, user_id=actor_id, replace=True)
        finally:
            close_old_connections()

    with patch("apps.documents.tasks.extract_document_text", side_effect=extract), ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(run)
        try:
            if not started.wait(30):
                # Surface a worker exception/early cancellation instead of
                # disguising it as a provider synchronization timeout.
                early_result = old.result(timeout=1)
                pytest.fail(f"Older worker exited before provider: {early_result}")
            run()
        finally:
            finish_old.set()
        old.result(timeout=30)
    document.refresh_from_db()
    assert document.ocr_text == "Newer provider result"
    assert document.page_count == 7
    assert document.files.first().page_count == 7


@pytest.mark.django_db
def test_failed_added_file_preserves_text_and_records_failure(users, document):
    with patch("apps.documents.services.extract_document_text", return_value=ExtractionResult(
        text="", status="FAILED", notes=["Provider refused this file"],
    )):
        add_file_to_document(document, upload(), user=users["med"])
    document.refresh_from_db()
    assert document.ocr_text == "Last usable text"
    assert document.ocr_status == OcrStatus.FAILED
    assert "Provider refused this file" in document.ocr_notes


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("callback_first", [False, True])
def test_failure_after_commit_does_not_remove_committed_files(users, document, callback_first):
    def callback():
        raise RuntimeError("unrelated callback failed after commit")

    with pytest.raises(RuntimeError, match="callback failed"):
        with cleanup_new_files(), transaction.atomic():
            if callback_first:
                transaction.on_commit(callback)
            added = add_file_to_document(document, upload(), user=users["med"])
            if not callback_first:
                transaction.on_commit(callback)
    assert DocumentFile.objects.filter(pk=added.pk).exists()
    assert added.file.storage.exists(added.file.name)


@pytest.mark.django_db
def test_outer_rollback_compensates_successful_nested_file_mutations(users, document, tmp_path):
    original = DocumentFile.objects.create(document=document, file=upload("original.txt"), original_name="original.txt")
    before = stored_files(tmp_path)
    with pytest.raises(RuntimeError, match="later mutation"):
        with cleanup_new_files(), transaction.atomic():
            add_file_to_document(document, upload(), user=users["med"])
            add_file_to_document(document, upload("second.txt"), user=users["med"])
            raise RuntimeError("later mutation failed")
    assert stored_files(tmp_path) == before
    assert document.files.count() == 1
    assert original.file.storage.exists(original.file.name)


@pytest.mark.django_db
def test_queued_full_retries_honor_request_order_even_when_delivered_backwards(
    users, document, settings, django_capture_on_commit_callbacks,
):
    settings.ENABLE_BACKGROUND_TASKS = True
    DocumentFile.objects.create(document=document, file=upload(), original_name="note.txt")
    with patch("django_q.tasks.async_task") as queue, django_capture_on_commit_callbacks(execute=True):
        re_extract_document(document, user=users["med"])
        re_extract_document(document, user=users["med"])
    older, newer = queue.call_args_list
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(text="Newest retry text", status="DONE")) as extractor:
        extract_document_task(*newer.args[1:], **newer.kwargs)
        result = extract_document_task(*older.args[1:], **older.kwargs)
    assert extractor.call_count == 1
    document.refresh_from_db()
    assert document.ocr_text == "Newest retry text"
    assert result["status"] == OcrStatus.SKIPPED


@pytest.mark.django_db
def test_cleanup_in_aborted_outer_transaction_preserves_original_exception(users, document, caplog):
    # The documented scope order puts cleanup outside atomic. A caller that
    # reverses them cannot query the aborted transaction to confirm rollback;
    # cleanup logs that failure and must preserve the original database error.
    with pytest.raises(IntegrityError) as caught:
        with transaction.atomic():
            with cleanup_new_files():
                added = add_file_to_document(document, upload(), user=users["med"], run_extraction=False)
                DocumentFile.objects.create(pk=added.pk, document=document, file=added.file.name,
                                            original_name="duplicate.txt")
    assert "duplicate key" in str(caught.value)
    assert "Could not remove new storage object" in caplog.text
    assert not DocumentFile.objects.filter(pk=added.pk).exists()
