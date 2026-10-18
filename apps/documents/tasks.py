from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import AuditLog, Notification, Tag
from apps.core.utils import log_action, normalise_text

from .extraction import extract_document_text
from .models import DELETION_RECOVERY_DAYS, Document, DocumentFile, MetadataSuggestion, OcrStatus, Source
from .suggestions import suggest_metadata

logger = logging.getLogger("doctrack")


def _extraction_actor(document, user_id):
    """Queued work must not retain permissions an account has since lost."""
    if not document.is_active:
        return None, False
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


def _cancel_extraction(document, generation=None):
    with transaction.atomic():
        current = Document.objects.select_for_update().filter(pk=document.pk, is_active=True)
        if generation is not None:
            current = current.filter(extraction_generation=generation)
        current = current.first()
        if current is not None:
            note = "Queued text extraction was cancelled because the requesting account no longer has permission. An authorized editor can retry."
            if current.extraction_failed_generation is not None or current.ocr_status == OcrStatus.FAILED:
                current.retain_extraction_failure(note)
            else:
                current.ocr_status = OcrStatus.SKIPPED
                current.ocr_notes = note
            current.save(update_fields=["ocr_status", "ocr_notes", "extraction_failed_generation", "updated_at"])
    return {"document_id": document.pk, "status": OcrStatus.SKIPPED, "characters": 0, "failures": []}


@contextmanager
def _extraction_finalization(document_id, generation):
    """Expose a failed result commit after its transaction has rolled back."""
    try:
        yield
    except Document.DoesNotExist:
        raise
    except Exception:
        logger.exception("Could not finalize text extraction for document %s", document_id)
        try:
            with transaction.atomic():
                current = Document.objects.select_for_update().filter(
                    pk=document_id, is_active=True, extraction_generation=generation,
                ).first()
                if current is not None:
                    current.retain_extraction_failure(
                        "Text extraction could not be finalized. Your files and previously saved text were preserved. Retry extraction."
                    )
                    current.save(update_fields=["ocr_status", "ocr_notes", "extraction_failed_generation", "updated_at"])
        except Exception:
            # A database outage may also prevent recording the failure. Retain
            # the original job error so queue failure reporting/retries see it.
            logger.exception("Could not record failed text extraction for document %s", document_id)
        raise


def extract_document_task(document_id: int, *, user_id=None, file_ids=None, replace=False, generation=None) -> dict:
    """A queued or in-flight job can outlive permanent repository deletion."""
    try:
        return _extract_document_task(document_id, user_id=user_id, file_ids=file_ids, replace=replace, generation=generation)
    except Document.DoesNotExist:
        return {"document_id": document_id, "status": OcrStatus.SKIPPED, "characters": 0, "failures": []}


def _extract_document_task(document_id: int, *, user_id=None, file_ids=None, replace=False, generation=None) -> dict:
    """Extract one document's files outside the request transaction.

    The task marks the row RUNNING before opening storage, then writes the
    result and rebuilds PostgreSQL's search vector in a short transaction.
    """
    full_retry = replace and file_ids is None
    document = Document.objects.select_related("office", "uploaded_by").get(pk=document_id)
    if generation is not None and document.extraction_generation != generation:
        return _superseded_extraction(document)
    selected = DocumentFile.objects.filter(document=document)
    if file_ids is not None:
        selected = selected.filter(pk__in=file_ids)

    with transaction.atomic():
        if user_id is not None:
            User.objects.select_for_update().filter(pk=user_id).first()
        document = Document.objects.select_for_update().get(pk=document.pk)
        if generation is not None and document.extraction_generation != generation:
            return _superseded_extraction(document)
        actor, allowed = _extraction_actor(document, user_id)
        if not allowed:
            return _cancel_extraction(document, generation)
        if document.ocr_status == OcrStatus.FAILED or document.extraction_failed_generation is not None:
            document.retain_extraction_failure()
            document.save(update_fields=["ocr_notes", "extraction_failed_generation"])
        if generation is None:
            if full_retry:
                document.extraction_generation += 1
                document.save(update_fields=["extraction_generation"])
            # Legacy direct appends also need a fence before provider I/O.
            generation = document.extraction_generation
        Document.objects.filter(pk=document.pk).update(ocr_status=OcrStatus.RUNNING, updated_at=timezone.now())
    parts = []
    engines = []
    failures = []
    notes = []
    statuses = []
    confidences = []
    pending_files = list(selected.order_by("created_at", "pk"))
    processed_file_ids = set()
    # A full retry includes files added while a provider was processing. Bound
    # discovery so continuous uploads cannot keep one worker running forever.
    for pass_number in range(3):
        for document_file in pending_files:
            document.refresh_from_db()
            if generation is not None and document.extraction_generation != generation:
                return _superseded_extraction(document)
            actor, allowed = _extraction_actor(document, user_id)
            if not allowed:
                return _cancel_extraction(document, generation)
            processed_file_ids.add(document_file.pk)
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
            # A document may be removed while a provider is processing the file.
            # Serialize this write with review decisions and recheck fresh access.
            with transaction.atomic():
                if user_id is not None:
                    User.objects.select_for_update().filter(pk=user_id).first()
                document = Document.objects.select_for_update().get(pk=document.pk)
                if generation is not None and document.extraction_generation != generation:
                    return _superseded_extraction(document)
                actor, allowed = _extraction_actor(document, user_id)
                if not allowed:
                    return _cancel_extraction(document, generation)
                if result.status not in {OcrStatus.FAILED, OcrStatus.SKIPPED}:
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
        with _extraction_finalization(document.pk, generation), transaction.atomic():
            # Match the actor-before-document lock order used by mutation services.
            # Demotion cannot race the final permission check and result write.
            if user_id is not None:
                User.objects.select_for_update().filter(pk=user_id).first()
            document = Document.objects.select_for_update().get(pk=document.pk)
            if generation is not None and document.extraction_generation != generation:
                return _superseded_extraction(document)
            actor, allowed = _extraction_actor(document, user_id)
            if not allowed:
                return _cancel_extraction(document, generation)
            if replace and file_ids is None:
                pending_files = list(DocumentFile.objects.filter(document=document).exclude(
                    pk__in=processed_file_ids,
                ).order_by("created_at", "pk"))
                if pending_files:
                    if pass_number < 2:
                        # Continue only after this transaction releases its locks.
                        continue
                    failures.append("Attachments kept changing during extraction. Existing text was preserved; retry when uploads finish.")
            incomplete = failures or any(item in {OcrStatus.FAILED, OcrStatus.SKIPPED} for item in statuses)
            if replace and incomplete and document.ocr_text:
                # A failed retry must not erase the last usable search text.
                combined = document.ocr_text
            elif replace or not document.ocr_text:
                combined = extracted
            else:
                combined = document.ocr_text
                normalized_combined = normalise_text(combined)
                for part in parts:
                    text = normalise_text(part)
                    if text and text not in normalized_combined:
                        combined = normalise_text(f"{combined}\n\n{text}")[:200000]
                        normalized_combined = combined
            if failures or OcrStatus.FAILED in statuses:
                status = OcrStatus.FAILED
            elif OcrStatus.SKIPPED in statuses:
                status = OcrStatus.SKIPPED
            elif combined:
                status = OcrStatus.DONE
            else:
                status = OcrStatus.EMPTY
            document.ocr_text = combined
            outcome_notes = "\n".join((notes + failures)[:20])[:4000]
            if status == OcrStatus.FAILED or (
                not full_retry and document.extraction_failed_generation is not None
            ) or (full_retry and incomplete and document.extraction_failed_generation is not None):
                document.retain_extraction_failure(outcome_notes)
                status = OcrStatus.FAILED
            else:
                document.ocr_status = status
                document.ocr_notes = outcome_notes
                if full_retry and not incomplete:
                    document.extraction_failed_generation = None
                    # All files visible under this lock were read. Retire jobs
                    # and late queue callbacks whose work this full retry covered;
                    # uploads after this commit capture the next generation.
                    document.extraction_generation += 1
            document.ocr_engine = engine or ("failed" if failures else "none")
            document.ocr_confidence = confidence
            document.page_count = document.file_page_count
            document.save(
                update_fields=[
                    "ocr_text", "ocr_status", "ocr_engine", "ocr_confidence", "ocr_notes", "page_count", "extraction_failed_generation", "extraction_generation", "updated_at"
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


def _superseded_extraction(document):
    """An older retry cannot alter a newer request's text, metrics or status."""
    return {"document_id": document.pk, "status": OcrStatus.SKIPPED,
            "characters": 0, "failures": []}


def purge_deleted_documents(*, now=None, limit=100):
    """Delete expired repository rows and exact unshared storage objects.

    Storage operations cannot roll back. Recovery is closed at the deadline;
    failed cleanup retains the expired row for an idempotent retry. Holding the
    document lock serializes cleanup with restore and duplicate workers.
    """
    from apps.tracking.models import Attachment, ExternalAttachment

    now = now or timezone.now()
    cutoff = now - timedelta(days=DELETION_RECOVERY_DAYS)
    candidates = list(Document.objects.filter(is_active=False, deleted_at__lte=cutoff)
                      .order_by(F("purge_attempted_at").asc(nulls_first=True), "deleted_at", "pk")
                      .values_list("pk", flat=True)[:limit])
    result = {"purged": 0, "failed": 0}
    for document_id in candidates:
        try:
            with transaction.atomic():
                document = Document.objects.select_for_update().filter(
                    pk=document_id, is_active=False, deleted_at__lte=cutoff,
                ).first()
                if document is None:
                    continue
                # Mandatory minimal receipt, without copying document content.
                AuditLog.objects.create(
                    actor_label="system", action=AuditLog.Action.DELETE,
                    target_type="Document", target_id=str(document.pk),
                    summary=f"Permanently deleted repository document #{document.pk} after 30 days",
                    extra={"permanent_repository_deletion": True, "audit_target_office_id": document.office_id,
                           "collection": document.collection, "external_record_id": document.external_tracking_record_id,
                           "deleted_at": document.deleted_at.isoformat()},
                )
                seen = set()
                for document_file in document.files.all():
                    name = document_file.file.name
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    shared = DocumentFile.objects.filter(file=name).exclude(document_id=document.pk).exists()
                    if shared or Attachment.objects.filter(file=name).exists() or ExternalAttachment.objects.filter(file=name).exists():
                        continue
                    storage = document_file.file.storage
                    storage.delete(name)
                    if storage.exists(name):
                        raise OSError("Repository storage did not remove the object")
                tag_ids = list(document.tags.values_list("pk", flat=True))
                Tag.objects.filter(pk__in=tag_ids, usage_count__gt=0).update(usage_count=F("usage_count") - 1)
                # SET_NULL would expose previously hidden document notices again.
                Notification.objects.filter(document=document).delete()
                # Detailed review history exists only during the recovery period.
                document.deletion_reviews.all().delete()
                document.delete()
            result["purged"] += 1
        except Exception:
            result["failed"] += 1
            logger.exception("Permanent deletion failed for repository document %s; cleanup will retry", document_id)
            # Commit this outside the rolled-back deletion transaction so a
            # failed oldest batch cannot indefinitely block newer documents.
            Document.objects.filter(pk=document_id, is_active=False, deleted_at__lte=cutoff).update(purge_attempted_at=now)
    return result
