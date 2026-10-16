"""Provider failures must not erase an existing scanned-document search index."""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.documents.extraction import ExtractionResult
from apps.documents.models import Document, DocumentFile, OcrStatus
from apps.documents.services import re_extract_document


@pytest.mark.django_db
@pytest.mark.parametrize("backend", ["auto", "ocrspace"])
def test_failed_scanned_pdf_provider_retry_preserves_existing_text(
    users, settings, tmp_path, backend,
):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.OCR_BACKEND = backend
    settings.OCR_SPACE_API_KEY = "test-configured-provider"
    settings.AZURE_DOCINT_ENDPOINT = ""
    settings.AZURE_DOCINT_KEY = ""
    document = Document.objects.create(
        title="Previously read scan", office=users["med"].office, uploaded_by=users["med"],
        year=2026, allow_external_ocr=True, ocr_status=OcrStatus.DONE,
        ocr_text="Previously indexed scanned document text", page_count=2,
    )
    DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("scan.pdf", b"%PDF-1.7\n"),
        original_name="scan.pdf", page_count=2, is_primary=True,
    )
    with (
        patch("apps.documents.extraction.extract_text_layer", return_value=ExtractionResult(status="EMPTY", pages=2)),
        patch("apps.documents.extraction._ocr_space", return_value=ExtractionResult(
            status="FAILED", engine="ocr.space", notes=["The provider is temporarily unavailable."],
        )),
    ):
        result = re_extract_document(document, user=users["med"])

    document.refresh_from_db()
    assert document.ocr_text == "Previously indexed scanned document text"
    assert result["status"] == OcrStatus.FAILED
