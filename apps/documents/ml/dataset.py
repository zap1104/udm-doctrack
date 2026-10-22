"""Immutable reviewed examples. Missing labels remain unknown."""

import hashlib

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.accounts.models import Office
from apps.core.models import Tag
from apps.core.permissions import active_writer

from ..models import AccessLevel, Collection, Document
from ..tagging import digest, input_revision, vocabulary_digest, vocabulary_snapshot
from ..tagging import known_labels as document_labels
from .models import SnapshotSource, TrainingSnapshot

MAX_EXAMPLES = 2000
MAX_INPUT_CHARS = 1_000_000
MAX_SNAPSHOT_CHARS = 50_000_000


def known_labels(example, tag_ids):
    return {tag_id: example.get("labels", {}).get(str(tag_id)) for tag_id in tag_ids}


def split_families(examples, *, seed=42):
    keys = sorted(
        {e["family"] for e in examples}, key=lambda value: hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()
    )
    n = len(keys)
    return {
        "train": keys[: int(n * 0.60)],
        "calibration": keys[int(n * 0.60) : int(n * 0.75)],
        "development": keys[int(n * 0.75) : int(n * 0.90)],
        "test": keys[int(n * 0.90) :],
    }


def _sysadmin(user):
    actor = active_writer(user)
    if not actor.is_system_admin:
        raise PermissionDenied("Only system administrators may prepare training data.")
    return actor


def content_zones(document):
    from django.db.models import Count, OuterRef, Subquery, Sum
    from django.db.models.functions import Length

    from ..file_versions import DocumentFileVersion
    from ..processing_contracts import coverage_summary
    from ..processing_models import ExtractionUnit

    latest = (
        DocumentFileVersion.objects.filter(document_file_id=OuterRef("run__file_version__document_file_id"))
        .order_by("-version_number", "-pk")
        .values("pk")[:1]
    )
    current_units = ExtractionUnit.objects.filter(
        run__document=document,
        run__is_current=True,
        run__file_version__document_file__document=document,
        run__file_version_id=Subquery(latest),
    )
    budget = current_units.aggregate(chars=Sum(Length("normalized_text")), units=Count("pk"))
    if (budget["chars"] or 0) + budget["units"] > MAX_INPUT_CHARS:
        raise ValidationError("This document exceeds the model input budget. No prefix was substituted.")
    units = list(
        current_units.order_by("run__file_version__document_file_id", "ordinal").values(
            "pk",
            "locator_key",
            "normalized_text",
            "coverage",
            "truncated",
            "run__generation",
            "run__file_version__original_name",
            "warnings",
        )
    )
    body = "\n".join(row["normalized_text"] for row in units) if units else document.ocr_text
    header = "\n".join(
        [document.description, document.reference_number]
        + list(document.files.order_by("pk").values_list("original_name", flat=True))
    )
    result = {"title": document.title, "header": header, "body": body}
    if sum(map(len, result.values())) > MAX_INPUT_CHARS:
        raise ValidationError("This document exceeds the model input budget. No prefix was substituted.")
    result["coverage_complete"] = bool(
        units
        and coverage_summary(document)["coverage"] == "complete"
        and all(row["run__generation"] == document.extraction_generation and not row["truncated"] for row in units)
    )
    result["automatic_safe"] = bool(
        result["coverage_complete"]
        and document.ocr_status == "DONE"
        and document.extraction_failed_generation is None
        and all(not row["warnings"] for row in units)
        and not document.extraction_runs.filter(is_current=True).exclude(warnings=[]).exists()
    )
    result["evidence"] = []
    offset = 0
    for row in units:
        end = offset + len(row["normalized_text"])
        result["evidence"].append(
            {
                "unit_id": row["pk"],
                "locator": row["locator_key"],
                "filename": row["run__file_version__original_name"],
                "start": offset,
                "end": end,
            }
        )
        offset = end + 1
    return result


def _assign_families(examples):
    # Union duplicates transitively, including shared tracking origins and
    # administrator-declared template families. Equal text is conservative.
    parent = {row["document_id"]: row["document_id"] for row in examples}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    seen = {}
    for row in examples:
        for token in row.pop("family_tokens"):
            if token in seen:
                a, b = root(row["document_id"]), root(seen[token])
                parent[max(a, b)] = min(a, b)
            else:
                seen[token] = row["document_id"]
    for row in examples:
        row["family"] = f"family-{root(row['document_id'])}"


@transaction.atomic
def prepare_dataset(*, user, office_id, collection, seed=42, dry_run=True, synthetic=False):
    actor = _sysadmin(user)
    if collection not in Collection.values or not Office.objects.filter(pk=office_id, is_active=True).exists():
        raise ValidationError("Select an active internal office and a valid collection.")
    documents = (
        Document.objects.select_for_update()
        .filter(office_id=office_id, collection=collection, is_active=True, training_eligible=True)
        .exclude(access_level=AccessLevel.RESTRICTED)
        .order_by("pk")
    )
    if documents.count() > MAX_EXAMPLES:
        raise ValidationError("The training snapshot exceeds its document budget.")
    tag_ids = list(Tag.objects.filter(is_active=True, canonical__isnull=True).values_list("pk", flat=True))
    examples = []
    total_chars = 0
    for document in documents:
        revision = input_revision(document)
        labels = document_labels(document, tag_ids, revision=revision)
        if not any(value is not None for value in labels.values()):
            continue
        zones = content_zones(document)
        total_chars += sum(len(zones[key]) for key in ("title", "header", "body"))
        if total_chars > MAX_SNAPSHOT_CHARS:
            raise ValidationError("The training snapshot exceeds its text budget.")
        tokens = ["text:" + digest([zones[key] for key in ("title", "header", "body")])]
        tokens += [
            "hash:" + checksum for checksum in document.files.exclude(checksum="").values_list("checksum", flat=True)
        ]
        if document.training_family:
            tokens.append("declared:" + document.training_family)
        if document.tracking_record_id:
            tokens.append(f"tracking:{document.tracking_record_id}")
        if document.external_tracking_record_id:
            tokens.append(f"external:{document.external_tracking_record_id}")
        examples.append(
            {
                "document_id": document.pk,
                "input_revision": revision,
                "office_id": office_id,
                "collection": collection,
                "declared_family": document.training_family,
                **zones,
                "labels": {str(k): v for k, v in labels.items()},
                "family_tokens": tokens,
                "review_ids": list(
                    document.tag_reviews.filter(input_revision=revision).order_by("pk").values_list("pk", flat=True)
                ),
            }
        )
    _assign_families(examples)
    payload = {
        "schema": "tag-dataset-1",
        "examples": examples,
        "splits": split_families(examples, seed=seed),
        "seed": seed,
        "vocabulary": vocabulary_digest(),
        "tags": vocabulary_snapshot(),
        "synthetic": synthetic,
    }
    if dry_run:
        return {
            "documents": len(examples),
            "families": len({e["family"] for e in examples}),
            "partitions": {k: len(v) for k, v in payload["splits"].items()},
            "text_chars": total_chars,
        }
    snapshot = TrainingSnapshot.objects.create(
        office_id=office_id,
        collection=collection,
        created_by=actor,
        payload=payload,
        digest=digest(payload),
        synthetic=synthetic,
    )
    SnapshotSource.objects.bulk_create(
        [
            SnapshotSource(snapshot=snapshot, document_id=row["document_id"], input_revision=row["input_revision"])
            for row in examples
        ]
    )
    return snapshot


def validate_snapshot(snapshot):
    snapshot.refresh_from_db()
    if (
        snapshot.invalidated_at
        or digest(snapshot.payload) != snapshot.digest
        or snapshot.payload["vocabulary"] != vocabulary_digest()
    ):
        raise ValidationError("This training snapshot is invalid or its vocabulary changed.")
    sources = list(snapshot.sources.select_related("document"))
    if len(sources) != len(snapshot.payload["examples"]):
        raise ValidationError("A training source was removed.")
    for source in sources:
        doc = source.document
        if (
            not doc.is_active
            or not doc.training_eligible
            or doc.access_level == AccessLevel.RESTRICTED
            or doc.office_id != snapshot.office_id
            or doc.collection != snapshot.collection
            or input_revision(doc) != source.input_revision
        ):
            raise ValidationError("A training source changed or its eligibility was withdrawn.")
        example = next(row for row in snapshot.payload["examples"] if row["document_id"] == doc.pk)
        if doc.training_family != example.get("declared_family", ""):
            raise ValidationError("A training family changed after snapshot preparation.")
        if (
            list(
                doc.tag_reviews.filter(input_revision=source.input_revision).order_by("pk").values_list("pk", flat=True)
            )
            != example["review_ids"]
        ):
            raise ValidationError("The reviewed labels changed after this snapshot was prepared.")
    return True
