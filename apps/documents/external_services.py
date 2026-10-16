"""Filing external cases without joining the existing OVPA collection."""

import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.core.models import AuditLog, Notification
from apps.core.notifications import notify_office, resolve_for_external_record
from apps.core.permissions import active_writer
from apps.core.utils import log_action, normalise_text
from apps.tracking.models import Status

from .models import AccessLevel, Collection, Document, DocumentFile, ExternalDocumentOffice, OcrStatus, Source
from .services import _enqueue_extraction, set_tags

logger = logging.getLogger("doctrack")


def archive_external_record(record, *, user, expected_revision, tag_names=None, description=""):
    """Serialize filing and remove every new storage copy if the transaction fails."""
    from apps.tracking.models import ExternalActivity, ExternalTrackingRecord

    created_files = []
    try:
        with transaction.atomic():
            actor = active_writer(user)
            locked = ExternalTrackingRecord.objects.select_for_update().get(pk=record.pk)
            if not locked.can_manage(actor):
                raise PermissionDenied("Only an administrator for the managing office can file this case.")
            existing = Document.objects.filter(external_tracking_record=locked).first()
            if existing:
                return existing
            if locked.revision != expected_revision:
                raise ValidationError("This case changed. Reload it before filing.")
            if locked.status != Status.COMPLETED_PENDING_UPLOAD:
                raise ValidationError("Complete this case before filing it in the repository.")
            filed_at = timezone.now()
            document_date = timezone.localdate(locked.completed_at or filed_at)
            document = Document.objects.create(
                title=locked.title[:255], description=description or locked.description,
                source=Source.DTS, collection=Collection.EXTERNAL, external_tracking_record=locked,
                reference_number=locked.tracking_number, office=locked.managing_office,
                document_type=locked.document_type, document_date=document_date, year=document_date.year,
                access_level=AccessLevel.OFFICE, uploaded_by=actor, allow_external_ocr=False,
                ocr_status=OcrStatus.PENDING,
                ocr_notes="External OCR is disabled by default for filed external cases.",
            )
            document.document_types.set(locked.selected_document_types)
            counterparts = {}
            for step in locked.routing_steps.select_related("from_external_office", "to_external_office"):
                for direction in ("from", "to"):
                    office = getattr(step, f"{direction}_external_office")
                    if office and office.pk not in counterparts:
                        counterparts[office.pk] = (office, getattr(step, f"{direction}_name_snapshot") or office.name)
            if locked.current_external_office_id and locked.current_external_office_id not in counterparts:
                counterparts[locked.current_external_office_id] = (locked.current_external_office, locked.current_office_snapshot or locked.current_external_office.name)
            if not counterparts:
                raise ValidationError("Record at least one external office in the case history before filing.")
            for office, name_snapshot in counterparts.values():
                ExternalDocumentOffice.objects.create(
                    document=document, external_office=office,
                    name_snapshot=name_snapshot, code_snapshot=getattr(office, "code", ""),
                )
            file_ids = []
            for attachment in locked.attachments.all():
                try:
                    with attachment.file.open("rb") as source_file:
                        payload = source_file.read()
                except Exception as exc:
                    raise ValidationError("An attachment could not be read. Restore the file and retry filing.") from exc
                copied = DocumentFile(
                    document=document, original_name=attachment.original_name,
                    content_type=attachment.content_type, size=attachment.size or len(payload),
                    checksum=attachment.checksum, is_primary=not file_ids, uploaded_by=actor,
                )
                copied.file.save(attachment.original_name, ContentFile(payload), save=False)
                created_files.append((copied.file.storage, copied.file.name))
                copied.save()
                file_ids.append(copied.pk)
            document.ocr_text = normalise_text("\n\n".join(filter(None, [locked.title, locked.description, locked.original_reference])))[:200000]
            document.ocr_status = OcrStatus.PENDING if file_ids else OcrStatus.DONE
            document.save(update_fields=["ocr_text", "ocr_status", "updated_at"])
            set_tags(document, tag_names, user=actor)
            document.rebuild_index()
            locked.status = Status.COMPLETED
            locked.filed_at = filed_at
            locked.revision += 1
            locked.save(update_fields=["status", "filed_at", "revision", "updated_at"])
            resolve_for_external_record(locked)
            ExternalActivity.objects.create(
                record=locked, event="FILED", message="Filed into the External Document Repository",
                actor=actor, actor_office=actor.office, actor_name_snapshot=actor.display_name[:150],
                actor_office_snapshot=actor.office.name if actor.office_id else "",
                actual_at=filed_at,
            )
            log_action(
                AuditLog.Action.ARCHIVE, f"Filed external case {locked.tracking_number}", actor=actor, target=document,
                extra={"collection": Collection.EXTERNAL, "external_record_id": locked.pk, "external_tracking_number": locked.tracking_number},
            )
            notify_office(
                locked.managing_office, kind=Notification.Kind.COMPLETED,
                title=f"External case filed: {locked.tracking_number}"[:180],
                message=f"{locked.title} is now in the External Document Repository.",
                url=document.get_absolute_url(), external_record=locked, document=document,
            )
            if file_ids:
                if settings.ENABLE_BACKGROUND_TASKS:
                    _enqueue_extraction(document, user_id=actor.pk, file_ids=file_ids, replace=False, robust=True)
                else:
                    # The case and document must exist before any extraction starts.
                    transaction.on_commit(lambda: _extract_after_filing(document.pk, actor.pk, file_ids))
        record.refresh_from_db()
        return document
    except Exception:
        for storage, name in created_files:
            try:
                storage.delete(name)
            except Exception:
                logger.exception("Could not remove failed external filing copy")
        raise


def _extract_after_filing(document_id, actor_id, file_ids):
    from .tasks import extract_document_task

    try:
        extract_document_task(document_id, user_id=actor_id, file_ids=file_ids, replace=False)
    except Exception:
        logger.exception("Text extraction failed after external filing; an administrator can retry")
