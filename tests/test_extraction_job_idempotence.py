"""Repeated or delayed append jobs must preserve unique text without duplicates."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.documents.models import Document, DocumentFile, OcrStatus
from apps.documents.tasks import extract_document_task


@pytest.fixture
def two_file_document(users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    primary_text = "Original primary attachment searchable passage must appear once."
    secondary_text = "Unique secondary attachment searchable passage must appear once."
    document = Document.objects.create(
        title="Repeatable extraction", office=users["med"].office, uploaded_by=users["med"],
        year=2026, ocr_text=primary_text, ocr_status=OcrStatus.DONE,
    )
    primary = DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("first.txt", primary_text.encode()),
        original_name="first.txt", page_count=1, is_primary=True,
    )
    secondary = DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("second.txt", secondary_text.encode()),
        original_name="second.txt",
    )
    return document, primary, secondary, primary_text, secondary_text


@pytest.mark.django_db
def test_duplicate_delivery_of_selective_append_does_not_repeat_text(users, two_file_document):
    document, _, secondary, primary_text, secondary_text = two_file_document
    extract_document_task(document.pk, user_id=users["med"].pk, file_ids=[secondary.pk])
    extract_document_task(document.pk, user_id=users["med"].pk, file_ids=[secondary.pk])

    document.refresh_from_db()
    assert document.ocr_text.count(primary_text) == 1
    assert document.ocr_text.count(secondary_text) == 1
    assert document.ocr_status == OcrStatus.DONE
    assert document.page_count == 2


@pytest.mark.django_db
def test_delayed_append_after_full_replacement_does_not_repeat_included_file(users, two_file_document):
    document, _, secondary, primary_text, secondary_text = two_file_document
    extract_document_task(document.pk, user_id=users["med"].pk, replace=True)
    document.refresh_from_db()
    complete_text = document.ocr_text

    extract_document_task(document.pk, user_id=users["med"].pk, file_ids=[secondary.pk])

    document.refresh_from_db()
    assert document.ocr_text == complete_text
    assert document.ocr_text.count(primary_text) == 1
    assert document.ocr_text.count(secondary_text) == 1


@pytest.mark.django_db
def test_append_selection_keeps_unique_text_when_another_selected_file_is_already_indexed(
    users, two_file_document,
):
    document, primary, secondary, primary_text, secondary_text = two_file_document
    extract_document_task(document.pk, user_id=users["med"].pk, file_ids=[primary.pk, secondary.pk])

    document.refresh_from_db()
    assert document.ocr_text.count(primary_text) == 1
    assert document.ocr_text.count(secondary_text) == 1
