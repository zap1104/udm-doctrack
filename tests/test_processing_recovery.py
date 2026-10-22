"""Durable actor-bound work survives queue gaps without stale or duplicate writes."""
import hashlib
from datetime import timedelta
from importlib.util import find_spec
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import User
from apps.documents.models import Document, DocumentFile, ExtractionRun, MetadataSuggestion

pytestmark = pytest.mark.django_db(transaction=True)


def service():
    assert find_spec("apps.documents.processing"), "Recoverable processing service is missing"
    from apps.documents import processing
    return processing


@pytest.fixture
def source(users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    settings.ENABLE_BACKGROUND_TASKS = False
    document = Document.objects.create(title="Current source", office=users["med"].office, uploaded_by=users["med"], year=2026)
    payload = b"A complete readable native document. The late appendix contains turbine calibration."
    root = DocumentFile.objects.create(document=document,
        file=SimpleUploadedFile("native.txt", payload), original_name="native.txt", size=len(payload), checksum=hashlib.sha256(payload).hexdigest())
    return document, root


def test_commit_enqueue_failure_retains_recoverable_actor_bound_intent(source, users, settings):
    api = service()
    settings.ENABLE_BACKGROUND_TASKS = True
    document, root = source
    with patch("django_q.tasks.async_task", side_effect=RuntimeError("queue unavailable")):
        runs = api.request_processing(document, user=users["med"], file_ids=[root.pk])
    run = ExtractionRun.objects.get(pk=runs[0].pk)
    assert run.actor_id == users["med"].pk
    assert run.actor_was_recorded is True
    assert run.state == "pending"
    result = api.recover_processing(limit=10, dry_run=True)
    assert run.pk in result["run_ids"]


def test_native_duplicate_delivery_is_idempotent(source, users):
    api = service()
    document, _root = source
    run = api.request_processing(document, user=users["med"])[0]
    first = api.process_run(run.pk, expected_generation=run.generation)
    second = api.process_run(run.pk, expected_generation=run.generation)
    document.refresh_from_db()
    assert first["status"] == "complete"
    assert second["status"] == "complete"
    assert run.units.count() == 1
    assert "turbine calibration" in document.ocr_text
    assert MetadataSuggestion.objects.filter(document=document).count() == 1


def test_worker_interruption_can_recover_expired_lease(source, users):
    api = service()
    document, _root = source
    run = api.request_processing(document, user=users["med"])[0]
    with patch("apps.documents.processing.read_source_units", side_effect=SystemExit("worker stopped")), pytest.raises(SystemExit):
        api.process_run(run.pk, expected_generation=run.generation)
    run.refresh_from_db()
    assert run.state == "running"
    ExtractionRun.objects.filter(pk=run.pk).update(lease_expires_at=timezone.now()-timedelta(seconds=1))
    result = api.recover_processing(limit=10, dry_run=False)
    assert run.pk in result["run_ids"]
    run.refresh_from_db()
    assert run.state == "pending"
    assert api.process_run(run.pk, expected_generation=run.generation)["status"] == "complete"


def test_revoked_actor_cannot_become_actorless_maintenance(source, users):
    api = service()
    document, _root = source
    run = api.request_processing(document, user=users["med"])[0]
    User.objects.filter(pk=users["med"].pk).update(is_active=False)
    result = api.process_run(run.pk, expected_generation=run.generation)
    assert result["status"] == "blocked"
    assert not run.units.exists()
    document.refresh_from_db()
    assert not document.ocr_text


def test_foreign_office_cannot_request_or_recover_as_owner(source, users):
    api = service()
    document, _root = source
    with pytest.raises(PermissionDenied):
        api.request_processing(document, user=users["sup"])


def test_stale_generation_does_not_publish_text(source, users):
    api = service()
    document, _root = source
    run = api.request_processing(document, user=users["med"])[0]
    Document.objects.filter(pk=document.pk).update(extraction_generation=run.generation+1, ocr_text="Newer text")
    api.process_run(run.pk, expected_generation=run.generation)
    document.refresh_from_db()
    assert document.ocr_text == "Newer text"
    assert not run.units.exists()


def test_suggestion_records_real_engine_version(source, users):
    api = service()
    document, _root = source
    run = api.request_processing(document, user=users["med"])[0]
    api.process_run(run.pk, expected_generation=run.generation)
    assert document.suggestions.get().engine_version == "1.0"


def test_failed_finalization_records_attention_and_retains_retry_intent(source, users):
    api = service()
    document, _root = source
    Document.objects.filter(pk=document.pk).update(ocr_text="Previously useful text")
    run = api.request_processing(document, user=users["med"])[0]
    with patch.object(Document, "rebuild_index", side_effect=RuntimeError("index unavailable")), pytest.raises(RuntimeError):
        api.process_run(run.pk, expected_generation=run.generation)
    document.refresh_from_db()
    run.refresh_from_db()
    assert document.ocr_text == "Previously useful text"
    assert document.extraction_failed_generation is not None
    assert run.state in {"retry_wait", "action_required"}


def test_new_worker_lease_is_not_cancelled_by_old_callback(source, users):
    api = service()
    from apps.documents.source_readers import read_source_units
    document, _root = source
    run = api.request_processing(document, user=users["med"])[0]
    def replaced(*args, **kwargs):
        ExtractionRun.objects.filter(pk=run.pk).update(lease_token="new-worker-token")
        Document.objects.filter(pk=document.pk).update(ocr_text="New worker result")
        return read_source_units(*args, **kwargs)
    with patch("apps.documents.processing.read_source_units", side_effect=replaced):
        api.process_run(run.pk, expected_generation=run.generation)
    run.refresh_from_db()
    document.refresh_from_db()
    assert run.lease_token == "new-worker-token"
    assert document.ocr_text == "New worker result"


def test_ocr_consent_revoked_in_flight_discards_new_provider_text(users, settings, tmp_path):
    api = service()
    from django.db import connection

    from apps.documents.processing_contracts import UnitResult
    from tests.intelligence_fixtures import image_bytes
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    settings.OCR_BACKEND = "ocrspace"
    document = Document.objects.create(title="Consent race", office=users["med"].office, uploaded_by=users["med"],
        year=2026, allow_external_ocr=True)
    payload = image_bytes()
    DocumentFile.objects.create(document=document, file=SimpleUploadedFile("scan.png", payload), original_name="scan.png",
        checksum=hashlib.sha256(payload).hexdigest())
    run = api.request_processing(document, user=users["med"])[0]
    def returned(*args, **kwargs):
        assert not connection.in_atomic_block
        assert kwargs["authorize_dispatch"]()
        Document.objects.filter(pk=document.pk).update(allow_external_ocr=False)
        return UnitResult(locator_key="image:frame:1", ordinal=1, kind="ocr_unit", raw_text="New provider canary",
            normalized_text="New provider canary", method="ocr.space:eng", state="complete", coverage="complete")
    with patch("apps.documents.processing.ocr_unit", side_effect=returned):
        api.process_run(run.pk, expected_generation=run.generation)
    document.refresh_from_db()
    assert "New provider canary" not in document.ocr_text
    assert all("New provider canary" not in unit.raw_text for unit in run.units.all())


def test_file_discovered_during_native_read_gets_own_intent(source, users):
    api = service()
    from apps.documents.source_readers import read_source_units
    document, _root = source
    run = api.request_processing(document, user=users["med"])[0]
    def appended(*args, **kwargs):
        DocumentFile.objects.create(document=document, file=SimpleUploadedFile("late.txt", b"Late source file"), original_name="late.txt")
        return read_source_units(*args, **kwargs)
    with patch("apps.documents.processing.read_source_units", side_effect=appended):
        api.process_run(run.pk, expected_generation=run.generation)
    assert document.extraction_runs.count() == 2
    document.refresh_from_db()
    assert document.ocr_status == "PENDING"


def test_disabled_rollout_keeps_pending_intent_without_processing(source, users, settings):
    api = service()
    document, _root = source
    run = api.request_processing(document, user=users["med"])[0]
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = False
    outcome = api.process_run(run.pk, expected_generation=run.generation)
    run.refresh_from_db()
    assert outcome["status"] == "blocked"
    assert run.state == "pending"
    assert not run.units.exists()


def test_one_invocation_dispatches_only_one_ocr_frame(users, settings, tmp_path):
    import io

    from PIL import Image

    from apps.documents.processing_contracts import UnitResult
    api = service()
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    settings.OCR_BACKEND = "ocrspace"
    document = Document.objects.create(title="Two scan frames", office=users["med"].office,
        uploaded_by=users["med"], year=2026, allow_external_ocr=True)
    payload = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(payload, "TIFF", save_all=True,
        append_images=[Image.new("RGB", (50, 50), "gray")])
    DocumentFile.objects.create(document=document, file=SimpleUploadedFile("frames.tiff", payload.getvalue()), original_name="frames.tiff")
    run = api.request_processing(document, user=users["med"])[0]
    def returned(*args, **kwargs):
        assert kwargs["authorize_dispatch"]()
        return UnitResult(locator_key=kwargs["source_locator"], ordinal=1, kind="ocr_unit", raw_text="Readable authored frame text.",
            normalized_text="Readable authored frame text.", method="ocr.space:eng", state="complete", coverage="complete")
    with patch("apps.documents.processing.ocr_unit", side_effect=returned):
        result = api.process_run(run.pk, expected_generation=run.generation)
    assert result["status"] == "pending"
    assert run.units.filter(source_metadata__ocr_attempted=True).count() == 1
    assert run.units.count() == 2


def test_legacy_file_without_checksum_gets_verified_run_hash(users, settings, tmp_path):
    api = service()
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    document = Document.objects.create(title="Legacy source", office=users["med"].office, uploaded_by=users["med"], year=2026)
    payload = b"Legacy bytes whose checksum was never recorded."
    root = DocumentFile.objects.create(document=document, file=SimpleUploadedFile("legacy.txt", payload), original_name="legacy.txt")
    run = api.request_processing(document, user=users["med"])[0]
    api.process_run(run.pk, expected_generation=run.generation)
    run.refresh_from_db()
    root.refresh_from_db()
    assert run.content_hash == hashlib.sha256(payload).hexdigest()
    assert root.checksum == ""  # Never rewrite immutable unknown provenance.


def test_changed_language_settings_do_not_reuse_old_processing_intent(source, users):
    api = service()
    document, _root = source
    run = api.request_processing(document, user=users["med"])[0]
    Document.objects.filter(pk=document.pk).update(ocr_language="fil")
    result = api.process_run(run.pk, expected_generation=run.generation)
    assert result["status"] == "blocked"
    assert not run.units.exists()
