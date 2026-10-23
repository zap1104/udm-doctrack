"""Audited availability changes. Physical cleanup is a separate committed job."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .file_lifecycle_models import lifecycle_write
from .models import FileLifecycleEvent, StorageCleanupIntent, UploadDraft
from .permissions import active_writer


def available_groups(record):
    return record.file_groups.filter(discarded_at__isnull=True)


def _parent(group):
    if hasattr(group, "record_id"):
        record = group.record
        external = record._meta.model_name == "externaltrackingrecord"
        return record, "external" if external else "tracking", "EXTERNAL" if external else "OVPA", getattr(record, "managing_office_id", None) if external else record.originating_office_id
    document = group.document
    return document, "repository", document.collection, document.office_id


def _receipt(group, *, actor, action, reason, version=None):
    parent, kind, collection, office_id = _parent(group)
    metadata = {} if version is None else {
        "version_number": version.version_number, "original_name": version.original_name,
        "checksum": version.checksum, "size": version.size,
        "change_summary": version.change_summary,
        "uploader_name": version.uploader_name_snapshot,
        "uploader_office": version.uploader_office_snapshot,
        "uploaded_at": (getattr(version, "created_at", None) or getattr(version, "uploaded_at", None)).isoformat() if (getattr(version, "created_at", None) or getattr(version, "uploaded_at", None)) else None,
    }
    return FileLifecycleEvent.objects.create(resource_kind=kind, parent_id=parent.pk,
        group_id=group.pk, version_id=version.pk if version else None, collection=collection,
        office_id_snapshot=office_id, actor_id_snapshot=actor.pk if actor else None,
        actor_name_snapshot=actor.display_name if actor else "",
        actor_office_snapshot=actor.office.name if actor and actor.office_id else "",
        action=action, reason=reason, metadata=metadata)


def _retire(group, versions, *, actor, reason):
    events = []
    parent, kind, collection, office_id = _parent(group)
    for version in versions:
        with lifecycle_write():
            type(version).objects.filter(pk=version.pk, retired_at__isnull=True).update(
                retired_at=timezone.now(), retirement_reason=reason[:255])
        if kind == "repository":
            # Historical source evidence must not act as an alternate copy.
            from apps.documents.models import ExtractionRun, ExtractionUnit, SearchChunk
            runs = ExtractionRun.objects.filter(file_version=version)
            runs.update(is_current=False)
            SearchChunk.objects.filter(unit__run__in=runs).delete()
            ExtractionUnit.objects.filter(run__in=runs).update(raw_text="", native_raw_text="", ocr_raw_text="", normalized_text="")
        events.append(_receipt(group, actor=actor, action="retired", reason=reason, version=version))
        if version.file.name:
            StorageCleanupIntent.objects.get_or_create(storage_identity=version.storage_identity,
                storage_key=version.file.name, defaults=dict(reason=reason[:255], resource_kind=kind,
                    parent_id=parent.pk, version_id=version.pk, collection=collection,
                    office_id_snapshot=office_id, state="pending" if version.storage_identity else "blocked",
                    last_error="" if version.storage_identity else "Legacy storage identity requires verified reconciliation."))
    return events


@transaction.atomic
def retire_excess_versions(group, *, policy_actor=None, limit=5):
    if limit != 5:
        raise ValidationError("The retention policy keeps five file versions.")
    actor = active_writer(policy_actor) if policy_actor is not None else None
    parent, kind, _, _ = _parent(group)
    parent = type(parent).objects.select_for_update().get(pk=parent.pk)
    group = type(group).objects.select_for_update().get(pk=group.pk)
    if kind == "repository":
        if not parent.is_active:
            return []
    elif group.discarded_at:
        return []
    if actor and kind != "repository" and not (parent.can_act(actor) if kind == "external" else parent.can_user_act(actor)):
        raise PermissionDenied("Only the current custodian can update files.")
    versions = group.versions.filter(retired_at__isnull=True).exclude(file="").order_by("-version_number")
    return _retire(group, list(versions[limit:]), actor=actor, reason="Five-version retention policy")


@transaction.atomic
def discard_draft_group(record, group_id, *, user, reason, expected_version, expected_revision=None):
    from apps.tracking.models import Status

    actor = active_writer(user)
    locked = type(record).objects.select_for_update().get(pk=record.pk)
    external = locked._meta.model_name == "externaltrackingrecord"
    if not type(record).objects.visible_to(actor).filter(pk=locked.pk).exists() or not (locked.can_act(actor) if external else locked.can_user_act(actor)):
        raise PermissionDenied("You cannot discard files from this draft.")
    if locked.status != Status.DRAFT or locked.routing_steps.exists():
        raise ValidationError("Files can only be discarded before the first endorsement or dispatch.")
    if external and str(expected_revision) != str(locked.revision):
        raise ValidationError("This record changed. Reload it before trying again.")
    reason = str(reason or "").strip()
    if not reason or len(reason) > 2000:
        raise ValidationError("Give a discard reason in 1 to 2000 characters.")
    relation = "external_group_id" if external else "tracking_group_id"
    drafts = list(UploadDraft.objects.select_for_update().filter(**{relation: group_id}, state="open").order_by("pk"))
    group = locked.file_groups.select_for_update().filter(pk=group_id, discarded_at__isnull=True).first()
    if group is None or group.latest_version is None or str(group.latest_version.version_number) != str(expected_version):
        raise ValidationError("This file changed. Reload it before discarding.")
    group.discarded_at, group.discarded_by, group.discard_reason = timezone.now(), actor, reason
    with lifecycle_write():
        group.save(update_fields=["discarded_at", "discarded_by", "discard_reason"])
    _retire(group, list(group.versions.filter(retired_at__isnull=True)), actor=actor, reason="Draft file discarded")
    event = _receipt(group, actor=actor, action="discarded", reason=reason)
    for draft in drafts:
        for staged in draft.files.filter(state="saved"):
            staged.state = "removed"
            staged.save(update_fields=["state"])
            StorageCleanupIntent.objects.get_or_create(storage_identity=staged.storage_identity, storage_key=staged.file.name,
                defaults={"reason": "Draft file discarded", "resource_kind": "upload", "collection": draft.collection})
        draft.state, draft.revision = "discarded", draft.revision + 1
        draft.save(update_fields=["state", "revision"])
    if external:
        locked.revision += 1
        locked.save(update_fields=["revision"])
    record.refresh_from_db()
    return event
