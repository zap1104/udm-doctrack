from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections

from apps.accounts.models import User
from apps.core.models import Tag
from apps.documents.automatic_tags import quality_reasons, rollback_auto_tags
from apps.documents.models import Document, TagContribution
from apps.documents.tagging import replace_human_tags
from tests.ml_fixtures import make_candidate


def test_quality_policy_recomputes_independent_interval_not_a_claimed_percentage():
    """Arithmetic-only counts; this is not a real model quality evaluation."""
    from types import SimpleNamespace

    report = {"predictions": 100, "true_positives": 100, "known": 150, "automatic_qualified": True}
    bundle = SimpleNamespace(
        classifiers={"1": {"calibrated": True}},
        evaluation={
            "synthetic": False,
            "production_eligible": True,
            "holdout_frozen": True,
            "source_coverage_complete": True,
            "tags": {"1": report},
            "partitions": {
                "calibration": {"1": {"known": 40, "positives": 20}},
                "development": {"1": {"predictions": 25, "true_positives": 25, "known": 50, "positives": 25}},
            },
        },
    )
    assert quality_reasons(bundle, 1, synthetic=False) == []
    report["true_positives"] = 95
    assert "precision_or_coverage_gate" in quality_reasons(bundle, 1, synthetic=False)
    report["true_positives"] = 100
    assert "synthetic_data" in quality_reasons(bundle, 1, synthetic=True)


@pytest.mark.django_db(transaction=True)
def test_simultaneous_human_confirmation_and_automatic_rollback_keep_one_assignment(users):
    model = make_candidate(users, n=30, approved=True)
    tag = Tag.objects.get(pk=model.topic_fixture_id)
    doc = Document.objects.create(
        title="Concurrent review", year=2026, office=users["med"].office, uploaded_by=users["med"]
    )
    doc.tags.add(tag)
    Tag.objects.filter(pk=tag.pk).update(usage_count=1)
    TagContribution.objects.create(document=doc, tag=tag, kind="automatic", source=f"model:{model.pk}")
    barrier = Barrier(2)

    def work(confirm):
        close_old_connections()
        try:
            actor = User.objects.get(pk=users["med"].pk if confirm else users["admin"].pk)
            barrier.wait(timeout=10)
            if confirm:
                replace_human_tags(Document.objects.get(pk=doc.pk), [Tag.objects.get(pk=tag.pk)], user=actor)
            else:
                rollback_auto_tags(user=actor, model_id=model.pk, dry_run=False)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(work, confirm) for confirm in (True, False)]
        for future in futures:
            future.result(timeout=30)
    assert set(doc.tags.values_list("pk", flat=True)) == {tag.pk}
    assert doc.tag_contributions.filter(kind="human", tag=tag).exists()
    assert not doc.tag_contributions.filter(kind="automatic").exists()
    tag.refresh_from_db()
    assert tag.usage_count == 1
