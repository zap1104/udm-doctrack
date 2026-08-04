"""Small helpers used across the whole project."""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import date, datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.utils import timezone

from .middleware import get_current_request
from .models import AuditLog

_signer = TimestampSigner(salt="doctrack.download")


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------
def client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def log_action(
    action: str,
    summary: str,
    *,
    actor=None,
    target=None,
    target_type: str = "",
    target_id: str = "",
    extra: dict | None = None,
    request=None,
) -> AuditLog:
    """Write one immutable audit entry. Never raises into the caller's flow."""
    request = request or get_current_request()
    if actor is None and request is not None:
        candidate = getattr(request, "user", None)
        if candidate is not None and candidate.is_authenticated:
            actor = candidate

    if target is not None:
        target_type = target_type or target.__class__.__name__
        target_id = target_id or str(getattr(target, "pk", ""))

    try:
        return AuditLog.objects.create(
            actor=actor if getattr(actor, "pk", None) else None,
            actor_label=str(actor) if actor else "system",
            action=action,
            target_type=target_type[:64],
            target_id=str(target_id)[:64],
            summary=summary[:255],
            ip_address=client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT", "")[:255] if request else ""),
            extra=extra or {},
        )
    except Exception:  # pragma: no cover - auditing must never break a request
        import logging

        logging.getLogger("doctrack").exception("Failed to write audit log entry")
        return AuditLog(action=action, summary=summary)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def file_extension(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lstrip(".").lower()


def validate_upload(uploaded_file) -> None:
    """Extension + size validation shared by every upload form."""
    extension = file_extension(getattr(uploaded_file, "name", ""))
    allowed = [item.lower() for item in settings.ALLOWED_UPLOAD_EXTENSIONS]
    if extension not in allowed:
        raise ValidationError(
            f"“.{extension or '?'}” files are not accepted. Allowed types: {', '.join(allowed)}."
        )
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if uploaded_file.size > max_bytes:
        raise ValidationError(
            f"“{uploaded_file.name}” is {human_size(uploaded_file.size)}. "
            f"The limit is {settings.MAX_UPLOAD_MB} MB per file."
        )


def checksum_of(uploaded_file) -> str:
    """SHA-256 of an uploaded file; used to spot duplicate uploads."""
    digest = hashlib.sha256()
    position = uploaded_file.tell() if hasattr(uploaded_file, "tell") else 0
    try:
        uploaded_file.seek(0)
        for chunk in uploaded_file.chunks():
            digest.update(chunk)
    finally:
        try:
            uploaded_file.seek(position)
        except Exception:
            pass
    return digest.hexdigest()


def human_size(num_bytes: int | None) -> str:
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def sign_download(token_value: str) -> str:
    return _signer.sign(token_value)


def unsign_download(signed_value: str, max_age: int | None = None) -> str | None:
    try:
        return _signer.unsign(signed_value, max_age=max_age or settings.SIGNED_URL_TTL_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def normalise_text(value: str | None) -> str:
    """Collapse whitespace and strip control characters from extracted text."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value)
    value = "".join(char for char in value if char == "\n" or char == "\t" or ord(char) >= 32)
    value = _WHITESPACE_RE.sub(" ", value)
    value = _MULTI_NEWLINE_RE.sub("\n\n", value)
    return value.strip()


def truncate(value: str, limit: int = 160) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def parse_date_guess(value: str) -> date | None:
    """Parse the date formats that actually appear on Philippine office documents."""
    value = (value or "").strip()
    if not value:
        return None
    formats = (
        "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%B %d, %Y", "%b %d, %Y",
        "%d %B %Y", "%d %b %Y", "%B %d %Y", "%Y/%m/%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def today() -> date:
    return timezone.localdate()
