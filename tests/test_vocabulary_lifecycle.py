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


@pytest.mark.django_db(transaction=True)
def test_concurrent_new_assignment_cannot_escape_a_merge(users):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    from unittest.mock import patch

    from django.db import close_old_connections

    service = api()
    first = Document.objects.create(title="Initially affected", office=users["med"].office, year=2026)
    late = Document.objects.create(title="New assignment", office=users["med"].office, year=2026)
    source = Tag.objects.create(name="merge source", globally_approved=True)
    target = Tag.objects.create(name="merge target", globally_approved=True)
    service.replace_human_tags(first, [source], user=users["med"])
    paused, resume = Event(), Event()
    original = Tag.objects.select_for_update

    def pause_before_tag_locks(*args, **kwargs):
        if current_thread().name.startswith("merge-race"):
            paused.set()
            assert resume.wait(15)
        return original(*args, **kwargs)

    def merge():
        close_old_connections()
        try:
            service.merge_tags(source.pk, target.pk, user=users["admin"], reason="duplicate")
            return "completed"
        except ValidationError:
            return "conflict"
        finally:
            close_old_connections()

    with (
        patch.object(Tag.objects, "select_for_update", side_effect=pause_before_tag_locks),
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="merge-race") as pool,
    ):
        future = pool.submit(merge)
        try:
            assert paused.wait(15)
            service.replace_human_tags(late, [source], user=users["med"])
        finally:
            resume.set()
        assert future.result(timeout=20) == "conflict"
    source.refresh_from_db()
    assert source.is_active
    service.merge_tags(source.pk, target.pk, user=users["admin"], reason="retry after concurrent assignment")
    assert not Document.objects.filter(tags=source).exists()
    assert set(Document.objects.filter(tags=target).values_list("pk", flat=True)) == {first.pk, late.pk}
    assert not source.tagcontribution_set.exists()
