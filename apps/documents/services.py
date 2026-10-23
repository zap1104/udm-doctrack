"""Business rules for the document repository."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.models import AuditLog, MetadataFieldDefinition, Tag
from apps.core.permissions import active_writer, locked_mutation
from apps.core.storage import compensate_storage, create_stored_file
from apps.core.utils import checksum_of, log_action, normalise_text, validate_upload
from apps.tracking.models import RecordActivity, Status
from apps.tracking.services import add_activity

from .extraction import extract_document_text
from .models import (
    AccessLevel,
    Collection,
    DeletionReview,
    Document,
    DocumentFile,
    DocumentMetadata,
    ExternalDocumentOffice,
    MetadataSuggestion,
    OcrStatus,
    Source,
)
from .suggestions import Suggestion, suggest_metadata

logger = logging.getLogger("doctrack")


def suggested_tags_for(user, *, collection=Collection.OVPA):
    """Tag suggestions must not disclose another office's document keywords."""
    tags = Tag.active.all()
    if not user.is_authenticated or not user.is_active:
        return tags.none()
    if user.is_system_admin:
        from django.db.models import Q

        return tags.filter(Q(documents__isnull=True) | Q(documents__collection=collection)).distinct().order_by("-usage_count", "name")
    documents = Document.objects.visible_to(user).in_collection(collection).filter(is_active=True)
    return tags.filter(documents__in=documents).distinct().order_by("name")


# ---------------------------------------------------------------------------
# Upload → text → suggestion (step 1 of the upload wizard)
# ---------------------------------------------------------------------------
def ingest_upload(
    *, user, uploaded_file=None, uploaded_files=None, office=None, source=Source.UPLOAD,
    ocr_language="auto", allow_external_ocr=False, collection=Collection.OVPA, external_offices=None, defer_extraction=False,
) -> tuple[Document, object]:
    document, suggestion = _persist_upload(user=user, uploaded_file=uploaded_file, uploaded_files=uploaded_files,
        office=office, source=source, ocr_language=ocr_language, allow_external_ocr=allow_external_ocr,
        collection=collection, external_offices=external_offices, defer_extraction=defer_extraction)
    if settings.ENABLE_SOURCE_UNIT_EXTRACTION:
        from apps.accounts.models import User

        document.refresh_from_db()
        # Commit hooks may have finished native work. In an outer transaction
        # the pending review stays usable and loads suggestions on next GET.
        actor = User.objects.filter(pk=user.pk, is_active=True).first()
        if actor and actor.can_start_work and document.suggestions.exists() and document.can_user_view(actor):
            suggestion = suggest_metadata(text=document.ocr_text, filename=document.title, office=document.office, user=actor, document=document)
    return document, suggestion


@compensate_storage
@transaction.atomic
def _persist_upload(
    *, user, uploaded_file=None, uploaded_files=None, office=None, source=Source.UPLOAD,
    ocr_language="auto", allow_external_ocr=False, collection=Collection.OVPA, external_offices=None, defer_extraction=False,
) -> tuple[Document, object]:
    """Create a document and either extract now or enqueue extraction after commit.

    The synchronous branch is deliberately retained for laptops where django-q2
    is not enabled. Production workers never hold the upload transaction open
    while a third-party OCR request runs.
    """
    user = active_writer(user)
    if collection not in Collection.values:
        raise ValidationError("Choose a valid repository collection.")
    if collection == Collection.EXTERNAL and not user.is_office_admin:
        raise PermissionDenied("Only administrators can upload external documents.")
    if uploaded_file is not None and uploaded_files is not None:
        raise ValidationError("Supply either one file or the initial file list.")
    initial_files = list(uploaded_files) if uploaded_files is not None else ([uploaded_file] if uploaded_file is not None else [])
    if not initial_files or len(initial_files) > settings.MAX_UPLOAD_FILES:
        raise ValidationError(f"Upload between 1 and {settings.MAX_UPLOAD_FILES} files at a time.")
    for initial_file in initial_files:
        validate_upload(initial_file)
    uploaded_file = initial_files[0]
    office = office or user.office
    if not user.is_authenticated or not user.is_active or user.is_viewer:
        raise PermissionDenied("Your account cannot upload documents.")
    if not user.is_system_admin and getattr(office, "pk", None) != user.office_id:
        raise PermissionDenied("You can only upload to your own office.")
    if office is None:
        raise ValidationError("Your account has no office, so it cannot own a document.")
    counterparts = []
    if collection == Collection.EXTERNAL:
        from apps.tracking.external_services import lock_external_offices, recording_office

        office = recording_office(user, office)
        supplied = {getattr(entry, "pk", entry) for entry in (external_offices or [])}
        counterparts = list(lock_external_offices(supplied).values())
        if not supplied or len(counterparts) != len(supplied):
            raise ValidationError("Choose at least one active external office from the shared directory.")

    extraction = (
        extract_document_text(
            uploaded_file,
            uploaded_file.name,
            language_hint=ocr_language,
            allow_external_ocr=allow_external_ocr,
        )
        if not defer_extraction and not settings.ENABLE_BACKGROUND_TASKS and not settings.ENABLE_SOURCE_UNIT_EXTRACTION
        else None
    )
    document = Document.objects.create(
        title=(uploaded_file.name.rsplit(".", 1)[0][:255] or "Untitled document"),
        source=source,
        collection=collection,
        office=office,
        year=timezone.localdate().year,
        uploaded_by=user,
        ocr_text=extraction.text if extraction else "",
        ocr_status=getattr(OcrStatus, extraction.status, OcrStatus.PENDING) if extraction else OcrStatus.PENDING,
        ocr_engine=extraction.engine[:32] if extraction else "pending",
        ocr_confidence=extraction.confidence if extraction else None,
        ocr_language=ocr_language,
        allow_external_ocr=allow_external_ocr,
        ocr_notes="\n".join(extraction.notes) if extraction else "",
        page_count=extraction.pages if extraction else 0,
        access_level=AccessLevel.OFFICE,
    )

    uploaded_file.seek(0)
    document_file = create_stored_file(DocumentFile,
        document=document,
        file=uploaded_file,
        original_name=uploaded_file.name[:255],
        content_type=getattr(uploaded_file, "content_type", "") or "",
        size=uploaded_file.size or 0,
        checksum=checksum_of(uploaded_file),
        is_primary=True,
        page_count=extraction.pages if extraction else 0,
        extracted_chars=extraction.char_count if extraction else 0,
        uploaded_by=user,
    )
    for counterpart in counterparts:
        ExternalDocumentOffice.objects.create(document=document, external_office=counterpart, name_snapshot=counterpart.name, code_snapshot=counterpart.code)

    for initial_file in initial_files[1:]:
        _append_initial_file(document, initial_file, user=user, run_extraction=not defer_extraction)

    if extraction is None:
        _enqueue_extraction(document, user_id=user.pk, file_ids=None if settings.ENABLE_SOURCE_UNIT_EXTRACTION or defer_extraction else [document_file.pk])
        if defer_extraction and not settings.ENABLE_BACKGROUND_TASKS and not settings.ENABLE_SOURCE_UNIT_EXTRACTION:
            transaction.on_commit(lambda: _extract_saved_upload(document.pk, user.pk), robust=True)
        suggestion = Suggestion()
    else:
        suggestion = suggest_metadata(
            text=document.ocr_text, filename=uploaded_file.name, office=office, user=user
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


def _extract_saved_upload(document_id, actor_id):
    """Legacy local extraction also waits until saved-upload consumption commits."""
    from .tasks import extract_document_task

    try:
        extract_document_task(document_id, user_id=actor_id)
    except Exception:
        logger.exception("Saved upload extraction failed; the document remains available for retry")


def _enqueue_extraction(document, *, user_id=None, file_ids=None, replace=False, robust=False) -> None:
    """Queue extraction only after the surrounding transaction commits."""
    if settings.ENABLE_SOURCE_UNIT_EXTRACTION:
        from apps.accounts.models import User

        from .processing import drive_local_processing, request_processing

        actor = User.objects.filter(pk=user_id).first() if user_id is not None else None
        if user_id is not None and actor is None:
            raise PermissionDenied("The extraction requester no longer exists.")
        request_processing(document, user=actor, file_ids=file_ids, replace=replace)
        if not settings.ENABLE_BACKGROUND_TASKS:
            transaction.on_commit(lambda: drive_local_processing(document.pk), robust=True)
        return
    if not settings.ENABLE_BACKGROUND_TASKS:
        return
    from django_q.tasks import async_task

    if replace and file_ids is None:
        document.extraction_generation += 1
        document.save(update_fields=["extraction_generation"])
    # Appends share the current generation; only a full retry supersedes them.
    generation = document.extraction_generation

    def submit():
        try:
            async_task(
                "apps.documents.tasks.extract_document_task", document.pk,
                user_id=user_id, file_ids=file_ids, replace=replace, generation=generation,
            )
        except Exception:
            logger.exception("Could not queue text extraction for document %s", document.pk)
            with transaction.atomic():
                current = Document.objects.select_for_update().filter(
                    pk=document.pk, is_active=True, extraction_generation=generation,
                ).first()
                if current is not None:
                    current.retain_extraction_failure(
                        "Text extraction could not be queued. Your files were saved. Retry extraction when the background service is available."
                    )
                    current.save(update_fields=["ocr_status", "ocr_notes", "extraction_failed_generation", "updated_at"])

    transaction.on_commit(submit, robust=True)


def duplicate_of(uploaded_file, *, user=None, collection=Collection.OVPA) -> Document | None:
    """Warn (never block) when the same file was already archived."""
    try:
        digest = checksum_of(uploaded_file)
    except Exception:
        return None
    if user is None:
        return None
    visible = Document.objects.visible_to(user).in_collection(collection).filter(is_active=True)
    existing = DocumentFile.objects.filter(checksum=digest, document__in=visible).select_related("document").first()
    return existing.document if existing else None


# ---------------------------------------------------------------------------
# Metadata review (step 2 of the upload wizard)
# ---------------------------------------------------------------------------
@locked_mutation
def save_document_metadata(document: Document, *, user, data: dict, tag_names, metadata_values: dict,
                           accepted_from_suggestion: dict | None = None) -> Document:
    if not document.can_user_edit(user):
        raise PermissionDenied("Only the owning office can edit this record.")
    if data.get("office") and not user.is_system_admin and data["office"].pk != document.office_id:
        raise PermissionDenied("Only a system administrator can transfer ownership.")
    if document.is_external and data.get("office") and data["office"].pk != document.office_id:
        raise ValidationError("An external document must retain its filing office.")
    from apps.core.document_types import resolve_document_types
    previous_types = document.selected_document_types
    main = data.get("document_type")
    selected = data.get("document_types")
    if selected is None:
        selected = previous_types if main and main.pk == document.document_type_id else ([main] if main else [])
    selected, main = resolve_document_types(selected, main, existing=previous_types)
    for field_name in (
        "title", "description", "reference_number", "author_name", "recipient_name",
        "signatory", "access_level",
    ):
        if field_name in data:
            setattr(document, field_name, data[field_name] or "")

    if data.get("office"):
        document.office = data["office"]
    first_classification = not document.document_type_id and main is not None
    document.document_type = main
    document.document_date = data.get("document_date")
    document.year = data.get("year") or (document.document_date or timezone.localdate()).year
    if "retention_until" in data:
        document.retention_until = data["retention_until"]
    if first_classification and document.retention_until is None:
        document.retention_until = document.default_retention_until()
    document.ocr_language = data.get("ocr_language") or document.ocr_language
    if "allow_external_ocr" in data:
        document.allow_external_ocr = bool(data["allow_external_ocr"])
    document.save()

    document.document_types.set(selected)
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


def deletion_review_state(document):
    return {
        "updated_at": document.updated_at.isoformat(),
        "retention_until": document.retention_until.isoformat() if document.retention_until else None,
        "is_active": document.is_active,
    }


@locked_mutation
def review_document_deletion(document, *, user, decision, reason, expected_state,
                             next_review_date=None, confirmed=False):
    """Make one audited decision against a fresh, locked repository document."""
    if not document.can_user_review_deletion(user, include_deleted=True):
        raise PermissionDenied("Only an authorized administrator for the owning office can review deletion.")
    if expected_state != deletion_review_state(document):
        raise ValidationError("This document changed after you opened it. Reload the review before deciding.")
    reason = str(reason or "").strip()
    if not reason or len(reason) > 2000:
        raise ValidationError("Enter a reason of up to 2,000 characters.")
    if decision not in DeletionReview.Decision.values:
        raise ValidationError("Choose a valid review decision.")
    restoring = decision == DeletionReview.Decision.RESTORE
    if restoring == document.is_active:
        raise ValidationError("This decision does not match the document's current state. Reload the review.")
    if restoring and not document.deletion_recovery_open:
        raise ValidationError("The 30-day recovery period has ended. This document cannot be restored.")
    if decision == DeletionReview.Decision.DELETE:
        if not document.retention_is_due:
            raise ValidationError("Deletion review must be due before a document can be removed.")
        if confirmed is not True:
            raise ValidationError("Confirm that this document is approved for removal from the repository.")
        if next_review_date is not None:
            raise ValidationError("A removed document cannot have a next review date.")
    elif next_review_date is None or next_review_date <= timezone.localdate():
        raise ValidationError("Choose a next review date after today.")

    # History is required, in the same transaction, unlike best-effort general logging.
    DeletionReview.objects.create(
        document=document, decision=decision, reason=reason,
        previous_review_date=document.retention_until,
        next_review_date=next_review_date, reviewed_by=user,
        reviewer_label=user.display_name[:150],
    )
    document.is_active = decision != DeletionReview.Decision.DELETE
    document.deleted_at = None if document.is_active else timezone.now()
    document.purge_attempted_at = None
    if document.is_active:
        document.retention_until = next_review_date
    update_fields = ["is_active", "deleted_at", "purge_attempted_at", "retention_until", "updated_at"]
    if not document.is_active and document.ocr_status in {OcrStatus.PENDING, OcrStatus.RUNNING}:
        document.ocr_status = OcrStatus.SKIPPED
        document.ocr_notes = "Text extraction was cancelled when this document was removed from the repository. Restore the document before retrying."
        update_fields.extend(["ocr_status", "ocr_notes"])
    document.save(update_fields=update_fields)
    log_action(
        AuditLog.Action.DELETE if not document.is_active else AuditLog.Action.UPDATE,
        f"Deletion review: {DeletionReview.Decision(decision).label} — {document.title}"[:255],
        actor=user, target=document, extra={"decision": decision, "next_review_date": str(next_review_date or "")},
    )
    return document


@transaction.atomic
def set_tags(document: Document, tag_names, *, user=None) -> None:
    """Explicit metadata edits are human contributions, never training defaults."""
    from .tagging import _authorize, replace_human_tags

    document, user = _authorize(document, user)
    tags = []
    for raw in tag_names or []:
        tag, _ = Tag.get_or_create_by_name(raw)
        if tag and tag not in tags:
            tags.append(tag)
    replace_human_tags(document, tags, user=user)


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


def add_file_to_document(document: Document, uploaded_file, *, user, run_extraction: bool = True) -> DocumentFile:
    raise PermissionDenied("Repository file contents are frozen after filing. Create a new Tracking record for new files.")


def _append_initial_file(document: Document, uploaded_file, *, user, run_extraction: bool = True) -> DocumentFile:
    """Append only inside ingest_upload's initial, compensated transaction."""
    if not document.can_user_edit(user):
        raise PermissionDenied("Only the owning office can add files.")
    if document.ocr_status == OcrStatus.FAILED or document.extraction_failed_generation is not None:
        document.retain_extraction_failure()
        document.save(update_fields=["ocr_notes", "extraction_failed_generation"])
    validate_upload(uploaded_file)
    use_async = settings.ENABLE_BACKGROUND_TASKS and run_extraction
    extraction = (
        extract_document_text(
            uploaded_file,
            uploaded_file.name,
            language_hint=document.ocr_language,
            allow_external_ocr=document.allow_external_ocr,
        )
        if run_extraction and not use_async and not settings.ENABLE_SOURCE_UNIT_EXTRACTION
        else None
    )
    uploaded_file.seek(0)
    document_file = create_stored_file(DocumentFile,
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
    if use_async and not settings.ENABLE_SOURCE_UNIT_EXTRACTION:
        document.ocr_status = OcrStatus.PENDING
        document.save(update_fields=["ocr_status", "updated_at"])
        _enqueue_extraction(document, user_id=user.pk, file_ids=[document_file.pk], replace=False)
    elif extraction:
        combined = (document.ocr_text + "\n\n" + extraction.text).strip()
        document.ocr_text = combined[:200000]
        outcome_notes = "\n".join(extraction.notes)[:4000]
        if extraction.status == OcrStatus.FAILED or document.extraction_failed_generation is not None:
            document.retain_extraction_failure(outcome_notes)
        else:
            document.ocr_status = extraction.status if extraction.status == OcrStatus.SKIPPED else (OcrStatus.DONE if combined else OcrStatus.EMPTY)
            document.ocr_notes = outcome_notes
        document.ocr_confidence = extraction.confidence
        document.save(
            update_fields=["ocr_text", "ocr_status", "ocr_confidence", "ocr_notes", "extraction_failed_generation", "updated_at"]
        )
        document.rebuild_index()
    document.page_count = document.file_page_count
    document.save(update_fields=["page_count", "updated_at"])
    return document_file


def re_extract_document(document: Document, *, user) -> dict:
    result = _request_document_extraction(document, user=user)
    if settings.ENABLE_SOURCE_UNIT_EXTRACTION:
        document.refresh_from_db()
        return {"status": document.ocr_status, "characters": len(document.ocr_text),
                "failures": [document.ocr_notes] if document.ocr_status == OcrStatus.FAILED else []}
    return result


@locked_mutation
def _request_document_extraction(document: Document, *, user) -> dict:
    """Retry all attachments with current authorization in either runtime mode."""
    if not document.can_user_edit(user):
        raise PermissionDenied("Only the owning office can re-run extraction.")
    if not document.files.exists():
        raise ValidationError("This record has no file to read.")
    if document.ocr_status == OcrStatus.FAILED or document.extraction_failed_generation is not None:
        document.retain_extraction_failure()
        document.save(update_fields=["ocr_notes", "extraction_failed_generation"])
    if settings.ENABLE_BACKGROUND_TASKS or settings.ENABLE_SOURCE_UNIT_EXTRACTION:
        document.ocr_status = OcrStatus.PENDING
        document.save(update_fields=["ocr_status", "updated_at"])
        _enqueue_extraction(document, user_id=user.pk, replace=True)
        return {"status": OcrStatus.PENDING, "failures": []}
    from .tasks import extract_document_task

    result = extract_document_task(document.pk, user_id=user.pk, replace=True)
    document.refresh_from_db()
    return result


# ---------------------------------------------------------------------------
# Archiving a completed tracking record
# ---------------------------------------------------------------------------
@locked_mutation
def archive_tracking_record(record, *, user, tag_names=None, description="") -> Document:
    """File a completed record and clean up new copies if filing rolls back."""
    created_files = []
    try:
        return _archive_tracking_record(
            record, user=user, tag_names=tag_names, description=description,
            created_files=created_files,
        )
    except Exception:
        for storage, name in created_files:
            try:
                storage.delete(name)
            except Exception:
                logger.exception("Could not clean up a new repository copy after failed filing")
        raise


def _archive_tracking_record(record, *, user, tag_names, description, created_files) -> Document:
    """Approve a finished DTS record into the repository, keeping its identity.

    This is the act that files the document, and it is what moves the record
    from COMPLETED_PENDING_UPLOAD to COMPLETED at the end. Before this stage
    existed, completing a record set COMPLETED straight away and filing was an
    optional afterthought, so "completed" told a reader nothing about whether
    the document had actually reached the repository.

    The approval permission applies here too, including callers outside views.
    """
    if not record.can_user_approve_upload(user):
        raise PermissionDenied("Only an administrator for this document's office can approve filing.")
    if record.status == Status.COMPLETED:
        raise ValidationError("This record has already been approved into the repository.")
    if record.status != Status.COMPLETED_PENDING_UPLOAD:
        raise ValidationError(
            "Only a completed record can be approved into the repository. "
            "Mark the document complete first."
        )
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
        allow_external_ocr=False,
        ocr_notes="External OCR is disabled by default for records archived from tracking. An authorized editor may opt in and re-run extraction.",
    )

    document.document_types.set(record.selected_document_types)
    text_parts = [record.subject, record.instructions, record.remarks, record.completion_note]
    created_file_ids = []
    extraction_failures = []
    from .file_versions import copy_tracking_history
    for document_file in copy_tracking_history(record, document, created_files):
        created_file_ids.append(document_file.pk)
        if not settings.ENABLE_BACKGROUND_TASKS and not settings.ENABLE_SOURCE_UNIT_EXTRACTION:
            with document_file.file.open("rb") as buffer:
                extraction = extract_document_text(
                    buffer,
                    document_file.original_name,
                    language_hint=document.ocr_language,
                    allow_external_ocr=document.allow_external_ocr,
                )
            document_file.page_count = extraction.pages
            document_file.extracted_chars = extraction.char_count
            document_file.save(update_fields=["page_count", "extracted_chars", "updated_at"])
            if extraction.text:
                text_parts.append(extraction.text)
            if extraction.status == OcrStatus.FAILED:
                extraction_failures.extend(extraction.notes or ["Text extraction failed. Retry extraction when available."])

    document.ocr_text = normalise_text("\n\n".join(part for part in text_parts if part))[:200000]
    document.ocr_status = OcrStatus.PENDING if settings.ENABLE_BACKGROUND_TASKS or settings.ENABLE_SOURCE_UNIT_EXTRACTION else (OcrStatus.DONE if document.ocr_text else OcrStatus.EMPTY)
    if extraction_failures:
        document.retain_extraction_failure("\n".join(extraction_failures))
    document.ocr_engine = "pending" if settings.ENABLE_BACKGROUND_TASKS else "archive-merge"
    document.page_count = document.file_page_count
    document.save(update_fields=["ocr_text", "ocr_status", "ocr_engine", "page_count", "ocr_notes", "extraction_failed_generation", "updated_at"])


    if settings.ENABLE_BACKGROUND_TASKS or settings.ENABLE_SOURCE_UNIT_EXTRACTION:
        suggestion = Suggestion()
        if not settings.ENABLE_SOURCE_UNIT_EXTRACTION:
            _enqueue_extraction(document, user_id=user.pk, file_ids=created_file_ids, replace=False)
    else:
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
    from .tagging import initialize_filing_tags

    initialize_filing_tags(document, tag_names or [], user=user, record=record)
    document.rebuild_index()

    # Only now does the record become COMPLETED: the status and the Document
    # are written in the same transaction, so "completed" and "in the
    # repository" can never disagree.
    #
    # Approving your own completion is allowed — a one- or two-person office has
    # nobody else, and a control that cannot be satisfied deadlocks the queue
    # rather than protecting anything. But it is not the same act as an
    # independent check, so it is not written down as though it were: both
    # trails name it, and `approved_by` makes it answerable in a query.
    self_approved = bool(record.completed_by_id) and record.completed_by_id == user.pk
    record.status = Status.COMPLETED
    record.approved_by = user
    record.approved_at = timezone.now()
    record.is_archived = True
    record.archived_at = record.approved_at
    record.save(
        update_fields=[
            "status", "approved_by", "approved_at", "is_archived", "archived_at", "updated_at",
        ]
    )
    if settings.ENABLE_SOURCE_UNIT_EXTRACTION:
        # The narrow local-filing permission checks the recorded approver.
        # Persist approval before creating this actor-bound intent.
        _enqueue_extraction(document, user_id=user.pk, file_ids=created_file_ids, replace=False)
    add_activity(
        record,
        RecordActivity.Event.ARCHIVED,
        (
            f"Self-approved into the Document Repository by {user.display_name} "
            "(same person marked it completed)"
            if self_approved
            else f"Approved into the Document Repository by {user.display_name}"
        ),
        actor=user,
    )
    log_action(
        AuditLog.Action.ARCHIVE,
        (
            f"Self-approved {record.tracking_number} into the repository "
            "(completed and approved by the same person)"
            if self_approved
            else f"Approved {record.tracking_number} into the repository"
        ),
        actor=user,
        target=document,
        extra={"self_approved": self_approved, "completed_by": record.completed_by_id},
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
