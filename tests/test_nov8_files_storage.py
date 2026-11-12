"""Extraction must not reinterpret private object keys after storage changes."""

import hashlib
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.documents.extraction import ExtractionResult
from apps.documents.models import Document, DocumentFile
from apps.documents.processing import process_run, request_processing
from apps.documents.tasks import extract_document_task

pytestmark = pytest.mark.django_db


def stored_document(users, settings, tmp_path, collection):
    settings.MEDIA_ROOT = tmp_path / "original-storage"
    document = Document.objects.create(
        title="Recorded storage binding", office=users["med"].office,
        uploaded_by=users["med"], year=2026, collection=collection,
        ocr_text="Last verified extraction", ocr_status="DONE",
    )
    payload = b"Original authorized document"
    file = DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("evidence.txt", payload),
        original_name="evidence.txt", checksum=hashlib.sha256(payload).hexdigest(),
        uploaded_by=users["med"],
    )
    return document, file


def reconfigure_storage(file, settings, tmp_path):
    replacement = tmp_path / "different-storage"
    storage = FileSystemStorage(location=replacement)
    storage.save(file.file.name, ContentFile(b"Unrelated object with the same storage key"))
    settings.MEDIA_ROOT = replacement


@pytest.mark.parametrize("collection", ["OVPA", "EXTERNAL"])
def test_extraction_request_rejects_changed_storage_binding(users, settings, tmp_path, collection):
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    document, file = stored_document(users, settings, tmp_path, collection)
    reconfigure_storage(file, settings, tmp_path)

    with pytest.raises(ValidationError, match="storage"):
        request_processing(document, user=users["med_admin"])

    document.refresh_from_db()
    assert document.ocr_text == "Last verified extraction"
    assert document.ocr_status == "DONE"
    assert not document.extraction_runs.exists()


@pytest.mark.parametrize("collection", ["OVPA", "EXTERNAL"])
def test_claimed_source_extraction_does_not_open_a_changed_backend(users, settings, tmp_path, collection):
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    settings.ENABLE_BACKGROUND_TASKS = False
    document, file = stored_document(users, settings, tmp_path, collection)
    run = request_processing(document, user=users["med_admin"])[0]
    reconfigure_storage(file, settings, tmp_path)

    with patch.object(file.file.storage, "open", wraps=file.file.storage.open) as opened:
        result = process_run(run.pk, expected_generation=run.generation)

    opened.assert_not_called()
    assert result["status"] in {"blocked", "skipped"}
    document.refresh_from_db()
    assert document.ocr_text == "Last verified extraction"
    assert not run.units.exists()


@pytest.mark.parametrize("collection", ["OVPA", "EXTERNAL"])
def test_legacy_extraction_does_not_read_or_publish_changed_backend(users, settings, tmp_path, collection):
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = False
    document, file = stored_document(users, settings, tmp_path, collection)
    reconfigure_storage(file, settings, tmp_path)

    with patch.object(file.file.storage, "open", wraps=file.file.storage.open) as opened, patch(
        "apps.documents.tasks.extract_document_text",
        return_value=ExtractionResult(text="Unrelated foreign extraction", status="DONE"),
    ) as extracted:
        result = extract_document_task(document.pk, user_id=users["med_admin"].pk, replace=True)

    opened.assert_not_called()
    extracted.assert_not_called()
    assert result["status"] == "FAILED"
    document.refresh_from_db()
    assert document.ocr_text == "Last verified extraction"
    assert document.ocr_status == "FAILED"
    assert "storage" in document.ocr_notes.lower()
