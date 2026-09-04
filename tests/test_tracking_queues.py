"""Incoming, Outgoing, Pending receipt, Received and Overdue as queues.

They are derived per office rather than stored, because direction is not a
property of a document — it is a property of a document *and* an office. The
same batch is outgoing for the office that sent it and incoming for the office
it went to, at the same instant, so no single status column could hold it.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.tracking.models import Status, TrackingRecord
from apps.tracking.services import (
    SCOPE_INCOMING,
    SCOPE_OUTGOING,
    SCOPE_OVERDUE,
    SCOPE_PENDING_RECEIPT,
    SCOPE_RECEIVED,
    apply_scope,
    confirm_receipt,
    create_draft_record,
    route_record,
)


def scoped(user, scope):
    records = TrackingRecord.objects.visible_to(user)
    return set(apply_scope(records, scope, user).distinct())


@pytest.fixture
def in_flight(users, offices, memo_type):
    """MED → SUP, unconfirmed."""
    record = create_draft_record(
        user=users["med"], subject="Purchase request", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    record.refresh_from_db()
    return record


@pytest.mark.django_db
def test_the_same_record_is_outgoing_for_the_sender_and_incoming_for_the_recipient(
    in_flight, users
):
    assert in_flight in scoped(users["sup"], SCOPE_INCOMING)
    assert in_flight in scoped(users["med"], SCOPE_OUTGOING)
    # And not the other way round.
    assert in_flight not in scoped(users["med"], SCOPE_INCOMING)
    assert in_flight not in scoped(users["sup"], SCOPE_OUTGOING)


@pytest.mark.django_db
def test_incoming_holds_both_pending_receipt_and_received(in_flight, users):
    assert in_flight in scoped(users["sup"], SCOPE_INCOMING)
    assert in_flight in scoped(users["sup"], SCOPE_PENDING_RECEIPT)

    confirm_receipt(in_flight, user=users["sup"])
    in_flight.refresh_from_db()

    # Still incoming after it is confirmed — it did not stop being ours.
    assert in_flight in scoped(users["sup"], SCOPE_INCOMING)
    assert in_flight in scoped(users["sup"], SCOPE_RECEIVED)
    assert in_flight not in scoped(users["sup"], SCOPE_PENDING_RECEIPT)


@pytest.mark.django_db
def test_in_process_belongs_to_incoming(in_flight, users):
    from apps.tracking.services import mark_in_process

    confirm_receipt(in_flight, user=users["sup"])
    in_flight.refresh_from_db()
    mark_in_process(in_flight, user=users["sup"])
    in_flight.refresh_from_db()

    assert in_flight.status == Status.IN_PROCESS
    assert in_flight in scoped(users["sup"], SCOPE_INCOMING)
    assert in_flight in scoped(users["sup"], SCOPE_RECEIVED)


@pytest.mark.django_db
def test_received_is_our_receipt_not_anybody_s(in_flight, users):
    """The old queue filtered on the record's status alone, so a document
    another office had received showed up in your Received queue."""
    confirm_receipt(in_flight, user=users["sup"])
    in_flight.refresh_from_db()

    assert in_flight.status == Status.RECEIVED
    assert in_flight in scoped(users["sup"], SCOPE_RECEIVED)
    assert in_flight not in scoped(users["med"], SCOPE_RECEIVED)


@pytest.mark.django_db
def test_a_draft_belongs_to_neither_direction(users, memo_type):
    draft = create_draft_record(
        user=users["med"], subject="Not sent yet", instructions="For action.",
        document_type=memo_type,
    )
    assert draft not in scoped(users["med"], SCOPE_INCOMING)
    assert draft not in scoped(users["med"], SCOPE_OUTGOING)


@pytest.mark.django_db
def test_overdue_is_derived_and_never_stored(in_flight, users):
    assert in_flight not in scoped(users["sup"], SCOPE_OVERDUE)

    in_flight.due_at = timezone.now() - timezone.timedelta(days=2)
    in_flight.save(update_fields=["due_at"])

    assert in_flight in scoped(users["sup"], SCOPE_OVERDUE)
    # The stored status is untouched, and now the shown one is too. It used to
    # be replaced: `display_status` returned "OVERDUE", so a record that was
    # both pending receipt and late showed only that it was late — and the
    # stage is the half a clerk acts on.
    in_flight.refresh_from_db()
    assert in_flight.status == Status.PENDING_RECEIPT
    assert in_flight.is_overdue is True


@pytest.mark.django_db
def test_a_completed_record_is_never_overdue_in_the_queue(in_flight, users):
    from apps.tracking.services import complete_record

    in_flight.due_at = timezone.now() - timezone.timedelta(days=2)
    in_flight.save(update_fields=["due_at"])
    confirm_receipt(in_flight, user=users["sup"])
    in_flight.refresh_from_db()
    complete_record(in_flight, user=users["sup"])
    in_flight.refresh_from_db()

    assert in_flight not in scoped(users["sup"], SCOPE_OVERDUE)


@pytest.mark.django_db
def test_a_user_with_no_office_matches_nothing_rather_than_everything(in_flight, users):
    homeless = users["hr"]
    homeless.office = None
    homeless.save(update_fields=["office"])

    for scope in (SCOPE_INCOMING, SCOPE_OUTGOING, SCOPE_PENDING_RECEIPT, SCOPE_RECEIVED):
        assert scoped(homeless, scope) == set(), scope


@pytest.mark.django_db
def test_the_queue_links_on_the_page_all_resolve(client, in_flight, users):
    """A queue tab that silently ignores its own scope reads as "none here"."""
    client.force_login(users["sup"])
    for scope in ("incoming", "outgoing", "pending-receipt", "received", "overdue"):
        response = client.get(f"/tracking/?scope={scope}")
        assert response.status_code == 200, scope
        # The form must accept it, or the page warns that it ignored a filter.
        assert "Ignored a filter" not in response.content.decode(), scope
