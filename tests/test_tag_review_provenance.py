from importlib.util import find_spec

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.core.models import Tag
from apps.documents.models import Document

pytestmark = pytest.mark.django_db


def api():
    assert find_spec("apps.documents.tagging"), "Explicit tag provenance is missing"
    from apps.documents import tagging

    return tagging


def test_review_unknown_rejection_and_human_priority(users):
    service = api()
    doc = Document.objects.create(title="Review", office=users["med"].office, uploaded_by=users["med"], year=2026)
    tag = Tag.objects.create(name="procurement", globally_approved=True)
    revision = service.input_revision(doc)
    vocab = service.vocabulary_digest()
    service.review_tag(
        doc, tag_id=tag.pk, decision="unsure", user=users["med"], expected_revision=revision, vocabulary=vocab
    )
    assert not doc.tags.exists()
    assert service.known_labels(doc, [tag.pk]) == {tag.pk: None}
    service.review_tag(
        doc, tag_id=tag.pk, decision="confirm", user=users["med"], expected_revision=revision, vocabulary=vocab
    )
    assert set(doc.tags.values_list("pk", flat=True)) == {tag.pk}
    service.review_tag(
        doc, tag_id=tag.pk, decision="reject", user=users["med"], expected_revision=revision, vocabulary=vocab
    )
    assert not doc.tags.exists()
    assert service.known_labels(doc, [tag.pk]) == {tag.pk: 0}
    review = doc.tag_reviews.latest("pk")
    assert review.input_revision == revision
    doc.title = "Changed"
    doc.save(update_fields=["title"])
    with pytest.raises(ValidationError):
        service.review_tag(
            doc, tag_id=tag.pk, decision="confirm", user=users["med"], expected_revision=revision, vocabulary=vocab
        )
    assert not doc.tags.exists()


def test_legacy_tags_preserved_and_cross_office_write_rejected(users):
    service = api()
    doc = Document.objects.create(title="Review", office=users["med"].office, uploaded_by=users["med"], year=2026)
    old = Tag.objects.create(name="old", usage_count=1)
    doc.tags.add(old)
    service.replace_human_tags(doc, [old], user=users["med"])
    assert set(doc.tag_contributions.filter(tag=old).values_list("kind", flat=True)) == {"human"}
    with pytest.raises(PermissionDenied):
        service.replace_human_tags(doc, [], user=users["hr"])
    old.refresh_from_db()
    assert old.usage_count == 1
    assert doc.tags.count() == 1


def test_complete_review_covers_only_explicit_ids(users):
    service = api()
    doc = Document.objects.create(title="Review", office=users["med"].office, uploaded_by=users["med"], year=2026)
    a, b = Tag.objects.create(name="one", globally_approved=True), Tag.objects.create(name="two", globally_approved=True)
    service.complete_review(
        doc,
        covered_ids=[a.pk],
        positive_ids=[],
        user=users["med"],
        expected_revision=service.input_revision(doc),
        vocabulary=service.vocabulary_digest(),
    )
    assert service.known_labels(doc, [a.pk, b.pk]) == {a.pk: 0, b.pk: None}


def test_review_labels_and_actor_names_survive_later_renames(users):
    service = api()
    doc = Document.objects.create(title="Review", office=users["med"].office, uploaded_by=users["med"], year=2026)
    tag = Tag.objects.create(name="original topic", globally_approved=True)
    service.review_tag(
        doc,
        tag_id=tag.pk,
        decision="confirm",
        user=users["med"],
        expected_revision=service.input_revision(doc),
        vocabulary=service.vocabulary_digest(),
    )
    review = doc.tag_reviews.get()
    original_actor = users["med"].get_full_name() or users["med"].username
    tag.name = "renamed topic"
    tag.save()
    assert review.tag_name_snapshot == "original topic"
    assert review.actor_name_snapshot == original_actor
