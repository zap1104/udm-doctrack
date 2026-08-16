"""Small helpers used across the whole project."""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import date, datetime
from functools import lru_cache

import segno
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe

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


# ---------------------------------------------------------------------------
# Colour
#
# Offices carry a badge colour an administrator can change at will, which means
# no pairing of background and text can be chosen up front — the readable
# combination has to be derived from whatever colour they pick. These helpers
# do that arithmetic, so a badly chosen colour costs legibility nowhere.
#
# Colour is never the only signal: every badge also prints the office code. See
# `partials/_office_badge.html`, and the same rule already stated for statuses
# in apps/core/views.STATUS_COLOURS.
# ---------------------------------------------------------------------------
_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

#: Minimum contrast for normal-size text under WCAG 2.1 AA.
MIN_CONTRAST = 4.5


def normalise_hex(value: str | None, fallback: str = "#63718a") -> str:
    """A validated `#rrggbb`, or the fallback.

    Everything that reaches a `style` attribute goes through here. The field is
    validated on the way in as well, but a colour arriving from a fixture, a
    shell session or a hand-edited row must not be able to put arbitrary text
    inside a style attribute.
    """
    value = (value or "").strip()
    if not _HEX_RE.match(value):
        return fallback
    if len(value) == 4:  # #abc -> #aabbcc
        value = "#" + "".join(char * 2 for char in value[1:])
    return value.lower()


def _to_rgb(value: str) -> tuple[float, float, float]:
    value = normalise_hex(value)
    return tuple(int(value[index : index + 2], 16) / 255 for index in (1, 3, 5))


def _to_hex(rgb) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, channel)) * 255):02x}" for channel in rgb)


def _mix(a: str, b: str, weight: float) -> str:
    """`weight` of colour `a` against `1 - weight` of colour `b`."""
    first, second = _to_rgb(a), _to_rgb(b)
    return _to_hex(x * weight + y * (1 - weight) for x, y in zip(first, second, strict=True))


def _luminance(value: str) -> float:
    """WCAG relative luminance, sRGB linearised."""
    channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in _to_rgb(value)]
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast between two colours, 1.0 (identical) to 21.0 (black on white)."""
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@lru_cache(maxsize=256)
def badge_palette(base: str) -> tuple[str, str]:
    """A readable (background, text) pair built from one office colour.

    The background is the colour thinned almost to white, which keeps a row of
    badges calm rather than a bag of sweets. The text starts as the colour
    itself and is darkened in steps until it clears AA against that background,
    so an administrator can choose a pale yellow and still get a legible badge
    instead of an invisible one.
    """
    base = normalise_hex(base)
    tint = _mix(base, "#ffffff", 0.14)
    ink = base
    for _ in range(24):  # bounded: each step darkens, so this always terminates
        if contrast_ratio(ink, tint) >= MIN_CONTRAST:
            break
        ink = _mix(ink, "#000000", 0.85)
    return tint, ink


# ---------------------------------------------------------------------------
# QR codes
# ---------------------------------------------------------------------------
#: Quiet zone in modules. The spec asks for 4; 2 still scans reliably and buys
#: back space on a slip where the code sits in a corner of the letterhead.
QR_BORDER = 2

_SVG_OPEN_TAG_RE = re.compile(r"^<svg[^>]*>")


@lru_cache(maxsize=512)
def qr_svg(data: str, *, label: str = "") -> str:
    """An inline `<svg>` QR code for `data`, sized by CSS.

    segno writes fixed `width`/`height` and no `viewBox`. Without a viewBox the
    drawing keeps its module-sized coordinates when CSS resizes the element, so
    the code sits in one corner of a larger blank canvas instead of scaling —
    the opening tag is rewritten here to scale properly at any size, on screen
    and at print millimetres alike.

    Cached because a tracking number never changes, and the printable slip is
    re-rendered on every view.
    """
    code = segno.make(data, error="m")
    width, height = code.symbol_size(border=QR_BORDER)
    svg = code.svg_inline(border=QR_BORDER, dark="#111820", light=None)

    attributes = (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" '
        f'role="img" aria-label="{escape(label or data)}" focusable="false" '
        f'xmlns="http://www.w3.org/2000/svg" class="segno">'
    )
    # Only the opening tag is replaced; the path data segno produced is
    # untouched. Safe to mark: every part is either generated by segno from a
    # server-issued tracking number, or escaped above.
    return mark_safe(_SVG_OPEN_TAG_RE.sub(attributes, svg, count=1))


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
