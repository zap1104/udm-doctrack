"""A successful append must not hide another saved file's extraction failure."""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import User
from apps.documents.extraction import ExtractionResult
from apps.documents.models import Document, DocumentFile, OcrStatus
from apps.documents.services import add_file_to_document, re_extract_document
from apps.documents.tasks import extract_document_task

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def document(users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = True
    document = Document.objects.create(
        title="Sibling extraction failure", year=2026,
        office=users["med"].office, uploaded_by=users["med"],
        ocr_status=OcrStatus.DONE, ocr_text="Previously usable text",
    )
    DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("original.txt", b"Previously usable text"),
        original_name="original.txt", extracted_chars=22, page_count=1, is_primary=True,
    )
    return document


@pytest.mark.parametrize("failure", ["queue", "provider"])
def test_successful_sibling_append_does_not_hide_saved_file_failure(users, document, failure):
    # Real service/transactions, private storage and worker writes; only the
    # external queue/provider boundaries are simulated. Both append jobs belong
    # to the same generation, so a full retry has not superseded either one.
    queue_effect = [ConnectionError("Sibling queue submission failed"), "queued"] if failure == "queue" else None
    with patch("django_q.tasks.async_task", side_effect=queue_effect) as queue:
        failed_file = add_file_to_document(
            document, SimpleUploadedFile("failed.txt", b"Unextracted failed file text"), user=users["med"],
        )
        successful_file = add_file_to_document(
            document, SimpleUploadedFile("success.txt", b"Successful sibling file text"), user=users["med"],
        )
    failed_job, successful_job = queue.call_args_list
    assert failed_job.kwargs["generation"] == successful_job.kwargs["generation"]
    assert failed_file.file.storage.exists(failed_file.file.name)
    assert successful_file.file.storage.exists(successful_file.file.name)

    if failure == "provider":
        with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
            status=OcrStatus.FAILED, notes=["Failed sibling provider could not extract this file"],
        )):
            extract_document_task(*failed_job.args[1:], **failed_job.kwargs)
        document.refresh_from_db()
        assert document.ocr_status == OcrStatus.FAILED
        assert "failed" in document.ocr_notes.lower()

    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
        text="Successful sibling extracted text", status=OcrStatus.DONE, pages=3,
    )):
        extract_document_task(*successful_job.args[1:], **successful_job.kwargs)

    document.refresh_from_db()
    failed_file.refresh_from_db()
    successful_file.refresh_from_db()
    assert document.files.count() == 3
    assert failed_file.extracted_chars == 0
    assert successful_file.extracted_chars > 0
    assert "Previously usable text" in document.ocr_text
    assert "Successful sibling extracted text" in document.ocr_text
    assert document.ocr_status == OcrStatus.FAILED, {
        "ocr_status": document.ocr_status, "ocr_notes": document.ocr_notes,
        "saved_files": document.files.count(), "failed_file_chars": failed_file.extracted_chars,
        "successful_file_chars": successful_file.extracted_chars,
    }
    assert document.ocr_notes.strip(), "The saved file's extraction failure must remain discoverable and retryable"

    # An explicit full retry reads every attachment and may clear the failure.
    with patch("django_q.tasks.async_task") as retry_queue:
        re_extract_document(document, user=users["med"])
    full_retry = retry_queue.call_args
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
        text="Fresh complete retry text", status=OcrStatus.DONE, pages=2,
    )):
        extract_document_task(*full_retry.args[1:], **full_retry.kwargs)
    document.refresh_from_db()
    assert document.ocr_status == OcrStatus.DONE
    assert not document.ocr_notes.strip()
    assert all(file.extracted_chars > 0 for file in document.files.all())


@pytest.mark.django_db(transaction=False)
@pytest.mark.parametrize("failure_time", ["during", "after"])
def test_sibling_queue_failure_is_visible_during_or_after_success(
    users, document, django_capture_on_commit_callbacks, failure_time,
):
    with patch("django_q.tasks.async_task", side_effect=["queued", ConnectionError("Later sibling queue failure")]) as queue:
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            add_file_to_document(document, SimpleUploadedFile("success.txt", b"First sibling"), user=users["med"])
            add_file_to_document(document, SimpleUploadedFile("failed.txt", b"Second sibling"), user=users["med"])
        assert len(callbacks) == 2
        callbacks[0]()
        first = queue.call_args

        def provider(*args, **kwargs):
            document.refresh_from_db()
            assert document.ocr_status == OcrStatus.RUNNING
            if failure_time == "during":
                callbacks[1]()
            return ExtractionResult(text="First sibling text", status=OcrStatus.DONE, pages=1)

        with patch("apps.documents.tasks.extract_document_text", side_effect=provider):
            extract_document_task(*first.args[1:], **first.kwargs)
        if failure_time == "after":
            document.refresh_from_db()
            assert document.ocr_status == OcrStatus.DONE
            callbacks[1]()
    document.refresh_from_db()
    assert document.ocr_status == OcrStatus.FAILED
    assert "retry" in document.ocr_notes.lower()
    assert "Previously usable text" in document.ocr_text
    assert "First sibling text" in document.ocr_text


@pytest.mark.parametrize("prior_failure,background", [("historical", True), ("historical", False), ("synchronous", False)])
def test_append_preserves_historical_or_synchronous_failed_file(users, document, settings, prior_failure, background):
    if prior_failure == "historical":
        # Existing FAILED rows predate the new latch and must not be bulk rewritten.
        Document.objects.filter(pk=document.pk).update(ocr_status=OcrStatus.FAILED, ocr_notes="Earlier saved file failed to extract")
    else:
        settings.ENABLE_BACKGROUND_TASKS = False
        with patch("apps.documents.services.extract_document_text", return_value=ExtractionResult(
            status=OcrStatus.FAILED, notes=["Earlier saved file failed to extract"],
        )):
            add_file_to_document(document, SimpleUploadedFile("failed.txt", b"Failed saved file"), user=users["med"])
    settings.ENABLE_BACKGROUND_TASKS = background
    if background:
        with patch("django_q.tasks.async_task") as queue:
            add_file_to_document(document, SimpleUploadedFile("success.txt", b"Successful file"), user=users["med"])
        queued = queue.call_args
        with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
            text="New sibling text", status=OcrStatus.DONE, pages=1,
        )):
            extract_document_task(*queued.args[1:], **queued.kwargs)
    else:
        with patch("apps.documents.services.extract_document_text", return_value=ExtractionResult(
            text="New sibling text", status=OcrStatus.DONE, pages=1,
        )):
            add_file_to_document(document, SimpleUploadedFile("success.txt", b"Successful file"), user=users["med"])
    document.refresh_from_db()
    assert document.ocr_status == OcrStatus.FAILED
    assert "Earlier saved file failed to extract" in document.ocr_notes
    assert "Previously usable text" in document.ocr_text
    assert "New sibling text" in document.ocr_text


@pytest.mark.django_db(transaction=False)
def test_old_generation_queue_failure_cannot_poison_completed_full_retry(
    users, document, django_capture_on_commit_callbacks,
):
    with patch("django_q.tasks.async_task", side_effect=["queued", ConnectionError("Old generation queue failure")]) as queue:
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            add_file_to_document(document, SimpleUploadedFile("later.txt", b"Saved file"), user=users["med"])
            re_extract_document(document, user=users["med"])
        assert len(callbacks) == 2
        callbacks[1]()
        newer = queue.call_args
        with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
            text="Complete new generation text", status=OcrStatus.DONE, pages=1,
        )):
            extract_document_task(*newer.args[1:], **newer.kwargs)
        callbacks[0]()
    document.refresh_from_db()
    assert document.ocr_status == OcrStatus.DONE
    assert not document.ocr_notes.strip()
    assert "Complete new generation text" in document.ocr_text


@pytest.mark.parametrize("legacy_direct", [False, True])
def test_targeted_replace_cannot_clear_another_file_failure(users, document, legacy_direct):
    Document.objects.filter(pk=document.pk).update(ocr_status=OcrStatus.FAILED, ocr_notes="Earlier saved file failed to extract")
    with patch("django_q.tasks.async_task") as queue:
        added = add_file_to_document(document, SimpleUploadedFile("success.txt", b"Successful saved file"), user=users["med"])
    queued = queue.call_args
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
        text="New targeted text", status=OcrStatus.DONE, pages=1,
    )):
        extract_document_task(document.pk, user_id=users["med"].pk, file_ids=[added.pk],
                              replace=True, generation=None if legacy_direct else queued.kwargs["generation"])
    document.refresh_from_db()
    assert document.ocr_status == OcrStatus.FAILED
    assert "Earlier saved file failed to extract" in document.ocr_notes


def test_skipped_full_retry_keeps_existing_failure_until_complete_retry(users, document, settings):
    settings.ENABLE_BACKGROUND_TASKS = False
    Document.objects.filter(pk=document.pk).update(ocr_status=OcrStatus.FAILED, ocr_notes="Earlier saved file failed to extract")
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
        status=OcrStatus.SKIPPED, notes=["Full retry did not read this file"],
    )):
        re_extract_document(document, user=users["med"])
    document.refresh_from_db()
    assert document.ocr_status == OcrStatus.FAILED
    assert document.ocr_notes.strip()
    with patch("apps.documents.services.extract_document_text", return_value=ExtractionResult(
        text="Successful later append text", status=OcrStatus.DONE, pages=1,
    )):
        add_file_to_document(document, SimpleUploadedFile("success.txt", b"Successful later append"), user=users["med"])
    document.refresh_from_db()
    assert document.ocr_status == OcrStatus.FAILED
    assert "Previously usable text" in document.ocr_text


@pytest.mark.django_db(transaction=False)
def test_successful_full_retry_covers_late_sibling_queue_failure(
    users, document, django_capture_on_commit_callbacks,
):
    with patch("django_q.tasks.async_task", side_effect=["queued", ConnectionError("Covered sibling queue failure")]) as queue:
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            re_extract_document(document, user=users["med"])
            add_file_to_document(document, SimpleUploadedFile("covered.txt", b"File added before retry starts"), user=users["med"])
        callbacks[0]()
        full_retry = queue.call_args
        with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
            text="Complete retry includes this saved file", status=OcrStatus.DONE, pages=1,
        )):
            extract_document_task(*full_retry.args[1:], **full_retry.kwargs)
        callbacks[1]()
    document.refresh_from_db()
    assert all(file.extracted_chars > 0 for file in document.files.all())
    assert document.ocr_status == OcrStatus.DONE
    assert not document.ocr_notes.strip()


def test_cancelled_retry_cannot_hide_failure_when_authorized_sibling_succeeds(users, document):
    Document.objects.filter(pk=document.pk).update(ocr_status=OcrStatus.FAILED, ocr_notes="Earlier saved file failed to extract")
    with patch("django_q.tasks.async_task") as queue:
        re_extract_document(document, user=users["med"])
    cancelled = queue.call_args
    User.objects.filter(pk=users["med"].pk).update(role=User.Role.VIEWER)
    result = extract_document_task(*cancelled.args[1:], **cancelled.kwargs)
    assert result["status"] == OcrStatus.SKIPPED
    with patch("django_q.tasks.async_task") as queue:
        add_file_to_document(document, SimpleUploadedFile("authorized.txt", b"New authorized file"), user=users["med_admin"])
    sibling = queue.call_args
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
        text="Authorized sibling text", status=OcrStatus.DONE, pages=1,
    )):
        extract_document_task(*sibling.args[1:], **sibling.kwargs)
    document.refresh_from_db()
    assert document.ocr_status == OcrStatus.FAILED
    assert "Earlier saved file failed to extract" in document.ocr_notes
    assert "Previously usable text" in document.ocr_text
    assert "Authorized sibling text" in document.ocr_text


@pytest.mark.parametrize("cancelled_full_retry", [False, True])
def test_clean_permission_cancellation_remains_disclosed_until_full_retry(
    users, document, cancelled_full_retry,
):
    with patch("django_q.tasks.async_task") as queue:
        unread_file = add_file_to_document(
            document, SimpleUploadedFile("unread.txt", b"File whose actor loses permission"), user=users["med"],
        )
        original_append = queue.call_args
        if cancelled_full_retry:
            re_extract_document(document, user=users["med"])
        cancelled = queue.call_args
    User.objects.filter(pk=users["med"].pk).update(role=User.Role.VIEWER)
    result = extract_document_task(*cancelled.args[1:], **cancelled.kwargs)
    assert result["status"] == OcrStatus.SKIPPED
    document.refresh_from_db()
    assert document.ocr_status == OcrStatus.SKIPPED
    assert "permission" in document.ocr_notes.lower()

    with patch("django_q.tasks.async_task") as queue:
        add_file_to_document(
            document, SimpleUploadedFile("authorized.txt", b"Authorized actor's new file"), user=users["med_admin"],
        )
    authorized = queue.call_args
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
        text="Authorized file's extracted text", status=OcrStatus.DONE, pages=1,
    )):
        extract_document_task(*authorized.args[1:], **authorized.kwargs)
    document.refresh_from_db()
    unread_file.refresh_from_db()
    assert unread_file.extracted_chars == 0
    assert document.ocr_status == OcrStatus.FAILED
    assert "permission" in document.ocr_notes.lower()
    assert "Previously usable text" in document.ocr_text
    assert "Authorized file's extracted text" in document.ocr_text

    with patch("django_q.tasks.async_task") as queue:
        re_extract_document(document, user=users["med_admin"])
    complete_retry = queue.call_args
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
        text="All files successfully retried", status=OcrStatus.DONE, pages=1,
    )):
        extract_document_task(*complete_retry.args[1:], **complete_retry.kwargs)
    document.refresh_from_db()
    assert document.ocr_status == OcrStatus.DONE
    assert document.extraction_failed_generation is None
    assert not document.ocr_notes.strip()
    assert all(file.extracted_chars > 0 for file in document.files.all())
    extract_document_task(*original_append.args[1:], **original_append.kwargs)
    document.refresh_from_db()
    assert document.ocr_status == OcrStatus.DONE
