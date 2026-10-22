"""Both upload modes avoid provider I/O under database mutation locks."""
from importlib.util import find_spec
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection

from apps.documents.processing_contracts import ExtractionPolicy, UnitBatch, UnitResult
from apps.documents.services import ingest_upload

pytestmark = pytest.mark.django_db(transaction=True)


def test_synchronous_versioned_upload_reads_after_commit(users, settings, tmp_path):
    assert find_spec("apps.documents.processing"), "Recoverable processing service is missing"
    from apps.documents.source_readers import read_source_units
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    observed = []
    def inspect(*args, **kwargs):
        observed.append(connection.in_atomic_block)
        return read_source_units(*args, **kwargs)
    with patch("apps.documents.processing.read_source_units", side_effect=inspect):
        document, _suggestion = ingest_upload(user=users["med"], uploaded_file=SimpleUploadedFile("plain.txt", b"Readable complete source text about maintenance."))
    assert observed == [False]
    document.refresh_from_db()
    assert "maintenance" in document.ocr_text
    assert document.extraction_runs.count() == 1


def test_generation_change_during_reader_keeps_newer_text(users, settings, tmp_path):
    assert find_spec("apps.documents.processing"), "Recoverable processing service is missing"
    from apps.documents import processing
    from apps.documents.models import Document, DocumentFile
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    document = Document.objects.create(title="Race", office=users["med"].office, uploaded_by=users["med"], year=2026)
    DocumentFile.objects.create(document=document, file=SimpleUploadedFile("race.txt", b"Old text"), original_name="race.txt")
    run = processing.request_processing(document, user=users["med"])[0]
    def changed(*args, **kwargs):
        Document.objects.filter(pk=document.pk).update(extraction_generation=run.generation+1, ocr_text="Newer exact content")
        return UnitBatch((UnitResult(locator_key="text:1", ordinal=1, kind="text", normalized_text="Old text", state="complete", coverage="complete"),), 1, policy_digest=ExtractionPolicy().digest)
    with patch("apps.documents.processing.read_source_units", side_effect=changed):
        processing.process_run(run.pk, expected_generation=run.generation)
    document.refresh_from_db()
    assert document.ocr_text == "Newer exact content"
    assert not run.units.exists()


def test_filing_admin_local_extraction_does_not_require_foreign_repository_access(users, offices, settings, tmp_path):
    assert find_spec("apps.documents.processing"), "Recoverable processing service is missing"
    from apps.documents.models import Document
    from apps.tracking.services import (
        approve_upload,
        complete_record,
        confirm_receipt,
        create_draft_record,
        route_record,
    )

    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    record = create_draft_record(user=users["med"], subject="Interoffice unit filing", instructions="Review")
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    complete_record(record, user=users["sup"])
    document = approve_upload(record, user=users["sup_admin"])
    assert isinstance(document, Document)
    assert not document.can_user_view(users["sup_admin"])
    assert document.tracking_record_id == record.pk
    document.refresh_from_db()
    assert document.ocr_status not in {"PENDING", "RUNNING"}


def test_physical_pdf_page_metrics_remain_consistent(users, settings, tmp_path):
    from tests.intelligence_fixtures import pdf_bytes

    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    document, _suggestion = ingest_upload(user=users["med"], uploaded_file=SimpleUploadedFile("mixed.pdf",
        pdf_bytes(second_text="Late page text"), content_type="application/pdf"))
    document.refresh_from_db()
    assert document.primary_file.page_count == 2
    assert document.page_count == 2


def test_filing_keeps_routing_context_searchable_after_file_extraction(users, offices, settings, tmp_path):
    from apps.tracking.services import (
        approve_upload,
        attach_files,
        complete_record,
        confirm_receipt,
        create_draft_record,
        route_record,
    )

    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    record = create_draft_record(user=users["med"], subject="Context retention", instructions="Original routing instruction canary")
    attach_files(record, [SimpleUploadedFile("final.txt", b"Current final attachment contents.")], user=users["med"])
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    complete_record(record, user=users["sup"], note="Different completion note")
    document = approve_upload(record, user=users["sup_admin"])
    document.refresh_from_db()
    assert "Original routing instruction canary" in document.ocr_text
    assert "Current final attachment contents" in document.ocr_text
