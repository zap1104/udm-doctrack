"""Human review must preserve files, office boundaries, and an audit trail."""

from datetime import date, timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.documents import services
from apps.documents.models import Document, DocumentFile


@pytest.fixture
def due_document(users, offices, memo_type):
    return Document.objects.create(
        title="Document for deletion review", office=offices["MED"],
        uploaded_by=users["med"], document_type=memo_type,
        retention_until=timezone.localdate(),
    )


def snapshot(document):
    return {"updated_at": document.updated_at.isoformat(),
            "retention_until": document.retention_until.isoformat() if document.retention_until else None,
            "is_active": document.is_active}


@pytest.mark.django_db
def test_cleared_schedule_stays_blank_after_unrelated_save(due_document):
    due_document.retention_until = None
    due_document.save(update_fields=["retention_until", "updated_at"])
    due_document.ocr_notes = "Text extraction note"
    due_document.save(update_fields=["ocr_notes", "updated_at"])
    due_document.refresh_from_db()
    assert due_document.retention_until is None


@pytest.mark.django_db
def test_unrepresentable_default_schedule_does_not_crash(users, offices, memo_type):
    document = Document(title="Very late date", office=offices["MED"],
                        document_type=memo_type, document_date=date(9998, 1, 1))
    assert document.default_retention_until() is None


@pytest.mark.django_db
def test_keep_records_reason_and_moves_document_out_of_due_queue(due_document, users):
    next_date = timezone.localdate() + timedelta(days=120)
    services.review_document_deletion(
        due_document, user=users["med_admin"], decision="KEEP", reason="Needed for office reference",
        next_review_date=next_date, expected_state=snapshot(due_document),
    )
    due_document.refresh_from_db()
    assert due_document.is_active and due_document.retention_until == next_date
    assert not Document.objects.due_for_retention_review().filter(pk=due_document.pk).exists()
    review = due_document.deletion_reviews.get()
    assert review.decision == "KEEP" and review.reason == "Needed for office reference"
    assert review.previous_review_date == timezone.localdate()
    assert review.next_review_date == next_date and review.reviewed_by_id == users["med_admin"].pk


@pytest.mark.django_db
def test_delete_preserves_document_files_and_blocks_direct_access(due_document, users, client):
    file = DocumentFile.objects.create(document=due_document, file="documents/review-proof.txt",
                                       original_name="review-proof.txt", size=10)
    services.review_document_deletion(
        due_document, user=users["med_admin"], decision="DELETE", reason="Approved records disposition",
        confirmed=True, expected_state=snapshot(due_document),
    )
    due_document.refresh_from_db()
    assert not due_document.is_active
    assert DocumentFile.objects.filter(pk=file.pk).exists()
    assert due_document.deletion_reviews.get().decision == "DELETE"
    for role in ("med", "med_admin", "admin", "viewer"):
        client.force_login(users[role])
        assert not due_document.can_user_view(users[role])
        assert not due_document.can_user_edit(users[role])
        assert client.get(due_document.get_absolute_url()).status_code in (403, 404)
        assert client.get(reverse("documents:file_download", args=[file.pk])).status_code in (403, 404)


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["med", "viewer", "sup_admin", "sup"])
def test_only_authorized_administrators_can_decide(due_document, users, role):
    with pytest.raises(PermissionDenied):
        services.review_document_deletion(
            due_document, user=users[role], decision="DELETE", reason="Not authorized",
            confirmed=True, expected_state=snapshot(due_document),
        )
    due_document.refresh_from_db()
    assert due_document.is_active and due_document.deletion_reviews.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("changes", [
    {"confirmed": False}, {"reason": "  "}, {"decision": "INVALID"},
    {"expected_state": {"updated_at": "old"}},
])
def test_invalid_decision_cannot_remove_document(due_document, users, changes):
    data = {"decision": "DELETE", "reason": "Approved", "confirmed": True,
            "expected_state": snapshot(due_document)}
    data.update(changes)
    with pytest.raises(ValidationError):
        services.review_document_deletion(due_document, user=users["admin"], **data)
    due_document.refresh_from_db()
    assert due_document.is_active and due_document.deletion_reviews.count() == 0


@pytest.mark.django_db
def test_future_document_cannot_be_deleted_early(due_document, users):
    due_document.retention_until = timezone.localdate() + timedelta(days=1)
    due_document.save()
    with pytest.raises(ValidationError):
        services.review_document_deletion(
            due_document, user=users["admin"], decision="DELETE", reason="Too early",
            confirmed=True, expected_state=snapshot(due_document),
        )
    assert due_document.deletion_reviews.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("offset", [None, 0, -1])
def test_keep_requires_future_review_date(due_document, users, offset):
    next_date = None if offset is None else timezone.localdate() + timedelta(days=offset)
    with pytest.raises(ValidationError):
        services.review_document_deletion(
            due_document, user=users["admin"], decision="KEEP", reason="Keep",
            next_review_date=next_date, expected_state=snapshot(due_document),
        )


@pytest.mark.django_db
def test_repeat_submission_rejected_and_restore_reuses_same_document(due_document, users):
    old_state = snapshot(due_document)
    services.review_document_deletion(
        due_document, user=users["admin"], decision="DELETE", reason="Approved",
        confirmed=True, expected_state=old_state,
    )
    with pytest.raises(ValidationError):
        services.review_document_deletion(
            due_document, user=users["admin"], decision="DELETE", reason="Repeated",
            confirmed=True, expected_state=old_state,
        )
    services.review_document_deletion(
        due_document, user=users["admin"], decision="RESTORE", reason="Deletion reconsidered",
        next_review_date=timezone.localdate() + timedelta(days=180), expected_state=snapshot(due_document),
    )
    assert due_document.is_active and due_document.deletion_reviews.count() == 2
    assert due_document.can_user_view(users["med"])


@pytest.mark.django_db
def test_review_form_uses_signed_state_and_explicit_post(due_document, users, client):
    client.force_login(users["med_admin"])
    url = reverse("documents:deletion_review", args=[due_document.pk])
    response = client.get(url)
    assert response.status_code == 200 and due_document.is_active
    token = response.context["form"]["state_token"].value()
    invalid = client.post(url, {"decision": "DELETE", "reason": "Approved", "confirmed": "on",
                                "state_token": token + "tampered"})
    assert invalid.status_code == 200 and invalid.context["form"].errors
    due_document.refresh_from_db()
    assert due_document.is_active
    success = client.post(url, {"decision": "DELETE", "reason": "Approved", "confirmed": "on",
                                "state_token": token})
    assert success.status_code == 302
    due_document.refresh_from_db()
    assert not due_document.is_active


@pytest.mark.django_db
def test_deletion_queue_is_scoped_to_administered_office(due_document, users, offices, client):
    other = Document.objects.create(title="Other office due document", office=offices["SUP"],
                                    access_level="OVPA", retention_until=timezone.localdate())
    client.force_login(users["med_admin"])
    url = reverse("documents:deletion_queue")
    response = client.get(url, {"office": offices["SUP"].pk})
    assert other.title not in response.content.decode()
    assert response.context["total"] == 0
    response = client.get(url)
    assert {item.pk for item in response.context["page_obj"]} == {due_document.pk}
    assert client.get(reverse("documents:deletion_review", args=[other.pk])).status_code in (403, 404)


def metadata_data(document, *, include_date=True):
    data = {"title": document.title, "office": document.office_id, "year": document.year,
            "document_type": document.document_type_id, "document_types": [document.document_type_id],
            "access_level": document.access_level, "ocr_language": document.ocr_language}
    if include_date:
        data["retention_until"] = document.retention_until.isoformat() if document.retention_until else ""
    return data


@pytest.mark.django_db
def test_upload_review_preserves_custom_schedule_even_when_older_form_omits_it(due_document, users, client):
    due_document.retention_until = timezone.localdate() + timedelta(days=456)
    due_document.save()
    custom = due_document.retention_until
    client.force_login(users["med"])
    response = client.get(reverse("documents:review", args=[due_document.pk]))
    assert 'name="retention_until"' in response.content.decode()
    assert response.context["form"]["retention_until"].value() == custom
    response = client.post(reverse("documents:review", args=[due_document.pk]), metadata_data(due_document, include_date=False))
    assert response.status_code == 302
    due_document.refresh_from_db()
    assert due_document.retention_until == custom


@pytest.mark.django_db
def test_metadata_edit_can_explicitly_clear_review_date(due_document, users, client):
    client.force_login(users["med"])
    data = metadata_data(due_document)
    data["retention_until"] = ""
    response = client.post(reverse("documents:edit", args=[due_document.pk]), data)
    assert response.status_code == 302
    due_document.refresh_from_db()
    assert due_document.retention_until is None


@pytest.mark.django_db
def test_first_classification_preserves_automatic_schedule(users, offices, memo_type):
    document = Document.objects.create(title="New upload", office=offices["MED"], uploaded_by=users["med"])
    services.save_document_metadata(document, user=users["med"], data={
        "document_type": memo_type, "document_types": [memo_type],
        "document_date": date(2020, 2, 29), "year": 2020, "retention_until": None,
    }, tag_names=[], metadata_values={})
    assert document.retention_until == date(2025, 2, 28)


@pytest.mark.django_db
@pytest.mark.parametrize("offset,status", [(0, "due"), (1, "soon"), (90, "soon"), (91, None)])
def test_review_queue_date_boundaries_agree_with_report_and_repository(due_document, users, client, offset, status):
    due_document.retention_until = timezone.localdate() + timedelta(days=offset)
    due_document.save()
    client.force_login(users["admin"])
    reports = client.get("/documents/reports/").context["retention"]
    for kind in ("due", "soon", "unscheduled"):
        expected = int(kind == status)
        assert reports[kind] == expected
        assert client.get(reports[f"{kind}_url"]).context["total"] == expected
        queue = client.get(reverse("documents:deletion_queue"), {"status": kind})
        assert queue.context["total"] == expected


@pytest.mark.django_db
def test_form_does_not_silently_drop_unrepresentable_initial_schedule(users, offices, memo_type):
    from apps.documents.forms import DocumentMetadataForm

    document = Document.objects.create(title="Unclassified late document", office=offices["MED"])
    data = metadata_data(document)
    data.update(document_type=memo_type.pk, document_types=[memo_type.pk], document_date="9998-01-01")
    form = DocumentMetadataForm(data, instance=document, user=users["admin"])
    assert not form.is_valid()
    assert "retention_until" in form.errors


@pytest.mark.django_db
def test_invalid_calendar_year_does_not_crash_default_schedule(users, offices, memo_type):
    document = Document(title="Imported invalid year", office=offices["MED"], document_type=memo_type, year=10000)
    assert document.default_retention_until() is None


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["soon", "unscheduled", "deleted"])
def test_current_queue_tab_keeps_its_status(due_document, users, client, status):
    from urllib.parse import parse_qs, urlsplit

    client.force_login(users["admin"])
    response = client.get(reverse("documents:deletion_queue"), {"status": status, "q": "Document"})
    assert parse_qs(urlsplit(response.context["queue_links"][status]).query)["status"] == [status]
    assert parse_qs(urlsplit(response.context["queue_links"][status]).query)["q"] == ["Document"]


@pytest.mark.django_db(transaction=True)
def test_two_administrators_cannot_record_conflicting_decisions(users, offices, memo_type):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections, connections

    from apps.accounts.models import User

    document = Document.objects.create(title="Concurrent deletion review", office=offices["MED"],
                                        document_type=memo_type, retention_until=timezone.localdate())
    state = snapshot(document)
    ready = Barrier(2)

    def decide(actor_id, decision):
        close_old_connections()
        try:
            actor = User.objects.get(pk=actor_id)
            current = Document.objects.get(pk=document.pk)
            ready.wait(timeout=10)
            try:
                services.review_document_deletion(
                    current, user=actor, decision=decision, reason="Concurrent administrator decision",
                    confirmed=True, expected_state=state,
                    next_review_date=timezone.localdate() + timedelta(days=180) if decision == "KEEP" else None,
                )
            except ValidationError:
                return "rejected"
            return "recorded"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as workers:
        removal = workers.submit(decide, users["med_admin"].pk, "DELETE")
        keep = workers.submit(decide, users["admin"].pk, "KEEP")
        assert sorted([removal.result(timeout=20), keep.result(timeout=20)]) == ["recorded", "rejected"]
    document.refresh_from_db()
    review = document.deletion_reviews.get()
    assert document.is_active == (review.decision == "KEEP")
