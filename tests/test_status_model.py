"""The status set, and the rules that hold it together.

Five statuses a reader ever sees — Draft, Pending receipt, Received, In process,
Overdue — plus the two halves of completion. FORWARDED and RETURNED are gone as
statuses and stay as actions and events; Overdue is still derived, never stored.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.tracking.models import (
    ACTIVE_STATUSES,
    AWAITING_RECEIPT_STATUSES,
    COMPLETED_STATUSES,
    RecordActivity,
    RoutingStep,
    Status,
)
from apps.tracking.services import (
    add_remark,
    complete_record,
    confirm_receipt,
    create_draft_record,
    mark_in_process,
    route_record,
)


@pytest.fixture
def sent_record(users, offices, memo_type):
    """MED has sent it to SUP. Nobody has confirmed receipt."""
    record = create_draft_record(
        user=users["med"], subject="A request in flight", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    record.refresh_from_db()
    return record


# --- 1.1 the collapse ------------------------------------------------------
def test_the_status_enum_no_longer_has_forwarded_or_returned():
    values = {value for value, _label in Status.choices}
    assert "FORWARDED" not in values
    assert "RETURNED" not in values
    assert values == {
        "DRAFT", "PENDING_RECEIPT", "RECEIVED", "IN_PROCESS",
        "COMPLETED_PENDING_UPLOAD", "COMPLETED",
    }


def test_the_actions_and_the_events_keep_both_names():
    """The distinction moves rather than disappears."""
    assert RoutingStep.Action.FORWARD in RoutingStep.Action
    assert RoutingStep.Action.RETURN in RoutingStep.Action
    assert RecordActivity.Event.FORWARDED in RecordActivity.Event
    assert RecordActivity.Event.RETURNED in RecordActivity.Event


def test_awaiting_receipt_is_one_status_now():
    assert AWAITING_RECEIPT_STATUSES == {Status.PENDING_RECEIPT}


@pytest.mark.django_db
@pytest.mark.parametrize("action", [RoutingStep.Action.FORWARD, RoutingStep.Action.RETURN])
def test_forwarding_and_returning_both_land_on_pending_receipt(
    sent_record, users, offices, action
):
    confirm_receipt(sent_record, user=users["sup"])
    sent_record.refresh_from_db()

    route_record(sent_record, [offices["HR"]], user=users["sup"], action=action)
    sent_record.refresh_from_db()

    assert sent_record.status == Status.PENDING_RECEIPT
    assert sent_record.awaiting_receipt is True
    # And the act itself is still on the record, in both places.
    assert sent_record.routing_steps.order_by("sequence").last().action == action
    assert sent_record.activities.filter(
        event__in=[RecordActivity.Event.FORWARDED, RecordActivity.Event.RETURNED]
    ).exists()


# --- 1.2 the approval stage ------------------------------------------------
@pytest.mark.django_db
def test_completing_keeps_the_record_in_tracking(sent_record, users):
    confirm_receipt(sent_record, user=users["sup"])
    sent_record.refresh_from_db()
    complete_record(sent_record, user=users["sup"])
    sent_record.refresh_from_db()

    assert sent_record.status == Status.COMPLETED_PENDING_UPLOAD
    assert sent_record.status in ACTIVE_STATUSES
    assert sent_record.status in COMPLETED_STATUSES


@pytest.mark.django_db
def test_a_completed_record_cannot_be_routed_from_either_half(sent_record, users, offices):
    confirm_receipt(sent_record, user=users["sup"])
    sent_record.refresh_from_db()
    complete_record(sent_record, user=users["sup"])
    sent_record.refresh_from_db()

    with pytest.raises(ValidationError):
        route_record(sent_record, [offices["HR"]], user=users["sup"],
                     action=RoutingStep.Action.FORWARD)


@pytest.mark.django_db
def test_an_overdue_record_stops_being_overdue_once_completed(sent_record, users):
    sent_record.due_at = timezone.now() - timezone.timedelta(days=1)
    sent_record.save(update_fields=["due_at"])
    assert sent_record.is_overdue is True

    confirm_receipt(sent_record, user=users["sup"])
    sent_record.refresh_from_db()
    complete_record(sent_record, user=users["sup"])
    sent_record.refresh_from_db()

    # Pending upload is still a completion: nobody is late finishing work that
    # is finished, only late filing it.
    assert sent_record.is_overdue is False
    assert sent_record.status == Status.COMPLETED_PENDING_UPLOAD


# --- 1.3 In process is gated on receipt ------------------------------------
@pytest.mark.django_db
def test_in_process_is_refused_before_receipt(sent_record, users):
    """Enforced in the service, not by hiding the option: the endpoint stays
    reachable to anyone who knows the URL."""
    assert sent_record.status == Status.PENDING_RECEIPT

    with pytest.raises(ValidationError):
        mark_in_process(sent_record, user=users["sup"])

    sent_record.refresh_from_db()
    assert sent_record.status == Status.PENDING_RECEIPT


@pytest.mark.django_db
def test_a_draft_cannot_be_in_process(users, memo_type):
    draft = create_draft_record(
        user=users["med"], subject="Still being written", instructions="For action.",
        document_type=memo_type,
    )
    with pytest.raises(ValidationError):
        mark_in_process(draft, user=users["med"])


@pytest.mark.django_db
def test_in_process_is_allowed_once_received(sent_record, users):
    confirm_receipt(sent_record, user=users["sup"])
    sent_record.refresh_from_db()

    mark_in_process(sent_record, user=users["sup"])
    sent_record.refresh_from_db()

    assert sent_record.status == Status.IN_PROCESS


@pytest.mark.django_db
def test_a_remark_before_receipt_does_not_promote_the_status(sent_record, users):
    """The automatic path has to obey the same rule as the explicit one."""
    add_remark(sent_record, user=users["admin"], remark="Chasing this up.")
    sent_record.refresh_from_db()

    assert sent_record.status == Status.PENDING_RECEIPT


@pytest.mark.django_db
def test_a_viewer_cannot_change_the_status(sent_record, users):
    confirm_receipt(sent_record, user=users["sup"])
    sent_record.refresh_from_db()

    with pytest.raises(PermissionDenied):
        mark_in_process(sent_record, user=users["viewer"])
