"""Filing and extraction preserve the entire document and current authority."""

from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import User
from apps.documents import services
from apps.documents.extraction import ExtractionResult
from apps.documents.models import Document, DocumentFile, OcrStatus
from apps.documents.tasks import extract_document_task
from apps.tracking.models import Attachment, Status
from apps.tracking.services import complete_record, confirm_receipt, create_draft_record, route_record

pytestmark = pytest.mark.django_db


@pytest.fixture
def processed_document(users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    document = Document.objects.create(
        title="Two-file record", office=users["med"].office, uploaded_by=users["med"],
        year=2026, ocr_text="Existing searchable text", page_count=7,
    )
    DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("first.txt", b"First file contents"),
        original_name="first.txt", page_count=7, is_primary=True,
    )
    return document


def test_partial_extraction_keeps_other_files_in_total_page_count(users, processed_document):
    extra = DocumentFile.objects.create(
        document=processed_document, file=SimpleUploadedFile("second.txt", b"Second file contents"),
        original_name="second.txt",
    )
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(text="New searchable text", pages=3, status="DONE")):
        extract_document_task(processed_document.pk, user_id=users["med"].pk, file_ids=[extra.pk])
    processed_document.refresh_from_db()
    assert processed_document.page_count == 10


def test_synchronous_added_file_updates_document_total(users, processed_document):
    with patch("apps.documents.services.extract_document_text", return_value=ExtractionResult(text="New searchable text", pages=3, status="DONE")):
        services.add_file_to_document(processed_document, SimpleUploadedFile("second.txt", b"Second file contents"), user=users["med"])
    processed_document.refresh_from_db()
    assert processed_document.page_count == 10


def test_append_job_merges_with_text_saved_while_file_was_processing(users, processed_document):
    def extract(*args, **kwargs):
        Document.objects.filter(pk=processed_document.pk).update(ocr_text="Text saved by another completed job")
        return ExtractionResult(text="This job's text", pages=7, status="DONE")

    with patch("apps.documents.tasks.extract_document_text", side_effect=extract):
        extract_document_task(processed_document.pk, user_id=users["med"].pk)
    processed_document.refresh_from_db()
    assert "Text saved by another completed job" in processed_document.ocr_text
    assert "This job's text" in processed_document.ocr_text


def test_retry_extraction_reads_all_files_and_reports_missing_secondary_file(client, users, processed_document):
    DocumentFile.objects.create(document=processed_document, file="documents/missing-secondary.txt", original_name="missing-secondary.txt")
    client.force_login(users["med"])
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(text="Primary file text", pages=7, status="DONE")):
        response = client.post(f"/documents/{processed_document.pk}/re-extract/")
    assert response.status_code == 302
    processed_document.refresh_from_db()
    assert "missing-secondary.txt" in processed_document.ocr_notes
    assert any("missing from storage" in str(message) for message in response.wsgi_request._messages)


def test_missing_attachment_does_not_silently_mark_tracking_record_filed(users, offices, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    record = create_draft_record(user=users["med"], subject="Keep the complete document", instructions="Review")
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    complete_record(record, user=users["sup"])
    Attachment.objects.create(record=record, file="tracking/missing.txt", original_name="missing.txt", uploaded_by=users["med"])
    with pytest.raises(ValidationError, match="Filing stopped"):
        services.archive_tracking_record(record, user=users["sup_admin"])
    record.refresh_from_db()
    assert record.status == Status.COMPLETED_PENDING_UPLOAD
    assert not Document.objects.filter(tracking_record=record).exists()
    assert not record.activities.filter(event="ARCHIVED").exists()


def test_retry_service_refuses_an_actor_demoted_after_loading(users, processed_document):
    User.objects.filter(pk=users["med"].pk).update(role=User.Role.VIEWER)
    with pytest.raises(PermissionDenied):
        services.re_extract_document(processed_document, user=users["med"])
    processed_document.refresh_from_db()
    assert processed_document.ocr_text == "Existing searchable text"
    assert processed_document.ocr_status != OcrStatus.RUNNING


@pytest.mark.parametrize("failure", ["missing", "failed", "skipped"])
def test_failed_retry_preserves_last_searchable_text(users, processed_document, failure):
    if failure == "missing":
        primary = processed_document.primary_file
        primary.file.delete(save=False)
        processed_document.files.filter(pk=primary.pk).update(file="documents/missing-primary.txt")
        result = services.re_extract_document(processed_document, user=users["med"])
    else:
        with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(status=failure.upper(), notes=["Could not finish"])):
            result = services.re_extract_document(processed_document, user=users["med"])
    processed_document.refresh_from_db()
    assert processed_document.ocr_text == "Existing searchable text"
    assert result["status"] in {OcrStatus.FAILED, OcrStatus.SKIPPED}


def test_retry_reads_both_files_and_replaces_old_text_with_complete_output(users, processed_document):
    DocumentFile.objects.create(
        document=processed_document, file=SimpleUploadedFile("second.txt", b"Second file contents"), original_name="second.txt",
    )
    results = [ExtractionResult(text="Fresh primary text", pages=2, status="DONE"), ExtractionResult(text="Fresh secondary text", pages=4, status="DONE")]
    with patch("apps.documents.tasks.extract_document_text", side_effect=results) as extractor:
        result = services.re_extract_document(processed_document, user=users["med"])
    assert extractor.call_count == 2
    processed_document.refresh_from_db()
    assert processed_document.page_count == 6
    assert "Fresh primary text" in processed_document.ocr_text
    assert "Fresh secondary text" in processed_document.ocr_text
    assert "Existing searchable text" not in processed_document.ocr_text
    assert result["status"] == OcrStatus.DONE


def test_background_retry_is_queued_after_commit_for_all_attachments(users, settings, processed_document, django_capture_on_commit_callbacks):
    settings.ENABLE_BACKGROUND_TASKS = True
    with patch("django_q.tasks.async_task") as queue, django_capture_on_commit_callbacks(execute=True):
        result = services.re_extract_document(processed_document, user=users["med"])
        queue.assert_not_called()
    assert result["status"] == OcrStatus.PENDING
    queue.assert_called_once()
    assert queue.call_args.kwargs["replace"] is True
    assert queue.call_args.kwargs["file_ids"] is None
    assert queue.call_args.kwargs["user_id"] == users["med"].pk


def test_explicit_empty_file_selection_does_not_process_other_attachments(users, processed_document):
    with patch("apps.documents.tasks.extract_document_text") as extractor:
        result = extract_document_task(processed_document.pk, user_id=users["med"].pk, file_ids=[])
    extractor.assert_not_called()
    processed_document.refresh_from_db()
    assert processed_document.ocr_text == "Existing searchable text"
    assert processed_document.page_count == 7
    assert result["status"] == OcrStatus.DONE
