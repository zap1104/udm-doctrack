import pytest

from apps.core.models import Tag
from apps.documents.models import Document
from apps.documents.tagging import input_revision, known_labels, review_tag, vocabulary_digest

pytestmark = pytest.mark.django_db


def test_unapproved_other_office_tag_does_not_invalidate_controlled_reviews(users):
    controlled = Tag.objects.create(name="approved topic", globally_approved=True)
    doc = Document.objects.create(title="Reviewed", year=2026, office=users["med"].office, uploaded_by=users["med"])
    original = vocabulary_digest()
    review_tag(
        doc,
        tag_id=controlled.pk,
        decision="confirm",
        user=users["med"],
        expected_revision=input_revision(doc),
        vocabulary=original,
    )
    Tag.objects.create(name="unrelated private office word")
    assert vocabulary_digest() == original
    assert known_labels(doc, [controlled.pk]) == {controlled.pk: 1}
