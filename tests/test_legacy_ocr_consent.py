"""An authorized legacy OCR attempt does not authorize later sends or writes."""

import io
import json
import sys
from types import ModuleType

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.documents import extraction
from apps.documents.models import Document, DocumentFile, OcrStatus
from apps.documents.tasks import extract_document_task

pytestmark = pytest.mark.django_db

PRIOR_TEXT = "Previously searchable text"
PROVIDER_TEXT = "External transcription to discard"
LOCAL_TEXT = "A second attachment holds enough native text to index locally."


@pytest.fixture
def queued_scan(users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = False
    settings.OCR_BACKEND = "ocrspace"
    settings.OCR_SPACE_API_KEY = "synthetic-test-key"
    settings.OCR_SPACE_ENDPOINT = "https://ocr.example.test/parse/image"
    settings.OCR_PROVIDER_RETRIES = 1
    actor = users["med"]
    document = Document.objects.create(title="OCR consent", office=actor.office, uploaded_by=actor,
        year=2026, allow_external_ocr=True, ocr_text=PRIOR_TEXT, ocr_status=OcrStatus.PENDING)
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(image, format="PNG")
    scan = DocumentFile.objects.create(document=document, original_name="scan.png",
        file=SimpleUploadedFile("scan.png", image.getvalue()), page_count=2, extracted_chars=len(PRIOR_TEXT))
    return document, scan, actor


def provider_response():
    return io.BytesIO(json.dumps({"ParsedResults": [{"ParsedText": PROVIDER_TEXT}]}).encode())


def withdraw_consent(document):
    Document.objects.filter(pk=document.pk).update(allow_external_ocr=False)


def test_legacy_ocr_retry_rechecks_consent_after_backoff(queued_scan, monkeypatch):
    document, _scan, actor = queued_scan
    dispatches = []

    def provider(request, timeout):
        dispatches.append(Document.objects.get(pk=document.pk).allow_external_ocr)
        assert b"\x89PNG\r\n\x1a\n" in request.data
        if len(dispatches) == 1:
            raise TimeoutError("Simulated temporary provider failure")
        return provider_response()

    monkeypatch.setattr(extraction.urllib.request, "urlopen", provider)
    monkeypatch.setattr(extraction.time, "sleep", lambda delay: withdraw_consent(document))

    result = extract_document_task(document.pk, user_id=actor.pk, generation=document.extraction_generation, replace=True)
    document.refresh_from_db()
    assert dispatches == [True]
    assert not document.allow_external_ocr
    assert result["status"] == OcrStatus.SKIPPED
    assert document.ocr_text == PRIOR_TEXT


@pytest.mark.parametrize("withdrawal", ["provider_response", "later_file"])
def test_legacy_ocr_withdrawal_discards_unpublished_provider_text(queued_scan, monkeypatch, withdrawal):
    document, scan, actor = queued_scan
    if withdrawal == "later_file":
        DocumentFile.objects.create(document=document, original_name="local.txt",
            file=SimpleUploadedFile("local.txt", LOCAL_TEXT.encode()))
        read_native = extraction.extract_text_layer

        def withdraw_during_native_read(file_obj, filename):
            withdraw_consent(document)
            return read_native(file_obj, filename)

        monkeypatch.setattr(extraction, "extract_text_layer", withdraw_during_native_read)

    def provider(request, timeout):
        assert b"\x89PNG\r\n\x1a\n" in request.data
        if withdrawal == "provider_response":
            withdraw_consent(document)
        return provider_response()

    monkeypatch.setattr(extraction.urllib.request, "urlopen", provider)

    result = extract_document_task(document.pk, user_id=actor.pk, generation=document.extraction_generation,
        replace=withdrawal == "provider_response")
    document.refresh_from_db()
    scan.refresh_from_db()
    assert not document.allow_external_ocr
    assert result["status"] == OcrStatus.SKIPPED
    expected = PRIOR_TEXT if withdrawal == "provider_response" else f"{PRIOR_TEXT}\n\n{LOCAL_TEXT}"
    assert document.ocr_text == expected
    assert scan.page_count == 2
    assert scan.extracted_chars == len(PRIOR_TEXT)
    assert all(PROVIDER_TEXT not in suggestion.text_sample for suggestion in document.suggestions.all())


def test_legacy_ocr_withdrawal_keeps_short_native_text(queued_scan, monkeypatch):
    document, _scan, actor = queued_scan
    native_text = "Native provenance"
    short_file = DocumentFile.objects.create(document=document, original_name="short.txt",
        file=SimpleUploadedFile("short.txt", native_text.encode()))

    def provider(request, timeout):
        assert native_text.encode() in request.data
        withdraw_consent(document)
        return provider_response()

    monkeypatch.setattr(extraction.urllib.request, "urlopen", provider)

    result = extract_document_task(document.pk, user_id=actor.pk, file_ids=[short_file.pk],
        generation=document.extraction_generation)
    document.refresh_from_db()
    short_file.refresh_from_db()
    assert not document.allow_external_ocr
    assert result["status"] == OcrStatus.DONE
    assert document.ocr_text == f"{PRIOR_TEXT}\n\n{native_text}"
    assert short_file.extracted_chars == len(native_text)


def test_legacy_ocr_withdrawal_drops_provider_errors_from_native_results(queued_scan, monkeypatch):
    document, _scan, actor = queued_scan
    native_text = "Native provenance"
    short_file = DocumentFile.objects.create(document=document, original_name="short.txt",
        file=SimpleUploadedFile("short.txt", native_text.encode()))

    def provider(request, timeout):
        assert native_text.encode() in request.data
        withdraw_consent(document)
        return io.BytesIO(json.dumps({"IsErroredOnProcessing": True, "ErrorMessage": [PROVIDER_TEXT]}).encode())

    monkeypatch.setattr(extraction.urllib.request, "urlopen", provider)

    result = extract_document_task(document.pk, user_id=actor.pk, file_ids=[short_file.pk],
        generation=document.extraction_generation)
    document.refresh_from_db()
    assert not document.allow_external_ocr
    assert result["status"] == OcrStatus.DONE
    assert document.ocr_text == f"{PRIOR_TEXT}\n\n{native_text}"
    assert PROVIDER_TEXT not in document.ocr_notes


def test_legacy_azure_retries_require_the_worker_authorization_check(queued_scan, settings, monkeypatch):
    from azure.core.exceptions import ServiceRequestError
    from azure.core.pipeline.policies import RetryPolicy

    document, _scan, actor = queued_scan
    settings.OCR_BACKEND = "azure"
    settings.AZURE_DOCINT_ENDPOINT = "https://azure.example.test/parse/image"
    settings.AZURE_DOCINT_KEY = "synthetic-test-key"
    dispatches = []

    class UnavailableClient:
        def __init__(self, *, endpoint, credential, **options):
            self.internal_retries = RetryPolicy(**options).total_retries

        def begin_analyze_document(self, model, *, body, **options):
            assert body.startswith(b"\x89PNG\r\n\x1a\n")
            dispatches.append(Document.objects.get(pk=document.pk).allow_external_ocr)
            withdraw_consent(document)
            if self.internal_retries:
                dispatches.append(Document.objects.get(pk=document.pk).allow_external_ocr)
            raise ServiceRequestError("Simulated transient Azure response")

    # The optional Document Intelligence client is not a project dependency.
    # Simulate its transport with the installed Azure SDK retry configuration.
    sdk = ModuleType("azure.ai.documentintelligence")
    sdk.DocumentIntelligenceClient = UnavailableClient
    monkeypatch.setitem(sys.modules, sdk.__name__, sdk)
    monkeypatch.setattr(extraction.time, "sleep", lambda delay: None)

    result = extract_document_task(document.pk, user_id=actor.pk, generation=document.extraction_generation, replace=True)
    assert dispatches == [True]
    assert result["status"] == OcrStatus.SKIPPED
