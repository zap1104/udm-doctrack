"""Coverage is independent of terminal status and bound to immutable bytes."""
from dataclasses import replace

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.documents import models


def contracts():
    # A missing implementation fails as an explicit capability assertion.
    from importlib.util import find_spec
    assert find_spec("apps.documents.processing_contracts"), "Version-bound processing contracts are missing"
    from apps.documents import processing_contracts
    return processing_contracts


@pytest.fixture
def document(users, offices):
    return models.Document.objects.create(title="Contract evidence", office=offices["MED"],
        uploaded_by=users["med"], year=2026, ocr_text="Previously read text", ocr_status="DONE")


@pytest.fixture
def root_file(document):
    return models.DocumentFile.objects.create(document=document, file="fixtures/evidence.txt",
        original_name="evidence.txt", checksum="a" * 64, size=12)


@pytest.mark.django_db
def test_legacy_text_does_not_imply_complete_coverage(document):
    summary = contracts().coverage_summary(document)
    assert summary["coverage"] == "unknown"
    assert summary["expected"] is None
    assert summary["readable"] == 0
    assert summary["reasons"]


@pytest.mark.django_db
def test_duplicate_run_and_locator_are_rejected(root_file, document):
    api = contracts()
    version = api.resolve_file_version(root_file)
    run = models.ExtractionRun.objects.create(document=document, file_version=version,
        generation=0, settings_digest="b" * 64)
    with pytest.raises(IntegrityError), transaction.atomic():
        models.ExtractionRun.objects.create(document=document, file_version=version,
            generation=0, settings_digest="b" * 64)
    models.ExtractionUnit.objects.create(run=run, locator_key="text:1", ordinal=1, kind="text")
    with pytest.raises(IntegrityError), transaction.atomic():
        models.ExtractionUnit.objects.create(run=run, locator_key="text:1", ordinal=2, kind="text")


@pytest.mark.django_db
def test_current_snapshot_must_match_root_checksum(root_file):
    api = contracts()
    first = api.resolve_file_version(root_file)
    assert first.pk == root_file.versions.get().pk
    models.DocumentFileVersion.objects.create(document_file=root_file, version_number=2,
        file=root_file.file.name, original_name="different.txt", checksum="c" * 64)
    with pytest.raises(ValidationError, match="current"):
        api.resolve_file_version(root_file)


def test_confidence_absence_is_null():
    unit = contracts().UnitResult(locator_key="pdf:page:1", ordinal=1, kind="pdf_page")
    assert unit.provider_confidence is None


def test_partial_work_never_adapts_to_green_done():
    api = contracts()
    readable = api.UnitResult(locator_key="pdf:page:1", ordinal=1, kind="pdf_page",
        raw_text="Readable native text", normalized_text="Readable native text", state="complete", coverage="complete")
    blocked = api.UnitResult(locator_key="pdf:page:2", ordinal=2, kind="pdf_page",
        state="blocked", coverage="partial", warnings=("OCR not authorized",))
    batch = api.UnitBatch(units=(readable, blocked), expected_units=2)
    assert api.legacy_ocr_status(batch) != "DONE"
    assert api.legacy_ocr_status(replace(batch, units=(readable,), expected_units=1)) == "DONE"
    assert api.legacy_ocr_status(replace(batch, units=(readable,), expected_units=2)) != "DONE"


def test_policy_rejects_unbounded_or_over_free_tier_configuration(settings):
    api = contracts()
    settings.PROCESSING_MAX_UNIT_CHARS = 0
    with pytest.raises(ValidationError):
        api.ExtractionPolicy.from_settings()
    settings.PROCESSING_MAX_UNIT_CHARS = 500_000
    settings.OCR_SPACE_MAX_REQUEST_BYTES = 1_500_000
    with pytest.raises(ValidationError):
        api.ExtractionPolicy.from_settings()


def test_disabled_rollout_does_not_restrict_legacy_provider_configuration(settings):
    from apps.core.checks import processing_configuration

    settings.ENABLE_SOURCE_UNIT_EXTRACTION = False
    settings.OCR_PROVIDER_TIMEOUT_SECONDS = 180
    assert processing_configuration(None) == []
