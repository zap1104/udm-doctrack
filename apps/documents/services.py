"""Business rules for the document repository."""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.core.models import AuditLog, MetadataFieldDefinition, Tag
from apps.core.utils import checksum_of, log_action, normalise_text, validate_upload
from apps.tracking.models import RecordActivity, Status
from apps.tracking.services import add_activity

from .extraction import extract_document_text
from .models import (
    AccessLevel,
    Document,
    DocumentFile,
    DocumentMetadata,
    MetadataSuggestion,
    OcrStatus,
    Source,
)
from .suggestions import suggest_metadata

logger = logging.getLogger("doctrack")


# ---------------------------------------------------------------------------
# Upload → text → suggestion (step 1 of the upload wizard)
# ---------------------------------------------------------------------------
@transaction.atomic
def ingest_upload(*, user, uploaded_file, office=None, source=Source.UPLOAD) -> tuple[Document, object]:
    """Create a placeholder document, extract its text, and propose metadata.

    The document is saved immediately (nothing is lost if the browser closes)
    but stays unreviewed until the user confirms the metadata screen.
    """
    validate_upload(uploaded_file)
    office = office or user.office
    if office is None:
        raise ValidationError("Your account has no office, so it cannot own a document.")

    extraction = extract_document_text(uploaded_file, uploaded_file.name)

    document = Document.objects.create(
        title=(uploaded_file.name.rsplit(".", 1)[0][:255] or "Untitled document"),
        source=source,
        office=office,
        year=timezone.localdate().year,
        uploaded_by=user,
        ocr_text=extraction.text,
        ocr_status=getattr(OcrStatus, extraction.status, OcrStatus.EMPTY),
        ocr_engine=extraction.engine[:32],
        ocr_confidence=extraction.confidence,
        page_count=extraction.pages,
        access_level=AccessLevel.OFFICE,
    )

    uploaded_file.seek(0)
    DocumentFile.objects.create(
        document=document,
        file=uploaded_file,
        original_name=uploaded_file.name[:255],
        content_type=getattr(uploaded_file, "content_type", "") or "",
        size=uploaded_file.size or 0,
        checksum=checksum_of(uploaded_file),
        is_primary=True,
        page_count=extraction.pages,
        extracted_chars=extraction.char_count,
        uploaded_by=user,
    )

    suggestion = suggest_metadata(
        text=extraction.text, filename=uploaded_file.name, office=office, user=user
    )
    suggestion.notes.extend(extraction.notes)

    MetadataSuggestion.objects.create(
        document=document,
        engine=suggestion.engine,
        engine_version=suggestion.engine_version,
        suggested=suggestion.as_dict(),
        text_sample=normalise_text(extraction.text)[:4000],
    )

    document.rebuild_index()
    log_action(
        AuditLog.Action.CREATE,
        f"Uploaded “{document.title}” to the repository",
        actor=user,
        target=document,
    )
    return document, suggestion


def duplicate_of(uploaded_file) -> Document | None:
    """Warn (never block) when the same file was already archived."""
    try:
        digest = checksum_of(uploaded_file)
    except Exception:
        return None
    existing = DocumentFile.objects.filter(checksum=digest).select_related("document").first()
    return existing.document if existing else None


# ---------------------------------------------------------------------------
# Metadata review (step 2 of the upload wizard)
# ---------------------------------------------------------------------------
@transaction.atomic
def save_document_metadata(document: Document, *, user, data: dict, tag_names, metadata_values: dict,
                           accepted_from_suggestion: dict | None = None) -> Document:
    for field_name in (
        "title", "description", "reference_number", "author_name", "recipient_name",
        "signatory", "access_level",
    ):
        if field_name in data:
            setattr(document, field_name, data[field_name] or "")

    if data.get("office"):
        document.office = data["office"]
    document.document_type = data.get("document_type")
    document.document_date = data.get("document_date")
    document.year = data.get("year") or (document.document_date or timezone.localdate()).year
    document.retention_until = data.get("retention_until")
    document.save()

    set_tags(document, tag_names, user=user)
    set_metadata_values(document, metadata_values)

    suggestion = document.suggestions.first()
    if suggestion and accepted_from_suggestion is not None:
        suggestion.accepted = accepted_from_suggestion
        suggestion.reviewed_by = user
        suggestion.reviewed_at = timezone.now()
        suggestion.save(update_fields=["accepted", "reviewed_by", "reviewed_at"])

    document.rebuild_index()
    log_action(AuditLog.Action.UPDATE, f"Saved metadata for “{document.title}”", actor=user, target=document)
    return document


def set_tags(document: Document, tag_names, *, user=None) -> None:
    """Replace the document's tags and keep `Tag.usage_count` accurate."""
    from django.db.models import F

    cleaned: list[str] = []
    for raw in tag_names or []:
        name = " ".join(str(raw).split()).lower()[:64]
        if name and name not in cleaned:
            cleaned.append(name)

    tags = []
    for name in cleaned:
        tag, _ = Tag.get_or_create_by_name(name)
        if tag:
            tags.append(tag)

    previous = set(document.tags.values_list("pk", flat=True))
    current = {tag.pk for tag in tags}
    document.tags.set(tags)

    added = current - previous
    removed = previous - current
    if added:
        Tag.objects.filter(pk__in=added).update(usage_count=F("usage_count") + 1)
    if removed:
        Tag.objects.filter(pk__in=removed, usage_count__gt=0).update(usage_count=F("usage_count") - 1)


def set_metadata_values(document: Document, metadata_values: dict) -> None:
    definitions = {definition.key: definition for definition in MetadataFieldDefinition.active.all()}
    for key, value in (metadata_values or {}).items():
        definition = definitions.get(key)
        if definition is None:
            continue
        value = str(value or "").strip()
        if value:
            DocumentMetadata.objects.update_or_create(
                document=document, field=definition, defaults={"value": value[:500]}
            )
        else:
            DocumentMetadata.objects.filter(document=document, field=definition).delete()


@transaction.atomic
def add_file_to_document(document: Document, uploaded_file, *, user, run_extraction: bool = True) -> DocumentFile:
    validate_upload(uploaded_file)
    extraction = extract_document_text(uploaded_file, uploaded_file.name) if run_extraction else None
    uploaded_file.seek(0)
    document_file = DocumentFile.objects.create(
        document=document,
        file=uploaded_file,
        original_name=uploaded_file.name[:255],
        content_type=getattr(uploaded_file, "content_type", "") or "",
        size=uploaded_file.size or 0,
        checksum=checksum_of(uploaded_file),
        is_primary=not document.files.exists(),
        page_count=extraction.pages if extraction else 0,
        extracted_chars=extraction.char_count if extraction else 0,
        uploaded_by=user,
    )
    if extraction and extraction.text:
        combined = (document.ocr_text + "\n\n" + extraction.text).strip()
        document.ocr_text = combined[:200000]
        document.ocr_status = OcrStatus.DONE
        document.save(update_fields=["ocr_text", "ocr_status", "updated_at"])
        document.rebuild_index()
    return document_file


# ---------------------------------------------------------------------------
# Archiving a completed tracking record
# ---------------------------------------------------------------------------
@transaction.atomic
def archive_tracking_record(record, *, user, tag_names=None, description="") -> Document:
    """Move a completed DTS record into the repository without changing its identity."""
    if record.status != Status.COMPLETED:
        raise ValidationError("Only completed records can be archived.")
    existing = getattr(record, "archived_document", None)
    if existing:
        return existing

    document = Document.objects.create(
        title=record.subject[:255],
        description=description or record.completion_note or record.instructions,
        source=Source.DTS,
        tracking_record=record,
        reference_number=record.tracking_number,
        office=record.originating_office,
        document_type=record.document_type,
        document_date=timezone.localdate(record.completed_at or record.created_at),
        year=timezone.localdate(record.completed_at or record.created_at).year,
        access_level=AccessLevel.OFFICE,
        uploaded_by=user,
        ocr_status=OcrStatus.PENDING,
    )

    text_parts = [record.subject, record.instructions, record.remarks, record.completion_note]
    for attachment in record.attachments.all():
        try:
            attachment.file.open("rb")
            payload = attachment.file.read()
            attachment.file.close()
        except Exception as exc:  # pragma: no cover - a storage hiccup must not block archiving
            logger.warning("Could not read attachment %s: %s", attachment.pk, exc)
            continue

        buffer = ContentFile(payload)
        extraction = extract_document_text(buffer, attachment.original_name)

        document_file = DocumentFile(
            document=document,
            original_name=attachment.original_name,
            content_type=attachment.content_type,
            size=attachment.size or len(payload),
            checksum=attachment.checksum,
            is_primary=not document.files.exists(),
            page_count=extraction.pages,
            extracted_chars=extraction.char_count,
            uploaded_by=user,
        )
        document_file.file.save(attachment.original_name, ContentFile(payload), save=False)
        document_file.save()

        if extraction.text:
            text_parts.append(extraction.text)

    document.ocr_text = normalise_text("\n\n".join(part for part in text_parts if part))[:200000]
    document.ocr_status = OcrStatus.DONE if document.ocr_text else OcrStatus.EMPTY
    document.ocr_engine = "archive-merge"
    document.save(update_fields=["ocr_text", "ocr_status", "ocr_engine", "updated_at"])

    suggestion = suggest_metadata(
        text=document.ocr_text, filename=record.subject, office=record.originating_office, user=user
    )
    MetadataSuggestion.objects.create(
        document=document,
        engine=suggestion.engine,
        engine_version=suggestion.engine_version,
        suggested=suggestion.as_dict(),
        text_sample=document.ocr_text[:4000],
    )
    set_tags(document, tag_names or suggestion.tags, user=user)
    document.rebuild_index()

    record.is_archived = True
    record.archived_at = timezone.now()
    record.save(update_fields=["is_archived", "archived_at", "updated_at"])
    add_activity(
        record,
        RecordActivity.Event.ARCHIVED,
        f"Archived to Document Management by {user.display_name}",
        actor=user,
    )
    log_action(
        AuditLog.Action.ARCHIVE,
        f"Archived {record.tracking_number} into the repository",
        actor=user,
        target=document,
    )
    return document


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------
def reindex_all(queryset=None, *, batch_size: int = 200) -> int:
    queryset = queryset if queryset is not None else Document.objects.all()
    count = 0
    for document in queryset.prefetch_related("tags", "metadata_values__field").iterator(chunk_size=batch_size):
        document.rebuild_index()
        count += 1
    return count
