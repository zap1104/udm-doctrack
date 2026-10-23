"""Explicit adapters from private saved uploads into existing Tracking services."""
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import Office
from apps.core.models import AuditLog
from apps.core.upload_drafts import UploadResult
from apps.core.utils import log_action

from . import external_services, services
from .file_versions import upload_attachment_version, upload_external_attachment_version
from .models import ExternalTrackingRecord, TrackingRecord

CREATE_FIELDS = ("subject", "instructions", "document_type", "document_types", "classification", "priority", "requested_action", "due_at")
EXTERNAL_FIELDS = ("title", "description", "original_reference", "document_type", "document_types", "from_office", "from_external_office", "actual_at")


def finalize_tracking_upload(context, data, files, *, user, expected_version):
    workflow = context.workflow
    external = context.collection == "EXTERNAL"
    model = ExternalTrackingRecord if external else TrackingRecord
    record = model.objects.get(pk=context.target_id) if context.target_id else None
    office = Office.objects.get(pk=context.creation_office_id, is_active=True) if workflow in {"tracking-create", "external-create"} else None
    version = None
    if workflow == "tracking-create":
        record = services.create_draft_record(user=user, originating_office=office,
            **{name: data[name] for name in CREATE_FIELDS if name in data})
        services.attach_files(record, files, user=user)
    elif workflow == "tracking-edit":
        from apps.core.document_types import resolve_document_types

        selected, main = resolve_document_types(data.get("document_types"), data.get("document_type"), existing=record.selected_document_types)
        for name in CREATE_FIELDS:
            if name not in {"document_types", "document_type"} and name in data:
                setattr(record, name, data[name])
        record.document_type = main
        record.save()
        record.document_types.set(selected)
        services.attach_files(record, files, user=user)
        log_action(AuditLog.Action.UPDATE, f"Updated draft “{record.subject}”", actor=user, target=record)
    elif workflow == "tracking-remark":
        services.add_remark(record, user=user, remark=data["remark"])
        services.attach_files(record, files, user=user)
    elif workflow == "tracking-route":
        services.attach_files(record, files, user=user)
        services.route_record(record, list(data["offices"]), user=user, instructions=data.get("instructions", ""),
            action=data["action"], due_at=data.get("due_at"))
    elif workflow in {"tracking-version", "external-version"}:
        if len(files) != 1:
            raise ValidationError("Choose exactly one file for the new version.")
        kwargs = {"user": user, "expected_version": expected_version if expected_version is not None else data.get("expected_version"),
                  "change_summary": data.get("change_summary", "")}
        if external:
            kwargs["expected_revision"] = data.get("expected_revision", data.get("revision"))
        version = (upload_external_attachment_version if external else upload_attachment_version)(
            record, context.group_id, files[0], **kwargs)
    elif workflow == "external-create":
        record = external_services.create_record(user=user, managing_office=office,
            **{name: data[name] for name in EXTERNAL_FIELDS if name in data})
        for uploaded in files:
            external_services.add_attachment(record, user=user, expected_revision=record.revision, uploaded_file=uploaded)
    elif workflow == "external-attachment":
        if len(files) != 1:
            raise ValidationError("Choose exactly one supporting attachment.")
        external_services.add_attachment(record, user=user, expected_revision=data.get("revision"),
            uploaded_file=files[0], description=data.get("description", ""))
    else:
        raise ValidationError("This saved upload action is not supported.")
    redirect_url = record.get_absolute_url()
    if workflow in {"tracking-create", "tracking-edit", "external-create"}:
        redirect_url = reverse("tracking:external_review" if external else "tracking:review", args=[record.pk])
    return UploadResult(workflow, "external" if external else "tracking", record.pk, redirect_url,
                        getattr(record, "revision", None), version.version_number if version else None)
