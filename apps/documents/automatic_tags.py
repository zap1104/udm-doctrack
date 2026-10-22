"""Qualified topic contributions. No ownership, workflow or metadata writes."""

import math

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Office, User
from apps.core.models import Tag
from apps.core.permissions import active_writer
from apps.core.utils import log_action

from .ml.dataset import _sysadmin, validate_snapshot
from .ml.inference import TagDecision, content_input
from .ml.lifecycle import cached_bundle
from .ml.metrics import wilson_lower
from .ml.models import TagModel
from .models import Document, ExtractionUnit, OcrStatus, TagContribution, TagPrediction
from .tagging import _bootstrap, _materialize, input_revision, vocabulary_digest


def quality_reasons(bundle, tag_id, *, synthetic):
    """Recompute support/interval gates from frozen independent counts."""
    reasons = []
    evaluation = bundle.evaluation
    if synthetic is not False or evaluation.get("synthetic") is not False:
        reasons.append("synthetic_data")
    if evaluation.get("holdout_frozen") is not True or evaluation.get("source_coverage_complete") is not True:
        reasons.append("incomplete_training_evidence")
    classifier = bundle.classifiers.get(str(tag_id), {})
    report = evaluation.get("tags", {}).get(str(tag_id), {})
    if evaluation.get("production_eligible") is not True or report.get("automatic_qualified") is not True:
        reasons.append("unqualified_evaluation")
    if classifier.get("calibrated") is not True:
        reasons.append("uncalibrated")
    calibration = evaluation.get("partitions", {}).get("calibration", {}).get(str(tag_id), {})
    if calibration.get("positives", 0) < 20 or calibration.get("known", 0) - calibration.get("positives", 0) < 20:
        reasons.append("insufficient_calibration")
    predictions, successes, known = (
        report.get("predictions", 0),
        report.get("true_positives", 0),
        report.get("known", 0),
    )
    if (
        any(type(v) is not int for v in (predictions, successes, known))
        or not 0 <= successes <= predictions <= known
        or predictions < 100
    ):
        reasons.append("insufficient_independent_predictions")
    elif (
        successes / predictions < 0.95
        or (wilson_lower(successes, predictions) or 0) < 0.95
        or predictions / known < 0.01
    ):
        reasons.append("precision_or_coverage_gate")
    development = evaluation.get("partitions", {}).get("development", {}).get(str(tag_id), {})
    dev_predictions, dev_tp = development.get("predictions", 0), development.get("true_positives", 0)
    if (
        not dev_predictions
        or dev_tp / dev_predictions < 0.95
        or development.get("positives", 0) < 2
        or development.get("known", 0) - development.get("positives", 0) < 2
    ):
        reasons.append("unsupported_development_threshold")
    return reasons


def _canonical(tag_id, redirects):
    seen = set()
    while redirects.get(tag_id):
        if tag_id in seen:
            raise ValidationError("The canonical vocabulary contains a redirect cycle.")
        seen.add(tag_id)
        tag_id = redirects[tag_id]
    return tag_id


def _human_decisions(document):
    redirects = dict(Tag.objects.exclude(canonical_id=None).values_list("pk", "canonical_id"))
    effective = {}
    for review in document.tag_reviews.order_by("pk").values("tag_id", "decision"):
        effective[_canonical(review["tag_id"], redirects)] = review["decision"]
    return effective


def _result(decisions, reason):
    return {"applied": [], "reasons": {str(d.tag_id): [reason] for d in decisions}}


def apply_tag_decisions(document, decisions, *, actor, expected_input_revision, _application_lease=None):
    if len(decisions) > 32 or any(type(d) is not TagDecision for d in decisions):
        raise ValidationError("Only bounded topic decisions are accepted; metadata fields are not permitted.")
    if len({d.tag_id for d in decisions}) != len(decisions) or len({d.model_id for d in decisions}) > 1:
        raise ValidationError("Use one current model and one decision per canonical tag.")
    fresh_actor = User.objects.filter(pk=getattr(actor, "pk", None), is_active=True).first()
    reference_doc = Document.objects.get(pk=document.pk)
    if fresh_actor is None or not reference_doc.can_user_edit(fresh_actor):
        raise PermissionDenied("Your current account cannot apply tags to this document.")
    if not decisions:
        return {"applied": [], "reasons": {}}
    reference = (
        TagModel.objects.select_related("snapshot")
        .filter(pk=decisions[0].model_id, office_id=reference_doc.office_id, collection=reference_doc.collection)
        .first()
    )
    if reference is None:
        return _result(decisions, "wrong_model_scope")
    # Bundle parsing is bounded and occurs before record locks. A second check
    # below fences the model identity and source eligibility at publication.
    try:
        bundle = cached_bundle(reference.pk, reference.artifact_digest)
    except (ImportError, ValidationError, ValueError, TypeError, KeyError, OSError):
        return _result(decisions, "model_unavailable")
    with transaction.atomic():
        writer = active_writer(actor)
        if not Office.objects.select_for_update().filter(pk=reference.office_id, is_active=True).exists():
            return _result(decisions, "inactive_office")
        ids = set(reference.snapshot.sources.values_list("document_id", flat=True)) | {document.pk}
        locked = {doc.pk: doc for doc in Document.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
        doc = locked.get(document.pk)
        if doc is None or not doc.can_user_edit(writer):
            raise PermissionDenied("Your current account cannot apply tags to this document.")
        if input_revision(doc) != expected_input_revision:
            return _result(decisions, "stale_input")
        model = TagModel.objects.select_related("snapshot").get(pk=reference.pk)
        if (
            model.artifact_digest != reference.artifact_digest
            or model.snapshot_id != reference.snapshot_id
            or model.office_id != doc.office_id
            or model.collection != doc.collection
        ):
            return _result(decisions, "model_changed")
        common = []
        if not settings.ENABLE_TAG_ML or not settings.ENABLE_AUTOMATIC_TAGS:
            common.append("automatic_disabled")
        if not model.is_active or model.invalidated_at or model.mode != "automatic":
            common.append("model_not_automatic")
        try:
            validate_snapshot(model.snapshot)
        except ValidationError:
            common.append("training_source_changed")
        current_vocabulary = vocabulary_digest()
        if current_vocabulary != bundle.vocabulary or current_vocabulary != model.vocabulary:
            common.append("vocabulary_changed")
        try:
            content = content_input(doc)
            complete = content.automatic_safe
        except ValidationError:
            complete = False
        if not complete or doc.ocr_status != OcrStatus.DONE or doc.extraction_failed_generation is not None:
            common.append("incomplete_source_evidence")
        if (
            doc.extraction_runs.filter(is_current=True).exclude(warnings=[]).exists()
            or ExtractionUnit.objects.filter(run__document=doc, run__is_current=True, truncated=True).exists()
            or ExtractionUnit.objects.filter(run__document=doc, run__is_current=True).exclude(warnings=[]).exists()
        ):
            common.append("source_warnings")
        tags = {
            tag.pk: tag
            for tag in Tag.objects.select_for_update().filter(pk__in=[d.tag_id for d in decisions]).order_by("pk")
        }
        human = _human_decisions(doc)
        applied, reasons = [], {}
        bootstrapped = False
        for decision in decisions:
            blocked = list(common)
            blocked += quality_reasons(bundle, decision.tag_id, synthetic=model.snapshot.synthetic)
            tag = tags.get(decision.tag_id)
            if (
                tag is None
                or not tag.is_active
                or tag.canonical_id
                or not tag.globally_approved
                or not tag.automatic_enabled
                or tag.category == Tag.Category.OFFICE
            ):
                blocked.append("tag_not_enabled")
            if human.get(decision.tag_id) in {"reject", "unsure"}:
                blocked.append("human_rejected" if human[decision.tag_id] == "reject" else "human_uncertain")
            classifier = bundle.classifiers.get(str(decision.tag_id), {})
            prediction = (
                TagPrediction.objects.filter(
                    document=doc,
                    tag_id=decision.tag_id,
                    model_reference=str(model.pk),
                    engine="ml",
                    input_revision=expected_input_revision,
                    vocabulary=current_vocabulary,
                    evidence__model_digest=model.artifact_digest,
                )
                .order_by("-pk")
                .first()
            )
            if (
                decision.input_revision != expected_input_revision
                or decision.vocabulary != current_vocabulary
                or decision.artifact_digest != model.artifact_digest
                or prediction is None
                or not math.isfinite(decision.score)
                or not 0 <= decision.score <= 1
                or not math.isclose(decision.score, prediction.score, abs_tol=1e-12, rel_tol=0)
                or decision.threshold != classifier.get("threshold")
                or decision.calibrated is not classifier.get("calibrated")
            ):
                blocked.append("unverified_prediction")
            if decision.action != "suggest" or decision.score < classifier.get("threshold", 1):
                blocked.append("below_threshold")
            reasons[str(decision.tag_id)] = list(dict.fromkeys(blocked))
            if blocked:
                continue
            if not bootstrapped:
                _bootstrap(doc)
                bootstrapped = True
            TagContribution.objects.get_or_create(
                document=doc,
                tag=tag,
                kind="automatic",
                source=f"model:{model.pk}",
                defaults={"actor": writer, "recorded_at": timezone.now()},
            )
            applied.append(tag.pk)
        if applied and _application_lease is not None:
            from .models import TagApplicationIntent

            intent_id, token = _application_lease
            if (
                not TagApplicationIntent.objects.select_for_update()
                .filter(pk=intent_id, state="running", lease_token=token, lease_expires_at__gt=timezone.now())
                .exists()
            ):
                raise RuntimeError("The automatic application lease changed; publication was rolled back.")
        if applied:
            _materialize(doc)
            log_action(
                "UPDATE",
                "Applied qualified automatic topic tags",
                actor=writer,
                target=doc,
                extra={
                    "tag_ids": applied,
                    "model_id": model.pk,
                    "input_revision": expected_input_revision,
                    "artifact_digest": model.artifact_digest,
                },
            )
        return {"applied": applied, "reasons": reasons}


@transaction.atomic
def rollback_auto_tags(*, user, model_id, document_ids=None, dry_run=True):
    actor = _sysadmin(user)
    model = TagModel.objects.get(pk=model_id)
    source = f"model:{model.pk}"
    documents = Document.objects.filter(tag_contributions__kind="automatic", tag_contributions__source=source)
    if document_ids is not None:
        documents = documents.filter(pk__in=document_ids)
    ids = list(documents.order_by("pk").values_list("pk", flat=True).distinct())
    if not dry_run:
        for doc in Document.objects.select_for_update().filter(pk__in=ids).order_by("pk"):
            _bootstrap(doc)
            doc.tag_contributions.filter(kind="automatic", source=source).delete()
            _materialize(doc)
        log_action(
            "UPDATE", "Rolled back automatic tag contributions", actor=actor, target=model, extra={"document_ids": ids}
        )
    return {"document_ids": ids, "dry_run": dry_run}
