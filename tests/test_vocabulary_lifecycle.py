import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.core.models import Tag
from apps.documents.models import Document
from tests.test_tag_review_provenance import api

pytestmark = pytest.mark.django_db


def test_merge_preserves_history_and_counts(users):
    service = api()
    doc = Document.objects.create(title="Case", office=users["med"].office, uploaded_by=users["med"], year=2026)
    a, b = Tag.objects.create(name="old label"), Tag.objects.create(name="canonical label")
    service.replace_human_tags(doc, [a, b], user=users["med"])
    historical = list(doc.tag_reviews.values_list("tag_id", "decision", "vocabulary"))
    with pytest.raises(PermissionDenied):
        service.merge_tags(a.pk, b.pk, user=users["med_admin"], reason="duplicate")
    service.merge_tags(a.pk, b.pk, user=users["admin"], reason="duplicate")
    assert set(doc.tags.values_list("pk", flat=True)) == {b.pk}
    a.refresh_from_db()
    b.refresh_from_db()
    assert (a.canonical_id, a.is_active, a.usage_count, b.usage_count) == (b.pk, False, 0, 1)
    assert list(doc.tag_reviews.values_list("tag_id", "decision", "vocabulary")) == historical
    with pytest.raises(ValidationError):
        service.merge_tags(b.pk, a.pk, user=users["admin"], reason="cycle")
