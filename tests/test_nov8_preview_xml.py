import io
import struct
import zipfile
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.urls import resolve
from openpyxl import Workbook

from apps.accounts.models import User
from apps.core.file_access import FileResource, file_url
from apps.core.file_previews import sanitized_docx, tabular_preview
from apps.core.file_views import FileContentView, FileVersionDownloadView
from apps.core.utils import validate_upload
from apps.tracking.models import TrackingRecord


def docx_with_document_xml(document_xml, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
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


def xlsx_sample(compression=zipfile.ZIP_DEFLATED):
    workbook = Workbook()
    workbook.active["A1"] = "Workbook preview"
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    if compression == zipfile.ZIP_DEFLATED:
        return output.getvalue()
    repackaged = io.BytesIO()
    with zipfile.ZipFile(output) as source, zipfile.ZipFile(repackaged, "w", compression=compression) as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
    return repackaged.getvalue()


def test_xlsx_preview_reports_unknown_zip_compression():
    data = with_unknown_zip_compression(xlsx_sample(), "xl/workbook.xml")
    validate_upload(ContentFile(data, name="preview.xlsx"))

    with pytest.raises(ValidationError, match="unsupported package compression"):
        tabular_preview(data, "xlsx")


def damaged_compressed_member(data, member):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entry = archive.getinfo(member)
    local_header = entry.header_offset
    filename_size, extra_size = struct.unpack_from("<HH", data, local_header + 26)
    compressed_start = local_header + 30 + filename_size + extra_size
    # DEFLATE rejects a reserved block type. LZMA rejects the invalid first
    # filter property after its four-byte ZIP/LZMA header.
    damaged_at = compressed_start + (4 if entry.compress_type == zipfile.ZIP_LZMA else 0)
    return data[:damaged_at] + b"\xff" + data[damaged_at + 1:]


def xlsx_with_unknown_worksheet_encoding(data):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w") as target:
        for name in source.namelist():
            content = source.read(name)
            if name == "xl/worksheets/sheet1.xml":
                content = b'<?xml version="1.0" encoding="no-such-encoding"?>' + content
            target.writestr(name, content)
    return output.getvalue()


@pytest.mark.parametrize("extension, problem, preview_purpose, status", [
    ("docx", zipfile.ZIP_DEFLATED, "content", 422),
    ("docx", zipfile.ZIP_LZMA, "content", 422),
    ("xlsx", zipfile.ZIP_DEFLATED, "preview", 200),
    ("xlsx", zipfile.ZIP_LZMA, "preview", 200),
    ("xlsx", "encoding", "preview", 200),
])
def test_malformed_office_preview_preserves_original_download(rf, monkeypatch, extension, problem, preview_purpose, status):
    if problem == "encoding":
        data = xlsx_with_unknown_worksheet_encoding(xlsx_sample())
    elif extension == "docx":
        data = damaged_compressed_member(docx_with_document_xml(b"<document/>", problem), "word/document.xml")
    else:
        data = damaged_compressed_member(xlsx_sample(problem), "xl/workbook.xml")
    filename = f"preview.{extension}"
    validate_upload(ContentFile(data, name=filename))
    item = SimpleNamespace(pk=1, original_name=filename, file=ContentFile(data), version_number=1,
                           available_content=True, storage_identity="")
    resource = FileResource(item, TrackingRecord(pk=1), "tracking", 1)
    user = User(pk=1, username="preview-user", role="USER")
    # Substitute the database reads and audit insert, keeping signed links,
    # validation, parsers, page rendering and original download real.
    monkeypatch.setattr("apps.core.file_views.resource_for", lambda kind, pk, user: resource)
    monkeypatch.setattr("apps.accounts.capabilities.require_capability", lambda user, area, action: None)
    monkeypatch.setattr("apps.accounts.capabilities.capability_allowed", lambda user, area, action: True)
    monkeypatch.setattr("apps.accounts.capabilities.permission_snapshot", lambda user: {})
    monkeypatch.setattr("apps.core.notifications.in_app_enabled", lambda user: False)
    monkeypatch.setattr(FileVersionDownloadView, "log_download", staticmethod(lambda request, resource: None))

    def file_response(purpose):
        request = rf.get(file_url("tracking", item.pk, user, purpose))
        request.user = user
        match = resolve(request.path)
        return match.func(request, **match.kwargs)

    preview = file_response(preview_purpose)
    assert preview.status_code == status
    assert b"Preview unavailable." in preview.content
    assert preview["Cache-Control"] == "private, no-store"
    download = file_response("download")
    try:
        assert download.status_code == 200
        assert download["Content-Disposition"].startswith("attachment;")
        assert b"".join(download.streaming_content) == data
    finally:
        download.close()
