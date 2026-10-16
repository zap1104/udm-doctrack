"""External workflow notifications preserve record-level authorization."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.core import notifications
from apps.core.models import AuditLog, Notification
from apps.core.utils import log_action


def test_external_reference_is_forwarded_without_claiming_a_legacy_record():
    office = SimpleNamespace(pk=1)
    record = SimpleNamespace(pk=2)
    with patch.object(Notification.objects, "create") as create:
        notifications.notify_office(
            office, kind=Notification.Kind.ROUTED, title="External slip", message="Needs receipt",
            url="/tracking/external/2/", external_record=record,
        )
    assert create.call_args.kwargs["external_record"] is record
    assert create.call_args.kwargs["tracking_record"] is None


def _case(users, offices):
    from apps.tracking.external_services import create_record, route_record

    record = create_record(user=users["med_admin"], title="External office correspondence")
    route_record(record, user=users["med_admin"], expected_revision=record.revision, to_office=offices["HR"])
    return record


@pytest.mark.django_db
def test_external_notice_cannot_reveal_a_case_to_an_uninvolved_office(users, offices):
    record = _case(users, offices)
    allowed = notifications.notify_office(
        offices["HR"], kind=Notification.Kind.ROUTED, title="External case", message="Visible",
        external_record=record,
    )
    forbidden = notifications.notify_office(
        offices["SUP"], kind=Notification.Kind.ROUTED, title="External case", message="Must stay hidden",
        external_record=record,
    )
    assert Notification.objects.visible_to(users["hr"]).filter(pk=allowed.pk).exists()
    routed = Notification.objects.get(external_record=record, kind=Notification.Kind.ROUTED, office=offices["HR"], title="External document awaiting receipt")
    assert routed.url == record.get_absolute_url()
    assert not Notification.objects.visible_to(users["sup"]).filter(pk=forbidden.pk).exists()
    assert not Notification.objects.visible_to(users["sup_admin"]).filter(pk=forbidden.pk).exists()


@pytest.mark.django_db
def test_resolving_external_notices_does_not_touch_matching_legacy_ids(users, offices):
    record = _case(users, offices)
    notice = notifications.notify_office(
        offices["MED"], kind=Notification.Kind.RECEIVED, title="External", message="Received",
        external_record=record,
    )
    ordinary = notifications.notify_office(
        offices["MED"], kind=Notification.Kind.RECEIVED, title="Ordinary", message="Independent",
    )
    notifications.resolve_for_external_record(record)
    notice.refresh_from_db()
    ordinary.refresh_from_db()
    assert notice.resolved_at is not None
    assert ordinary.resolved_at is None


@pytest.mark.django_db
def test_external_audit_uses_managing_office_not_physical_custodian(users, offices):
    from apps.core.views import audit_entries_for
    from apps.tracking.external_services import confirm_receipt

    record = _case(users, offices)
    confirm_receipt(record, user=users["hr"], expected_revision=record.revision)
    entry = log_action(AuditLog.Action.UPDATE, "External correction", actor=users["med_admin"], target=record)
    assert entry.extra["audit_target_office_id"] == offices["MED"].pk
    assert audit_entries_for(users["med_admin"]).filter(pk=entry.pk).exists()
    assert not audit_entries_for(users["sup_admin"]).filter(pk=entry.pk).exists()


@pytest.mark.django_db
def test_external_followup_is_idempotent_and_legacy_chase_does_not_resolve_it(users, offices):
    from datetime import timedelta

    from django.utils import timezone

    from apps.core import tasks
    from apps.tracking.external_services import create_external_office, create_record, route_record

    external = create_external_office(user=users["med_admin"], name="Dean's Office")
    record = create_record(user=users["med_admin"], title="Paper slip", from_external_office=external)
    route_record(record, user=users["med_admin"], expected_revision=record.revision,
                 to_office=offices["HR"], sent_at=timezone.now() - timedelta(days=10))
    assert tasks.chase_external_followups()["unreceived"] == 1
    assert tasks.chase_external_followups()["unreceived"] == 0
    notice = Notification.objects.get(external_record=record, kind=Notification.Kind.UNRECEIVED,
                                      office=offices["MED"])
    tasks.chase_unreceived_and_overdue()
    notice.refresh_from_db()
    assert notice.resolved_at is None


@pytest.mark.django_db
def test_external_case_print_audit_checks_record_access(client, users, offices):
    from django.urls import reverse

    record = _case(users, offices)
    data = {"target_type": "ExternalTrackingRecord", "target_id": str(record.pk), "label": "external routing slip"}
    client.force_login(users["viewer"])
    assert client.post(reverse("core:log_print"), data).status_code == 200
    client.force_login(users["sup"])
    assert client.post(reverse("core:log_print"), data).status_code == 404


@pytest.mark.django_db
def test_external_repository_print_audit_is_not_restricted_to_ovpa_collection(client, users, offices):
    from django.urls import reverse

    from apps.documents.models import Collection, Document

    document = Document.objects.create(title="External paper", office=offices["MED"], year=2026,
                                       collection=Collection.EXTERNAL, uploaded_by=users["med_admin"])
    client.force_login(users["viewer"])
    response = client.post(reverse("core:log_print"), {"target_type": "Document", "target_id": str(document.pk)})
    assert response.status_code == 200


@pytest.mark.django_db
def test_completion_resolves_overdue_and_notifies_the_managing_office(users, offices):
    from apps.tracking.external_services import complete_record, confirm_receipt

    record = _case(users, offices)
    confirm_receipt(record, user=users["hr"], expected_revision=record.revision)
    overdue = notifications.notify_office(offices["HR"], kind=Notification.Kind.OVERDUE,
                                         title="External overdue", message="Action needed", external_record=record)
    complete_record(record, user=users["hr"], expected_revision=record.revision)
    overdue.refresh_from_db()
    assert overdue.resolved_at is not None
    filing = Notification.objects.get(external_record=record, kind=Notification.Kind.COMPLETED,
                                       resolved_at__isnull=True)
    assert filing.office_id == offices["MED"].pk
    assert filing.url == record.get_absolute_url()
