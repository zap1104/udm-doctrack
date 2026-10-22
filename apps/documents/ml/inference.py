"""Fresh-authorized predictions on the current document's evidence only."""

import re
from dataclasses import asdict, dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import User
from apps.core.models import Tag

from ..models import Document, TagPrediction
from ..tagging import input_revision, vocabulary_digest
from .dataset import content_zones, validate_snapshot
from .lifecycle import cached_bundle
from .models import TagModel


@dataclass(frozen=True)
class ContentInput:
    title: str
    header: str
    body: str
    input_revision: str
    coverage_complete: bool
    evidence: list
    automatic_safe: bool = False


@dataclass(frozen=True)
class TagDecision:
    tag_id: int
    score: float
    action: str
    model_id: int
    artifact_digest: str
    input_revision: str
    vocabulary: str
    threshold: float
    calibrated: bool
    evidence: dict


class PredictionResult(list):
    def __init__(self, decisions=(), *, mode="fallback", reason="No valid scoped model is active."):
        super().__init__(decisions)
        self.mode = mode
        self.reason = reason


def content_input(document):
    current = Document.objects.get(pk=document.pk)
    zones = content_zones(current)
    return ContentInput(**zones, input_revision=input_revision(current))


def _fresh_reader(document, user):
    actor = User.objects.filter(pk=getattr(user, "pk", None), is_active=True).first()
    if (
        actor is None
        or not document.can_user_view(actor)
        or (not actor.is_system_admin and actor.office_id != document.office_id)
    ):
        return None
    return actor


def _evidence(bundle, classifier, content, matrix):
    # Feature evidence comes only from this document, never example text or
    # a downloadable model vocabulary. Offsets refer to normalized zones.
    evidence = []
    cursor = 0
    scale = classifier["calibration_coef"] if classifier["calibrated"] else 1.0
    contributions = [
        (float(value * classifier["coef"][index] * scale), int(index))
        for index, value in zip(matrix.indices, matrix.data, strict=True)
        if value * classifier["coef"][index] * scale > 0
    ]
    selected = {index: weight for weight, index in sorted(contributions, reverse=True)[:25]}
    for branch_name, branch in bundle.branches.items():
        zone, kind = branch_name.rsplit("_", 1)
        width = len(branch["vocabulary"])
        if kind == "word":
            text = getattr(content, zone)
            weighted = sorted(
                (
                    (selected[cursor + index], term)
                    for term, index in branch["vocabulary"].items()
                    if cursor + index in selected
                ),
                reverse=True,
            )
            for weight, term in weighted:
                match = re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.IGNORECASE)
                if weight > 0 and match:
                    source = (
                        next(
                            (
                                row
                                for row in content.evidence
                                if row["start"] <= match.start() and row["end"] >= match.end()
                            ),
                            None,
                        )
                        if zone == "body"
                        else None
                    )
                    evidence.append(
                        {
                            "zone": zone,
                            "term": match.group(),
                            "start": match.start(),
                            "end": match.end(),
                            "source": {**source, "unit_offset": match.start() - source["start"]} if source else None,
                        }
                    )
                    if len(evidence) >= 5:
                        break
        cursor += width
    return {
        "features": evidence[:5],
        "sources": [row["source"] for row in evidence[:5] if row["source"]],
        "coverage_complete": content.coverage_complete,
    }


def predict_tags(document, *, user=None):
    if not settings.ENABLE_TAG_ML:
        return PredictionResult(reason="Local topic models are disabled.")
    current = Document.objects.get(pk=document.pk)
    actor = _fresh_reader(current, user)
    if actor is None:
        return PredictionResult(reason="The current account cannot use topic inference for this record.")
    model = (
        TagModel.objects.filter(
            office_id=current.office_id,
            collection=current.collection,
            is_active=True,
            invalidated_at__isnull=True,
            office__is_active=True,
        )
        .select_related("snapshot")
        .first()
    )
    if model is None:
        return PredictionResult()
    try:
        validate_snapshot(model.snapshot)
        if model.vocabulary != vocabulary_digest():
            raise ValidationError("Vocabulary changed")
        content = content_input(current)
        bundle = cached_bundle(model.pk, model.artifact_digest)
        probabilities = bundle.predict([asdict(content)])[0]
        # Local sparse evidence only. Never cache another document's feature
        # vector on the shared model bundle.
        matrix = bundle._features.transform([asdict(content)]).tocsr()
        active_tags = set(
            Tag.objects.filter(pk__in=probabilities, is_active=True, canonical__isnull=True).values_list(
                "pk", flat=True
            )
        )
        decisions = []
        for tag_id, probability in probabilities.items():
            if tag_id not in active_tags:
                continue
            classifier = bundle.classifiers[str(tag_id)]
            decisions.append(
                TagDecision(
                    tag_id=tag_id,
                    score=probability,
                    action="suggest" if matrix.nnz and probability >= classifier["threshold"] else "abstain",
                    model_id=model.pk,
                    artifact_digest=model.artifact_digest,
                    input_revision=content.input_revision,
                    vocabulary=bundle.vocabulary,
                    threshold=classifier["threshold"],
                    calibrated=classifier["calibrated"],
                    evidence=_evidence(bundle, classifier, content, matrix),
                )
            )
        # Shadow and normal predictions share the same stale-result checks.
        with transaction.atomic():
            fresh_actor = User.objects.select_for_update(no_key=True).filter(pk=actor.pk, is_active=True).first()
            fresh = Document.objects.select_for_update().get(pk=current.pk)
            live = TagModel.objects.filter(
                pk=model.pk,
                artifact_digest=model.artifact_digest,
                is_active=True,
                invalidated_at__isnull=True,
                mode=model.mode,
            ).first()
            if (
                not fresh_actor
                or not fresh.can_user_view(fresh_actor)
                or (not fresh_actor.is_system_admin and fresh_actor.office_id != fresh.office_id)
                or not live
                or input_revision(fresh) != content.input_revision
                or vocabulary_digest() != bundle.vocabulary
            ):
                return PredictionResult(reason="The input or model changed during inference.")
            validate_snapshot(live.snapshot)
            for decision in decisions:
                TagPrediction.objects.get_or_create(
                    document=fresh,
                    tag_id=decision.tag_id,
                    input_revision=decision.input_revision,
                    vocabulary=decision.vocabulary,
                    engine="ml",
                    model_reference=str(model.pk),
                    evidence__mode=model.mode,
                    defaults={
                        "score": decision.score,
                        "decision": decision.action,
                        "evidence": {**decision.evidence, "model_digest": model.artifact_digest, "mode": model.mode},
                    },
                )
        return PredictionResult(decisions, mode=model.mode, reason="")
    except (ImportError, ValidationError, ValueError, KeyError, TypeError, AttributeError, OSError):
        # Do not expose private vocabulary, coefficients or source filenames in
        # a fallback message. Manual metadata and rules remain available.
        return PredictionResult(
            reason="The local topic model is unavailable or no longer eligible. Rule-based suggestions were used."
        )
