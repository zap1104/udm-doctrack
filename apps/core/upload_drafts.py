"""Private upload staging with serialized quotas, authority and retry identity."""
import hashlib
import json
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import Model, QuerySet, Sum
from django.utils import timezone

from .models import StagedUpload, StorageCleanupIntent, UploadDraft
from .permissions import active_writer
from .storage import cleanup_new_files, create_stored_file, storage_identity
from .upload_context import UploadContext, resolve_upload_context
from .utils import checksum_of, validate_upload


@dataclass
class StageResult:
    revision: int
    accepted_ids: list = field(default_factory=list)
    errors: list = field(default_factory=list)


@dataclass(frozen=True)
class UploadResult:
    workflow: str
    parent_kind: str
    parent_id: int
    redirect: str
    record_revision: int | None = None
    version_number: int | None = None


def _fresh_context(actor, context, *, lock_parent=False):
    query = {"office": context.creation_office_id}
    fresh = resolve_upload_context(actor, context.workflow, target_id=context.target_id,
        group_id=context.group_id, query=query, lock_parent=lock_parent)
    if fresh != context:
        raise PermissionDenied("Your office or upload permissions changed. Start a new upload form.")
    return fresh


def _expiry():
    return timezone.now() + timedelta(hours=settings.UPLOAD_DRAFT_HOURS)


def _touch(draft):
    draft.revision += 1
    draft.expires_at = _expiry()
    draft.updated_at = timezone.now()
    draft.save(update_fields=["revision", "expires_at", "updated_at", "form_state", "validation_errors"])


@transaction.atomic
def open_draft(*, user, context, form_nonce, allow_consumed=False):
    actor = active_writer(user)
    _fresh_context(actor, context, lock_parent=True)
    try:
        signed = signing.loads(form_nonce, salt="doctrack-upload-form", max_age=settings.UPLOAD_DRAFT_HOURS * 3600)
        nonce = UUID(signed["nonce"])
    except (signing.BadSignature, KeyError, ValueError, TypeError) as exc:
        raise PermissionDenied("This upload form expired. Reload it to start again.") from exc
    issued = signed.get("context")
    matches = issued == context.as_dict()
    if actor.is_system_admin and context.workflow in {"tracking-create", "external-create", "repository-upload", "external-repository-upload"} and isinstance(issued, dict):
        # No manifest exists yet: a system administrator can select a permitted
        # office on the initial form before choosing files. Existing bindings
        # below remain exact and cannot be moved by replaying this nonce.
        matches = {key: value for key, value in issued.items() if key != "creation_office_id"} == {
            key: value for key, value in context.as_dict().items() if key != "creation_office_id"}
    if signed.get("actor") != actor.pk or not matches:
        raise PermissionDenied("This upload form belongs to another user or action.")
    previous = UploadDraft.objects.select_for_update().filter(form_nonce=nonce).first()
    if previous is not None:
        if previous.owner_id != actor.pk or previous.binding != context.as_dict():
            raise PermissionDenied("This saved upload belongs to another form.")
        if previous.state == "consumed" and allow_consumed:
            return previous
        if previous.state != "open" or previous.expires_at <= timezone.now():
            raise ValidationError("This saved upload expired or was already submitted.")
        return previous
    if UploadDraft.objects.filter(owner=actor, state="open", expires_at__gt=timezone.now()).count() >= settings.UPLOAD_DRAFT_MAX_OPEN:
        raise ValidationError("Finish or discard an existing saved upload before starting another.")
    fields = {"tracking_record_id": context.target_id} if context.target_kind == "tracking" else (
        {"external_record_id": context.target_id} if context.target_kind == "external" else {})
    if context.group_id:
        fields["external_group_id" if context.target_kind == "external" else "tracking_group_id"] = context.group_id
    return UploadDraft.objects.create(form_nonce=nonce, owner=actor, owner_office_id_snapshot=actor.office_id,
        workflow=context.workflow, collection=context.collection, binding=context.as_dict(), expires_at=_expiry(), **fields)


@contextmanager
def locked_draft(draft_id, *, user, context=None, allow_consumed=False):
    """Lock actor -> target parent -> manifest -> group, consistently with finalization."""
    with transaction.atomic():
        actor = active_writer(user)
        try:
            preview = UploadDraft.objects.filter(pk=draft_id).first()
        except (ValueError, ValidationError, TypeError) as exc:
            raise PermissionDenied("Saved upload not found.") from exc
        if preview is None or preview.owner_id != actor.pk or preview.owner_office_id_snapshot != actor.office_id:
            raise PermissionDenied("This saved upload is outside your account or office.")
        binding = UploadContext(**preview.binding)
        if context is not None and binding != context:
            raise PermissionDenied("This saved upload belongs to another action.")
        if allow_consumed and preview.state == "consumed" and binding.target_id:
            # Replaying a committed route need not regain custody to read its
            # result. This path performs no mutation and still requires access.
            from apps.tracking.models import ExternalTrackingRecord, TrackingRecord

            model = ExternalTrackingRecord if binding.target_kind == "external" else TrackingRecord
            visible = model.objects.visible_to(actor).filter(pk=binding.target_id).values("pk")
            if not model.objects.select_for_update().filter(pk__in=visible).exists():
                raise PermissionDenied("The submitted record is no longer accessible.")
        else:
            _fresh_context(actor, binding, lock_parent=True)
        draft = UploadDraft.objects.select_for_update().get(pk=preview.pk)
        if draft.binding != preview.binding or draft.owner_id != actor.pk:
            raise PermissionDenied("This saved upload changed. Reload the form.")
        if draft.state == "consumed" and allow_consumed:
            yield actor, draft, binding
            return
        if draft.state != "open" or draft.expires_at <= timezone.now():
            raise ValidationError("This saved upload expired or is no longer available.")
        if binding.group_id:
            field_name = "external_group" if binding.target_kind == "external" else "tracking_group"
            group_model = UploadDraft._meta.get_field(field_name).remote_field.model
            group = group_model.objects.select_for_update().filter(pk=binding.group_id).first()
            if group is None or group.discarded_at:
                raise PermissionDenied("This file group is no longer available.")
        yield actor, draft, binding


def read_draft(draft_id, *, user, context):
    with locked_draft(draft_id, user=user, context=context) as (_, draft, _):
        return draft


def _check_revision(draft, expected_revision):
    if str(expected_revision) != str(draft.revision):
        raise ValidationError("This saved upload changed. Reload it before continuing.")


def queue_stage_cleanup(staged, *, reason):
    if not staged.file.name:
        return
    StorageCleanupIntent.objects.get_or_create(storage_identity=staged.storage_identity,
        storage_key=staged.file.name, defaults={"reason": reason, "resource_kind": "upload",
            "version_id": None, "collection": staged.draft.collection,
            "office_id_snapshot": staged.draft.owner_office_id_snapshot})


def stage_files(draft_id, files, *, user, expected_revision, client_upload_ids):
    files, supplied_ids = list(files), list(client_upload_ids)
    if len(files) != len(supplied_ids) or len(files) > settings.MAX_UPLOAD_FILES:
        raise ValidationError("Choose a bounded file list with one upload identity per file.")
    try:
        identities = [UUID(str(value)) for value in supplied_ids]
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Choose valid upload identities.") from exc
    if len(set(identities)) != len(identities):
        raise ValidationError("Each selected file needs its own upload identity.")
    with locked_draft(draft_id, user=user) as (actor, draft, binding):
        existing = {row.client_upload_id: row for row in draft.files.filter(client_upload_id__in=identities)}
        if any(identity not in existing for identity in identities):
            _check_revision(draft, expected_revision)
        result = StageResult(draft.revision)
        current_count = draft.files.filter(state="saved").count()
        total_size = StagedUpload.objects.filter(draft__owner=actor).exclude(file="").aggregate(total=Sum("size"))["total"] or 0
        limit = 1 if binding.workflow.endswith("-version") else settings.MAX_UPLOAD_FILES
        changed = False
        for uploaded, identity in zip(files, identities, strict=True):
            try:
                if not uploaded.size:
                    raise ValidationError("The selected file is empty. Choose a file with content.")
                validate_upload(uploaded)
                checksum = checksum_of(uploaded)
                prior = existing.get(identity)
                if prior:
                    if prior.state != "saved":
                        raise ValidationError("This upload was removed. Choose the file again to add it.")
                    if prior.checksum != checksum or prior.original_name != uploaded.name[:255]:
                        raise ValidationError("This upload identity was already used for a different file.")
                    result.accepted_ids.append(str(prior.pk))
                    continue
                if current_count >= limit:
                    raise ValidationError(f"This form accepts at most {limit} saved file(s).")
                if total_size + uploaded.size > settings.UPLOAD_DRAFT_MAX_BYTES:
                    raise ValidationError("Your saved uploads reached the storage limit. Remove or finish an earlier upload.")
                uploaded.seek(0)
                with cleanup_new_files(), transaction.atomic():
                    saved = create_stored_file(StagedUpload, draft=draft, client_upload_id=identity, file=uploaded,
                        original_name=uploaded.name[:255], content_type=(getattr(uploaded, "content_type", "") or "")[:120],
                        size=uploaded.size, checksum=checksum, storage_identity=storage_identity(StagedUpload._meta.get_field("file").storage))
                current_count += 1
                total_size += saved.size
                changed = True
                result.accepted_ids.append(str(saved.pk))
            except ValidationError as exc:
                result.errors.append({"client_upload_id": str(identity), "filename": uploaded.name[:255],
                                      "message": "; ".join(exc.messages)[:1000]})
            except OSError:
                result.errors.append({"client_upload_id": str(identity), "filename": uploaded.name[:255],
                                      "message": "This file could not be saved. Retry the upload."})
        if changed:
            _touch(draft)
        result.revision = draft.revision
        return result


def save_form_state(draft_id, values, *, user, expected_revision, errors=None):
    # View adapters supply only declared normal form fields. This public service
    # independently strips dangerous names and bounds all values as defense in depth.
    forbidden = {"csrfmiddlewaretoken", "password", "form_nonce", "upload_draft", "collection", "target_id", "action"}
    if len(values) > 100:
        raise ValidationError("This form has too many saved fields.")
    safe = {}
    for name, items in values.items():
        if name in forbidden or not isinstance(name, str) or len(name) > 100:
            continue
        items = items if isinstance(items, (list, tuple)) else [items]
        if len(items) > 100 or any(len(str(value)) > 10000 for value in items):
            raise ValidationError("This saved form exceeds its field limits.")
        safe[name] = [str(value) for value in items]
    with locked_draft(draft_id, user=user) as (_, draft, _):
        _check_revision(draft, expected_revision)
        draft.form_state = safe
        if errors is not None:
            draft.validation_errors = errors
        _touch(draft)
        return draft


def discard_staged_file(draft_id, file_id, *, user, expected_revision):
    with locked_draft(draft_id, user=user) as (_, draft, _):
        _check_revision(draft, expected_revision)
        try:
            saved = draft.files.select_for_update().get(pk=file_id)
        except (StagedUpload.DoesNotExist, ValueError, ValidationError) as exc:
            raise PermissionDenied("This file is outside the saved upload.") from exc
        if saved.state == "saved":
            saved.state = "removed"
            saved.save(update_fields=["state"])
            queue_stage_cleanup(saved, reason="Removed from private upload")
            _touch(draft)
        return draft


def _normalize(value):
    if isinstance(value, Model):
        return {"model": value._meta.label_lower, "id": value.pk}
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, QuerySet)):
        return [_normalize(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValidationError("This upload contains an unsupported form value.")


def _submission_fingerprint(draft, data, expected_version):
    ignored = {"file", "files", "attachments", "uploaded_file", "csrfmiddlewaretoken", "upload_draft", "form_nonce"}
    values = {key: value for key, value in data.items() if key not in ignored}
    files = [(str(row.pk), row.checksum) for row in draft.files.exclude(state="removed").order_by("pk")]
    serialized = json.dumps(_normalize({"values": values, "files": files, "expected_version": expected_version}),
                            sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _visible_result(result, actor):
    if result.parent_kind == "repository":
        from apps.documents.models import Document
        model = Document
    else:
        from apps.tracking.models import ExternalTrackingRecord, TrackingRecord
        model = ExternalTrackingRecord if result.parent_kind == "external" else TrackingRecord
    if not model.objects.visible_to(actor).filter(pk=result.parent_id).exists():
        raise PermissionDenied("The submitted record is no longer accessible.")
    return result


def finalize_upload(draft_id, *, user, context, cleaned_data, expected_revision, expected_version=None):
    """Commit one business action and its saved-upload receipt together."""
    with cleanup_new_files(), locked_draft(draft_id, user=user, context=context, allow_consumed=True) as (actor, draft, binding):
        fingerprint = _submission_fingerprint(draft, cleaned_data, expected_version)
        if draft.state == "consumed":
            if fingerprint != draft.consumed_fingerprint:
                raise ValidationError("This saved upload was already submitted with different values. Start a new form.")
            return _visible_result(UploadResult(**draft.result), actor)
        _check_revision(draft, expected_revision)
        staged = list(draft.files.select_for_update().filter(state="saved").order_by("created_at", "pk"))
        limit = 1 if binding.workflow.endswith("-version") else settings.MAX_UPLOAD_FILES
        if len(staged) > limit:
            raise ValidationError(f"This form accepts at most {limit} saved file(s).")
        with ExitStack() as handles:
            uploads = []
            for saved in staged:
                if saved.storage_identity != storage_identity(saved.file.storage):
                    raise ValidationError("Saved-file storage changed. Contact an administrator before continuing.")
                try:
                    stream = handles.enter_context(saved.file.open("rb"))
                    if checksum_of(stream) != saved.checksum:
                        raise ValidationError("A saved file changed or failed its checksum. Upload it again.")
                    stream.seek(0)
                    uploaded = UploadedFile(stream, name=saved.original_name, content_type=saved.content_type, size=saved.size)
                    validate_upload(uploaded)
                    uploads.append(uploaded)
                except OSError as exc:
                    raise ValidationError("A saved file is unavailable. Retry its upload before submitting.") from exc
            if binding.workflow in {"repository-upload", "external-repository-upload"}:
                from apps.documents.upload_drafts import finalize_repository_upload
                result = finalize_repository_upload(binding, cleaned_data, uploads, user=actor)
            else:
                from apps.tracking.upload_drafts import finalize_tracking_upload
                result = finalize_tracking_upload(binding, cleaned_data, uploads, user=actor, expected_version=expected_version)
        for saved in staged:
            saved.state = "consumed"
            saved.save(update_fields=["state"])
            queue_stage_cleanup(saved, reason="Saved upload consumed into permanent files")
        draft.state = "consumed"
        draft.result = asdict(result)
        draft.consumed_fingerprint = fingerprint
        draft.revision += 1
        draft.updated_at = timezone.now()
        draft.save(update_fields=["state", "result", "consumed_fingerprint", "revision", "updated_at"])
        return result
