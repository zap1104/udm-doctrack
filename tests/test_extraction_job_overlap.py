"""A retry's older file snapshot must not remove text from a newer attachment."""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.documents.extraction import ExtractionResult
from apps.documents.models import Document, DocumentFile, OcrStatus
from apps.documents.services import add_file_to_document
from apps.documents.tasks import extract_document_task


@pytest.mark.django_db
def test_replacement_job_keeps_text_from_a_file_added_during_extraction(
    users, settings, tmp_path,
):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    document = Document.objects.create(
        title="Changing attachment set", office=users["med"].office, uploaded_by=users["med"],
        year=2026, ocr_text="Previous primary text", ocr_status=OcrStatus.DONE,
    )
    DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("first.txt", b"Primary attachment contents"),
        original_name="first.txt", page_count=1, is_primary=True,
    )
    secondary_text = "Secondary attachment text must stay searchable after the older retry completes."
    added = False

    def add_secondary_during_primary_extraction(*args, **kwargs):
        nonlocal added
        if not added:
            added = True
            add_file_to_document(
                document, SimpleUploadedFile("second.txt", secondary_text.encode()), user=users["med"],
            )
            document.refresh_from_db()
            assert secondary_text in document.ocr_text
        text = secondary_text if args[1] == "second.txt" else "Fresh primary text"
        return ExtractionResult(text=text, pages=1, status="DONE")

    with patch("apps.documents.tasks.extract_document_text", side_effect=add_secondary_during_primary_extraction):
        result = extract_document_task(document.pk, user_id=users["med"].pk, replace=True)

    document.refresh_from_db()
    assert document.files.count() == 2
    assert secondary_text in document.ocr_text
    assert "Fresh primary text" in document.ocr_text
    assert result["status"] == OcrStatus.DONE


@pytest.mark.django_db
def test_continuous_additions_stop_with_a_retry_without_erasing_previous_text(
    users, settings, tmp_path,
):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    document = Document.objects.create(
        title="Continuous additions", office=users["med"].office, uploaded_by=users["med"],
        year=2026, ocr_text="Last complete searchable text", ocr_status=OcrStatus.DONE,
    )
    DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("first.txt", b"Primary file"),
        original_name="first.txt", is_primary=True,
    )
    additions = 0

    def add_file_during_each_extraction(*args, **kwargs):
        nonlocal additions
        # Keep a broken unbounded implementation finite, while uploads continue
        # through every pass of the bounded production retry.
        if additions < 6:
            additions += 1
            name = f"added-{additions}.txt"
            DocumentFile.objects.create(
                document=document, file=SimpleUploadedFile(name, b"New attachment contents"),
                original_name=name,
            )
        return ExtractionResult(text=f"Fresh text from {args[1]}", pages=1, status="DONE")

    with patch("apps.documents.tasks.extract_document_text", side_effect=add_file_during_each_extraction):
        result = extract_document_task(document.pk, user_id=users["med"].pk, replace=True)

    document.refresh_from_db()
    assert result["status"] == OcrStatus.FAILED
    assert document.ocr_text == "Last complete searchable text"
    assert "retry" in document.ocr_notes.lower()


@pytest.mark.django_db
def test_explicit_replacement_selection_does_not_read_other_files(users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    document = Document.objects.create(
        title="Selected extraction", office=users["med"].office, uploaded_by=users["med"], year=2026,
    )
    first_text = "Selected primary attachment contains the text for this extraction."
    selected = DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("selected.txt", first_text.encode()),
        original_name="selected.txt", is_primary=True,
    )
    excluded = DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("excluded.txt", b"Excluded secondary attachment contents must stay outside this extraction."),
        original_name="excluded.txt", page_count=23,
    )

    extract_document_task(document.pk, user_id=users["med"].pk, file_ids=[selected.pk], replace=True)

    document.refresh_from_db()
    excluded.refresh_from_db()
    assert document.ocr_text == first_text
    assert excluded.page_count == 23
    assert excluded.extracted_chars == 0
