"""The tracking page's filters must describe the records it actually holds.

Two things went wrong here and both read to a user as "there are none":

1. The status dropdown offered every status there is, including Completed —
   but the page shows active records only, so picking it always returned an
   empty table for a page that never holds those records in the first place.
2. "Pending Receipt" quietly redirected an ordinary user to their own inbox,
   so it returned exactly the same rows as "Incoming" and could not answer the
   question it is named for: is anything we sent still unconfirmed?
"""

from __future__ import annotations

import pytest

from apps.tracking.forms import TrackingFilterForm
from apps.tracking.models import ACTIVE_STATUSES, Status
from apps.tracking.services import (
    apply_scope,
    complete_record,
    confirm_receipt,
    create_draft_record,
    route_record,
)


# --- the status dropdown ---------------------------------------------------
def test_the_status_filter_offers_only_statuses_this_page_can_show():
    offered = {value for value, _label in TrackingFilterForm().fields["status"].choices}

    assert "COMPLETED" not in offered, "completed records live in Documents, not here"
    assert offered == {"", "OVERDUE"} | {str(s) for s in ACTIVE_STATUSES}


def test_the_status_filter_uses_the_new_name():
    labels = dict(TrackingFilterForm().fields["status"].choices)

    assert labels["PENDING_RECEIPT"] == "Pending receipt"
    assert "IN_TRANSIT" not in labels


def test_routing_leaves_the_record_pending_receipt():
    assert Status.PENDING_RECEIPT.value == "PENDING_RECEIPT"
    assert Status.PENDING_RECEIPT.label == "Pending receipt"
    assert not hasattr(Status, "IN_TRANSIT")


# --- the Pending Receipt queue ---------------------------------------------
@pytest.fixture
def sent_record(users, offices, memo_type):
    """MED sends to SUP. Nobody has confirmed it."""
    record = create_draft_record(
        user=users["med"], subject="Electrical supplies request", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    record.refresh_from_db()
    return record


@pytest.mark.django_db
def test_the_sending_office_can_see_what_it_is_still_waiting_on(sent_record, users):
    """The regression: MED sent it, so MED must be able to see it unconfirmed."""
    from apps.tracking.models import TrackingRecord

    visible = TrackingRecord.objects.visible_to(users["med"])
    awaiting = apply_scope(visible, "awaiting", users["med"])

    assert sent_record in awaiting


@pytest.mark.django_db
def test_the_receiving_office_still_sees_it_too(sent_record, users):
    from apps.tracking.models import TrackingRecord

    visible = TrackingRecord.objects.visible_to(users["sup"])
    assert sent_record in apply_scope(visible, "awaiting", users["sup"])


@pytest.mark.django_db
def test_pending_receipt_is_no_longer_a_duplicate_of_incoming(sent_record, users):
    """For the sender the two queues must now differ — that is the whole point."""
    from apps.tracking.models import TrackingRecord

    visible = TrackingRecord.objects.visible_to(users["med"])
    inbox = set(apply_scope(visible, "inbox", users["med"]))
    awaiting = set(apply_scope(visible, "awaiting", users["med"]))

    assert sent_record not in inbox, "MED sent it; it is not in MED's inbox"
    assert sent_record in awaiting
    assert inbox != awaiting


@pytest.mark.django_db
def test_a_confirmed_record_leaves_the_queue(sent_record, users):
    from apps.tracking.models import TrackingRecord

    confirm_receipt(sent_record, user=users["sup"])
    sent_record.refresh_from_db()

    visible = TrackingRecord.objects.visible_to(users["med"])
    assert sent_record not in apply_scope(visible, "awaiting", users["med"])


@pytest.mark.django_db
def test_a_completed_record_never_shows_as_pending(sent_record, users):
    """A record completed while a recipient never confirmed is finished, not waiting."""
    from apps.tracking.models import TrackingRecord

    complete_record(sent_record, user=users["admin"])
    sent_record.refresh_from_db()

    visible = TrackingRecord.objects.visible_to(users["admin"])
    assert sent_record not in apply_scope(visible, "awaiting", users["admin"])


@pytest.mark.django_db
def test_a_partly_received_batch_still_counts_as_pending(users, offices, memo_type):
    """Two recipients, one confirms: the record reads RECEIVED but one still owes."""
    from apps.tracking.models import TrackingRecord

    record = create_draft_record(
        user=users["med"], subject="Circular for two offices", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"], offices["HR"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()

    assert record.status == Status.RECEIVED
    visible = TrackingRecord.objects.visible_to(users["med"])
    assert record in apply_scope(visible, "awaiting", users["med"]), (
        "HR has not confirmed, so the record is still pending a receipt"
    )
