"""Failed result persistence must leave extraction visibly retryable."""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.documents.extraction import ExtractionResult
from apps.documents.models import Document, DocumentFile, MetadataSuggestion, OcrStatus
from apps.documents.services import re_extract_document
from apps.documents.tasks import _extraction_finalization, extract_document_task

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("failed_write", ["index", "suggestion"])
def test_extraction_finalization_failure_preserves_text_and_reports_retry(users, settings, tmp_path, failed_write):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = True
    document = Document.objects.create(
        title="Finalization rollback", office=users["med"].office, year=2026,
        uploaded_by=users["med"], ocr_text="Last committed usable text", ocr_status=OcrStatus.DONE,
    )
    document_file = DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("evidence.txt", b"Private saved evidence"),
        original_name="evidence.txt", is_primary=True,
    )
    document.rebuild_index()
    original_vector = Document.objects.values_list("search_vector", flat=True).get(pk=document.pk)
    failed_operation = (
        "apps.documents.models.Document.rebuild_index" if failed_write == "index"
        else "apps.documents.tasks.MetadataSuggestion.objects.create"
    )
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
        text="New text whose finalization fails", status=OcrStatus.DONE, pages=3,
    )), patch(failed_operation, side_effect=RuntimeError("Simulated extraction finalization failure")):
        # The worker still raises, allowing the queue's failure reporting/retry
        # machinery to observe it rather than falsely returning success.
        with pytest.raises(RuntimeError, match="Simulated extraction finalization failure"):
            extract_document_task(document.pk, user_id=users["med"].pk, replace=True)
    document.refresh_from_db()
    assert document.ocr_text == "Last committed usable text"
    assert document.search_vector == original_vector
    assert document_file.file.storage.exists(document_file.file.name)
    assert not MetadataSuggestion.objects.filter(document=document).exists()
    assert document.ocr_status == OcrStatus.FAILED
    assert "retry" in document.ocr_notes.lower()
    assert "Simulated extraction finalization failure" not in document.ocr_notes


@pytest.mark.parametrize("changed_state", ["newer", "deleted"])
def test_old_finalization_failure_cannot_change_newer_or_deleted_document(users, settings, tmp_path, changed_state):
    settings.MEDIA_ROOT = tmp_path
    document = Document.objects.create(
        title="Late finalization error", office=users["med"].office, year=2026,
        uploaded_by=users["med"], ocr_text="Original text", ocr_status=OcrStatus.DONE,
    )
    DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("evidence.txt", b"Private saved evidence"),
        original_name="evidence.txt", is_primary=True,
    )
    old_generation = document.extraction_generation
    # Exercise the post-rollback error boundary after another committed action.
    # The main cases above prove the real task encloses result persistence in
    # this boundary; this case checks which fresh rows the handler may mutate.
    with pytest.raises(RuntimeError, match="Old finalization failed"):
        with _extraction_finalization(document.pk, old_generation):
            if changed_state == "newer":
                with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
                    text="Newer successfully committed text", status=OcrStatus.DONE, pages=2,
                )):
                    extract_document_task(document.pk, user_id=users["med"].pk, replace=True)
            else:
                Document.objects.filter(pk=document.pk).update(is_active=False)
            raise RuntimeError("Old finalization failed")
    document.refresh_from_db()
    assert document.ocr_status == OcrStatus.DONE
    assert not document.ocr_notes.strip()
    assert document.extraction_failed_generation is None
    if changed_state == "newer":
        assert document.ocr_text == "Newer successfully committed text"
    else:
        assert document.is_active is False


@pytest.mark.parametrize("failed_write", ["index", "suggestion"])
def test_synchronous_retry_finalization_failure_rolls_back_whole_service(users, settings, tmp_path, failed_write):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    document = Document.objects.create(
        title="Synchronous rollback", office=users["med"].office, year=2026,
        uploaded_by=users["med"], ocr_text="Synchronous original text", ocr_status=OcrStatus.DONE,
    )
    document_file = DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("evidence.txt", b"Private saved evidence"),
        original_name="evidence.txt", is_primary=True, page_count=1, extracted_chars=22,
    )
    document.rebuild_index()
    before = Document.objects.filter(pk=document.pk).values(
        "ocr_status", "ocr_text", "ocr_notes", "page_count", "search_vector",
        "extraction_generation", "extraction_failed_generation", "updated_at",
    ).get()
    file_before = DocumentFile.objects.filter(pk=document_file.pk).values(
        "file", "page_count", "extracted_chars", "updated_at",
    ).get()
    failed_operation = (
        "apps.documents.models.Document.rebuild_index" if failed_write == "index"
        else "apps.documents.tasks.MetadataSuggestion.objects.create"
    )
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
        text="Uncommitted synchronous retry text", status=OcrStatus.DONE, pages=3,
    )), patch(failed_operation, side_effect=RuntimeError("Simulated synchronous finalization failure")):
        with pytest.raises(RuntimeError, match="Simulated synchronous finalization failure"):
            re_extract_document(document, user=users["med"])
    after = Document.objects.filter(pk=document.pk).values(*before).get()
    assert after == before
    assert DocumentFile.objects.filter(pk=document_file.pk).values(*file_before).get() == file_before
    assert document_file.file.storage.exists(document_file.file.name)
    assert not MetadataSuggestion.objects.filter(document=document).exists()
