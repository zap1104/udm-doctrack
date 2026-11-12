import io
import struct
import zipfile
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from openpyxl import Workbook

from apps.core.file_previews import sanitized_docx, tabular_preview
from apps.core.file_views import FileContentView
from apps.core.utils import validate_upload


def docx_with_document_xml(document_xml):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document_xml)
    data = output.getvalue()
    validate_upload(ContentFile(data, name="preview.docx"))
    return data


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_docx_preview_rejects_dtd_in_utf16(encoding):
    data = docx_with_document_xml(
        (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<!DOCTYPE document [<!ENTITY marker "expanded marker">]>'
            "<document><p>&marker;</p></document>"
        ).encode(encoding)
    )
    with pytest.raises(ValidationError, match="unsupported XML declarations"):
        sanitized_docx(data)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_docx_preview_preserves_supported_xml_encodings(encoding):
    data = docx_with_document_xml(
        (
            f'<?xml version="1.0" encoding="{encoding}"?>'
            "<document><p>Original &amp; reviewed</p></document>"
        ).encode(encoding)
    )
    with zipfile.ZipFile(io.BytesIO(sanitized_docx(data))) as archive:
        assert b"Original &amp; reviewed" in archive.read("word/document.xml")


def assert_preview_unavailable(data):
    item = SimpleNamespace(original_name="preview.docx", file=ContentFile(data))
    response = FileContentView().respond(None, SimpleNamespace(item=item))
    assert response.status_code == 422
    assert b"The original file is unchanged" in response.content
    assert response["Cache-Control"] == "private, no-store"
    with item.file.open("rb") as original:
        assert original.read() == data


def test_docx_content_reports_unknown_xml_encoding():
    data = docx_with_document_xml(
        b'<?xml version="1.0" encoding="no-such-encoding"?><document/>'
    )
    assert_preview_unavailable(data)


def with_unknown_zip_compression(data, member):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        local_header = archive.getinfo(member).header_offset
    data = bytearray(data)
    # The fixture's last occurrence of the member name is in the central directory.
    central_header = data.rfind(member.encode("ascii")) - 46
    assert data[central_header:central_header + 4] == b"PK\x01\x02"
    struct.pack_into("<H", data, central_header + 10, 99)
    struct.pack_into("<H", data, local_header + 8, 99)
    return bytes(data)


def test_docx_content_reports_unknown_zip_compression():
    data = docx_with_document_xml(b"<document/>")
    assert_preview_unavailable(with_unknown_zip_compression(data, "word/document.xml"))


def test_docx_content_reports_excessive_xml_nesting():
    data = docx_with_document_xml(b"<p>" * 2000 + b"text" + b"</p>" * 2000)
    assert_preview_unavailable(data)


def test_xlsx_preview_reports_unknown_zip_compression():
    workbook = Workbook()
    workbook.active["A1"] = "Workbook preview"
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    data = with_unknown_zip_compression(output.getvalue(), "xl/workbook.xml")
    validate_upload(ContentFile(data, name="preview.xlsx"))

    with pytest.raises(ValidationError, match="unsupported package compression"):
        tabular_preview(data, "xlsx")
