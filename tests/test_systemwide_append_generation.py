"""Append workers must not publish results after a newer full retry wins."""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.documents.extraction import ExtractionResult
from apps.documents.models import Document, DocumentFile, MetadataSuggestion, OcrStatus
from apps.documents.services import add_file_to_document, re_extract_document
from apps.documents.tasks import extract_document_task


@pytest.fixture
def document(users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    document = Document.objects.create(
        title="Append generation regression", year=2026, office=users["med"].office,
        uploaded_by=users["med"], ocr_text="Previous complete text", ocr_status=OcrStatus.DONE,
    )
    DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("append.txt", b"Stored attachment contents"),
        original_name="append.txt", is_primary=True,
    )
    return document


def snapshot(document):
    document.refresh_from_db()
    return (
        Document.objects.filter(pk=document.pk).values(
            "ocr_text", "ocr_status", "ocr_engine", "ocr_confidence", "ocr_notes",
            "page_count", "extraction_generation", "search_vector", "updated_at",
        ).get(),
        list(document.files.order_by("pk").values(
            "pk", "page_count", "extracted_chars", "updated_at",
        )),
        MetadataSuggestion.objects.filter(document=document).count(),
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("queued", [False, True], ids=["legacy-direct", "queued"])
def test_old_append_cannot_change_newer_full_retry_results(users, document, settings, queued):
    actor_id = users["med"].pk
    append_kwargs = {"user_id": actor_id, "file_ids": [document.files.get().pk]}
    if queued:
        settings.ENABLE_BACKGROUND_TASKS = True
        with patch("django_q.tasks.async_task") as queue:
            appended = add_file_to_document(
                document, SimpleUploadedFile("queued.txt", b"Queued attachment contents"),
                user=users["med"],
            )
        append_kwargs = queue.call_args.kwargs
        assert append_kwargs["file_ids"] == [appended.pk]
    newer_finished = False
    before = None

    def provider(*args, **kwargs):
        nonlocal newer_finished, before
        if not newer_finished:
            # Pause A at its provider boundary. Complete B on this same thread
            # before releasing A's old result, avoiding timing-dependent waits
            # and extra database connections while preserving the write order.
            newer_finished = True
            newer = extract_document_task(document.pk, user_id=actor_id, replace=True)
            assert newer["status"] == OcrStatus.DONE
            before = snapshot(document)
            assert before[0]["ocr_text"].count("Newer full retry result") == len(before[1])
            assert all(row["page_count"] == 7 for row in before[1])
            return ExtractionResult(
                text="Obsolete append result", pages=2, status="DONE", engine="old",
                confidence=0.2, notes=["old provider note"],
            )
        return ExtractionResult(
            text="Newer full retry result", pages=7, status="DONE", engine="new",
            confidence=0.9, notes=["new provider note"],
        )

    with patch("apps.documents.tasks.extract_document_text", side_effect=provider):
        obsolete = extract_document_task(document.pk, **append_kwargs)
    assert before is not None, "old append never reached its provider boundary"
    after = snapshot(document)
    assert after == before, "old append changed newer text, metrics, notes, status, suggestions or index"
    assert obsolete["status"] == OcrStatus.SKIPPED



@pytest.mark.django_db
def test_queued_append_is_rejected_when_newer_retry_finishes_before_delivery(
    users, document, settings, django_capture_on_commit_callbacks,
):
    settings.ENABLE_BACKGROUND_TASKS = True
    with patch("django_q.tasks.async_task") as queue, django_capture_on_commit_callbacks(execute=True):
        add_file_to_document(document, SimpleUploadedFile("later.txt", b"Later file"), user=users["med"])
        re_extract_document(document, user=users["med"])
    append, retry = queue.call_args_list
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
        text="Newest searchable result", pages=7, status="DONE", notes=["new note"],
    )) as provider:
        extract_document_task(*retry.args[1:], **retry.kwargs)
        before = snapshot(document)
        provider.reset_mock()
        result = extract_document_task(*append.args[1:], **append.kwargs)
    provider.assert_not_called()
    assert snapshot(document) == before
    assert result["status"] == OcrStatus.SKIPPED


@pytest.mark.django_db
def test_sibling_queued_appends_capture_generation_without_superseding_each_other(
    users, document, settings, django_capture_on_commit_callbacks,
):
    settings.ENABLE_BACKGROUND_TASKS = True
    Document.objects.filter(pk=document.pk).update(extraction_generation=5)
    with patch("django_q.tasks.async_task") as queue, django_capture_on_commit_callbacks(execute=True):
        add_file_to_document(document, SimpleUploadedFile("first.txt", b"First new file"), user=users["med"])
        add_file_to_document(document, SimpleUploadedFile("second.txt", b"Second new file"), user=users["med"])
    assert [call.kwargs["generation"] for call in queue.call_args_list] == [5, 5]
    document.refresh_from_db()
    assert document.extraction_generation == 5
    with patch("apps.documents.tasks.extract_document_text", side_effect=lambda file, name, **kwargs: ExtractionResult(
        text=f"Unique result from {name}", pages=1, status="DONE",
    )):
        for queued in queue.call_args_list:
            assert extract_document_task(*queued.args[1:], **queued.kwargs)["status"] == OcrStatus.DONE
    document.refresh_from_db()
    assert "Unique result from first.txt" in document.ocr_text
    assert "Unique result from second.txt" in document.ocr_text
