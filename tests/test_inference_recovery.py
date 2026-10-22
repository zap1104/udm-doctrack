from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.documents.models import Document, DocumentFile
from apps.documents.processing import process_run, request_processing

pytestmark = pytest.mark.django_db(transaction=True)


def test_inference_interruption_keeps_intent_and_does_not_repeat_extraction(users, settings, tmp_path):
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.MEDIA_ROOT = tmp_path
    doc = Document.objects.create(title="Source", year=2026, office=users["med"].office, uploaded_by=users["med"])
    DocumentFile.objects.create(
        document=doc,
        file=SimpleUploadedFile("source.txt", b"Complete readable source text for durable suggestion recovery."),
        original_name="source.txt",
    )
    run = request_processing(doc, user=users["med"])[0]
    with (
        patch("apps.documents.processing.suggest_metadata", side_effect=SystemExit("interrupted after extraction")),
        pytest.raises(SystemExit),
    ):
        process_run(run.pk, expected_generation=run.generation)
    run.refresh_from_db()
    assert run.state == "complete"
    assert run.suggestion_intent.state == "running"
    assert not doc.suggestions.exists()
    from apps.documents.inference_jobs import recover_suggestions

    type(run.suggestion_intent).objects.filter(pk=run.suggestion_intent.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    with patch("apps.documents.processing.read_source_units", side_effect=AssertionError("native extraction repeated")):
        recover_suggestions(limit=10, dry_run=False)
    assert doc.suggestions.count() == 1
    assert process_run(run.pk, expected_generation=run.generation)["status"] == "complete"
    assert doc.suggestions.count() == 1


def test_revoked_actor_and_exhausted_suggestion_attempts_fail_closed(users, settings, tmp_path):
    from apps.accounts.models import User
    from apps.documents.inference_jobs import recover_suggestions
    from apps.documents.models import InferenceIntent

    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.MEDIA_ROOT = tmp_path
    doc = Document.objects.create(title="Source", year=2026, office=users["med"].office, uploaded_by=users["med"])
    DocumentFile.objects.create(
        document=doc,
        file=SimpleUploadedFile("source.txt", b"Complete readable source text for durable recovery."),
        original_name="source.txt",
    )
    run = request_processing(doc, user=users["med"])[0]
    with patch("apps.documents.processing.suggest_metadata", side_effect=RuntimeError("inference unavailable")):
        assert process_run(run.pk, expected_generation=run.generation)["status"] == "complete"
    intent = InferenceIntent.objects.get(run=run)
    assert intent.state == "retry_wait"
    InferenceIntent.objects.filter(pk=intent.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
    User.objects.filter(pk=users["med"].pk).update(is_active=False)
    recover_suggestions(limit=10, dry_run=False)
    intent.refresh_from_db()
    assert intent.state == "blocked"
    assert not doc.suggestions.exists()
