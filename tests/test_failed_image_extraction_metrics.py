"""A failed image retry must retain metrics for the text it keeps searchable."""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.documents.extraction import ExtractionResult
from apps.documents.models import Document, DocumentFile, OcrStatus
from apps.documents.tasks import extract_document_task


@pytest.mark.django_db
@pytest.mark.parametrize("status", [OcrStatus.FAILED, OcrStatus.SKIPPED])
def test_unsuccessful_image_replacement_preserves_known_text_and_file_metrics(
    users, settings, tmp_path, status,
):
    settings.MEDIA_ROOT = tmp_path
    old_text = "Previously indexed image contents remain searchable when OCR cannot finish."
    document = Document.objects.create(
        title="Previously read image", office=users["med"].office, uploaded_by=users["med"],
        year=2026, ocr_text=old_text, ocr_status=OcrStatus.DONE, page_count=1,
        allow_external_ocr=True,
    )
    image = DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("scan.png", b"stored-image-placeholder"),
        original_name="scan.png", is_primary=True, page_count=1, extracted_chars=len(old_text),
    )

    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(
        status=status, engine="ocr.space", notes=["Image OCR did not finish."],
    )):
        result = extract_document_task(document.pk, user_id=users["med"].pk, replace=True)

    document.refresh_from_db()
    image.refresh_from_db()
    assert result["status"] == status
    assert document.ocr_text == old_text
    assert image.page_count == 1
    assert image.extracted_chars == len(old_text)
    assert document.page_count == 1
