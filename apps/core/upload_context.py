"""Server-resolved upload contexts. Selecting or staging files grants no custody."""
from dataclasses import asdict, dataclass
from uuid import uuid4

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import Office, User
from apps.tracking.models import COMPLETED_STATUSES, ExternalTrackingRecord, Status, TrackingRecord

from .upload_models import CREATION_WORKFLOWS, EXTERNAL_WORKFLOWS, INTERNAL_WORKFLOWS


@dataclass(frozen=True)
class UploadContext:
    workflow: str
    collection: str
    actor_office_id: int | None
    creation_office_id: int | None
    target_kind: str = ""
    target_id: int | None = None
    group_id: int | None = None

    def as_dict(self):
        return asdict(self)


def resolve_upload_context(user, workflow, *, target_id=None, group_id=None, query=None, lock_parent=False):
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Sign in to upload files.")
    actor = User.objects.select_related("office").filter(pk=user.pk, is_active=True).first()
    if actor is None or not actor.can_start_work:
        raise PermissionDenied("Your account cannot upload files.")
    if not actor.is_system_admin and (not actor.office_id or not actor.office.is_active):
        if workflow in CREATION_WORKFLOWS and query is not None and "office" in query:
            raise ValidationError("Choose one active office available to your account.")
        raise PermissionDenied("Your account cannot upload files.")
    if workflow not in (*INTERNAL_WORKFLOWS, *EXTERNAL_WORKFLOWS):
        raise ValidationError("Choose a supported upload action.")
    external = workflow in EXTERNAL_WORKFLOWS
    creation_office_id = actor.office_id
    if query is not None and "office" in query:
        choices = query.getlist("office") if hasattr(query, "getlist") else [query["office"]]
        all_perspective = choices == ["all"] and actor.is_system_admin and workflow not in CREATION_WORKFLOWS
        if len(choices) != 1:
            raise ValidationError("Choose one valid office view.")
        if not all_perspective:
            value = str(choices[0])
            if value.isascii() and value.isdecimal():
                selected = Office.objects.filter(pk=int(value), is_active=True).first()
            elif not external and workflow not in CREATION_WORKFLOWS:
                # Existing internal detail URLs also accept office codes. The
                # viewing perspective never replaces the actor's own custody.
                selected = Office.objects.filter(code=value, is_active=True).first()
            else:
                raise ValidationError("Choose one valid office view.")
            if selected is None or (not actor.is_system_admin and selected.pk != actor.office_id):
                if external and workflow in CREATION_WORKFLOWS:
                    raise ValidationError("Choose one active office available to your account.")
                raise PermissionDenied("This office view is not available to your account.")
            creation_office_id = selected.pk
    if workflow in CREATION_WORKFLOWS:
        if not creation_office_id or not Office.objects.filter(pk=creation_office_id, is_active=True).exists():
            if external:
                raise ValidationError("Select an internal office perspective before creating an external entry.")
            raise PermissionDenied("Select an active office before creating an upload.")
        if target_id is not None or group_id is not None:
            raise ValidationError("A creation upload cannot target an existing record.")
        if external and not actor.is_office_admin:
            raise PermissionDenied("Only administrators can create external entries.")
        return UploadContext(workflow, "EXTERNAL" if external else "OVPA", actor.office_id, creation_office_id)
    model = ExternalTrackingRecord if external else TrackingRecord
    # Visibility queries may use DISTINCT for routing/grant joins. PostgreSQL
    # cannot lock that shape; lock only the exact parent in the narrowed set.
    records = model.objects.filter(pk__in=model.objects.visible_to(actor).values("pk"))
    if lock_parent:
        records = records.select_for_update(of=("self",))
    try:
        record = records.filter(pk=target_id).first()
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValidationError("This tracking record is unavailable.") from exc
    if record is None:
        raise PermissionDenied("This tracking record is outside your access.")
    allowed = record.can_act(actor) if external else record.can_user_act(actor)
    if record.status in COMPLETED_STATUSES or not allowed:
        raise PermissionDenied("Confirm receipt and return completed work to tracking before changing files.")
    if workflow == "tracking-edit" and record.status != Status.DRAFT:
        raise PermissionDenied("This record is no longer an editable draft.")
    if workflow.endswith("-version"):
        group = record.file_groups.filter(pk=group_id, discarded_at__isnull=True).first()
        if group is None:
            raise PermissionDenied("This file is outside the record or is no longer available.")
        group_id = group.pk
    elif group_id is not None:
        raise ValidationError("Choose a file group only when uploading a new version.")
    return UploadContext(workflow, "EXTERNAL" if external else "OVPA", actor.office_id,
                         creation_office_id, "external" if external else "tracking", record.pk, group_id)


def form_nonce_for(user, context):
    return signing.dumps({"nonce": str(uuid4()), "actor": user.pk, "context": context.as_dict()},
                         salt="doctrack-upload-form", compress=True)
