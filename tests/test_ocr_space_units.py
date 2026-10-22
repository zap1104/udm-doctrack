"""Wire-level fixture tests for the guarded OCR.space unit adapter."""
import io
import json
from importlib.util import find_spec
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from apps.documents.processing_contracts import ExtractionPolicy
from tests.intelligence_fixtures import pdf_bytes


def adapter():
    assert find_spec("apps.documents.ocr_provider"), "Guarded unit OCR adapter is missing"
    from apps.documents.ocr_provider import ocr_unit
    return ocr_unit


@pytest.fixture(autouse=True)
def provider_settings(settings):
    settings.OCR_SPACE_API_KEY = "fixture-secret-not-live"
    settings.OCR_SPACE_ENDPOINT = "https://api.ocr.space/parse/image"


def response(*, overall=1, result=1, text="Fully readable body text from the authored fixture page.", results=None):
    return io.BytesIO(json.dumps({"OCRExitCode": overall, "IsErroredOnProcessing": False,
        "ParsedResults": results if results is not None else [{"FileParseExitCode": result, "ParsedText": text}]}).encode())


def call(payload=b"fixture", **kwargs):
    return adapter()(payload, "fixture.png", source_locator="image:frame:1", policy=ExtractionPolicy(),
        language_hint="eng", authorize_dispatch=kwargs.pop("authorize_dispatch", lambda: True), **kwargs)


def test_success_checks_result_codes_and_keeps_absent_confidence_null():
    with patch("urllib.request.urlopen", return_value=response()):
        result = call()
    assert result.coverage == "complete"
    assert result.provider_confidence is None
    assert "authored fixture" in result.normalized_text


@pytest.mark.parametrize("endpoint", ["https://[broken", "https://api.ocr.space:bad/parse/image"])
def test_malformed_endpoint_is_a_blocked_outcome_without_network(endpoint, settings):
    settings.OCR_SPACE_ENDPOINT = endpoint
    with patch("urllib.request.urlopen", side_effect=AssertionError("No network permitted")):
        assert call().state == "blocked"


@pytest.mark.parametrize("overall,result_code", [(2, 1), (3, -10), (1, -30)])
def test_partial_or_failed_provider_status_preserves_text_but_not_complete(overall, result_code):
    with patch("urllib.request.urlopen", return_value=response(overall=overall, result=result_code)):
        result = call()
    assert result.coverage != "complete"
    assert result.warnings
    assert "authored fixture" in result.raw_text


def test_missing_results_is_not_success_despite_http_200():
    with patch("urllib.request.urlopen", return_value=response(results=[])):
        result = call()
    assert result.coverage != "complete"
    assert not result.normalized_text


def test_actual_multipart_body_respects_free_budget():
    submitted = []
    def transport(request, **kwargs):
        submitted.append(request.data)
        return response()
    with patch("urllib.request.urlopen", side_effect=transport):
        accepted = call(b"x" * 998_000)
        refused = call(b"x" * 1_000_000)
    assert accepted.coverage == "complete"
    assert len(submitted) == 1
    assert len(submitted[0]) <= 1_000_000
    assert refused.state == "action_required"


def test_revoked_guard_blocks_retry_and_returns_policy_state():
    permissions = iter([True, False])
    with patch("urllib.request.urlopen", side_effect=URLError("temporary outage")), patch("time.sleep"):
        result = call(authorize_dispatch=lambda: next(permissions))
    assert result.state == "blocked"
    assert result.metadata["provider_attempts"] == 1
    assert not result.normalized_text


def test_retry_exhaustion_is_finite_and_sanitized():
    def unavailable(*args, **kwargs):
        raise URLError("fixture-secret-not-live")
    with patch("urllib.request.urlopen", side_effect=unavailable), patch("time.sleep"):
        result = call()
    assert result.state == "retry_wait"
    assert result.metadata["provider_attempts"] == 3
    assert "fixture-secret-not-live" not in str(result)


def test_rate_limit_becomes_resumable_wait_instead_of_request_loop():
    error = HTTPError("https://api.ocr.space/parse/image", 429, "quota", {"Retry-After": "3600"}, None)
    with patch("urllib.request.urlopen", side_effect=error), patch("time.sleep"):
        result = call()
    assert result.state == "retry_wait"
    assert result.metadata["provider_attempts"] == 1
    assert result.metadata["retry_after_seconds"] == 3600


def test_pdf_payload_with_multiple_pages_is_rejected_before_dispatch():
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    writer.add_blank_page(width=200, height=300)
    output = io.BytesIO()
    writer.write(output)
    result = adapter()(output.getvalue(), "two.pdf", source_locator="pdf:page:1", policy=ExtractionPolicy(),
        language_hint="eng", authorize_dispatch=lambda: True)
    assert result.state == "action_required"
    assert result.metadata.get("provider_attempts", 0) == 0


def test_prepared_pdf_keeps_exact_source_page_mapping():
    adapter()
    from apps.documents import ocr_provider
    from apps.documents.processing_contracts import UnitResult
    assert hasattr(ocr_provider, "prepare_unit_payload"), "Bounded page preparation is missing"
    from pypdf import PdfReader
    original = pdf_bytes(scanned=False, second_text="Second page evidence")
    source = io.BytesIO(original)
    prepared = ocr_provider.prepare_unit_payload(source, "source.pdf",
        UnitResult(locator_key="pdf:page:2", ordinal=2, kind="pdf_page", metadata={"page_number": 2}),
        policy=ExtractionPolicy())
    pdf = PdfReader(io.BytesIO(prepared.data))
    assert len(pdf.pages) == 1
    assert "Second page evidence" in pdf.pages[0].extract_text()
    assert source.getvalue() == original
    assert len(prepared.data) <= 1_000_000


def test_unusable_provider_confidence_does_not_discard_readable_text():
    row = {"FileParseExitCode": 1, "ParsedText": "Readable and complete page text from the authored provider fixture.",
        "TextOverlay": {"Lines": [{"Words": [{"Confidence": "NaN"}, {"Confidence": "unknown"}]}]}}
    with patch("urllib.request.urlopen", return_value=response(results=[row])):
        result = call()
    assert result.coverage == "complete"
    assert result.provider_confidence is None


def test_partial_response_truncation_is_explicit():
    from dataclasses import replace
    with patch("urllib.request.urlopen", return_value=response(overall=2, text="text " * 100)):
        result = adapter()(b"fixture", "page.png", source_locator="image:frame:1",
            policy=replace(ExtractionPolicy(), max_unit_chars=50), language_hint="eng", authorize_dispatch=lambda: True)
    assert result.truncated is True
    assert len(result.raw_text) == 50


def test_malformed_optional_overlay_does_not_erase_successful_text():
    row = {"FileParseExitCode": 1, "ParsedText": "Readable and complete authored text without optional word confidence.",
        "TextOverlay": ["unusable optional overlay"]}
    with patch("urllib.request.urlopen", return_value=response(results=[row])):
        result = call()
    assert "authored text" in result.normalized_text
    assert result.provider_confidence is None


def test_transparent_signature_image_uses_visible_white_background():
    adapter()
    from PIL import Image

    from apps.documents.ocr_provider import prepare_unit_payload
    from apps.documents.processing_contracts import UnitResult
    source = io.BytesIO()
    image = Image.new("RGBA", (30, 30), (0, 0, 0, 0))
    image.putpixel((15, 15), (0, 0, 0, 255))
    image.save(source, "PNG")
    result = prepare_unit_payload(source, "signature.png",
        UnitResult(locator_key="image:frame:1", ordinal=1, kind="image_frame", metadata={"frame": 1}), policy=ExtractionPolicy())
    prepared = Image.open(io.BytesIO(result.data))
    assert prepared.getpixel((0, 0)) == (255, 255, 255)
    assert prepared.getpixel((15, 15)) == (0, 0, 0)
