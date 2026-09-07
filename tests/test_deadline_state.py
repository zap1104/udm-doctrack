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


# --- sorting the list by deadline --------------------------------------------
TRACKING = "/tracking/"


@pytest.fixture
def spread(users, offices, memo_type):
    """Four records: late, due soon, far off, and one with no deadline."""
    made = {}
    for subject, days in [("Late", -2), ("Soon", 0.5), ("Far", 30), ("Unscheduled", None)]:
        record = create_draft_record(
            user=users["med"], subject=subject, instructions="x", document_type=memo_type,
        )
        route_record(record, [offices["SUP"]], user=users["med"])
        TrackingRecord.objects.filter(pk=record.pk).update(
            due_at=None if days is None else timezone.now() + timedelta(days=days)
        )
        made[subject] = record
    return made


def _subjects(client, query=""):
    return [
        record.subject
        for record in client.get(f"{TRACKING}{query}").context["page_obj"].object_list
    ]


def test_the_default_order_is_unchanged(client, users, spread):
    """Most recently moved first. Adding a sort must not quietly re-order the
    page for everybody who never touches it."""
    client.force_login(users["med"])

    assert _subjects(client) == ["Unscheduled", "Far", "Soon", "Late"]


def test_soonest_first_puts_the_most_urgent_at_the_top(client, users, spread):
    client.force_login(users["med"])

    assert _subjects(client, "?sort=deadline")[:3] == ["Late", "Soon", "Far"]


def test_latest_first_reverses_the_scheduled_ones(client, users, spread):
    client.force_login(users["med"])

    assert _subjects(client, "?sort=deadline-desc")[:3] == ["Far", "Soon", "Late"]


@pytest.mark.parametrize("query", ["?sort=deadline", "?sort=deadline-desc"])
def test_records_with_no_deadline_sort_last_in_both_directions(
    client, users, spread, query
):
    """A record with no deadline has not been scheduled at all, so it belongs
    after everything that has been — on "latest first" as much as on "soonest
    first", where NULL sorting high would put unscheduled work above the
    genuinely distant."""
    client.force_login(users["med"])

    assert _subjects(client, query)[-1] == "Unscheduled"


def test_an_unrecognised_sort_falls_back_rather_than_failing(client, users, spread):
    """The same lenient handling every other filter on this page gets: the form
    drops the value, and the page renders in its default order."""
    client.force_login(users["med"])
    response = client.get(f"{TRACKING}?sort=nonsense")

    assert response.status_code == 200
    assert _subjects(client, "?sort=nonsense") == _subjects(client)


def test_the_sort_survives_paging(client, users, spread):
    client.force_login(users["med"])

    body = client.get(f"{TRACKING}?sort=deadline&per_page=10").content.decode()

    assert "sort=deadline" in body


def test_sorting_composes_with_a_filter(client, users, spread, offices):
    """A sort is not a filter and must not replace one."""
    client.force_login(users["med"])

    response = client.get(f"{TRACKING}?sort=deadline&status=PENDING_RECEIPT")

    assert response.context["resolved"].statuses == ["PENDING_RECEIPT"]
    assert response.context["selected_sort"] == "deadline"


# --- what the row shows -------------------------------------------------------
def test_the_row_carries_the_deadline_and_the_right_tag(client, users, spread):
    """Overdue and Due soon are one control with two states — mutually exclusive
    by construction, so a row can never wear both."""
    client.force_login(users["med"])
    body = client.get(f"{TRACKING}?sort=deadline").content.decode()

    assert ">Overdue</span>" in body
    assert ">Due soon</span>" in body
    # Three of the four have a deadline; the unscheduled one shows no date line.
    assert body.count("deadline-when-cell") == 3


def test_a_far_off_deadline_is_shown_without_a_warning(client, users, offices, memo_type):
    """"Scheduled" is a state with a date and no badge. A warning on everything
    is a warning about nothing."""
    record = create_draft_record(
        user=users["med"], subject="Far", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    TrackingRecord.objects.filter(pk=record.pk).update(
        due_at=timezone.now() + timedelta(days=30)
    )
    client.force_login(users["med"])

    body = client.get(TRACKING).content.decode()

    assert "deadline-when-cell" in body
    assert ">Due soon</span>" not in body
    assert ">Overdue</span>" not in body


def test_the_table_kept_its_column_count(client, users, spread):
    """The deadline went inline under the status pills, so the header and the
    empty-state colspan are untouched. A ninth column would have pushed a table
    that already carries a 720px min-width further past it."""
    import pathlib

    markup = pathlib.Path("templates/tracking/list.html").read_text(encoding="utf-8")

    assert 'colspan="{% if can_bulk_receive %}8{% else %}7{% endif %}"' in markup
    assert "<th>Deadline</th>" not in markup
