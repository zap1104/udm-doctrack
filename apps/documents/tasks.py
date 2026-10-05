from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.core.utils import log_action, normalise_text

from .extraction import extract_document_text
from .models import Document, DocumentFile, MetadataSuggestion, OcrStatus, Source
from .suggestions import suggest_metadata

logger = logging.getLogger("doctrack")


def _extraction_actor(document, user_id):
    """Queued work must not retain permissions an account has since lost."""
    if user_id is None:
        # Actorless jobs are private maintenance calls, never user endpoints.
        return None, True
    actor = User.objects.filter(pk=user_id, is_active=True).first()
    if actor is None or not actor.can_start_work:
        return actor, False
    if document.can_user_edit(actor):
        return actor, True
    # An office administrator can approve a record held by their office into
    # its originating office's repository. That authorized filing includes
    # local extraction, while repository visibility remains office-scoped.
    record = document.tracking_record if document.source == Source.DTS else None
    filing_actor = bool(record and record.approved_by_id == actor.pk and actor.is_office_admin and (
        actor.is_system_admin or actor.office_id in {record.originating_office_id, record.current_office_id}
    ))
    return actor, filing_actor and not document.allow_external_ocr


def _cancel_extraction(document):
    Document.objects.filter(pk=document.pk).update(
        ocr_status=OcrStatus.SKIPPED,
        ocr_notes="Queued text extraction was cancelled because the requesting account no longer has permission. An authorized editor can retry.",
        updated_at=timezone.now(),
    )
    return {"document_id": document.pk, "status": OcrStatus.SKIPPED, "characters": 0, "failures": []}


def extract_document_task(document_id: int, *, user_id=None, file_ids=None, replace=False) -> dict:
    """Extract one document's files outside the request transaction.

    The task marks the row RUNNING before opening storage, then writes the
    result and rebuilds PostgreSQL's search vector in a short transaction.
    """
    document = Document.objects.select_related("office", "uploaded_by").get(pk=document_id)
    actor, allowed = _extraction_actor(document, user_id)
    if not allowed:
        return _cancel_extraction(document)
    selected = DocumentFile.objects.filter(document=document)
    if file_ids is not None:
        selected = selected.filter(pk__in=file_ids)

    Document.objects.filter(pk=document.pk).update(ocr_status=OcrStatus.RUNNING, updated_at=timezone.now())
    parts = []
    engines = []
    failures = []
    notes = []
    statuses = []
    confidences = []
    for document_file in selected.order_by("created_at"):
        document.refresh_from_db()
        actor, allowed = _extraction_actor(document, user_id)
        if not allowed:
            return _cancel_extraction(document)
        try:
            document_file.file.open("rb")
            try:
                result = extract_document_text(
                    document_file.file,
                    document_file.original_name,
                    language_hint=document.ocr_language,
                    allow_external_ocr=document.allow_external_ocr,
                )
            finally:
                document_file.file.close()
        except (FileNotFoundError, OSError) as exc:
            failures.append(f"{document_file.original_name}: file is missing from storage")
            logger.warning("Extraction could not open %s: %s", document_file.pk, exc)
            continue
        document_file.page_count = result.pages
        document_file.extracted_chars = result.char_count
        document_file.save(update_fields=["page_count", "extracted_chars", "updated_at"])
        engines.append(result.engine)
        statuses.append(result.status)
        notes.extend(f"{document_file.original_name}: {note}" for note in result.notes)
        if result.confidence is not None:
            confidences.append(result.confidence)
        if result.text:
            parts.append(result.text)
        if result.status == OcrStatus.FAILED:
            failures.extend(result.notes)

    extracted = normalise_text("\n\n".join(parts))[:200000]
    engine = ", ".join(dict.fromkeys(item for item in engines if item))[:32]
    confidence = round(sum(confidences) / len(confidences), 4) if confidences else None
    with transaction.atomic():
        # Match the actor-before-document lock order used by mutation services.
        # Demotion cannot race the final permission check and result write.
        if user_id is not None:
            User.objects.select_for_update().filter(pk=user_id).first()
        document = Document.objects.select_for_update().get(pk=document.pk)
        actor, allowed = _extraction_actor(document, user_id)
        if not allowed:
            return _cancel_extraction(document)
        incomplete = failures or any(item in {OcrStatus.FAILED, OcrStatus.SKIPPED} for item in statuses)
        if replace and incomplete and document.ocr_text:
            # A failed retry must not erase the last usable search text.
            combined = document.ocr_text
        elif replace or not document.ocr_text:
            combined = extracted
        else:
            combined = normalise_text(f"{document.ocr_text}\n\n{extracted}")[:200000]
        if failures or OcrStatus.FAILED in statuses:
            status = OcrStatus.FAILED
        elif OcrStatus.SKIPPED in statuses:
            status = OcrStatus.SKIPPED
        elif combined:
            status = OcrStatus.DONE
        else:
            status = OcrStatus.EMPTY
        document.ocr_text = combined
        document.ocr_status = status
        document.ocr_engine = engine or ("failed" if failures else "none")
        document.ocr_confidence = confidence
        document.ocr_notes = "\n".join((notes + failures)[:20])[:4000]
        document.page_count = document.file_page_count
        document.save(
            update_fields=[
                "ocr_text", "ocr_status", "ocr_engine", "ocr_confidence", "ocr_notes", "page_count", "updated_at"
            ]
        )
        suggestion = suggest_metadata(
            text=document.ocr_text, filename=document.title, office=document.office, user=actor
        )
        MetadataSuggestion.objects.create(
            document=document,
            engine=suggestion.engine,
            engine_version=suggestion.engine_version,
            suggested=suggestion.as_dict(),
            text_sample=document.ocr_text[:4000],
        )
        document.rebuild_index()
        log_action(
            AuditLog.Action.UPDATE,
            f"Finished text extraction for “{document.title}”",
            actor=actor,
            target=document,
            extra={"status": status, "failures": failures[:5], "notes": notes[:5], "confidence": confidence},
        )
    return {"document_id": document.pk, "status": status, "characters": len(combined), "failures": failures}
