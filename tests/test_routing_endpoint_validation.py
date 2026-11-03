"""Routing rechecks active destinations and preserves deadline chronology."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import OperationalError, close_old_connections, connection, connections, transaction
from django.utils import timezone

from apps.accounts.models import Office
from apps.tracking import external_services, services
from apps.tracking.models import ExternalRoutingStep, RoutingStep, Status, TrackingNumberSequence


@pytest.mark.django_db
def test_internal_route_rejects_destination_deactivated_after_selection(users, offices):
    record = services.create_draft_record(user=users["med"], subject="Unsent paper", instructions="Review")
    destination = offices["SUP"]
    original_number = record.tracking_number
    Office.objects.filter(pk=destination.pk).update(is_active=False)

    with pytest.raises(ValidationError, match="active receiving office"):
        services.route_record(record, [destination], user=users["med"])

    record.refresh_from_db()
    assert record.status == Status.DRAFT
    assert record.tracking_number == original_number
    assert record.current_batch == 0
    assert not record.routing_steps.exists()
    assert not TrackingNumberSequence.objects.exists()


def _deactivation_is_blocked(office_id):
    close_old_connections()
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '100ms'")
            Office.objects.filter(pk=office_id).update(is_active=False)
        return False
    except OperationalError as exc:
        if getattr(exc.__cause__, "sqlstate", None) != "55P03":
            raise
        return True
    finally:
        connections.close_all()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("external", [False, True], ids=["internal-tracking", "external-tracking"])
def test_destination_cannot_be_deactivated_during_routing(users, offices, monkeypatch, external):
    destination = offices["SUP"]
    if external:
        record = external_services.create_record(user=users["med_admin"], title="External transfer")
        manager = ExternalRoutingStep.objects
    else:
        record = services.create_draft_record(user=users["med"], subject="Internal transfer", instructions="Review")
        manager = RoutingStep.objects
    create = manager.create

    def create_after_deactivation_attempt(*args, **kwargs):
        # Attempt an actual administrative update after destination validation,
        # before the transfer's routing row is written.
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(_deactivation_is_blocked, destination.pk).result(timeout=5)
        return create(*args, **kwargs)

    monkeypatch.setattr(manager, "create", create_after_deactivation_attempt)
    if external:
        external_services.route_record(
            record, user=users["med_admin"], expected_revision=record.revision, to_office=destination,
        )
    else:
        services.route_record(record, [destination], user=users["med"])
    destination.refresh_from_db()
    assert destination.is_active
    assert record.routing_steps.get().to_office_id == destination.pk


@pytest.mark.django_db
def test_corrected_external_dispatch_cannot_follow_existing_deadline(users, offices):
    source = external_services.create_external_office(user=users["med_admin"], name="Registrar")
    record = external_services.create_record(
        user=users["med_admin"], title="External paper", from_external_office=source,
    )
    sent_at = timezone.now() - timedelta(days=3)
    deadline = sent_at + timedelta(days=1)
    external_services.route_record(
        record, user=users["med_admin"], expected_revision=record.revision,
        to_office=offices["SUP"], sent_at=sent_at, due_at=deadline,
    )
    step = record.current_step
    revision = record.revision

    with pytest.raises(ValidationError, match="deadline"):
        external_services.correct_step(
            record, step=step, user=users["med_admin"], expected_revision=revision,
            reason="Correct the dispatch register", changes={"sent_at": deadline + timedelta(hours=1)},
        )

    step.refresh_from_db()
    record.refresh_from_db()
    assert step.sent_at == sent_at
    assert step.due_at == record.due_at == deadline
    assert record.revision == revision
    assert not record.revisions.exists()
