from dataclasses import replace
from importlib.util import find_spec

import pytest
from django.core.exceptions import ValidationError

from apps.core.models import Tag
from apps.documents.models import Document
from tests.ml_fixtures import make_candidate

pytestmark = pytest.mark.django_db


def api():
    assert find_spec("apps.documents.automatic_tags"), "Qualified automatic-tag gates are missing"
    from apps.documents import automatic_tags

    return automatic_tags


def test_synthetic_unqualified_and_crafted_decisions_cannot_change_metadata(users, settings):
    service = api()
    from apps.documents.ml.inference import predict_tags
    from apps.documents.ml.lifecycle import activate_model
    from apps.documents.ml.models import TagModel
    from apps.documents.tagging import input_revision

    settings.ENABLE_TAG_ML = True
    model = make_candidate(users, n=30, approved=True)
    activate_model(model.pk, user=users["admin"], mode="suggestion")
    doc = Document.objects.create(
        title="Request",
        ocr_text="purchase procurement supplier",
        year=2026,
        office=users["med"].office,
        uploaded_by=users["med"],
    )
    before = (doc.office_id, doc.collection, doc.retention_until, doc.access_level)
    decisions = predict_tags(doc, user=users["med"])
    assert decisions
    first = service.apply_tag_decisions(
        doc, list(decisions), actor=users["med"], expected_input_revision=input_revision(doc)
    )
    assert first["applied"] == []
    assert "automatic_disabled" in first["reasons"][str(model.topic_fixture_id)]
    settings.ENABLE_AUTOMATIC_TAGS = True
    # A crafted registry write cannot turn synthetic quality evidence into
    # qualified production evidence, even if an activation view were bypassed.
    TagModel.objects.filter(pk=model.pk).update(mode="automatic")
    result = service.apply_tag_decisions(
        doc, list(decisions), actor=users["med"], expected_input_revision=input_revision(doc)
    )
    assert "synthetic_data" in result["reasons"][str(model.topic_fixture_id)]
    assert doc.tags.count() == 0
    forged = replace(decisions[0], score=1.0)
    result = service.apply_tag_decisions(doc, [forged], actor=users["med"], expected_input_revision=input_revision(doc))
    assert result["applied"] == []
    with pytest.raises(ValidationError):
        service.apply_tag_decisions(
            doc,
            [{"tag_id": model.topic_fixture_id, "office_id": users["hr"].office_id}],
            actor=users["med"],
            expected_input_revision=input_revision(doc),
        )
    doc.refresh_from_db()
    assert (doc.office_id, doc.collection, doc.retention_until, doc.access_level) == before


def test_human_rejection_survives_new_content_and_auto_rollback_keeps_human(users, settings):
    service = api()
    from apps.documents.ml.inference import predict_tags
    from apps.documents.ml.lifecycle import activate_model
    from apps.documents.models import TagContribution
    from apps.documents.tagging import input_revision, review_tag, vocabulary_digest

    settings.ENABLE_TAG_ML = True
    settings.ENABLE_AUTOMATIC_TAGS = True
    model = make_candidate(users, n=30, approved=True)
    activate_model(model.pk, user=users["admin"], mode="suggestion")
    doc = Document.objects.create(
        title="Request",
        ocr_text="purchase procurement supplier",
        year=2026,
        office=users["med"].office,
        uploaded_by=users["med"],
    )
    tag = Tag.objects.get(pk=model.topic_fixture_id)
    review_tag(
        doc,
        tag_id=tag.pk,
        decision="reject",
        user=users["med"],
        expected_revision=input_revision(doc),
        vocabulary=vocabulary_digest(),
    )
    doc.ocr_text += " signed update"
    doc.save(update_fields=["ocr_text"])
    decisions = predict_tags(doc, user=users["med"])
    result = service.apply_tag_decisions(
        doc, list(decisions), actor=users["med"], expected_input_revision=input_revision(doc)
    )
    assert "human_rejected" in result["reasons"][str(tag.pk)]
    review_tag(
        doc,
        tag_id=tag.pk,
        decision="confirm",
        user=users["med"],
        expected_revision=input_revision(doc),
        vocabulary=vocabulary_digest(),
    )
    TagContribution.objects.create(document=doc, tag=tag, kind="automatic", source=f"model:{model.pk}")
    assert service.rollback_auto_tags(user=users["admin"], model_id=model.pk, dry_run=True)["document_ids"] == [doc.pk]
    assert doc.tag_contributions.filter(kind="automatic").exists()
    service.rollback_auto_tags(user=users["admin"], model_id=model.pk, dry_run=False)
    assert not doc.tag_contributions.filter(kind="automatic").exists()
    assert set(doc.tags.values_list("pk", flat=True)) == {tag.pk}
    tag.refresh_from_db()
    assert tag.usage_count == 1
