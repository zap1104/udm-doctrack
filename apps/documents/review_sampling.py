"""Bounded, reproducible review selection. GET never generates predictions or labels."""

import math
import re
from urllib.parse import urlencode

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import CharField, Value
from django.db.models.functions import MD5, Cast, Concat
from django.urls import reverse

from apps.accounts.models import User

from .ml.models import TagModel
from .models import Collection, Document, TagPrediction
from .tagging import input_revision, vocabulary_digest

SALT = "documents.topic-review-sampling.v1"
MAX_CANDIDATES = 200


def validate_sample(token, document, actor):
    if not token:
        return {"source": "manual"}
    try:
        receipt = signing.loads(token, salt=SALT, max_age=7 * 86400)
    except signing.BadSignature as exc:
        raise ValidationError("This review sample expired or is invalid. Open the review queue again.") from exc
    if (
        not isinstance(receipt, dict)
        or receipt.get("actor_id") != actor.pk
        or receipt.get("document_id") != document.pk
        or receipt.get("collection") != document.collection
        or receipt.get("input_revision") != input_revision(document)
        or receipt.get("vocabulary") != vocabulary_digest()
        or receipt.get("source") not in {"random_audit", "uncertainty"}
    ):
        raise ValidationError("This review sample no longer matches the document or reviewer. Open the queue again.")
    return receipt


def review_samples(*, user, collection, seed, office_id=None):
    actor = User.objects.filter(pk=user.pk, is_active=True).select_related("office").first()
    if not actor or not actor.can_start_work or not actor.is_records_staff:
        raise PermissionDenied("An authorized document editor is required for the review queue.")
    if (
        collection not in Collection.values
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", seed)
        or (office_id is not None and (not actor.is_system_admin and office_id != actor.office_id))
    ):
        raise ValidationError("Choose one valid collection, office and review seed.")
    from apps.accounts.models import Office

    if office_id is not None and not Office.objects.filter(pk=office_id, is_active=True).exists():
        raise ValidationError("Choose an active internal office.")
    if not actor.is_system_admin and (not actor.office or not actor.office.is_active):
        raise PermissionDenied("An active office is required for the review queue.")
    docs = Document.objects.visible_to(actor).in_collection(collection).filter(is_active=True)
    if not actor.is_system_admin:
        docs = docs.filter(office_id=actor.office_id)
        if collection == Collection.EXTERNAL and not actor.is_office_admin:
            raise PermissionDenied("External topic reviews require an office administrator.")
    elif office_id is not None:
        docs = docs.filter(office_id=office_id)
    # Hash ordering applies to the full authorized population before LIMIT.
    ordered = docs.annotate(sample_order=MD5(Concat(Value(seed + ":"), Cast("pk", CharField()))))
    random_rows = list(ordered.select_related("office", "uploaded_by").order_by("sample_order", "pk")[:20])
    samples, selected = [], set()
    vocabulary = vocabulary_digest()

    def add(doc, method, **extra):
        if doc.pk in selected or not doc.can_user_edit(actor):
            return
        receipt = {
            "source": method,
            "seed": seed,
            "actor_id": actor.pk,
            "document_id": doc.pk,
            "collection": collection,
            "input_revision": input_revision(doc),
            "vocabulary": vocabulary,
            **extra,
        }
        token = signing.dumps(receipt, salt=SALT, compress=True)
        samples.append(
            {
                "document": doc,
                "method": method,
                "token": token,
                "url": reverse("documents:source_units", args=[doc.pk]) + "?" + urlencode({"review_sample": token}),
                **extra,
            }
        )
        selected.add(doc.pk)

    for doc in random_rows[:10]:
        add(doc, "random_audit")
    models = {
        str(row.pk): row
        for row in TagModel.objects.filter(
            is_active=True,
            invalidated_at__isnull=True,
            collection=collection,
            mode__in=["suggestion", "automatic"],
            office_id__in=docs.values("office_id"),
        )
    }
    predictions = (
        TagPrediction.objects.filter(
            document_id__in=docs.values("pk"),
            engine="ml",
            vocabulary=vocabulary,
            model_reference__in=models,
            evidence__mode__in=["suggestion", "automatic"],
        )
        .annotate(sample_order=MD5(Concat(Value(seed + ":"), Cast("pk", CharField()))))
        .select_related("document__office", "document__uploaded_by")
        .order_by("sample_order", "pk")[:MAX_CANDIDATES]
    )
    candidates = []
    for prediction in predictions:
        evidence = prediction.evidence
        threshold = evidence.get("threshold") if isinstance(evidence, dict) else None
        score = prediction.score
        model = models[prediction.model_reference]
        if (
            model.office_id == prediction.document.office_id
            and evidence.get("mode") == model.mode
            and isinstance(threshold, (int, float))
            and not isinstance(threshold, bool)
            and isinstance(score, (int, float))
            and math.isfinite(score)
            and math.isfinite(threshold)
            and 0 <= threshold <= 1
            and 0 <= score <= 1
        ):
            candidates.append((abs(score - threshold), prediction.pk, prediction))
    for _, _, prediction in sorted(candidates):
        if len(samples) >= 20:
            break
        if prediction.document_id in selected or prediction.input_revision != input_revision(prediction.document):
            continue
        add(
            prediction.document,
            "uncertainty",
            model_id=int(prediction.model_reference),
            score=prediction.score,
            threshold=prediction.evidence["threshold"],
            candidate_limit=MAX_CANDIDATES,
        )
    for doc in random_rows:
        if len(samples) >= 20:
            break
        add(doc, "random_audit")
    return samples
