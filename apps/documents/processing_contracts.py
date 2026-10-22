"""Versioned, bounded extraction contracts. No parser, storage or provider I/O."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

EXTRACTOR_VERSION = "units-1"


class WorkState(models.TextChoices):
    PENDING = "pending", "Waiting"
    RUNNING = "running", "Reading"
    RETRY_WAIT = "retry_wait", "Waiting to retry"
    COMPLETE = "complete", "Processing finished"
    BLOCKED = "blocked", "Blocked by policy"
    UNSUPPORTED = "unsupported", "Unsupported format"
    FAILED = "failed", "Processing failed"
    ACTION_REQUIRED = "action_required", "Needs attention"


class Coverage(models.TextChoices):
    UNKNOWN = "unknown", "Coverage unknown"
    PARTIAL = "partial", "Partly readable"
    COMPLETE = "complete", "Supported content read"


@dataclass(frozen=True)
class UnitResult:
    locator_key: str
    ordinal: int
    kind: str
    raw_text: str = ""
    normalized_text: str = ""
    method: str = "native"
    state: str = WorkState.PENDING
    coverage: str = Coverage.UNKNOWN
    warnings: tuple[str, ...] = ()
    truncated: bool = False
    provider_confidence: float | None = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.locator_key or len(self.locator_key) > 240 or self.ordinal < 1:
            raise ValidationError("A source unit needs a bounded locator and positive position.")
        if self.state not in WorkState.values or self.coverage not in Coverage.values:
            raise ValidationError("Invalid processing or coverage state.")
        if self.provider_confidence is not None and (
            not math.isfinite(self.provider_confidence) or not 0 <= self.provider_confidence <= 1
        ):
            raise ValidationError("Provider confidence must be absent or between zero and one.")


@dataclass(frozen=True)
class UnitBatch:
    units: tuple[UnitResult, ...] = ()
    expected_units: int | None = None
    omitted_reasons: tuple[str, ...] = ()
    policy_digest: str = ""

    @property
    def coverage(self):
        if self.expected_units is None:
            return Coverage.UNKNOWN
        if (self.omitted_reasons or len(self.units) != self.expected_units or any(
            unit.coverage != Coverage.COMPLETE or unit.state != WorkState.COMPLETE or unit.truncated
            for unit in self.units
        )):
            return Coverage.PARTIAL
        return Coverage.COMPLETE


@dataclass(frozen=True)
class ExtractionPolicy:
    max_unit_chars: int = 500_000
    max_pages: int = 200
    max_frames: int = 100
    max_rows: int = 10_000
    max_columns: int = 256
    max_cells: int = 100_000
    max_archive_members: int = 2000
    max_archive_bytes: int = 100 * 1024 * 1024
    max_input_bytes: int = 25 * 1024 * 1024
    max_image_pixels: int = 16_000_000
    render_dpi: int = 144
    native_min_chars: int = 40
    image_coverage_threshold: float = 0.15
    max_request_bytes: int = 1_000_000
    max_body_bytes: int = 1_000_000
    provider_pages: int = 1
    provider_timeout: int = 90
    provider_retries: int = 2
    worker_timeout: int = 600

    def __post_init__(self):
        for key, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValidationError(f"Invalid processing limit: {key}.")
            if (key == "provider_retries" and not 0 <= value <= 3) or (key != "provider_retries" and value <= 0):
                raise ValidationError(f"Processing limit {key} is outside its safe range.")
        if self.max_request_bytes > 1_000_000 or self.max_body_bytes > 1_000_000 or self.provider_pages != 1:
            raise ValidationError("OCR.space Free requires a request budget at most 1 MB and one page per dispatch.")
        if not 0 < self.image_coverage_threshold <= 1 or not 72 <= self.render_dpi <= 300:
            raise ValidationError("Invalid image assessment or rendering policy.")
        if self.provider_timeout > 120 or self.provider_timeout * (self.provider_retries + 1) + 60 >= self.worker_timeout:
            raise ValidationError("Provider attempts must fit within the worker runtime budget.")

    @classmethod
    def from_settings(cls):
        return cls(
            max_unit_chars=settings.PROCESSING_MAX_UNIT_CHARS, max_pages=settings.OCR_MAX_PAGES,
            max_frames=settings.PROCESSING_MAX_FRAMES, max_rows=settings.XLSX_MAX_ROWS,
            max_columns=settings.XLSX_MAX_COLUMNS, max_cells=settings.XLSX_MAX_CELLS,
            max_archive_members=settings.MAX_UPLOAD_ARCHIVE_MEMBERS,
            max_archive_bytes=settings.MAX_UPLOAD_UNCOMPRESSED_MB * 1024 * 1024,
            max_input_bytes=settings.MAX_UPLOAD_MB * 1024 * 1024,
            max_image_pixels=settings.PROCESSING_MAX_IMAGE_PIXELS, render_dpi=settings.PROCESSING_RENDER_DPI,
            native_min_chars=settings.PROCESSING_NATIVE_MIN_CHARS,
            image_coverage_threshold=settings.PROCESSING_IMAGE_COVERAGE_THRESHOLD,
            max_request_bytes=settings.OCR_SPACE_MAX_REQUEST_BYTES, max_body_bytes=settings.OCR_SPACE_MAX_BODY_BYTES,
            provider_timeout=settings.OCR_PROVIDER_TIMEOUT_SECONDS, provider_retries=settings.OCR_PROVIDER_RETRIES,
            worker_timeout=settings.Q_CLUSTER["timeout"],
        )

    @property
    def digest(self):
        encoded = json.dumps({"version": EXTRACTOR_VERSION, **asdict(self)}, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()


def legacy_ocr_status(batch: UnitBatch) -> str:
    """Adapter only: terminal work is never a claim of readable coverage."""
    states = {unit.state for unit in batch.units}
    if WorkState.FAILED in states:
        return "FAILED"
    if batch.coverage != Coverage.COMPLETE:
        return "SKIPPED" if states and states <= {WorkState.BLOCKED, WorkState.UNSUPPORTED} else "FAILED"
    return "DONE" if any(unit.normalized_text.strip() for unit in batch.units) else "EMPTY"


def resolve_file_version(document_file):
    """Resolve the final immutable snapshot, without inventing missing history."""
    version = document_file.versions.order_by("-version_number", "-pk").first()
    if version is None or version.file.name != document_file.file.name or (
        version.checksum and document_file.checksum and version.checksum != document_file.checksum
    ):
        raise ValidationError("The current file snapshot does not match the repository file. Contact the administrator.")
    return version


def coverage_summary(document) -> dict:
    """Counts semantic units, never inventing physical Word/spreadsheet pages."""
    from .processing_models import ExtractionRun

    unknown = {"coverage": Coverage.UNKNOWN, "readable": 0, "expected": None,
               "reasons": ["Source coverage has not been verified for every current file."]}
    if not document.is_active:
        return unknown
    roots = list(document.files.all())
    if not roots:
        return unknown
    readable = expected = 0
    reasons = []
    complete = known = True
    for root in roots:
        try:
            version = resolve_file_version(root)
        except ValidationError:
            known = complete = False
            reasons.extend(unknown["reasons"])
            continue
        run = ExtractionRun.objects.filter(document=document, file_version=version, is_current=True).order_by("-pk").first()
        if run is None:
            known = complete = False
            reasons.extend(unknown["reasons"])
            continue
        if run.expected_units is None:
            known = False
        else:
            expected += run.expected_units
        count = run.units.filter(state=WorkState.COMPLETE, coverage=Coverage.COMPLETE, truncated=False).count()
        readable += count
        complete &= run.coverage == Coverage.COMPLETE and run.state == WorkState.COMPLETE and count == run.expected_units
        reasons.extend(run.warnings)
    return {"coverage": Coverage.COMPLETE if known and complete else (Coverage.PARTIAL if known else Coverage.UNKNOWN),
            "readable": readable, "expected": expected if known else None,
            "reasons": list(dict.fromkeys(reasons))}
