import io
import random
import struct
import zipfile
import zlib

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

from apps.core.file_previews import sanitized_docx, tabular_preview
from apps.core.utils import validate_upload


def office_zip(payload, compression, *, part="word/document.xml"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(part, payload)
    return output.getvalue()


def disguise_part_size(data, part, visible_prefix):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        central_start = archive.start_dir
    edited = bytearray(data)
    central_header = edited.index(part.encode("ascii"), central_start) - 46
    assert edited[central_header:central_header + 4] == b"PK\x01\x02"
    struct.pack_into("<I", edited, central_header + 16, zlib.crc32(visible_prefix))
    struct.pack_into("<I", edited, central_header + 24, len(visible_prefix))
    return bytes(edited)


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_docx_preview_rejects_parts_that_expand_past_declared_size(compression):
    part = "word/document.xml"
    visible = b"<document><p>Reported text</p></document>"
    data = disguise_part_size(office_zip(visible + b" " * 250_000, compression), part, visible)
    # The ZIP convenience reader trims to the declared size and accepts its CRC.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.read(part) == visible

    upload = ContentFile(data, name="original.docx")
    upload.seek(8)
    validate_upload(upload)
    assert upload.tell() == 8
    with pytest.raises(ValidationError, match="damaged compressed part"):
        sanitized_docx(data)


@pytest.mark.parametrize("compression", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_docx_preview_rejects_unsupported_compression_before_opening_members(compression, monkeypatch):
    data = office_zip(b"<document/>", compression)

    def unsafe_member_read(*args, **kwargs):
        pytest.fail("Office preview opened a ZIP member before checking compression")

    monkeypatch.setattr(zipfile.ZipFile, "open", unsafe_member_read)
    with pytest.raises(ValidationError, match="unsupported package compression"):
        sanitized_docx(data)


def test_xlsx_preview_checks_declared_size_before_starting_openpyxl(monkeypatch):
    part = "xl/workbook.xml"
    visible = b"<workbook/>"
    data = disguise_part_size(office_zip(visible + b" " * 250_000, zipfile.ZIP_DEFLATED, part=part), part, visible)

    def unsafe_workbook_read(*args, **kwargs):
        pytest.fail("Workbook preview started openpyxl before checking the ZIP member sizes")

    monkeypatch.setattr("openpyxl.load_workbook", unsafe_workbook_read)
    with pytest.raises(ValidationError, match="damaged compressed part"):
        tabular_preview(data, "xlsx")


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_docx_preview_supports_zip64_data_descriptors_and_multichunk_parts(compression):
    class NonSeekableBuffer(io.BytesIO):
        def seek(self, *args, **kwargs):
            raise io.UnsupportedOperation("not seekable")

    output = NonSeekableBuffer()
    part = "word/document.xml"
    visible = b"<document><p>Report" + b" " * (3 * 64 * 1024) + b"</p></document>"
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        # An incompressible part exercises reads across compressed input chunks.
        archive.writestr("word/media/data.bin", random.Random(88).randbytes(3 * 64 * 1024))
        with archive.open(part, "w", force_zip64=True) as member:
            member.write(visible)

    data = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.getinfo(part)
        assert info.flag_bits & 0x08  # Sizes follow the payload in a data descriptor.
        assert struct.unpack_from("<II", data, info.header_offset + 18) == (0xFFFFFFFF, 0xFFFFFFFF)
    with zipfile.ZipFile(io.BytesIO(sanitized_docx(data))) as archive:
        assert b"Report" in archive.read(part)
