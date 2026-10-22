"""Authored disposable examples. Never evidence of production accuracy."""

from apps.core.models import Tag
from apps.documents.ml.dataset import prepare_dataset
from apps.documents.ml.training import train_candidate
from apps.documents.models import Document, TagReview
from apps.documents.tagging import input_revision, vocabulary_digest


def make_candidate(users, n=70, approved=True):
    tag = Tag.objects.create(name="synthetic procurement topic", globally_approved=approved, automatic_enabled=approved)
    vocabulary = vocabulary_digest()
    for i in range(n):
        doc = Document.objects.create(
            title=f"Authored record {i}",
            year=2026,
            office=users["med"].office,
            uploaded_by=users["med"],
            training_eligible=True,
            ocr_text=("purchase procurement supplier" if i % 2 == 0 else "maintenance facilities repair")
            + f" unique authored fixture {i}",
        )
        TagReview.objects.create(
            document=doc,
            tag=tag,
            decision="confirm" if i % 2 == 0 else "reject",
            input_revision=input_revision(doc),
            vocabulary=vocabulary,
            actor=users["med"],
        )
    snapshot = prepare_dataset(
        user=users["admin"], office_id=users["med"].office_id, collection="OVPA", dry_run=False, synthetic=True
    )
    # Test helper stores the ID separately rather than editing the immutable
    # snapshot payload to add test-only state.
    model = train_candidate(snapshot.pk, user=users["admin"])
    model.topic_fixture_id = tag.pk
    return model
