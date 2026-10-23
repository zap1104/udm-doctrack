"""Defense audit: preserve filed ownership and refuse ambiguous scope filters."""

import pytest
from django.http import QueryDict
from django.urls import reverse

from apps.accounts.models import User
from apps.documents.models import Document
from apps.tracking import external_services
from apps.tracking.models import ExternalTrackingRecord, Status

pytestmark = pytest.mark.django_db


def test_unsaved_external_case_cannot_inherit_an_unrelated_repository_office(users, offices):
    Document.objects.create(title="Unrelated repository entry", office=offices["HR"], year=2026)
    record = ExternalTrackingRecord(managing_office=offices["MED"], created_by=users["med_admin"])
    assert record.responsible_office_id == offices["MED"].pk


def test_existing_filing_owner_can_access_history_without_a_routing_participation(users, offices):
    owner = User.objects.create_user(username="legacy_filing_admin", office=offices["HR"], role="ADMIN")
    record = external_services.create_record(user=users["med_admin"], title="Legacy filed ownership")
    record.status = Status.COMPLETED
    record.save(update_fields=["status"])
    Document.objects.create(
        title=record.title, collection="EXTERNAL", office=offices["HR"], year=2026,
        external_tracking_record=record, uploaded_by=users["admin"],
    )
    assert record.responsible_office_id == offices["HR"].pk
    assert record.can_manage(owner)
    assert type(record).objects.visible_to(owner).filter(pk=record.pk).exists()
    external_services.annotate_record(
        record, user=owner, expected_revision=record.revision, message="Preserve the original filing owner"
    )
    assert record.activities.filter(event="ANNOTATION", actor=owner).exists()
    assert not record.can_manage(users["med_admin"])


@pytest.mark.parametrize("field", ["office", "external_office", "q", "status", "direction", "overdue"])
def test_repeated_external_filters_return_explicit_error_and_no_rows(client, users, offices, field):
    external_services.create_record(user=users["med_admin"], title="Scope guard record")
    values = {
        "office": [str(offices["MED"].pk)] * 2,
        "external_office": ["1", "2"],
        "q": ["Scope", "guard"],
        "status": [Status.DRAFT, Status.RECEIVED],
        "direction": ["incoming", "outgoing"],
        "overdue": ["yes", "no"],
    }
    params = QueryDict(mutable=True)
    params.setlist(field, values[field])
    client.force_login(users["med_admin"])
    response = client.get(reverse("tracking:external_list"), params)
    assert response.status_code == 400
    assert response.context["filter_errors"]
    assert response.context["total_count"] == 0
