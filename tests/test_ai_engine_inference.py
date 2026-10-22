from importlib.util import find_spec

import pytest

from apps.documents.models import Document
from apps.documents.suggestions import suggest_metadata
from tests.ml_fixtures import make_candidate

pytestmark = pytest.mark.django_db


def test_actual_model_changes_ai_output_and_fallback_is_truthful(users, settings):
    assert find_spec("apps.documents.ml.inference"), "Real AIEngine inference is missing"
    from apps.documents.ml.lifecycle import activate_model

    settings.ENABLE_TAG_ML = True
    settings.SUGGESTION_ENGINE = "ai"
    model = make_candidate(users)
    doc = Document.objects.create(
        title="Purchase request",
        ocr_text="purchase procurement supplier",
        year=2026,
        office=users["med"].office,
        uploaded_by=users["med"],
    )
    before = suggest_metadata(text=doc.ocr_text, filename=doc.title, office=doc.office, user=users["med"], document=doc)
    assert before.engine == "rules"
    activate_model(model.pk, user=users["admin"], mode="suggestion")
    after = suggest_metadata(text=doc.ocr_text, filename=doc.title, office=doc.office, user=users["med"], document=doc)
    assert after.engine == "ml"
    assert "synthetic procurement topic" in after.tags
    assert after.topic_decisions[0]["tag_id"] == model.topic_fixture_id
    doc.collection = "EXTERNAL"
    doc.save(update_fields=["collection"])
    external = suggest_metadata(
        text=doc.ocr_text, filename=doc.title, office=doc.office, user=users["med_admin"], document=doc
    )
    assert external.engine == "rules"
    doc.collection = "OVPA"
    doc.save(update_fields=["collection"])
    activate_model(model.pk, user=users["admin"], mode="shadow")
    shadow = suggest_metadata(text=doc.ocr_text, filename=doc.title, office=doc.office, user=users["med"], document=doc)
    assert shadow.engine == "rules"
    assert doc.tag_predictions.filter(engine="ml").exists()
    source = model.snapshot.sources.first().document
    source.training_eligible = False
    source.save(update_fields=["training_eligible"])
    fallback = suggest_metadata(
        text=doc.ocr_text, filename=doc.title, office=doc.office, user=users["med"], document=doc
    )
    assert fallback.engine == "rules"


def test_corrupt_model_and_missing_ml_dependency_keep_rules_usable(users, settings):
    import hashlib
    from unittest.mock import patch

    from apps.documents.ml.lifecycle import activate_model, clear_model_cache
    from apps.documents.ml.models import TagModel

    settings.ENABLE_TAG_ML = True
    settings.SUGGESTION_ENGINE = "ai"
    model = make_candidate(users)
    activate_model(model.pk, user=users["admin"], mode="suggestion")
    doc = Document.objects.create(
        title="Request",
        ocr_text="purchase procurement supplier",
        year=2026,
        office=users["med"].office,
        uploaded_by=users["med"],
    )
    with patch("apps.documents.ml.inference.cached_bundle", side_effect=ImportError("optional ML profile absent")):
        missing = suggest_metadata(text=doc.ocr_text, user=users["med"], document=doc)
    assert missing.engine == "rules"
    TagModel.objects.filter(pk=model.pk).update(
        artifact=b"damaged", artifact_digest=hashlib.sha256(b"damaged").hexdigest()
    )
    clear_model_cache()
    corrupt = suggest_metadata(text=doc.ocr_text, user=users["med"], document=doc)
    assert corrupt.engine == "rules"
    assert "synthetic procurement topic" not in corrupt.tags


def test_transfer_during_inference_cannot_publish_other_office_model_output(users, settings):
    from unittest.mock import patch

    from apps.accounts.models import User
    from apps.documents.ml.lifecycle import activate_model, cached_bundle

    settings.ENABLE_TAG_ML = True
    settings.SUGGESTION_ENGINE = "ai"
    model = make_candidate(users)
    activate_model(model.pk, user=users["admin"], mode="suggestion")
    doc = Document.objects.create(
        title="Request",
        ocr_text="purchase procurement supplier",
        year=2026,
        office=users["med"].office,
        uploaded_by=users["med"],
    )
    bundle = cached_bundle(model.pk, model.artifact_digest)
    original = bundle.predict

    def transfer_after_prediction(inputs):
        result = original(inputs)
        User.objects.filter(pk=users["med"].pk).update(office=users["hr"].office)
        return result

    with patch.object(bundle, "predict", side_effect=transfer_after_prediction):
        suggestion = suggest_metadata(text=doc.ocr_text, user=users["med"], document=doc)
    assert suggestion.engine == "rules"
    assert not doc.tag_predictions.exists()
