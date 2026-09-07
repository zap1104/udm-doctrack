"""What a row should say about a record's deadline.

Overdue was already visible — a red tag beside the status pill. Nothing said a
deadline was *approaching*, which is the state an office can still act on: once
a record is overdue the warning has arrived too late to be a warning.

`deadline_state` is derived on the model rather than worked out in the template.
A template comparing `due_at` against `timezone.now()` would be a second rule,
and the two would part company the first time the window changed.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from apps.tracking.models import Status, TrackingRecord
from apps.tracking.services import (
    complete_record,
    confirm_receipt,
    create_draft_record,
    route_record,
)


@pytest.fixture
def routed(users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Supplies", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    record.refresh_from_db()
    return record


def _at(record, **delta):
    """Put the deadline somewhere relative to now and re-read the state."""
    TrackingRecord.objects.filter(pk=record.pk).update(
        due_at=timezone.now() + timedelta(**delta)
    )
    record.refresh_from_db()
    return record.deadline_state


# --- the three live states ---------------------------------------------------
def test_a_passed_deadline_is_overdue(db, routed):
    assert _at(routed, hours=-1) == TrackingRecord.DEADLINE_OVERDUE


def test_a_deadline_inside_the_window_is_due_soon(db, routed):
    assert _at(routed, hours=settings.DEADLINE_WARNING_HOURS - 1) == (
        TrackingRecord.DEADLINE_DUE_SOON
    )


def test_a_deadline_beyond_the_window_is_merely_scheduled(db, routed):
    assert _at(routed, hours=settings.DEADLINE_WARNING_HOURS + 1) == (
        TrackingRecord.DEADLINE_SCHEDULED
    )


def test_the_boundary_counts_as_due_soon(db, routed):
    """Exactly at the window. Inclusive, so a deadline that is 24 hours away to
    the second warns rather than falling through the crack between two states."""
    assert _at(routed, hours=settings.DEADLINE_WARNING_HOURS, seconds=-1) == (
        TrackingRecord.DEADLINE_DUE_SOON
    )


# --- and the states it must not claim ----------------------------------------
def test_no_deadline_is_no_state(db, users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Supplies", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"], due_at=None)
    record.refresh_from_db()

    assert record.due_at is None
    assert record.deadline_state == TrackingRecord.DEADLINE_NONE


@pytest.mark.parametrize("hours", [-1, 1])
def test_completed_work_owes_nothing_whatever_its_deadline_said(
    db, users, routed, hours
):
    """Both sides of the deadline: a finished record is neither overdue nor due
    soon. That is the exclusion `is_overdue` already makes, and this delegates
    to it rather than repeating the status test."""
    confirm_receipt(routed, user=users["sup"])
    complete_record(routed, user=users["sup"])
    routed.refresh_from_db()
    assert routed.status in (Status.COMPLETED, Status.COMPLETED_PENDING_UPLOAD)

    assert _at(routed, hours=hours) == TrackingRecord.DEADLINE_NONE


def test_a_record_awaiting_receipt_can_be_due_soon(db, routed):
    """The row a warning is most useful on. The receipt is the act that is owed
    and nobody has performed it."""
    assert routed.status == Status.PENDING_RECEIPT

    assert _at(routed, hours=1) == TrackingRecord.DEADLINE_DUE_SOON


def test_the_window_is_configurable(db, routed):
    """An office that opens its queue every other day wants more notice, and
    that is a local decision rather than something this code decides."""
    with override_settings(DEADLINE_WARNING_HOURS=72):
        assert _at(routed, hours=48) == TrackingRecord.DEADLINE_DUE_SOON
    assert _at(routed, hours=48) == TrackingRecord.DEADLINE_SCHEDULED


def test_overdue_still_wins_over_due_soon(db, routed):
    """The states are exclusive and ordered: once the deadline has passed the
    row says overdue, not "due soon", however recently it went by."""
    assert _at(routed, seconds=-1) == TrackingRecord.DEADLINE_OVERDUE
    assert routed.is_overdue
