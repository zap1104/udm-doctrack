"""Receipt controls and compound submissions follow the lifecycle rules."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.models import Office
from apps.core.models import AuditLog
from apps.tracking import services
from apps.tracking.models import TrackingRecord

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def private_storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_BACKGROUND_TASKS = False


@pytest.fixture
def sent_record(users, offices):
    record = services.create_draft_record(
        user=users["med"], subject="Document for lifecycle review", instructions="Please review the request.",
    )
    services.route_record(record, [offices["SUP"]], user=users["med"])
    return record


@pytest.fixture
def received_record(sent_record, users):
    services.confirm_receipt(sent_record, user=users["sup"])
    return sent_record


def _new_record_data(offices, **changes):
    data = {
        "subject": "Request for electrical supplies",
        "instructions": "Please review the supply request.",
        "classification": "INTERNAL", "priority": "NORMAL", "requested_action": "2",
        "receiving_offices": [offices["SUP"].pk], "deadline_choice": "none",
    }
    data.update(changes)
    return data


def _invalid_pdf():
    return SimpleUploadedFile("pretend.pdf", b"This is not a PDF.", content_type="application/pdf")


def test_viewer_is_not_offered_individual_or_bulk_receipt(client, users, offices):
    record = services.create_draft_record(
        user=users["sup"], subject="Read-only recipient", instructions="Please review.",
    )
    services.route_record(record, [offices["MED"]], user=users["sup"])
    client.force_login(users["viewer"])

    response = client.get(reverse("tracking:list"))

    assert response.status_code == 200
    assert response.context["can_bulk_receive"] is False
    assert response.context["records"][0].can_confirm_now is False
    assert 'name="record_ids"' not in response.content.decode()
    assert "Confirm Receipt" not in response.content.decode()
    assert client.post(reverse("tracking:bulk_confirm_receipt"), {
        "record_ids": [record.pk], "confirm_custody": "on",
    }).status_code == 403
    assert record.routing_steps.get().received_at is None


@pytest.mark.parametrize("filed", [False, True])
def test_completed_parallel_recipient_has_no_receipt_action(client, users, offices, filed):
    record = services.create_draft_record(
        user=users["med"], subject="Parallel review completed", instructions="Review the request.",
    )
    services.route_record(record, [offices["SUP"], offices["HR"]], user=users["med"])
    services.confirm_receipt(record, user=users["sup"])
    services.complete_record(record, user=users["sup"])
    if filed:
        services.approve_upload(record, user=users["sup_admin"])
    client.force_login(users["hr"])

    response = client.get(record.get_absolute_url())

    assert response.status_code == 200
    assert record.can_user_confirm_receipt(users["hr"]) is False
    assert response.context["can_confirm"] is False
    assert response.context["pending_offices"] == []
    assert "Confirm Document Receipt" not in response.content.decode()
    assert "Waiting for receipt from:" not in response.content.decode()
    services.annotate_can_confirm([record], users["hr"])
    assert record.can_confirm_now is False
    # The historic unreceived step is retained; completion does not invent a receipt.
    assert record.routing_steps.get(to_office=offices["HR"]).received_at is None


def test_rejected_new_draft_attachment_does_not_leave_a_draft_or_creation_history(client, users, offices):
    client.force_login(users["med"])
    before_audits = list(AuditLog.objects.values())

    response = client.post(reverse("tracking:create"), _new_record_data(offices, attachments=_invalid_pdf()))

    assert response.status_code == 200
    assert "do not match" in response.content.decode()
    assert not TrackingRecord.objects.exists()
    assert list(AuditLog.objects.values()) == before_audits


def test_rejected_remark_attachment_does_not_save_the_remark_or_promote_status(client, users, received_record):
    record = received_record
    client.force_login(users["sup"])
    before_record = TrackingRecord.objects.values().get(pk=record.pk)
    before_history = list(record.activities.values())
    before_audits = list(AuditLog.objects.values())

    response = client.post(reverse("tracking:add_remark", args=[record.pk]), {
        "remark": "This remark must not survive a rejected attachment.", "attachments": _invalid_pdf(),
    })

    assert response.status_code == 302
    assert response["Location"] == record.get_absolute_url()
    assert any("do not match" in str(message) for message in response.wsgi_request._messages)
    assert TrackingRecord.objects.values().get(pk=record.pk) == before_record
    assert list(record.activities.values()) == before_history
    assert list(AuditLog.objects.values()) == before_audits
    assert not record.attachments.exists()


def test_failed_forward_does_not_keep_its_attachment(client, users, offices, received_record, monkeypatch):
    record = received_record
    client.force_login(users["sup"])
    before_record = TrackingRecord.objects.values().get(pk=record.pk)
    before_history = list(record.activities.values())
    real_route = services.route_record

    def deactivate_destination_and_route(record, recipients, **kwargs):
        # Reproduce retirement after the form accepted the office, while keeping
        # the real routing validation, upload, and transaction behavior.
        Office.objects.filter(pk=offices["HR"].pk).update(is_active=False)
        recipients = list(Office.objects.filter(pk__in=[office.pk for office in recipients]))
        return real_route(record, recipients, **kwargs)

    monkeypatch.setattr(services, "route_record", deactivate_destination_and_route)
    response = client.post(reverse("tracking:route", args=[record.pk]), {
        "action": "FORWARD", "offices": [offices["HR"].pk], "deadline_choice": "none",
        "attachments": SimpleUploadedFile("response.txt", b"Supporting response."),
    })

    assert response.status_code == 302
    assert any("active receiving office" in str(message) for message in response.wsgi_request._messages)
    assert not record.attachments.exists()
    assert TrackingRecord.objects.values().get(pk=record.pk) == before_record
    assert list(record.activities.values()) == before_history
    assert record.routing_steps.count() == 1


def test_administrator_completion_before_receipt_returns_a_validation_message(client, users, sent_record):
    client.force_login(users["admin"])
    client.raise_request_exception = False
    before_record = TrackingRecord.objects.values().get(pk=sent_record.pk)
    before_history = list(sent_record.activities.values())

    response = client.post(reverse("tracking:complete", args=[sent_record.pk]), {"note": "Too early"})

    assert response.status_code == 302
    assert response["Location"] == sent_record.get_absolute_url()
    assert any("Confirm receipt" in str(message) for message in response.wsgi_request._messages)
    assert TrackingRecord.objects.values().get(pk=sent_record.pk) == before_record
    assert list(sent_record.activities.values()) == before_history


def test_administrator_is_not_offered_completion_before_any_receipt(client, users, sent_record):
    client.force_login(users["admin"])

    response = client.get(sent_record.get_absolute_url())

    assert response.status_code == 200
    assert 'id="tab-complete-button"' not in response.content.decode()
    assert 'id="tab-route-button"' in response.content.decode()


@pytest.mark.parametrize("actor", ["sup", "sup_admin", "admin"])
def test_authorized_completion_remains_available_after_receipt(client, users, received_record, actor):
    client.force_login(users[actor])

    response = client.get(received_record.get_absolute_url())

    assert response.status_code == 200
    assert 'id="tab-complete-button"' in response.content.decode()
    assert 'action="' + reverse("tracking:complete", args=[received_record.pk]) + '"' in response.content.decode()


def test_active_parallel_recipient_can_still_confirm_after_the_first_receipt(client, users, offices):
    record = services.create_draft_record(
        user=users["med"], subject="Parallel receipt still required", instructions="Please review.",
    )
    services.route_record(record, [offices["SUP"], offices["HR"]], user=users["med"])
    services.confirm_receipt(record, user=users["sup"])
    client.force_login(users["hr"])

    response = client.get(record.get_absolute_url())

    assert record.can_user_confirm_receipt(users["hr"]) is True
    assert response.context["can_confirm"] is True
    services.annotate_can_confirm([record], users["hr"])
    assert record.can_confirm_now is True
    receipt = client.post(reverse("tracking:confirm_receipt", args=[record.pk]), {})
    assert receipt.status_code == 302
    assert record.routing_steps.get(to_office=offices["HR"]).received_at is not None


def test_bulk_receipt_race_returns_to_the_dashboard_that_submitted_it(client, users, sent_record, monkeypatch):
    client.force_login(users["sup"])
    real_bulk = services.bulk_confirm_receipts

    def receive_elsewhere_then_bulk(records, **kwargs):
        # Another request confirms after this selection was validated.
        services.confirm_receipt(sent_record, user=users["sup"])
        return real_bulk(records, **kwargs)

    monkeypatch.setattr(services, "bulk_confirm_receipts", receive_elsewhere_then_bulk)
    response = client.post(reverse("tracking:bulk_confirm_receipt"), {
        "record_ids": [sent_record.pk], "confirm_custody": "on", "next": "dashboard",
    })

    assert response.status_code == 302
    assert response["Location"] == reverse("core:dashboard")
    assert sent_record.routing_steps.get().received_at is not None
    assert sent_record.activities.filter(event="RECEIVED").count() == 1


@pytest.mark.parametrize("stage", ["edit", "review"])
@pytest.mark.parametrize("has_office", [True, False])
def test_administrator_draft_recipient_choices_follow_the_actual_origin(client, users, offices, stage, has_office):
    record = services.create_draft_record(
        user=users["med"], subject="Draft prepared by Maintenance", instructions="Please review.",
    )
    administrator = users["admin"]
    if not has_office:
        administrator.office = None
        administrator.save(update_fields=["office"])
    client.force_login(administrator)
    url = f"{reverse('tracking:create')}?draft={record.pk}" if stage == "edit" else reverse("tracking:review", args=[record.pk])

    response = client.get(url)

    assert response.status_code == 200
    offered = set(response.context["form"].fields["receiving_offices"].queryset.values_list("pk", flat=True))
    assert offices["MED"].pk not in offered
    assert offices["REC"].pk in offered
    assert offices["SUP"].pk in offered


def test_administrator_draft_edit_can_route_to_the_administrators_office(client, users, offices):
    record = services.create_draft_record(
        user=users["med"], subject="Draft prepared by Maintenance", instructions="Please review.",
    )
    client.force_login(users["admin"])

    response = client.post(f"{reverse('tracking:create')}?draft={record.pk}", _new_record_data(
        offices, receiving_offices=[offices["REC"].pk],
    ))

    assert response.status_code == 302
    assert response["Location"] == reverse("tracking:review", args=[record.pk])
    assert client.session[f"draft_offices_{record.pk}"] == [offices["REC"].pk]
    record.refresh_from_db()
    assert record.originating_office_id == offices["MED"].pk
    assert record.created_by_id == users["med"].pk


def test_administrator_draft_review_sends_from_the_actual_origin(client, users, offices):
    record = services.create_draft_record(
        user=users["med"], subject="Draft prepared by Maintenance", instructions="Please review.",
    )
    client.force_login(users["admin"])

    response = client.post(reverse("tracking:review", args=[record.pk]), {
        "receiving_offices": [offices["REC"].pk], "deadline_choice": "none",
    })

    assert response.status_code == 302
    assert response["Location"] == record.get_absolute_url()
    step = record.routing_steps.get()
    assert step.from_office_id == offices["MED"].pk
    assert step.to_office_id == offices["REC"].pk
    assert step.sent_by_id == users["admin"].pk
    record.refresh_from_db()
    assert record.originating_office_id == offices["MED"].pk
    assert record.created_by_id == users["med"].pk


def test_administrator_draft_creation_still_uses_the_initiating_office(client, users, offices):
    client.force_login(users["admin"])
    response = client.get(reverse("tracking:create"))
    offered = set(response.context["form"].fields["receiving_offices"].queryset.values_list("pk", flat=True))
    assert offices["REC"].pk not in offered
    assert offices["MED"].pk in offered

    response = client.post(reverse("tracking:create"), _new_record_data(offices))

    assert response.status_code == 302
    record = TrackingRecord.objects.get()
    assert record.originating_office_id == offices["REC"].pk
    assert record.created_by_id == users["admin"].pk
