"""Guarded OCR.space unit transport. No response bodies or keys enter notes/logs."""
from __future__ import annotations

import io
import json
import math
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.core.utils import file_extension, normalise_text

from .processing_contracts import Coverage, ExtractionPolicy, UnitResult, WorkState


def _outcome(locator, *, state, note, attempts=0, text="", metadata=None, confidence=None, truncated=False):
    return UnitResult(locator_key=locator, ordinal=1, kind="ocr_unit", raw_text=text,
        normalized_text=normalise_text(text), method="ocr.space:eng", state=state,
        coverage=Coverage.PARTIAL, warnings=(note,), provider_confidence=confidence, truncated=truncated,
        metadata={"provider_attempts": attempts, **(metadata or {})})


def _confidence(results):
    values = []
    for result in results:
        overlay = result.get("TextOverlay")
        lines = overlay.get("Lines") if isinstance(overlay, dict) else None
        for line in lines if isinstance(lines, list) else []:
            if not isinstance(line, dict):
                continue
            words = line.get("Words")
            for word in words if isinstance(words, list) else []:
                if not isinstance(word, dict):
                    continue
                try:
                    value = float(word.get("Confidence"))
                except (ValueError, TypeError):
                    continue
                if math.isfinite(value) and 0 <= value <= 100:
                    values.append(value / 100 if value > 1 else value)
    return round(sum(values)/len(values), 4) if values else None


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _retry_after(headers, default):
    value = headers.get("Retry-After", "") if headers else ""
    try:
        delay = int(value)
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            delay = int((date - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            delay = default
    return max(1, min(delay, 3600))


def _multipart(payload, filename, key):
    boundary = "doctrack-unit-" + uuid4().hex
    parts = []
    for name, value in {"apikey": key, "language": "eng", "OCREngine": "2", "isOverlayRequired": "true",
                        "isCreateSearchablePdf": "false", "scale": "false"}.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", filename.rsplit("/", 1)[-1])[:180] or "source.png"
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{safe_name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
    parts.extend([payload, f"\r\n--{boundary}--\r\n".encode()])
    return b"".join(parts), boundary


def _parse(data, *, locator, policy, attempts, sent_bytes, language_hint):
    if not isinstance(data, dict):
        raise ValueError("Invalid result structure")
    results = data.get("ParsedResults")
    if not isinstance(results, list) or not results or not all(isinstance(row, dict) for row in results):
        return _outcome(locator, state=WorkState.ACTION_REQUIRED, note="OCR returned no mapped page result; coverage remains incomplete.", attempts=attempts)
    # One dispatched page/frame has one source locator. Extra rows cannot be
    # silently assigned to a different source or concatenated into this one.
    first = results[0]
    raw = first.get("ParsedText")
    text = raw if isinstance(raw, str) else ""
    confidence = _confidence([first])
    metadata = {"provider_attempts": attempts, "submitted_bytes": sent_bytes, "provider_results": len(results),
                "provider_confidence_available": confidence is not None}
    complete = (_integer(data.get("OCRExitCode")) == 1 and data.get("IsErroredOnProcessing") is False
                and len(results) == 1 and _integer(first.get("FileParseExitCode")) == 1)
    if not complete:
        retryable = _integer(first.get("FileParseExitCode")) == -20
        return _outcome(locator, state=WorkState.RETRY_WAIT if retryable else WorkState.ACTION_REQUIRED,
            note="OCR reported partial, failed or unmapped processing; readable text was preserved for review.",
            attempts=attempts, text=text[:policy.max_unit_chars], confidence=confidence,
            truncated=len(text) > policy.max_unit_chars,
            metadata={**metadata, "retry_after_seconds": 60} if retryable else metadata)
    warnings = []
    if len(text.strip()) < policy.native_min_chars or "\ufffd" in text:
        warnings.append("OCR text is sparse or suspect; visual content may remain unread.")
    if len(text) > policy.max_unit_chars:
        warnings.append("OCR text reached the per-unit storage safety limit.")
    if language_hint in {"auto", "fil"}:
        metadata["language_note"] = "English OCR was used; Filipino/Taglish quality has not been evaluated."
    raw = text[:policy.max_unit_chars]
    return UnitResult(locator_key=locator, ordinal=1, kind="ocr_unit", raw_text=raw, normalized_text=normalise_text(raw),
        method="ocr.space:eng", state=WorkState.COMPLETE if not warnings else WorkState.ACTION_REQUIRED,
        coverage=Coverage.COMPLETE if not warnings else Coverage.PARTIAL, warnings=tuple(warnings),
        truncated=len(text) > policy.max_unit_chars, provider_confidence=confidence, metadata=metadata)


def ocr_unit(payload: bytes, filename: str, *, source_locator: str, policy: ExtractionPolicy,
             language_hint: str, authorize_dispatch) -> UnitResult:
    """One source page/frame; every retry gets fresh dispatch authorization."""
    locator = source_locator
    key = settings.OCR_SPACE_API_KEY
    endpoint = urlsplit(settings.OCR_SPACE_ENDPOINT)
    if not key:
        return _outcome(locator, state=WorkState.BLOCKED, note="OCR.space is not configured; enter metadata manually or retry after setup.")
    if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password:
        return _outcome(locator, state=WorkState.BLOCKED, note="OCR requires a configured HTTPS endpoint without embedded credentials.")
    extension = file_extension(filename)
    if extension not in {"pdf", "png", "jpg", "jpeg"} or len(payload) > policy.max_request_bytes:
        return _outcome(locator, state=WorkState.ACTION_REQUIRED, note="This OCR unit exceeds the safe format or Free-tier byte budget.")
    if extension == "pdf":
        try:
            from pypdf import PdfReader, apply_configuration
            with apply_configuration(zlib_maximum_output_length=16*1024*1024, jbig2dec_binary=None):
                pdf = PdfReader(io.BytesIO(payload))
                if pdf.is_encrypted or len(pdf.pages) != policy.provider_pages:
                    raise ValueError("Unsupported page mapping")
        except Exception:
            return _outcome(locator, state=WorkState.ACTION_REQUIRED, note="OCR PDF units must contain exactly one readable, unencrypted source page.")
    body, boundary = _multipart(payload, filename, key)
    if len(body) > policy.max_body_bytes:
        return _outcome(locator, state=WorkState.ACTION_REQUIRED, note="The complete OCR request exceeds the conservative Free-tier byte budget.")
    attempts = 0
    for attempt in range(policy.provider_retries+1):
        if not authorize_dispatch():
            return _outcome(locator, state=WorkState.BLOCKED, note="OCR dispatch was cancelled because current authorization or consent no longer permits it.", attempts=attempts)
        request = urllib.request.Request(settings.OCR_SPACE_ENDPOINT, data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        attempts += 1
        try:
            with urllib.request.urlopen(request, timeout=policy.provider_timeout) as response:
                raw_response = response.read(2*1024*1024+1)
            if len(raw_response) > 2*1024*1024:
                raise ValueError("Response resource budget exceeded")
            data = json.loads(raw_response.decode("utf-8"))
            return _parse(data, locator=locator, policy=policy, attempts=attempts,
                          sent_bytes=len(body), language_hint=language_hint)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                return _outcome(locator, state=WorkState.RETRY_WAIT, note="The provider rate or quota limit was reached. Retry is scheduled with finite attempts.",
                    attempts=attempts, metadata={"retry_after_seconds": _retry_after(exc.headers, 3600), "reason": "provider_limit"})
            retryable = exc.code in {408, 425} or 500 <= exc.code < 600
            delay = min(60, _retry_after(exc.headers, 2**attempt))
            if not retryable:
                return _outcome(locator, state=WorkState.ACTION_REQUIRED, note="The OCR provider rejected this request. Check configuration or the original file.", attempts=attempts)
        except (urllib.error.URLError, TimeoutError):
            delay = min(30, 2**attempt + random.uniform(0, 1))
        except (ValueError, UnicodeError, TypeError, KeyError):
            return _outcome(locator, state=WorkState.ACTION_REQUIRED, note="The provider response was invalid or exceeded safe limits; coverage is incomplete.", attempts=attempts)
        if attempt < policy.provider_retries:
            time.sleep(delay)
    return _outcome(locator, state=WorkState.RETRY_WAIT, note="The bounded OCR attempts were exhausted. Saved text and the original file are preserved.",
        attempts=attempts, metadata={"retry_after_seconds": 300, "reason": "provider_unavailable"})


@dataclass(frozen=True)
class PreparedPayload:
    data: bytes
    filename: str
    notes: tuple[str, ...] = ()


def _fits(payload, filename, policy):
    return len(payload) <= policy.max_request_bytes and len(_multipart(payload, filename, settings.OCR_SPACE_API_KEY)[0]) <= policy.max_body_bytes


def _encode_image(image, *, policy, filename, notes):
    from PIL import Image, ImageOps

    if image.width * image.height > policy.max_image_pixels:
        raise ValidationError("The selected image exceeds the decoded-pixel budget; no silent downsampling was performed.")
    normalized = ImageOps.exif_transpose(image)
    if "A" in normalized.getbands() or "transparency" in normalized.info:
        rgba = normalized.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        flattened = Image.alpha_composite(background, rgba)
        converted = flattened.convert("RGB")
        flattened.close()
        background.close()
        rgba.close()
        notes = (*notes, "Transparency was composited onto white for OCR; original alpha pixels are unchanged.")
    else:
        converted = normalized.convert("RGB")
    try:
        output = io.BytesIO()
        converted.save(output, "PNG", optimize=True)
        payload = output.getvalue()
    finally:
        converted.close()
        if normalized is not image:
            normalized.close()
    if not _fits(payload, filename, policy):
        raise ValidationError("The selected image cannot fit the Free-tier budget at the current safe resolution. Review it manually or adjust the verified processing policy.")
    return PreparedPayload(payload, filename, tuple(notes))


def prepare_unit_payload(file_obj, filename, unit: UnitResult, *, policy: ExtractionPolicy) -> PreparedPayload:
    """Select exactly one immutable source page/frame; no remote URL or resizing."""
    position = file_obj.tell()
    try:
        file_obj.seek(0)
        payload = file_obj.read(policy.max_input_bytes+1)
        if len(payload) > policy.max_input_bytes:
            raise ValidationError("The source exceeds the input-byte budget.")
        if unit.kind == "pdf_page":
            from pypdf import PdfReader, PdfWriter, apply_configuration

            page_number = int(unit.metadata.get("page_number", 0))
            if not 1 <= page_number <= policy.max_pages:
                raise ValidationError("The source page mapping is invalid.")
            with apply_configuration(zlib_maximum_output_length=32*1024*1024, jbig2dec_binary=None):
                reader = PdfReader(io.BytesIO(payload))
                if reader.is_encrypted or page_number > len(reader.pages):
                    raise ValidationError("The source page cannot be read safely.")
                writer = PdfWriter()
                writer.add_page(reader.pages[page_number-1])
                output = io.BytesIO()
                writer.write(output)
                sliced = output.getvalue()
                provider_name = f"source-page-{page_number}.pdf"
                if _fits(sliced, provider_name, policy):
                    return PreparedPayload(sliced, provider_name, (f"Lossless slice of source page {page_number}.",))
            from .pdf_assessment import PDFIUM_LOCK
            try:
                import pypdfium2
                with PDFIUM_LOCK, pypdfium2.PdfDocument(payload) as pdf:
                    page = pdf[page_number-1]
                    try:
                        width, height = page.get_size()
                        pixels = math.ceil(width*policy.render_dpi/72)*math.ceil(height*policy.render_dpi/72)
                        if pixels > policy.max_image_pixels:
                            raise ValidationError("PDF rasterization exceeds the pixel budget.")
                        bitmap = page.render(scale=policy.render_dpi/72)
                        try:
                            image = bitmap.to_pil().copy()
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
                try:
                    return _encode_image(image, policy=policy, filename=f"source-page-{page_number}.png",
                        notes=(f"Source page {page_number} rasterized at {policy.render_dpi} DPI; original PDF bytes are preserved. Rasterization is lossy.",))
                finally:
                    image.close()
            except ImportError as exc:
                raise ValidationError("PDF rasterization is unavailable; the original page remains unread by OCR.") from exc
        from PIL import Image
        if unit.kind == "embedded_image":
            from .source_readers import _archive
            member = unit.metadata.get("archive_member")
            with _archive(payload, policy) as archive:
                if not member or member not in archive.namelist() or archive.getinfo(member).file_size > policy.max_archive_bytes:
                    raise ValidationError("The embedded image mapping is invalid or exceeds the resource budget.")
                image_payload = archive.read(member)
            frame = 1
        elif unit.kind == "image_frame":
            image_payload = payload
            frame = int(unit.metadata.get("frame", 0))
            if not 1 <= frame <= policy.max_frames:
                raise ValidationError("The source frame mapping is invalid.")
        else:
            raise ValidationError("This source unit has no supported OCR preparation path.")
        with Image.open(io.BytesIO(image_payload)) as image:
            image.seek(frame-1)
            return _encode_image(image, policy=policy, filename=f"source-frame-{frame}.png",
                notes=("Selected source image/frame normalized to RGB PNG at its original pixel dimensions; color/bit depth and orientation may change. Original bytes are preserved.",))
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError("This source unit could not be prepared within safe limits. The original file is unchanged.") from exc
    finally:
        file_obj.seek(position)
