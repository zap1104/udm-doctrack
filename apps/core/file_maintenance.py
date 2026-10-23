"""Bounded maintenance of committed lifecycle intents and private upload expiry."""
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import StorageCleanupIntent, UploadDraft
from .storage_references import locked_storage_key, referenced_storage_key, storage_for_identity


def _bound(limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValidationError("Use a maintenance batch size from 1 to 1000.")
    return limit


def _summary():
    return dict(candidates=0, expired=0, retired=0, removed=0, shared=0, blocked=0, failed=0)


@contextmanager
def locked_manifest(draft_id):
    """Maintenance does not need lost edit rights, but uses the same lock order."""
    from apps.accounts.models import User
    from apps.tracking.models import ExternalTrackingRecord, TrackingRecord
    with transaction.atomic():
        preview = UploadDraft.objects.get(pk=draft_id)
        if preview.owner_id:
            User.objects.select_for_update(no_key=True).filter(pk=preview.owner_id).first()
        for model, pk in ((TrackingRecord, preview.tracking_record_id), (ExternalTrackingRecord, preview.external_record_id)):
            if pk:
                model.objects.select_for_update().filter(pk=pk).first()
        yield UploadDraft.objects.select_for_update().get(pk=draft_id)


def _close_manifest(draft, state):
    from .upload_drafts import queue_stage_cleanup
    draft.state, draft.revision = state, draft.revision + 1
    draft.updated_at = timezone.now()
    draft.save(update_fields=["state", "revision", "updated_at"])
    for staged in draft.files.all():
        if staged.state == "saved":
            staged.state = "removed"
            staged.save(update_fields=["state"])
        queue_stage_cleanup(staged, reason=f"Private upload {state}")


def discard_manifest(draft_id, *, user, expected_revision):
    from .upload_drafts import _check_revision, locked_draft
    with locked_draft(draft_id, user=user) as (_, draft, _):
        _check_revision(draft, expected_revision)
        _close_manifest(draft, "discarded")


@contextmanager
def _locked_owner(intent):
    from apps.core.models import StagedUpload
    from apps.documents.models import Document
    from apps.tracking.models import ExternalTrackingRecord, TrackingRecord
    # Temporary quota accounting requires the manifest lock before the key.
    stages = list(StagedUpload.objects.filter(storage_identity=intent.storage_identity,
        file=intent.storage_key).values_list("draft_id", flat=True).distinct())
    if stages:
        # Immutable stage keys are unique to a single manifest. More than one
        # is a corruption requiring operator review, never guessed ownership.
        if len(stages) != 1:
            raise ValidationError("Ambiguous temporary storage ownership.")
        with locked_manifest(stages[0]):
            yield
    else:
        model = {"tracking": TrackingRecord, "external": ExternalTrackingRecord, "repository": Document}.get(intent.resource_kind)
        with transaction.atomic():
            if model and intent.parent_id:
                model.objects.select_for_update().filter(pk=intent.parent_id).first()
            yield


def _claim(pk, now):
    with transaction.atomic():
        intent = StorageCleanupIntent.objects.select_for_update(skip_locked=True).filter(pk=pk).first()
        if intent is None or intent.state in {"complete", "blocked"} or intent.superseded_by_id:
            return None
        if intent.state == "running" and intent.lease_until and intent.lease_until > now:
            return None
        if intent.retry_at and intent.retry_at > now:
            return None
        intent.state, intent.lease_token = "running", uuid4()
        intent.lease_until, intent.attempts = now + timedelta(minutes=5), intent.attempts + 1
        intent.save(update_fields=["state", "lease_token", "lease_until", "attempts"])
        return intent


def _finish(intent, state, *, now, error=""):
    intent.state, intent.last_error = state, error
    intent.lease_until = None
    intent.retry_at = now + timedelta(seconds=min(3600, 30 * 2 ** min(intent.attempts, 7))) if state == "retry" else None
    intent.completed_at = now if state == "complete" else None
    intent.save(update_fields=["state", "last_error", "lease_until", "retry_at", "completed_at"])


def _remove_intent(claimed, now):
    from apps.core.models import StagedUpload
    with _locked_owner(claimed), locked_storage_key(claimed.storage_identity, claimed.storage_key):
        intent = StorageCleanupIntent.objects.select_for_update().get(pk=claimed.pk)
        if intent.state != "running" or intent.lease_token != claimed.lease_token:
            return None
        storage = storage_for_identity(intent.storage_identity)
        if storage is None:
            _finish(intent, "blocked", now=now, error="Storage identity is unknown or no longer configured. Verified reconciliation is required.")
            return "blocked"
        if referenced_storage_key(intent.storage_identity, intent.storage_key):
            _finish(intent, "retry", now=now, error="The exact object is still referenced; it has been preserved.")
            return "shared"
        storage.delete(intent.storage_key)
        if storage.exists(intent.storage_key):
            raise OSError("Storage did not confirm removal")
        # Only temporary keys are cleared, under the manifest lock. Immutable
        # version keys remain as historical provenance, never as live access.
        StagedUpload.objects.filter(storage_identity=intent.storage_identity, file=intent.storage_key).update(file="")
        _finish(intent, "complete", now=now)
        return "removed"


def maintain_file_uploads(*, now=None, limit=100, dry_run=True):
    if not dry_run and connection.in_atomic_block:
        raise ValidationError("Run physical cleanup after the caller transaction commits.")
    limit, now, result = _bound(limit), now or timezone.now(), _summary()
    expired = list(UploadDraft.objects.filter(state="open", expires_at__lte=now).order_by("expires_at", "pk").values_list("pk", flat=True)[:limit])
    result["candidates"] += len(expired)
    if not dry_run:
        for pk in expired:
            with locked_manifest(pk) as draft:
                if draft.state == "open" and draft.expires_at <= now:
                    _close_manifest(draft, "expired")
                    result["expired"] += 1
    # Unknown backend identities require verified reconciliation or explicit
    # operator retry. They must not monopolize every oldest-first batch.
    due = StorageCleanupIntent.objects.filter(superseded_by__isnull=True).exclude(state__in=["complete", "blocked"]).filter(
        Q(retry_at__isnull=True) | Q(retry_at__lte=now)).filter(
        ~Q(state="running") | Q(lease_until__lte=now) | Q(lease_until__isnull=True))
    ids = list(due.order_by("created_at", "pk").values_list("pk", flat=True)[:limit])
    result["candidates"] += len(ids)
    if dry_run:
        result["blocked"] = StorageCleanupIntent.objects.filter(state="blocked", superseded_by__isnull=True).count()
        return result
    for pk in ids:
        claimed = _claim(pk, now)
        if claimed is None:
            continue
        try:
            outcome = _remove_intent(claimed, now)
            if outcome:
                result[outcome] += 1
        except Exception as exc:
            with transaction.atomic():
                intent = StorageCleanupIntent.objects.select_for_update().get(pk=claimed.pk)
                if intent.lease_token == claimed.lease_token:
                    _finish(intent, "retry", now=now, error=f"Cleanup failed ({type(exc).__name__}); the durable intent is retained for retry.")
            result["failed"] += 1
    return result


def reconcile_file_retention(*, limit=100, dry_run=True):
    from apps.documents.models import DocumentFile
    from apps.tracking.models import AttachmentGroup, ExternalAttachmentGroup

    from .file_lifecycle import retire_excess_versions
    limit, result = _bound(limit), _summary()
    remaining = limit
    for model in (AttachmentGroup, ExternalAttachmentGroup, DocumentFile):
        groups = model.objects.all()
        groups = groups.filter(document__is_active=True) if model is DocumentFile else groups.filter(discarded_at__isnull=True)
        groups = groups.annotate(available_count=Count("versions", filter=Q(versions__retired_at__isnull=True) & ~Q(versions__file=""))).filter(available_count__gt=5)
        candidates = list(groups.order_by("pk")[:remaining])
        result["candidates"] += len(candidates)
        remaining -= len(candidates)
        if not dry_run:
            for group in candidates:
                try:
                    result["retired"] += len(retire_excess_versions(group))
                except Exception:
                    result["failed"] += 1
        if not remaining:
            break
    return result


def cleanup_file_uploads():
    """Trusted scheduled entry point; user-facing commands default to dry-run."""
    return maintain_file_uploads(dry_run=False)


def bind_legacy_identity(kind, version_id, *, dry_run=True):
    """Explicit operator action: verify original bytes before binding a backend."""
    from apps.documents.models import DocumentFileVersion
    from apps.tracking.models import Attachment, ExternalAttachment

    from .file_lifecycle import _parent, _receipt
    from .file_lifecycle_models import lifecycle_write
    from .storage import storage_identity
    from .utils import checksum_of
    model = {"tracking": Attachment, "external": ExternalAttachment, "repository": DocumentFileVersion}.get(kind)
    if model is None:
        raise ValidationError("Choose tracking, external or repository history.")
    item = model.objects.get(pk=version_id)
    group = item.document_file if kind == "repository" else item.file_group
    parent, _, _, _ = _parent(group)
    with transaction.atomic():
        type(parent).objects.select_for_update().get(pk=parent.pk)
        item = model.objects.select_for_update().get(pk=version_id)
        if item.storage_identity or not item.file.name or not item.checksum:
            raise ValidationError("Only an unknown legacy identity with a stored checksum can be bound.")
        identity = storage_identity(item.file.storage)
        with locked_storage_key(identity, item.file.name):
            try:
                with item.file.open("rb") as stream:
                    digest = checksum_of(stream)
                size = item.file.storage.size(item.file.name)
            except OSError as exc:
                raise ValidationError("Legacy bytes are unavailable; no identity was inferred.") from exc
            if digest != item.checksum or size != item.size:
                raise ValidationError("Legacy bytes do not match the recorded checksum and size.")
            if dry_run:
                return {"verified": 1, "bound": 0}
            with lifecycle_write():
                model.objects.filter(pk=item.pk).update(storage_identity=identity)
            _receipt(group, actor=None, action="identity-bound", reason="Operator verified original checksum and size before binding storage identity", version=item)
            for unknown in StorageCleanupIntent.objects.filter(storage_identity="", storage_key=item.file.name):
                known = StorageCleanupIntent.objects.filter(storage_identity=identity, storage_key=item.file.name).first()
                if known:
                    unknown.last_error = f"Verified identity is handled by cleanup intent #{known.pk}. Historical unknown intent retained."
                    unknown.superseded_by = known
                    unknown.save(update_fields=["last_error", "superseded_by"])
                else:
                    unknown.storage_identity, unknown.state, unknown.last_error = identity, "pending", ""
                    unknown.save(update_fields=["storage_identity", "state", "last_error"])
            return {"verified": 1, "bound": 1}
