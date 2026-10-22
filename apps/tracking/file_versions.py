"""Authoritative, serialized creation of a new Tracking file version."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.core.permissions import locked_mutation
from apps.core.storage import compensate_storage, create_stored_file
from apps.core.utils import checksum_of, validate_upload

from .external_services import _event, _mutation, _open
from .models import COMPLETED_STATUSES, Attachment, ExternalAttachment, RecordActivity, Status
from .services import add_activity, ensure_received, refuse_viewers


def _new_version(record, file_group_id, uploaded_file, *, user, expected_version, change_summary, model):
    summary = (change_summary or "").strip()
    if not summary or len(summary) > 1000:
        raise ValidationError("Describe the change in 1 to 1000 characters.")
    group_model = model._meta.get_field("file_group").remote_field.model
    group = group_model.objects.select_for_update().filter(pk=file_group_id, record_id=record.pk).first()
    if group is None:
        raise ValidationError("This file group does not belong to this record.")
    latest = group.latest_version
    if latest is None or str(expected_version) != str(latest.version_number):
        raise ValidationError("This file changed. Reload it before uploading a new version.")
    validate_upload(uploaded_file)
    digest = checksum_of(uploaded_file)
    current_digest = latest.checksum
    if not current_digest:
        try:
            with latest.file.open("rb") as stream:
                current_digest = checksum_of(stream)
        except Exception as exc:
            raise ValidationError("The current file could not be read. Restore it before uploading a version.") from exc
    if digest == current_digest:
        raise ValidationError("This file has the same content as the current version.")
    step = record.current_step if model is ExternalAttachment else record.current_step_queryset.filter(
        to_office_id=user.office_id, received_at__isnull=False,
    ).first()
    uploaded_file.seek(0)
    return create_stored_file(model, record=record, file_group=group, version_number=latest.version_number + 1,
        change_summary=summary, routing_step=step, file=uploaded_file, original_name=uploaded_file.name[:255],
        content_type=(getattr(uploaded_file, "content_type", "") or "")[:120], size=uploaded_file.size or 0,
        checksum=digest, uploaded_by=user)


@compensate_storage
@locked_mutation
def upload_attachment_version(record, file_group_id, uploaded_file, *, user, expected_version, change_summary):
    refuse_viewers(user, "upload file versions")
    if record.status in COMPLETED_STATUSES:
        raise ValidationError("Completed records cannot receive new file versions.")
    if record.status != Status.DRAFT:
        ensure_received(record)
    if not record.can_user_act(user):
        raise PermissionDenied("Only the office holding the document can upload a file version.")
    attachment = _new_version(record, file_group_id, uploaded_file, user=user, expected_version=expected_version,
        change_summary=change_summary, model=Attachment)
    add_activity(record, RecordActivity.Event.ATTACHMENT,
        f"{user.display_name} uploaded version {attachment.version_number} of {attachment.original_name}"[:255],
        actor=user, detail=attachment.change_summary)
    record.recalculate_status()
    record.touch_movement()
    return attachment


@compensate_storage
@_mutation
def upload_external_attachment_version(record, file_group_id, uploaded_file, *, user, expected_version, change_summary):
    _open(record)
    if not record.can_act(user):
        raise PermissionDenied("Only the confirmed custodian can upload a file version.")
    attachment = _new_version(record, file_group_id, uploaded_file, user=user, expected_version=expected_version,
        change_summary=change_summary, model=ExternalAttachment)
    if record.status != Status.DRAFT and record.current_office_id:
        record.status = Status.IN_PROCESS
    _event(record, user, "ATTACHMENT", "File version uploaded", detail=attachment.change_summary,
        actual_at=timezone.now() if record.current_office_id else None)
    return attachment
