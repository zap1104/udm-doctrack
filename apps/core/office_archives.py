"""Verify Office ZIP payloads before handing them to library decompressors."""

import struct
import zipfile
import zlib

_CHUNK_SIZE = 64 * 1024
_LOCAL_HEADER = struct.Struct("<4s2xHH16xHH")
_UNSUPPORTED_FLAGS = (1 << 0) | (1 << 5) | (1 << 6)  # Encryption and patched data.
_DAMAGED_PART = "This Office file contains a damaged compressed part."


class OfficeArchiveError(ValueError):
    """The package cannot be read within the Office processing limits."""


def validate_office_archive_members(
    archive, *, max_members, max_uncompressed_bytes, reject_duplicate_names=False,
):
    members = archive.infolist()
    if (
        len(members) > max_members
        or sum(member.file_size for member in members) > max_uncompressed_bytes
        or any(member.flag_bits & 1 for member in members)
    ):
        raise OfficeArchiveError("This Office file is encrypted or expands beyond the safe processing limit.")
    if reject_duplicate_names and len({member.filename for member in members}) != len(members):
        raise OfficeArchiveError("This Office file contains duplicate package paths.")
    return members


def _verify_member(stream, member, next_header):
    if member.header_offset < 0 or member.header_offset + _LOCAL_HEADER.size > next_header:
        raise OfficeArchiveError(_DAMAGED_PART)
    stream.seek(member.header_offset)
    header = stream.read(_LOCAL_HEADER.size)
    if len(header) != _LOCAL_HEADER.size:
        raise OfficeArchiveError(_DAMAGED_PART)
    signature, flags, compression, name_length, extra_length = _LOCAL_HEADER.unpack(header)
    if (
        compression not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
        or (flags | member.flag_bits) & _UNSUPPORTED_FLAGS
    ):
        raise OfficeArchiveError("This Office file uses unsupported package compression.")

    data_offset = stream.tell() + name_length + extra_length
    if (
        signature != b"PK\x03\x04"
        or compression != member.compress_type
        or data_offset + member.compress_size > next_header
    ):
        raise OfficeArchiveError(_DAMAGED_PART)
    # ZIP64 extra fields and data descriptors do not change the data offset
    # calculation. The central directory supplies the resolved sizes.
    stream.seek(data_offset)
    if compression == zipfile.ZIP_STORED:
        if member.compress_size != member.file_size:
            raise OfficeArchiveError(_DAMAGED_PART)
        checksum = _stored_crc(stream, member.compress_size)
    else:
        checksum = _deflated_crc(stream, member.compress_size, member.file_size)
    if checksum != member.CRC:
        raise OfficeArchiveError(_DAMAGED_PART)


def _stored_crc(stream, remaining):
    checksum = 0
    while remaining:
        data = stream.read(min(_CHUNK_SIZE, remaining))
        if not data:
            raise OfficeArchiveError(_DAMAGED_PART)
        remaining -= len(data)
        checksum = zlib.crc32(data, checksum)
    return checksum


def _deflated_crc(stream, compressed_remaining, output_remaining):
    decoder = zlib.decompressobj(-zlib.MAX_WBITS)
    pending = b""
    checksum = 0
    while not decoder.eof:
        if not pending and compressed_remaining:
            pending = stream.read(min(_CHUNK_SIZE, compressed_remaining))
            if not pending:
                raise OfficeArchiveError(_DAMAGED_PART)
            compressed_remaining -= len(pending)
        # A byte beyond the claimed size is enough to reject the entire part.
        # Keep every allocation bounded, including the final decoder call.
        try:
            data = decoder.decompress(pending, min(_CHUNK_SIZE, output_remaining + 1))
        except zlib.error as exc:
            raise OfficeArchiveError(_DAMAGED_PART) from exc
        output_remaining -= len(data)
        if output_remaining < 0 or decoder.unused_data:
            raise OfficeArchiveError(_DAMAGED_PART)
        checksum = zlib.crc32(data, checksum)
        pending = decoder.unconsumed_tail
        if not data and not pending and not compressed_remaining and not decoder.eof:
            raise OfficeArchiveError(_DAMAGED_PART)
    if compressed_remaining or output_remaining:
        raise OfficeArchiveError(_DAMAGED_PART)
    return checksum


def checked_office_archive(
    stream, *, max_members, max_uncompressed_bytes, reject_duplicate_names=False,
):
    """Return a validated ZipFile. Keep the borrowed stream unchanged until closed."""
    archive = zipfile.ZipFile(stream)
    try:
        members = validate_office_archive_members(
            archive, max_members=max_members, max_uncompressed_bytes=max_uncompressed_bytes,
            reject_duplicate_names=reject_duplicate_names,
        )
        # ZipFile resolves the central directory offset, including ZIP64 and
        # any bytes prepended before the archive.
        next_header = archive.start_dir
        for member in sorted(members, key=lambda entry: entry.header_offset, reverse=True):
            _verify_member(stream, member, next_header)
            next_header = member.header_offset
    except BaseException:
        archive.close()
        raise
    return archive
