"""Office time at each hop of one record, on its timeline.

Counted by the engine every turnaround figure uses, lunch and holidays
included, so a hop's wait on the detail page is the same arithmetic as the
averages built from it.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from django.utils import timezone

from apps.core import analytics
from apps.core.models import Holiday
from apps.tracking.models import RoutingStep, TrackingRecord
from apps.tracking.services import complete_record, confirm_receipt, create_draft_record, route_record

MONDAY, TUESDAY = date(2026, 8, 24), date(2026, 8, 25)


def _at(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _raised(users, memo_type):
    return create_draft_record(
        user=users["med"], subject="Timed", instructions="x", document_type=memo_type,
    )


@pytest.fixture
def completed_at_sup(users, offices, memo_type):
    """Sent Monday 9AM, confirmed 11AM, completed by SUP Tuesday 5PM."""
    record = _raised(users, memo_type)
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    complete_record(record, user=users["sup"])
    RoutingStep.objects.filter(record=record).update(sent_at=_at(MONDAY, 9), received_at=_at(MONDAY, 11))
    TrackingRecord.objects.filter(pk=record.pk).update(
        created_at=_at(MONDAY, 8), first_received_at=_at(MONDAY, 11), completed_at=_at(TUESDAY, 17),
    )
    record.refresh_from_db()
    return record


def _waits(record):
    steps = list(record.routing_steps.order_by("sequence"))
    return steps, analytics.record_waits(record, steps)


@pytest.mark.django_db
def test_a_hop_states_its_receipt_wait_and_how_long_it_was_held(completed_at_sup):
    steps, waits = _waits(completed_at_sup)
    wait = waits[steps[0].pk]

    assert wait["receipt"]["office"] == "2 hrs"
    # Monday 11AM-5PM less lunch is five hours, then Tuesday's eight.
    assert wait["held"]["office"] == "1 day 5 hrs"
    assert wait["held_until"] == "completing it"
    assert wait["held"]["live"] is False


@pytest.mark.django_db
def test_the_timeline_prints_them(client, users, completed_at_sup):
    client.force_login(users["med"])
    body = " ".join(client.get(completed_at_sup.get_absolute_url()).content.decode().split())

    assert "Confirmed after 2 hrs of office time" in body
    assert "SUP held it 1 day 5 hrs before completing it" in body
    assert "Turnaround is counted in office hours (8 hours = 1 working day" in body


@pytest.mark.django_db
def test_a_forwarded_hop_is_held_until_it_was_sent_on(users, offices, memo_type):
    record = _raised(users, memo_type)
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    route_record(record, [offices["HR"]], user=users["sup"], action="FORWARD")
    first, second = record.routing_steps.order_by("sequence")
    RoutingStep.objects.filter(pk=first.pk).update(sent_at=_at(MONDAY, 9), received_at=_at(MONDAY, 10))
    RoutingStep.objects.filter(pk=second.pk).update(sent_at=_at(MONDAY, 15))
    record.refresh_from_db()

    steps, waits = _waits(record)
    assert waits[steps[0].pk]["held"]["office"] == "4 hrs", "10AM-3PM less lunch"
    assert waits[steps[0].pk]["held_until"] == "sending it on"
    assert waits[steps[1].pk]["receipt"]["live"] is True, "HR has not confirmed: so far"


@pytest.mark.django_db
def test_a_holiday_inside_a_hop_is_not_counted(completed_at_sup):
    Holiday.objects.create(date=TUESDAY, name="Suspension")
    steps, waits = _waits(completed_at_sup)

    assert waits[steps[0].pk]["held"]["office"] == "5 hrs"


@pytest.mark.django_db
def test_the_record_level_figures_match_the_stage_definitions(completed_at_sup):
    steps = list(completed_at_sup.routing_steps.all())
    durations = analytics.record_durations(completed_at_sup, steps)

    assert durations["receipt"] == 2 * 3600
    assert durations["processing"] == 13 * 3600, "Monday 11AM to Tuesday 5PM"
    assert durations["lifetime"] == 16 * 3600, "Monday 8AM to Tuesday 5PM"


@pytest.mark.django_db
def test_a_draft_has_no_waits_to_state(users, memo_type):
    record = _raised(users, memo_type)
    assert analytics.record_waits(record, []) == {}
    assert analytics.record_durations(record, []) == {"receipt": None, "processing": None, "lifetime": None}
