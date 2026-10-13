"""Deletion review preserves history and closes delayed/direct access paths."""

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import AuditLog, Notification
from apps.core.notifications import mark_all_read, unread_count
from apps.documents import services
from apps.documents.extraction import ExtractionResult
from apps.documents.models import Document, DocumentFile, MetadataSuggestion, OcrStatus, Source
from apps.documents.tasks import extract_document_task
from apps.search.services import search_documents
from apps.tracking.models import Attachment, Status, TrackingRecord
from apps.tracking.services import (
    approve_upload,
    complete_record,
    confirm_receipt,
    create_draft_record,
    route_record,
)

pytestmark = pytest.mark.django_db


def _state(document):
    return {
        "updated_at": document.updated_at.isoformat(),
        "retention_until": document.retention_until.isoformat() if document.retention_until else None,
        "is_active": document.is_active,
    }


def _remove(document, actor):
    return services.review_document_deletion(
        document, user=actor, decision="DELETE", reason="Approved repository removal",
        confirmed=True, expected_state=_state(document),
    )


@pytest.fixture
def review_document(users, memo_type, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    document = Document.objects.create(
        title="Review safety document", office=users["med"].office,
        document_type=memo_type, uploaded_by=users["med"],
        retention_until=timezone.localdate(), ocr_text="Existing searchable text",
        ocr_status=OcrStatus.DONE, page_count=2,
    )
    DocumentFile.objects.create(
        document=document, file=SimpleUploadedFile("review.txt", b"Existing searchable text"),
        original_name="review.txt", page_count=2, extracted_chars=24, is_primary=True,
    )
    document.rebuild_index()
    return document


@pytest.mark.parametrize("actor", [None, "med_admin", "admin"])
def test_removed_document_never_starts_queued_extraction(review_document, users, actor):
    _remove(review_document, users["med_admin"])
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(text="Unexpected", pages=9, status="DONE")) as extractor:
        result = extract_document_task(
            review_document.pk, user_id=users[actor].pk if actor else None,
        )
    extractor.assert_not_called()
    review_document.refresh_from_db()
    assert result["status"] == OcrStatus.SKIPPED
    assert not review_document.is_active
    assert review_document.ocr_text == "Existing searchable text"
    assert review_document.suggestions.count() == 0


def test_removed_document_cannot_use_the_filing_extraction_exception(review_document, users):
    record = TrackingRecord.objects.create(
        subject="Finished interoffice record", instructions="For filing",
        originating_office=users["med"].office, current_office=users["sup"].office,
        created_by=users["med"], approved_by=users["sup_admin"],
        status=Status.COMPLETED, is_archived=True,
    )
    review_document.tracking_record = record
    review_document.source = Source.DTS
    review_document.save(update_fields=["tracking_record", "source", "updated_at"])
    _remove(review_document, users["med_admin"])
    with patch("apps.documents.tasks.extract_document_text", return_value=ExtractionResult(text="Unexpected", pages=9, status="DONE")) as extractor:
        result = extract_document_task(review_document.pk, user_id=users["sup_admin"].pk)
    extractor.assert_not_called()
    assert result["status"] == OcrStatus.SKIPPED
    record.refresh_from_db()
    assert record.status == Status.COMPLETED and record.is_archived


def test_removal_during_extraction_cannot_commit_new_text_or_file_metadata(review_document, users):
    original_file = review_document.files.get()

    def remove_during_extraction(*args, **kwargs):
        current = Document.objects.get(pk=review_document.pk)
        _remove(current, users["med_admin"])
        return ExtractionResult(text="New processing output", pages=9, status="DONE")

    with patch("apps.documents.tasks.extract_document_text", side_effect=remove_during_extraction):
        result = extract_document_task(review_document.pk, user_id=users["med"].pk, replace=True)
    review_document.refresh_from_db()
    original_file.refresh_from_db()
    assert result["status"] == OcrStatus.SKIPPED
    assert not review_document.is_active
    assert review_document.ocr_text == "Existing searchable text"
    assert review_document.page_count == original_file.page_count == 2
    assert original_file.extracted_chars == 24
    assert review_document.suggestions.count() == 0
    assert review_document.deletion_reviews.count() == 1


@pytest.mark.parametrize("changes", [
    {"is_active": False}, {"role": "USER"}, {"role": "VIEWER"}, {"office": None},
])
def test_review_reloads_stale_administrator_permissions(review_document, users, changes):
    actor = users["med_admin"]
    old_state = _state(review_document)
    User.objects.filter(pk=actor.pk).update(**changes)
    with pytest.raises(PermissionDenied):
        services.review_document_deletion(
            review_document, user=actor, decision="DELETE", reason="Stale administrator",
            confirmed=True, expected_state=old_state,
        )
    review_document.refresh_from_db()
    assert review_document.is_active
    assert review_document.deletion_reviews.count() == 0


def test_review_reloads_ownership_even_when_the_client_has_a_matching_timestamp(review_document, users):
    old_state = _state(review_document)
    # Direct updates do not change updated_at, so checking the submitted version
    # alone cannot authorize the old owning office after a transfer.
    Document.objects.filter(pk=review_document.pk).update(office=users["sup"].office)
    with pytest.raises(PermissionDenied):
        services.review_document_deletion(
            review_document, user=users["med_admin"], decision="DELETE", reason="Old owning office",
            confirmed=True, expected_state=old_state,
        )
    review_document.refresh_from_db()
    assert review_document.is_active
    assert review_document.office_id == users["sup"].office_id
    assert review_document.deletion_reviews.count() == 0


def test_failed_history_write_rolls_back_the_removal(review_document, users):
    old_state = _state(review_document)
    review_model = review_document.deletion_reviews.model
    with patch.object(review_model.objects, "create", side_effect=RuntimeError("History unavailable")):
        with pytest.raises(RuntimeError, match="History unavailable"):
            _remove(review_document, users["med_admin"])
    review_document.refresh_from_db()
    assert _state(review_document) == old_state
    assert review_document.deletion_reviews.count() == 0
    assert not AuditLog.objects.filter(
        target_type="Document", target_id=str(review_document.pk), action=AuditLog.Action.DELETE,
    ).exists()


def test_repository_removal_preserves_filed_tracking_identity_and_original_files(users, memo_type, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False
    record = create_draft_record(
        user=users["med"], subject="Filed document to review", instructions="For filing",
        document_type=memo_type,
    )
    original = Attachment.objects.create(
        record=record, file=SimpleUploadedFile("tracking-proof.txt", b"Original tracking contents"),
        original_name="tracking-proof.txt", uploaded_by=users["med"],
    )
    route_record(record, [users["sup"].office], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    complete_record(record, user=users["sup"])
    with patch("apps.documents.services.extract_document_text", return_value=ExtractionResult(
        text="Original tracking contents", pages=1, status="DONE",
    )):
        document = approve_upload(record, user=users["sup_admin"])
    document.retention_until = timezone.localdate()
    document.save(update_fields=["retention_until", "updated_at"])
    record.refresh_from_db()
    original_state = (record.status, record.completed_at, record.approved_at, record.approved_by_id)
    activities = set(record.activities.values_list("pk", flat=True))
    copied_files = set(document.files.values_list("pk", flat=True))

    _remove(document, users["med_admin"])

    record.refresh_from_db()
    assert (record.status, record.completed_at, record.approved_at, record.approved_by_id) == original_state
    assert record.status == Status.COMPLETED and record.is_archived
    assert record.archived_document.pk == document.pk
    assert activities <= set(record.activities.values_list("pk", flat=True))
    assert set(document.files.values_list("pk", flat=True)) == copied_files
    assert original.file.storage.exists(original.file.name)
    assert all(file.file.storage.exists(file.file.name) for file in document.files.all())
    assert not TrackingRecord.objects.pending_filing().filter(pk=record.pk).exists()


@pytest.mark.parametrize("role", ["admin", "med_admin", "med", "viewer"])
def test_repository_counts_charts_search_and_drilldowns_agree_after_removal(review_document, users, client, role):
    retained = Document.objects.create(
        title="Review safety retained", office=review_document.office,
        document_type=review_document.document_type,
        retention_until=timezone.localdate() + timedelta(days=30),
    )
    retained.rebuild_index()
    client.force_login(users[role])
    assert client.get("/documents/reports/").context["total_documents"] == 2
    _remove(review_document, users["med_admin"])

    repository = client.get("/documents/").context
    reports = client.get("/documents/reports/").context
    dashboard = client.get("/").context
    expected = {retained.pk}
    assert {document.pk for document in repository["page_obj"]} == expected
    assert repository["total"] == reports["total_documents"] == dashboard["repository_donut"]["total"] == 1
    assert reports["document_months"]["total"] == 1
    assert sum(row["total"] for row in reports["document_months"]["rows"]) == 1
    assert sum(row["total"] for row in reports["document_types"]) == 1
    for status in ("due", "soon", "unscheduled"):
        listed = client.get(reports["retention"][f"{status}_url"]).context["page_obj"].paginator.count
        assert reports["retention"][status] == listed
    assert reports["retention"]["due"] == 0 and reports["retention"]["soon"] == 1
    results = search_documents(user=users[role], query="Review safety", min_relevance=0, log=False)
    assert {result.document.pk for result in results.results} == expected


@pytest.mark.parametrize("role", ["admin", "med_admin", "med", "viewer"])
def test_removed_document_notifications_do_not_leak_through_lists_badges_or_read_actions(review_document, users, client, role):
    actor = users[role]
    notice = Notification.objects.create(
        office=actor.office, document=review_document, kind=Notification.Kind.SHARED,
        title="Removed document notification", message=review_document.title,
        url=review_document.get_absolute_url(),
    )
    active_notice = Notification.objects.create(
        office=actor.office, kind=Notification.Kind.SHARED,
        title="Independent office notice", message="Still available",
    )
    assert unread_count(actor) == 2
    _remove(review_document, users["med_admin"])
    client.force_login(actor)
    response = client.get("/notifications/")
    assert response.status_code == 200
    assert {row.pk for row in response.context["notifications"]} == {active_notice.pk}
    assert notice.title not in response.content.decode()
    assert unread_count(actor) == 1
    assert client.post(reverse("core:notification_read", args=[notice.pk])).status_code == 404
    assert mark_all_read(actor) == 1
    assert unread_count(actor) == 0


def test_future_training_exports_exclude_removed_records_without_erasing_examples(review_document, users, tmp_path):
    retained = Document.objects.create(title="Retained training example", office=review_document.office)
    for document in (review_document, retained):
        MetadataSuggestion.objects.create(
            document=document, text_sample="Training text " * 20,
            suggested={"title": document.title}, accepted={"title": document.title},
            reviewed_by=users["med_admin"], reviewed_at=timezone.now(),
        )
    _remove(review_document, users["med_admin"])
    output = tmp_path / "review-training.jsonl"
    call_command("export_training_data", out=str(output))
    examples = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert {example["document_id"] for example in examples} == {retained.pk}
    assert review_document.suggestions.count() == 1
