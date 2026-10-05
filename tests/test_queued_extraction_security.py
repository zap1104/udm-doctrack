"""Delayed processing re-checks role, access, office and external OCR consent."""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import User
from apps.documents.extraction import ExtractionResult
from apps.documents.models import AccessLevel, Document, DocumentFile, OcrStatus
from apps.documents.tasks import extract_document_task
from apps.tracking.services import approve_upload, complete_record, confirm_receipt, create_draft_record, route_record

pytestmark = pytest.mark.django_db


@pytest.fixture
def queued_document(users, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    document = Document.objects.create(title="Queued private file", year=2026, office=users["med"].office, uploaded_by=users["admin"])
    DocumentFile.objects.create(document=document, file=SimpleUploadedFile("note.txt", b"Internal text"), original_name="note.txt")
    return document


@pytest.mark.parametrize("change", ["suspend", "viewer", "unknown", "unassigned", "office-transfer", "restricted", "uploader-office-transfer", "uploader-document-transfer"])
def test_delayed_jobs_cannot_keep_revoked_permission(users, queued_document, change):
    actor = users["med"]
    updates = {"suspend": {"is_active": False}, "viewer": {"role": "VIEWER"}, "unknown": {"role": "UNKNOWN"},
               "unassigned": {"office": None}, "office-transfer": {"office": users["sup"].office}}
    if change.startswith("uploader-"):
        queued_document.uploaded_by = actor
        if change == "uploader-document-transfer":
            queued_document.office = users["sup"].office
        else:
            User.objects.filter(pk=actor.pk).update(office=users["sup"].office)
        queued_document.save()
    elif change == "restricted":
        queued_document.access_level = AccessLevel.RESTRICTED
        queued_document.save()
    else:
        User.objects.filter(pk=actor.pk).update(**updates[change])
    with patch("apps.documents.tasks.extract_document_text") as extractor:
        result = extract_document_task(queued_document.pk, user_id=actor.pk)
    extractor.assert_not_called()
    queued_document.refresh_from_db()
    assert result["status"] == OcrStatus.SKIPPED
    assert queued_document.ocr_status == OcrStatus.SKIPPED
    assert not queued_document.ocr_text


def test_allowed_delayed_job_uses_current_consent(users, queued_document):
    queued_document.allow_external_ocr = False
    queued_document.save()
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(text="Permitted local text", status="DONE")) as extractor:
        result = extract_document_task(queued_document.pk, user_id=users["med"].pk)
    assert result["status"] == OcrStatus.DONE
    assert extractor.call_args.kwargs["allow_external_ocr"] is False


def test_large_page_totals_do_not_overflow_repository_fields(queued_document):
    # PostgreSQL smallint stopped at 32767 even though a page count can be
    # the total of many attachments. Processing limits remain separate.
    queued_document.page_count = 50_000
    queued_document.save(update_fields=["page_count"])
    document_file = queued_document.files.first()
    document_file.page_count = 40_000
    document_file.save(update_fields=["page_count"])
    queued_document.refresh_from_db()
    document_file.refresh_from_db()
    assert queued_document.page_count == 50_000
    assert document_file.page_count == 40_000


def test_filing_admin_can_finish_local_extraction_without_foreign_repository_access(users, offices, settings):
    record = create_draft_record(user=users["med"], subject="Interoffice filing", instructions="Review")
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    complete_record(record, user=users["sup"])
    document = approve_upload(record, user=users["sup_admin"])
    assert not document.can_user_view(users["sup_admin"])
    result = extract_document_task(document.pk, user_id=users["sup_admin"].pk)
    assert result["status"] in {OcrStatus.DONE, OcrStatus.EMPTY}
    assert not document.can_user_view(users["sup_admin"])
