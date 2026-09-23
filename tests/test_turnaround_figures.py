"""The turnaround figures: one definition of a working day, the month a label
names, and a count of what is really still waiting.

Three faults, found by reading the two turnaround panels side by side:

- The office-hours text counted a working day as seven hours and the trend
  chart divided by eight, so one wait read "1 day" beside a point at 0.9.
- The dashboard summary was headed with the current month and printed the
  all-time averages under it, while the on-time meter beneath it was that
  month's: "In process 3 days 3 hrs" next to a line reading about one day.
- Reports' "N routing steps are still waiting" counted every unconfirmed step
  ever written, on finished documents and superseded hops included: 83 on the
  seeded data, beside a Pending receipt card reading 20.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from django.utils import timezone

from apps.core import analytics
from apps.core.business_time import (
    business_seconds_between,
    humanise_business_seconds,
    working_day_seconds,
)
from apps.tracking.models import RoutingStep, TrackingRecord
from apps.tracking.services import (
    complete_record,
    confirm_receipt,
    create_draft_record,
    route_record,
)


def _local(day, hour):
    return timezone.make_aware(datetime.combine(day, time(hour)), timezone.get_current_timezone())


def _a_monday():
    today = timezone.localdate()
    return today - timedelta(days=today.weekday() + 7)


# --- one working day ------------------------------------------------------------
def test_a_full_office_day_is_one_working_day_in_every_figure():
    monday = _a_monday()
    seconds = business_seconds_between(_local(monday, 8), _local(monday, 17))

    assert seconds == working_day_seconds()
    assert humanise_business_seconds(seconds) == "1 day"
    assert round(seconds / working_day_seconds(), 1) == 1.0


@pytest.mark.django_db
def test_the_trend_chart_counts_days_the_way_the_text_does(users, offices, memo_type):
    """A document that took exactly one office day plots at 1.0 and reads
    "1 day"; with an eight-hour chart day it plotted at 0.9."""
    record = create_draft_record(
        user=users["med"], subject="One day", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    complete_record(record, user=users["sup"])
    monday = _a_monday()
    TrackingRecord.objects.filter(pk=record.pk).update(
        created_at=_local(monday, 8), first_received_at=_local(monday, 8),
        completed_at=_local(monday, 17),
    )

    trend = analytics.turnaround_by_month(TrackingRecord.objects.filter(pk=record.pk))
    month = next(row for row in trend["rows"] if row["lifetime"] is not None)

    assert month["lifetime"] == 1.0
    assert month["lifetime_label"] == "1 day"
    assert trend["working_day_hours"] == working_day_seconds() / 3600


def test_durations_drop_a_zero_second_unit():
    assert humanise_business_seconds(4 * working_day_seconds()) == "4 days"
    assert humanise_business_seconds(3 * 3600) == "3 hrs"
    assert analytics.humanise_duration(timedelta(days=4)) == "4 days"
    assert analytics.humanise_duration(timedelta(days=1, hours=2)) == "1 day 2 hrs"


# --- the month the summary names ------------------------------------------------
@pytest.fixture
def slow_then_fast(users, offices, memo_type):
    """One document that took weeks, finished months ago, and one that took an
    hour, finished this month: the all-time average and this month's are far
    apart, which is what makes the mislabelling visible."""
    made = []
    for subject in ("Slow, long ago", "Fast, this month"):
        record = create_draft_record(
            user=users["med"], subject=subject, instructions="x", document_type=memo_type,
        )
        route_record(record, [offices["SUP"]], user=users["med"])
        confirm_receipt(record, user=users["sup"])
        record.refresh_from_db()
        complete_record(record, user=users["sup"])
        made.append(record)
    now = timezone.now()
    long_ago = now - timedelta(days=120)
    TrackingRecord.objects.filter(pk=made[0].pk).update(
        created_at=long_ago - timedelta(days=30), first_received_at=long_ago - timedelta(days=29),
        completed_at=long_ago,
    )
    TrackingRecord.objects.filter(pk=made[1].pk).update(
        created_at=now - timedelta(hours=1), first_received_at=now - timedelta(minutes=50),
        completed_at=now,
    )
    return made


@pytest.mark.django_db
def test_the_summary_shows_the_month_it_is_headed_with(client, users, slow_then_fast):
    client.force_login(users["admin"])
    response = client.get("/")
    context = response.context
    latest = context["turnaround_trend"]["latest"]
    body = " ".join(response.content.decode().split())

    assert latest["lifetime_samples"] == 1, "only this month's completion"
    assert f'{latest["month"]:%B} so far' in body
    assert latest["lifetime_label"] in body
    assert context["turnaround"]["lifetime"] != latest["lifetime_label"], (
        "the all-time figure differs, and it is not the one printed under this month"
    )
    summary = body[body.index(f'{latest["month"]:%B} so far'):]
    summary = summary[: summary.index("Completed on time") if "Completed on time" in summary else 800]
    assert context["turnaround"]["lifetime"] not in summary


@pytest.mark.django_db
def test_a_stage_with_nothing_this_month_says_so(client, users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Received only", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    client.force_login(users["admin"])

    body = client.get("/").content.decode()

    assert "None completed yet" in body


# --- what is really still waiting -----------------------------------------------
@pytest.mark.django_db
def test_only_handovers_that_can_still_be_confirmed_are_waiting(users, offices, memo_type):
    def raised(subject):
        return create_draft_record(
            user=users["med"], subject=subject, instructions="x", document_type=memo_type,
        )

    waiting = raised("Waiting on SUP")
    route_record(waiting, [offices["SUP"]], user=users["med"])

    finished = raised("Finished, HR never signed")
    route_record(finished, [offices["SUP"], offices["HR"]], user=users["med"])
    confirm_receipt(finished, user=users["sup"])
    finished.refresh_from_db()
    complete_record(finished, user=users["sup"])

    moved_on = raised("Moved past HR")
    route_record(moved_on, [offices["SUP"], offices["HR"]], user=users["med"])
    confirm_receipt(moved_on, user=users["sup"])
    moved_on.refresh_from_db()
    route_record(moved_on, [offices["MED"]], user=users["sup"], action="FORWARD")
    confirm_receipt(moved_on, user=users["med"])

    figures = analytics.turnaround(TrackingRecord.objects.all())

    assert RoutingStep.objects.filter(received_at__isnull=True).count() == 3, (
        "three unconfirmed steps exist"
    )
    assert figures["awaiting_confirmation"] == 1, "and only one can still be confirmed"


@pytest.mark.django_db
def test_every_average_says_how_many_it_is_taken_over(client, users, slow_then_fast):
    client.force_login(users["admin"])
    response = client.get("/reports/")
    figures = response.context["turnaround"]
    body = " ".join(response.content.decode().split())

    assert figures["receipt_samples"] == RoutingStep.objects.filter(received_at__isnull=False).count()
    assert figures["processing_samples"] == 2
    assert figures["lifetime_samples"] == 2
    assert f'{figures["receipt_samples"]} handovers' in body
    assert "2 documents" in body
    assert "completed documents that had a deadline" in body or figures["on_time_total"] == 0


# --- the axis --------------------------------------------------------------------
@pytest.mark.django_db
def test_the_trend_rules_are_its_labelled_ticks(client, users, slow_then_fast):
    """Rules at 0, 2, 4, 6 working days, each labelled, instead of four
    unlabelled rules at quarters of the height."""
    client.force_login(users["admin"])
    context = client.get("/").context
    trend = context["turnaround_trend"]
    grid = context["turnaround_trend_geometry"]["grid"]

    assert [line["value"] for line in grid] == [tick["value"] for tick in reversed(trend["ticks"])]
    assert grid[0]["value"] == trend["ceiling"] and grid[-1]["value"] == 0
    assert grid[-1]["axis"] is True
