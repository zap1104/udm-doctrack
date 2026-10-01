"""Repository aggregates and pending lists must obey object visibility."""

from datetime import date

import pytest
from django.utils import timezone

from apps.core.models import Tag
from apps.documents.models import AccessLevel, Document, DocumentAccessGrant, Source
from apps.tracking.models import Status, TrackingRecord


@pytest.fixture
def repository_records(users, offices, memo_type):
    visible = Document.objects.create(title="Visible office file", office=offices["MED"], uploaded_by=users["med"], source=Source.UPLOAD, document_type=memo_type, year=2026, document_date=date(2026, 9, 1))
    hidden = Document.objects.create(title="Hidden office file", office=offices["HR"], uploaded_by=users["hr"], source=Source.UPLOAD, document_type=memo_type, year=2025)
    restricted = Document.objects.create(title="Restricted file", office=offices["MED"], uploaded_by=users["admin"], access_level=AccessLevel.RESTRICTED, year=2026)
    common, _ = Tag.get_or_create_by_name("Shared tag")
    secret, _ = Tag.get_or_create_by_name("Hidden confidential tag")
    visible.tags.add(common)
    hidden.tags.add(common, secret)
    Tag.objects.filter(pk=common.pk).update(usage_count=987)
    pending = TrackingRecord.objects.create(subject="Ready to file", tracking_number="REPO-PENDING-MED", originating_office=offices["MED"], current_office=offices["MED"], created_by=users["med"], completed_by=users["med"], completed_at=timezone.now(), status=Status.COMPLETED_PENDING_UPLOAD, document_type=memo_type)
    hidden_pending = TrackingRecord.objects.create(subject="Hidden pending record", tracking_number="REPO-PENDING-HR", originating_office=offices["HR"], current_office=offices["HR"], created_by=users["hr"], completed_at=timezone.now(), status=Status.COMPLETED_PENDING_UPLOAD)
    return visible, hidden, restricted, pending, hidden_pending


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["med", "med_admin", "viewer"])
def test_repository_rows_folder_counts_and_tags_are_scoped(client, users, repository_records, role):
    client.force_login(users[role])
    response = client.get("/documents/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "Visible office file" in body and "Ready to file" in body
    for secret in ("Hidden office file", "Restricted file", "Hidden confidential tag", "REPO-PENDING-HR"):
        assert secret not in body
    assert response.context["all_count"] == response.context["filed_count"] == 1
    assert response.context["pending_count"] == 1
    assert list(response.context["popular_tags"])[0].visible_usage == 1
    assert len(response.context["smart_folders"]) == 1
    assert ("Review &amp; file" in body) == (role == "med_admin")


@pytest.mark.django_db
def test_pending_queue_has_its_own_pagination_and_search(client, users, offices, repository_records):
    client.force_login(users["med"])
    for index in range(12):
        TrackingRecord.objects.create(subject=f"Queued memo {index}", tracking_number=f"QUEUE-{index}", originating_office=offices["MED"], created_by=users["med"], status=Status.COMPLETED_PENDING_UPLOAD, completed_at=timezone.now())
    response = client.get("/documents/?view=pending&per_page=10")
    assert response.context["total"] == 13
    assert response.context["smart_folders"][0]["pending_count"] == 13
    assert len(response.context["pending_upload"]) == 10
    assert response.context["documents"] == []
    searched = client.get("/documents/?view=pending&q=QUEUE-11")
    assert searched.context["total"] == 1
    assert searched.context["pending_upload"][0].tracking_number == "QUEUE-11"


@pytest.mark.django_db
def test_private_folder_parameter_does_not_disclose_office_or_documents(client, users, offices, repository_records):
    client.force_login(users["med"])
    for view in ("", "pending"):
        response = client.get(f"/documents/?office={offices['HR'].pk}&view={view}")
        assert response.context["total"] == 0
        assert response.context["selected_office"] is None
        body = response.content.decode()
        assert "Hidden office file" not in body and "Hidden pending record" not in body
        assert offices["HR"].name not in body


@pytest.mark.django_db
def test_explicit_document_grant_changes_folder_and_result_counts(client, users, repository_records):
    restricted = repository_records[2]
    DocumentAccessGrant.objects.create(document=restricted, user=users["med"], granted_by=users["admin"])
    client.force_login(users["med"])
    response = client.get("/documents/")
    assert response.context["all_count"] == 2
    assert "Restricted file" in response.content.decode()


@pytest.mark.django_db
def test_pending_folder_exists_even_before_its_first_filed_document(client, users, offices, repository_records):
    client.force_login(users["hr"])
    Document.objects.filter(office=offices["HR"]).update(is_active=False)
    response = client.get("/documents/?view=pending")
    assert response.context["all_count"] == 0
    assert response.context["pending_count"] == 1
    assert response.context["smart_folders"][0]["total"] == 0
    assert response.context["smart_folders"][0]["pending_count"] == 1


@pytest.mark.django_db
def test_inactive_accounts_have_no_repository_or_tracking_queryset(users, repository_records):
    user = users["med"]
    user.is_active = False
    assert not Document.objects.visible_to(user).exists()
    assert not TrackingRecord.objects.visible_to(user).exists()
