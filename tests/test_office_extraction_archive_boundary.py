"""Both extraction paths must validate Office packages before parsing XML."""

import io
import struct
import zipfile
import zlib

import pytest
from docx import Document
from openpyxl import Workbook

from apps.documents.extraction import extract_text_layer
from apps.documents.processing_contracts import ExtractionPolicy
from apps.documents.source_readers import read_source_units


@pytest.mark.parametrize("extension, xml_name", [
    ("docx", "word/document.xml"), ("xlsx", "xl/worksheets/sheet1.xml"),
])
def test_office_extractors_reject_archive_with_underreported_expansion(extension, xml_name):
    if extension == "docx":
        document = Document()
        document.add_paragraph("Readable Office content")
    else:
        document = Workbook()
        document.active["A1"] = "Readable Office content"
    original = io.BytesIO()
    document.save(original)

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(archive_bytes, "w", zipfile.ZIP_DEFLATED) as target:
        xml = source.read(xml_name)
        for name in source.namelist():
            if name != xml_name:
                target.writestr(name, source.read(name))
        target.writestr(xml_name, xml + b" " * 8192)
    forged = bytearray(archive_bytes.getvalue())
    with zipfile.ZipFile(archive_bytes) as archive:
        local_header = archive.getinfo(xml_name).header_offset
    central_header = forged.rindex(b"PK\x01\x02")  # Target member was written last.
    for header, crc_offset, size_offset in ((local_header, 14, 22), (central_header, 16, 24)):
        struct.pack_into("<I", forged, header + crc_offset, zlib.crc32(xml))
        struct.pack_into("<I", forged, header + size_offset, len(xml))

    filename = f"fixture.{extension}"
    assert extract_text_layer(original, filename).status == "DONE"
    assert read_source_units(original, filename, policy=ExtractionPolicy()).coverage == "complete"

    legacy = extract_text_layer(io.BytesIO(forged), filename)
    source_units = read_source_units(io.BytesIO(forged), filename, policy=ExtractionPolicy())
    assert legacy.status == "FAILED"
    assert not legacy.text
    assert source_units.coverage != "complete"
    assert not any(unit.normalized_text for unit in source_units.units)
