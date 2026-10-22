from datetime import timedelta
from importlib.util import find_spec
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.documents.models import Document, DocumentFile
from apps.documents.processing import process_run, request_processing
from tests.ml_fixtures import make_candidate

pytestmark = pytest.mark.django_db(transaction=True)


def test_auto_application_has_its_own_recoverable_intent(users, settings, tmp_path):
    assert find_spec("apps.documents.tag_application_jobs"), "Automatic publication intent is missing"
    from apps.documents.ml.lifecycle import activate_model
    from apps.documents.models import TagApplicationIntent
    from apps.documents.tag_application_jobs import recover_applications

    settings.ENABLE_TAG_ML = True
    settings.ENABLE_AUTOMATIC_TAGS = True
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.SUGGESTION_ENGINE = "ai"
    settings.MEDIA_ROOT = tmp_path
    model = make_candidate(users, n=30, approved=True)
    activate_model(model.pk, user=users["admin"], mode="suggestion")
    doc = Document.objects.create(
        title="Purchase request", year=2026, office=users["med"].office, uploaded_by=users["med"]
    )
    DocumentFile.objects.create(
        document=doc,
        file=SimpleUploadedFile("source.txt", b"Purchase procurement supplier request supporting office items."),
        original_name="source.txt",
    )
    run = request_processing(doc, user=users["med"])[0]
    with (
        patch("apps.documents.automatic_tags.apply_tag_decisions", side_effect=SystemExit("worker stopped")),
        pytest.raises(SystemExit),
    ):
        process_run(run.pk, expected_generation=run.generation)
    assert doc.suggestions.count() == 1
    intent = TagApplicationIntent.objects.get(suggestion__document=doc)
    assert intent.state == "running"
    TagApplicationIntent.objects.filter(pk=intent.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    with (
        patch("apps.documents.processing.read_source_units", side_effect=AssertionError("source read again")),
        patch("apps.documents.ml.inference.predict_tags", side_effect=AssertionError("inference repeated")),
    ):
        recover_applications(limit=10, dry_run=False)
    intent.refresh_from_db()
    assert intent.state == "complete"
    assert "synthetic_data" in intent.result["reasons"][str(model.topic_fixture_id)]
    assert doc.tags.count() == 0
