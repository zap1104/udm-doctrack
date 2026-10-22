"""Explicit, scoped activation. Training never activates a candidate."""

from functools import lru_cache

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Office
from apps.core.utils import log_action

from ..models import Collection, Document
from .dataset import _sysadmin, validate_snapshot
from .models import TagModel


@lru_cache(maxsize=1)
def cached_bundle(model_id, artifact_digest):
    from .artifacts import load_bundle

    model = TagModel.objects.get(pk=model_id, artifact_digest=artifact_digest)
    return load_bundle(model, scope={"office_id": model.office_id, "collection": model.collection})


def clear_model_cache():
    cached_bundle.cache_clear()


def _scope_lock(office_id, collection):
    if (
        collection not in Collection.values
        or not Office.objects.select_for_update().filter(pk=office_id, is_active=True).exists()
    ):
        raise ValidationError("Select an active internal office and a valid collection.")


@transaction.atomic
def activate_model(model_id, *, user, mode):
    actor = _sysadmin(user)
    if mode not in {"shadow", "suggestion", "automatic"}:
        raise ValidationError("Choose shadow, suggestion or automatic mode.")
    reference = TagModel.objects.get(pk=model_id)
    _scope_lock(reference.office_id, reference.collection)
    list(
        Document.objects.select_for_update()
        .filter(pk__in=reference.snapshot.sources.values("document_id"))
        .order_by("pk")
    )
    model = TagModel.objects.select_for_update().get(pk=model_id)
    if model.invalidated_at or model.mode == "invalid":
        raise ValidationError("This model is invalid and must be retrained.")
    validate_snapshot(model.snapshot)
    from .artifacts import load_bundle

    bundle = load_bundle(model, scope={"office_id": model.office_id, "collection": model.collection})
    if mode == "automatic" and not bundle.evaluation.get("production_eligible"):
        raise ValidationError(
            "This model has no independently qualified automatic topics. Use shadow or suggestion mode."
        )
    TagModel.objects.filter(office_id=model.office_id, collection=model.collection, is_active=True).exclude(
        pk=model.pk
    ).update(is_active=False, mode="retired")
    model.mode, model.is_active = mode, True
    model.save(update_fields=["mode", "is_active"])
    transaction.on_commit(clear_model_cache)
    log_action(
        "UPDATE",
        f"Activated topic model {model.pk} in {mode} mode",
        actor=actor,
        target=model,
        extra={"office_id": model.office_id, "collection": model.collection, "artifact_digest": model.artifact_digest},
    )
    return model


@transaction.atomic
def rollback_model(*, user, office_id, collection, target_model_id=None):
    actor = _sysadmin(user)
    _scope_lock(office_id, collection)
    if target_model_id is not None:
        if not TagModel.objects.filter(pk=target_model_id, office_id=office_id, collection=collection).exists():
            raise ValidationError("The rollback target belongs to another model scope.")
        return activate_model(target_model_id, user=actor, mode="suggestion")
    TagModel.objects.filter(office_id=office_id, collection=collection, is_active=True).update(
        is_active=False, mode="retired"
    )
    transaction.on_commit(clear_model_cache)
    log_action(
        "UPDATE", "Disabled scoped topic model", actor=actor, extra={"office_id": office_id, "collection": collection}
    )
    return None


def invalidate_models(snapshot_ids, *, reason):
    from .models import TrainingSnapshot

    now = timezone.now()
    TrainingSnapshot.objects.filter(pk__in=snapshot_ids, invalidated_at__isnull=True).update(
        invalidated_at=now, invalidation_reason=reason[:255]
    )
    TagModel.objects.filter(snapshot_id__in=snapshot_ids).update(
        is_active=False, mode="invalid", invalidated_at=now, invalidation_reason=reason[:255]
    )
    clear_model_cache()
