from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import Tag
from apps.documents.ml.models import SnapshotSource, TagModel, TrainingSnapshot
from apps.documents.models import Document
from apps.documents.tagging import input_revision, known_labels, review_tag, vocabulary_digest

pytestmark = pytest.mark.django_db


def source_model(users):
    doc = Document.objects.create(
        title="Private source",
        ocr_text="private content canary",
        office=users["med"].office,
        year=2026,
        training_eligible=True,
    )
    snapshot = TrainingSnapshot.objects.create(
        office=doc.office,
        collection="OVPA",
        payload={"examples": [{"document_id": doc.pk, "body": doc.ocr_text}]},
        digest="a" * 64,
    )
    SnapshotSource.objects.create(snapshot=snapshot, document=doc, input_revision=input_revision(doc))
    model = TagModel.objects.create(
        snapshot=snapshot,
        office=doc.office,
        collection="OVPA",
        artifact=b"private trained content",
        artifact_digest="b" * 64,
        artifact_origin="local-training-1",
        vocabulary=vocabulary_digest(),
        is_active=True,
        mode="suggestion",
    )
    return doc, snapshot, model


@pytest.mark.parametrize(
    "field,value",
    [("title", "Corrected title"), ("is_active", False), ("training_eligible", False), ("access_level", "RESTRICTED")],
)
def test_source_changes_immediately_invalidate_and_restore_does_not_reactivate(users, field, value):
    doc, snapshot, model = source_model(users)
    original = getattr(doc, field)
    setattr(doc, field, value)
    doc.save(update_fields=[field])
    model.refresh_from_db()
    snapshot.refresh_from_db()
    assert not model.is_active
    assert model.invalidated_at and snapshot.invalidated_at
    setattr(doc, field, original)
    doc.save(update_fields=[field])
    model.refresh_from_db()
    assert not model.is_active


def test_purge_removes_private_training_content_but_keeps_noncontent_receipt(users):
    from apps.documents.tasks import purge_deleted_documents

    doc, snapshot, model = source_model(users)
    doc.is_active, doc.deleted_at = False, timezone.now() - timedelta(days=31)
    doc.save(update_fields=["is_active", "deleted_at"])
    assert purge_deleted_documents()["purged"] == 1
    snapshot.refresh_from_db()
    model.refresh_from_db()
    assert snapshot.payload == {}
    assert bytes(model.artifact) == b""
    assert model.invalidated_at and not model.is_active


def test_unrelated_vocabulary_addition_preserves_explicit_label_but_definition_edit_does_not(users):
    doc = Document.objects.create(title="Reviewed", office=users["med"].office, year=2026)
    topic = Tag.objects.create(name="approved purchasing", globally_approved=True)
    review_tag(
        doc,
        tag_id=topic.pk,
        decision="confirm",
        user=users["med"],
        expected_revision=input_revision(doc),
        vocabulary=vocabulary_digest(),
    )
    other = Tag.objects.create(name="approved facilities", globally_approved=True)
    assert known_labels(doc, [topic.pk, other.pk]) == {topic.pk: 1, other.pk: None}
    topic.description = "A changed definition"
    topic.save(update_fields=["description"])
    assert known_labels(doc, [topic.pk])[topic.pk] is None


def test_equal_body_with_different_metadata_is_one_training_family(users):
    from apps.documents.ml.dataset import prepare_dataset

    topic = Tag.objects.create(name="approved fixture topic", globally_approved=True)
    for title in ("Paper A", "Different filename B"):
        doc = Document.objects.create(
            title=title,
            ocr_text="identical complete source body",
            office=users["med"].office,
            year=2026,
            training_eligible=True,
        )
        review_tag(
            doc,
            tag_id=topic.pk,
            decision="confirm",
            user=users["med"],
            expected_revision=input_revision(doc),
            vocabulary=vocabulary_digest(),
        )
    result = prepare_dataset(user=users["admin"], office_id=doc.office_id, collection="OVPA", dry_run=True)
    assert result["documents"] == 2
    assert result["families"] == 1


def test_review_service_rejects_another_offices_private_tag_id(users):
    from django.core.exceptions import ValidationError

    private = Tag.objects.create(name="private hr classification canary")
    other = Document.objects.create(title="HR private record", office=users["hr"].office, year=2026)
    other.tags.add(private)
    own = Document.objects.create(title="MED record", office=users["med"].office, year=2026)
    with pytest.raises(ValidationError):
        review_tag(own, tag_id=private.pk, decision="confirm", user=users["med"],
            expected_revision=input_revision(own), vocabulary=vocabulary_digest())
    assert not own.tags.exists() and not own.tag_reviews.exists()
