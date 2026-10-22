"""The mutation boundary for human and machine topic assignments."""

import hashlib
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.core.models import Tag
from apps.core.permissions import active_writer
from apps.core.utils import log_action

from .models import Document, TagAlias, TagContribution, TagReview, VocabularyRevision


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def vocabulary_snapshot():
    return list(
        Tag.objects.filter(globally_approved=True)
        .order_by("pk")
        .values("id", "name", "description", "category", "is_active", "canonical_id", "globally_approved")
    ) + [
        {"alias": a.phrase, "tag": a.tag_id}
        for a in TagAlias.objects.filter(is_active=True, tag__globally_approved=True).order_by("pk")
    ]


def vocabulary_digest():
    return digest(vocabulary_snapshot())


def reviewable_tags(document, user):
    visible = Document.objects.visible_to(user).in_collection(document.collection).filter(is_active=True)
    tags = Tag.objects.filter(is_active=True, canonical__isnull=True)
    if user.is_system_admin:
        return tags.order_by("name")
    allowed = Q(globally_approved=True) | Q(documents__in=visible)
    if user.office_id == document.office_id:
        allowed |= Q(tagprediction__document=document)
    return tags.filter(allowed).distinct().order_by("name")


def input_revision(document):
    doc = Document.objects.get(pk=document.pk)
    hasher = hashlib.sha256(b"document-input-2")

    def append(value):
        # Length framing prevents ambiguity while keeping one source unit in
        # memory. All text participates, including the last unit.
        encoded = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")).encode()
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)

    append(
        [
            doc.title,
            doc.description,
            doc.reference_number,
            doc.office_id,
            doc.collection,
            doc.extraction_generation,
            doc.ocr_text,
            doc.is_active,
        ]
    )
    for row in (
        doc.extraction_runs.filter(is_current=True)
        .order_by("pk")
        .values_list("pk", "generation", "content_hash", "settings_digest")
        .iterator(chunk_size=8)
    ):
        append(["run", row])
    for row in (
        doc.extraction_runs.filter(is_current=True)
        .order_by("pk", "units__ordinal", "units__pk")
        .values_list("units__locator_key", "units__normalized_text")
        .iterator(chunk_size=2)
    ):
        append(["unit", row])
    for row in doc.files.order_by("pk").values_list("pk", "checksum", "file").iterator(chunk_size=20):
        append(["file", row])
    return hasher.hexdigest()


def _authorize(document, user):
    actor = active_writer(user)
    doc = Document.objects.select_for_update().get(pk=document.pk)
    if not doc.is_active or not doc.can_user_edit(actor):
        raise PermissionDenied("You cannot edit this document's tags.")
    return doc, actor


def _bootstrap(document):
    for tag_id in document.tags.values_list("pk", flat=True):
        if not document.tag_contributions.filter(tag_id=tag_id).exists():
            TagContribution.objects.create(document=document, tag_id=tag_id, kind="human", source="legacy")


def _materialize(document):
    previous = set(document.tags.values_list("pk", flat=True))
    current = set(document.tag_contributions.values_list("tag_id", flat=True))
    list(Tag.objects.select_for_update().filter(pk__in=previous | current).order_by("pk"))
    document.tags.set(current)
    Tag.objects.filter(pk__in=current - previous).update(usage_count=F("usage_count") + 1)
    Tag.objects.filter(pk__in=previous - current, usage_count__gt=0).update(usage_count=F("usage_count") - 1)
    document.rebuild_index()


def _decision(doc, tag, decision, actor, revision, vocabulary, complete=False, sampling=None):
    VocabularyRevision.objects.get_or_create(digest=vocabulary, defaults={"payload": vocabulary_snapshot()})
    TagReview.objects.create(
        document=doc,
        tag=tag,
        decision=decision,
        actor=actor,
        input_revision=revision,
        vocabulary=vocabulary,
        office_snapshot=str(actor.office or ""),
        tag_name_snapshot=tag.name,
        actor_name_snapshot=actor.get_full_name() or actor.username,
        complete_review=complete,
        sampling=sampling if sampling is not None else {"source": "manual"},
    )
    if decision in {"confirm", "add"}:
        TagContribution.objects.get_or_create(
            document=doc,
            tag=tag,
            kind="human",
            source="review",
            defaults={"actor": actor, "recorded_at": timezone.now()},
        )
    elif decision == "reject":
        doc.tag_contributions.filter(tag=tag).delete()


def _validate_revision(doc, revision, vocabulary):
    if revision != input_revision(doc) or vocabulary != vocabulary_digest():
        raise ValidationError("The content or vocabulary changed. Reload before reviewing.")


@transaction.atomic
def review_tag(document, *, tag_id, decision, user, expected_revision, vocabulary, sampling_token=""):
    doc, actor = _authorize(document, user)
    _validate_revision(doc, expected_revision, vocabulary)
    from .review_sampling import validate_sample

    sampling = validate_sample(sampling_token, doc, actor)
    if decision not in {"confirm", "reject", "add", "unsure"}:
        raise ValidationError("Choose a valid review decision.")
    tag = (
        Tag.objects.select_for_update()
        .filter(pk=tag_id, pk__in=reviewable_tags(doc, actor).order_by().values("pk"))
        .first()
    )
    if tag is None:
        raise ValidationError("This tag is no longer available.")
    _bootstrap(doc)
    _decision(doc, tag, decision, actor, expected_revision, vocabulary, sampling=sampling)
    _materialize(doc)


@transaction.atomic
def complete_review(document, *, covered_ids, positive_ids, user, expected_revision, vocabulary, sampling_token=""):
    doc, actor = _authorize(document, user)
    _validate_revision(doc, expected_revision, vocabulary)
    from .review_sampling import validate_sample

    sampling = validate_sample(sampling_token, doc, actor)
    covered, positives = set(covered_ids), set(positive_ids)
    tags = list(
        Tag.objects.select_for_update()
        .filter(pk__in=covered)
        .filter(pk__in=reviewable_tags(doc, actor).order_by().values("pk"))
        .order_by("pk")
    )
    if not covered or positives - covered or len(tags) != len(covered):
        raise ValidationError("Select the exact active tags covered by this review.")
    _bootstrap(doc)
    for tag in tags:
        _decision(
            doc,
            tag,
            "confirm" if tag.pk in positives else "reject",
            actor,
            expected_revision,
            vocabulary,
            True,
            sampling=sampling,
        )
    _materialize(doc)


def known_labels(document, tag_ids, *, revision=None):
    # A newer unsure decision restores unknown; old-content reviews never
    # manufacture labels for changed evidence. Assignments alone are not labels.
    result = dict.fromkeys(tag_ids)
    rows = document.tag_reviews.filter(input_revision=revision or input_revision(document)).order_by("pk")
    current_digest, current_payload = vocabulary_digest(), vocabulary_snapshot()
    recorded = dict(
        VocabularyRevision.objects.filter(digest__in=rows.values("vocabulary")).values_list("digest", "payload")
    )

    def definition(payload, tag_id):
        return [row for row in payload if row.get("id") == tag_id or row.get("tag") == tag_id]

    for row in rows:
        if row.tag_id in result:
            if row.vocabulary != current_digest:
                previous = recorded.get(row.vocabulary, [])
                if not definition(previous, row.tag_id) or definition(previous, row.tag_id) != definition(
                    current_payload, row.tag_id
                ):
                    continue
            result[row.tag_id] = 1 if row.decision in {"confirm", "add"} else 0 if row.decision == "reject" else None
    return result


@transaction.atomic
def replace_human_tags(document, tags, *, user):
    doc, actor = _authorize(document, user)
    _replace_locked_tags(doc, tags, actor)


def _replace_locked_tags(doc, tags, actor):
    _bootstrap(doc)
    desired = {t.pk for t in tags}
    previous = set(doc.tags.values_list("pk", flat=True))
    active = {t.pk: t for t in Tag.objects.select_for_update().filter(pk__in=desired | previous).order_by("pk")}
    if any(not active[t].is_active or active[t].canonical_id for t in desired - previous):
        raise ValidationError("Choose active canonical tags.")
    revision, vocabulary = input_revision(doc), vocabulary_digest()
    for tag_id in sorted(previous | desired):
        _decision(doc, active[tag_id], "confirm" if tag_id in desired else "reject", actor, revision, vocabulary)
    _materialize(doc)


@transaction.atomic
def initialize_filing_tags(document, tag_names, *, user, record):
    """Only the initial authorized filing may set tags across office boundaries.

    A filing administrator does not gain later repository edit permission.
    Rule suggestions are not accepted labels unless explicitly submitted.
    """
    from apps.tracking.models import Status, TrackingRecord

    actor = active_writer(user)
    current_record = TrackingRecord.objects.select_for_update().get(pk=record.pk)
    doc = Document.objects.select_for_update().get(pk=document.pk)
    if (
        not doc.is_active
        or doc.tracking_record_id != current_record.pk
        or doc.uploaded_by_id != actor.pk
        or current_record.status != Status.COMPLETED_PENDING_UPLOAD
        or not current_record.can_user_approve_upload(actor)
        or doc.tags.exists()
        or doc.tag_reviews.exists()
        or doc.tag_contributions.exists()
    ):
        raise PermissionDenied("Only initial filing can initialize these document tags.")
    tags = []
    for name in tag_names:
        tag, _ = Tag.get_or_create_by_name(name)
        if tag and tag not in tags:
            tags.append(tag)
    _replace_locked_tags(doc, tags, actor)


@transaction.atomic
def merge_tags(source_id, target_id, *, user, reason):
    actor = active_writer(user)
    if not actor.is_system_admin:
        raise PermissionDenied("Only system administrators can merge the shared vocabulary.")
    if source_id == target_id or not str(reason).strip():
        raise ValidationError("Choose distinct tags and provide a merge reason.")
    affected = Document.objects.filter(tags__pk__in=[source_id, target_id]).values_list("pk", flat=True).distinct()
    documents = list(Document.objects.select_for_update().filter(pk__in=affected).order_by("pk"))
    tags = {t.pk: t for t in Tag.objects.select_for_update().filter(pk__in=[source_id, target_id]).order_by("pk")}
    source, target = tags.get(source_id), tags.get(target_id)
    if not source or not target or not source.is_active or not target.is_active or target.canonical_id:
        raise ValidationError("Both tags must be active canonical entries.")
    if Document.objects.filter(tags=source).exclude(pk__in=[doc.pk for doc in documents]).exists():
        # Assignment writers take document then tag locks. Do not reverse that
        # order by acquiring newly discovered document locks while holding a
        # tag. A retry discovers the complete, stable population instead.
        raise ValidationError("Topic assignments changed while this merge was starting. Retry the merge.")
    for doc in documents:
        _bootstrap(doc)
        for row in list(doc.tag_contributions.filter(tag=source)):
            TagContribution.objects.get_or_create(
                document=doc,
                tag=target,
                kind=row.kind,
                source=row.source,
                defaults={"actor": row.actor, "recorded_at": row.recorded_at},
            )
            row.delete()
        _materialize(doc)
    source.canonical = target
    source.is_active = False
    source.automatic_enabled = False
    source.save(update_fields=["canonical", "is_active", "automatic_enabled"])
    changes = {"tag": target}
    if not source.globally_approved:
        changes["is_active"] = False
    TagAlias.objects.filter(tag=source).update(**changes)
    log_action(
        "UPDATE",
        "Merged topic tags",
        actor=actor,
        target=source,
        extra={"target_tag": target_id, "reason": str(reason)[:1000]},
    )


@transaction.atomic
def approve_alias(tag_id, phrase, *, user):
    actor = active_writer(user)
    if not actor.is_system_admin:
        raise PermissionDenied("Only system administrators approve shared aliases.")
    tag = (
        Tag.objects.select_for_update()
        .filter(pk=tag_id, is_active=True, canonical__isnull=True, globally_approved=True)
        .first()
    )
    if tag is None:
        raise ValidationError("Choose an active approved canonical topic.")
    phrase = " ".join(str(phrase).lower().split())
    if not phrase or len(phrase) > 128:
        raise ValidationError("Use an alias between 1 and 128 characters.")
    alias = TagAlias.objects.select_for_update().filter(phrase=phrase).first()
    if alias and alias.tag_id != tag.pk:
        raise ValidationError("This alias already belongs to another topic.")
    if alias is None:
        alias = TagAlias.objects.create(tag=tag, phrase=phrase, approved_by=actor)
    elif not alias.is_active:
        alias.is_active, alias.approved_by = True, actor
        alias.save(update_fields=["is_active", "approved_by"])
    log_action("UPDATE", "Approved shared topic alias", actor=actor, target=alias, extra={"tag_id": tag.pk})
    return alias


@transaction.atomic
def deactivate_alias(alias_id, *, user):
    actor = active_writer(user)
    if not actor.is_system_admin:
        raise PermissionDenied("Only system administrators can change shared aliases.")
    alias = TagAlias.objects.select_for_update().get(pk=alias_id)
    alias.is_active = False
    alias.save(update_fields=["is_active"])
    log_action("UPDATE", "Deactivated shared topic alias", actor=actor, target=alias)
