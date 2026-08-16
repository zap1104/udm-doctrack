"""The flowchart is explicit: sending is not receiving. Custody only changes when
a human at the receiving office presses Confirm receipt, and the time recorded is
the server's, never the user's."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.tracking.services import confirm_receipt, create_draft_record, route_record


@pytest.fixture
def routed_record(users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Request for electrical supplies",
        instructions="Please act within three days.", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"], instructions="For action.")
    record.refresh_from_db()
    return record


@pytest.mark.django_db
def test_routing_leaves_the_record_pending_receipt_not_received(routed_record):
    assert routed_record.status == "PENDING_RECEIPT"
    assert routed_record.current_office is None or routed_record.current_office.code != "SUP"
    assert routed_record.routing_steps.filter(received_at__isnull=True).count() == 1


@pytest.mark.django_db
def test_only_the_receiving_office_can_confirm(routed_record, users):
    # The sender cannot confirm receipt on the receiver's behalf.
    assert routed_record.can_user_confirm_receipt(users["med"]) is False
    # An unrelated office cannot either.
    assert routed_record.can_user_confirm_receipt(users["hr"]) is False
    # The receiving office can.
    assert routed_record.can_user_confirm_receipt(users["sup"]) is True


@pytest.mark.django_db
def test_receipt_records_who_where_and_when(routed_record, users):
    step = confirm_receipt(routed_record, user=users["sup"], note="Received at the office.")
    routed_record.refresh_from_db()

    assert step.received_by == users["sup"]
    assert step.to_office.code == "SUP"
    assert step.received_at is not None
    assert routed_record.status == "RECEIVED"
    assert routed_record.current_office.code == "SUP"
    assert routed_record.current_holder == users["sup"]


@pytest.mark.django_db
def test_receipt_cannot_be_confirmed_twice(routed_record, users):
    confirm_receipt(routed_record, user=users["sup"])
    with pytest.raises(ValidationError):
        confirm_receipt(routed_record, user=users["sup"])
