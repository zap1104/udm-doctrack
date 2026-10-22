from importlib.util import find_spec

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.core.models import Tag
from apps.documents.models import Document
from apps.documents.tagging import input_revision, review_tag, vocabulary_digest

pytestmark = pytest.mark.django_db


def api():
    assert find_spec("apps.documents.ml"), "Scoped training datasets are missing"
    from apps.documents.ml import dataset

    return dataset


def test_dataset_scope_unknown_and_immutable_snapshot(users):
    dataset = api()
    a, b = Tag.objects.create(name="purchasing"), Tag.objects.create(name="maintenance")
    docs = []
    for owner, collection in [("med", "OVPA"), ("hr", "OVPA"), ("med_admin", "EXTERNAL")]:
        doc = Document.objects.create(
            title="Authored fixture",
            ocr_text="independent synthetic purchase evidence",
            office=users[owner].office,
            uploaded_by=users[owner],
            collection=collection,
            year=2026,
            training_eligible=True,
        )
        review_tag(
            doc,
            tag_id=a.pk,
            decision="confirm",
            user=users[owner],
            expected_revision=input_revision(doc),
            vocabulary=vocabulary_digest(),
        )
        docs.append(doc)
    with pytest.raises(PermissionDenied):
        dataset.prepare_dataset(user=users["med_admin"], office_id=docs[0].office_id, collection="OVPA", dry_run=False)
    snap = dataset.prepare_dataset(
        user=users["admin"], office_id=docs[0].office_id, collection="OVPA", dry_run=False, synthetic=True
    )
    assert [row["document_id"] for row in snap.payload["examples"]] == [docs[0].pk]
    row = snap.payload["examples"][0]
    assert dataset.known_labels(row, [a.pk, b.pk]) == {a.pk: 1, b.pk: None}
    frozen = row["body"]
    docs[0].ocr_text = "new evidence"
    docs[0].save(update_fields=["ocr_text"])
    snap.refresh_from_db()
    assert snap.payload["examples"][0]["body"] == frozen
    with pytest.raises(ValidationError):
        dataset.validate_snapshot(snap)


def test_family_partitions_are_deterministic_and_disjoint():
    dataset = api()
    examples = [{"family": f"family-{i // 2}", "document_id": i} for i in range(100)]
    splits = dataset.split_families(examples, seed=42)
    assert splits == dataset.split_families(list(reversed(examples)), seed=42)
    sets = [set(ids) for ids in splits.values()]
    assert len(set.union(*sets)) == 50
    assert sum(map(len, sets)) == 50
    assert len(splits["train"]) == 30
