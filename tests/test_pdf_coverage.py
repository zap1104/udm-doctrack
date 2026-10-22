"""Real PDF placements and rendering, rather than image-resource counts."""
import io
from dataclasses import replace
from importlib.util import find_spec
from unittest.mock import patch

import pytest

from tests.intelligence_fixtures import pdf_bytes


def reader():
    assert find_spec("apps.documents.source_readers"), "Source-unit reader is missing"
    from apps.documents.source_readers import read_source_units
    return read_source_units


@pytest.mark.parametrize("nested", [False, True])
def test_header_with_scanned_body_requires_ocr_and_placement_union(nested):
    from apps.documents.processing_contracts import ExtractionPolicy
    batch = reader()(io.BytesIO(pdf_bytes(nested=nested)), "mixed.pdf", policy=ExtractionPolicy())
    page = batch.units[0]
    assert len(page.normalized_text) >= 40
    assert page.metadata["ocr_needed"] is True
    assert 0.60 <= page.metadata["image_coverage"] <= 0.65
    assert page.coverage != "complete"


def test_late_native_page_remains_available():
    from apps.documents.processing_contracts import ExtractionPolicy
    batch = reader()(io.BytesIO(pdf_bytes(scanned=False, second_text="late appendix phrase")),
        "long.pdf", policy=ExtractionPolicy())
    assert batch.expected_units == 2
    assert batch.units[-1].locator_key == "pdf:page:2"
    assert "late appendix phrase" in batch.units[-1].normalized_text


def test_rendering_pixel_limit_does_not_silently_downsample():
    from apps.documents.processing_contracts import ExtractionPolicy
    policy = replace(ExtractionPolicy(), max_image_pixels=100)
    batch = reader()(io.BytesIO(pdf_bytes()), "mixed.pdf", policy=policy)
    assert batch.coverage != "complete"
    assert any("pixel" in note.lower() for unit in batch.units for note in unit.warnings)


def test_missing_renderer_preserves_native_text_with_warning():
    from apps.documents.processing_contracts import ExtractionPolicy
    with patch.dict("sys.modules", {"pypdfium2": None}):
        batch = reader()(io.BytesIO(pdf_bytes(scanned=False)), "native.pdf", policy=ExtractionPolicy())
    assert "digital header" in batch.units[0].normalized_text
    assert batch.coverage != "complete"
    assert any("renderer" in note.lower() for note in batch.units[0].warnings)


def test_near_blank_page_is_not_verified_blank():
    from pypdf import PdfWriter

    from apps.documents.processing_contracts import ExtractionPolicy
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    output = io.BytesIO()
    writer.write(output)
    batch = reader()(output, "blank.pdf", policy=ExtractionPolicy())
    assert batch.coverage != "complete"
    assert batch.units[0].metadata["ocr_needed"]


def test_encrypted_pdf_is_action_required():
    from pypdf import PdfReader, PdfWriter

    from apps.documents.processing_contracts import ExtractionPolicy
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes())))
    writer.encrypt("fixture password")
    output = io.BytesIO()
    writer.write(output)
    batch = reader()(output, "locked.pdf", policy=ExtractionPolicy())
    assert batch.units[0].state == "action_required"
    assert "Encrypted" in batch.units[0].warnings[0]


@pytest.mark.parametrize("content", ["vector", "annotation"])
def test_native_text_does_not_hide_vector_or_annotation_content(content):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

    from apps.documents.processing_contracts import ExtractionPolicy

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes(scanned=False))))
    page = writer.pages[0]
    if content == "vector":
        stream = DecodedStreamObject()
        stream.set_data(page.get_contents().get_data() + b"\n10 10 400 400 re f\n")
        page[NameObject("/Contents")] = writer._add_object(stream)
    else:
        page[NameObject("/Annots")] = ArrayObject([writer._add_object(DictionaryObject({
            NameObject("/Subtype"): NameObject("/Ink")}))])
    output = io.BytesIO()
    writer.write(output)
    batch = reader()(output, "unmeasured.pdf", policy=ExtractionPolicy())
    assert batch.coverage != "complete"
    assert batch.units[0].metadata["ocr_needed"]
    assert batch.units[0].warnings
