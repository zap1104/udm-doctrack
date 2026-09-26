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
@pytest.mark.django_db  # the interval reads the holiday table
def test_a_full_office_day_is_one_working_day_in_every_figure():
    monday = _a_monday()
    seconds = business_seconds_between(_local(monday, 8), _local(monday, 17))

    assert seconds == working_day_seconds()
    assert humanise_business_seconds(seconds) == "1 day"
    assert round(seconds / working_day_seconds(), 1) == 1.0


@pytest.mark.django_db
def test_the_trend_chart_counts_days_the_way_the_text_does(users, offices, memo_type):
    """A document that took exactly one office day plots at 1.0 and reads
    "1 day"; when the chart used a day of its own length it plotted at 0.9."""
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
    month = context["turnaround"]
    body = " ".join(response.content.decode().split())

    assert month["month"] == latest["month"], "the current month unless another is picked"
    assert month["lifetime_samples"] == latest["lifetime_samples"] == 1, "only this month's completion"
    assert month["lifetime"] == latest["lifetime_label"], "the summary is the chart's last point"
    heading = f'{latest["month"]:%B %Y} so far'
    assert heading in body
    all_time = analytics.turnaround(TrackingRecord.objects.all())["lifetime"]
    assert all_time != month["lifetime"], "the all-time figure differs"
    summary = body[body.index(heading):]
    summary = summary[: summary.index("Completed on time") if "Completed on time" in summary else 800]
    assert month["lifetime"] in summary
    assert all_time not in summary


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
    response = client.get("/tracking/reports/")
    figures = response.context["turnaround"]
    body = " ".join(response.content.decode().split())
    this_month = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    assert figures["month"] == this_month.date(), "Reports answers for the current month too"
    assert figures["receipt_samples"] == RoutingStep.objects.filter(received_at__gte=this_month).count()
    assert figures["processing_samples"] == 1, "only the document finished this month"
    assert figures["lifetime_samples"] == 1
    assert f'{figures["receipt_samples"]} handovers' in body
    assert "created → completed &middot; 1 document" in body
    assert "that had a deadline" in body or figures["on_time_total"] == 0


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


# --- the line chart: readable, and something to point at ----------------------
def _dashboard(client, users):
    client.force_login(users["admin"])
    return client.get("/")


@pytest.mark.django_db
def test_every_month_has_a_hover_target_placed_on_its_dots(client, users, slow_then_fast):
    """The overlay is HTML over an SVG; a dot and its hover marker must land on
    the same spot, so both come from one geometry."""
    context = _dashboard(client, users).context
    geometry = context["turnaround_trend_geometry"]
    height = geometry["height"]
    months = geometry["months"]

    assert len(months) == len(context["turnaround_trend"]["rows"]) == 12
    for series in context["turnaround_trend_points"]:
        drawn = [round(100 * dot["y"] / height, 1) for dot in series["dots"]]
        marked = [
            round(point["top_percent"], 1)
            for month in months for point in month["points"] if point["label"] == series["label"]
        ]
        assert drawn == marked, series["label"]


@pytest.mark.django_db
def test_a_month_reads_out_in_words_with_what_it_is_averaged_over(client, users, slow_then_fast):
    response = _dashboard(client, users)
    body = " ".join(response.content.decode().split())
    latest = response.context["turnaround_trend"]["latest"]
    month = response.context["turnaround_trend_geometry"]["months"][-1]

    assert month["label"] == f'{latest["month"]:%B %Y}'
    lifetime = next(point for point in month["points"] if point["label"] == "Total lifetime")
    assert lifetime["text"] == latest["lifetime_label"]
    assert lifetime["unit"] == "document" and lifetime["samples"] == 1
    assert f'{latest["month"]:%B %Y}: ' in month["summary"]
    assert "Nothing received or completed" in body, "an empty month says so"


@pytest.mark.django_db
def test_the_tooltip_opens_away_from_the_nearer_edge(client, users, slow_then_fast):
    months = _dashboard(client, users).context["turnaround_trend_geometry"]["months"]

    assert months[0]["side"] == "right" and months[-1]["side"] == "left"


@pytest.mark.django_db
def test_the_chart_is_one_tab_stop_with_a_label_for_every_month(client, users, slow_then_fast):
    body = _dashboard(client, users).content.decode()
    layer = body[body.index("data-trend-hover"):]

    assert body.count("data-trend-hover") == 1
    assert 'tabindex="0" role="group" data-trend-hover' in body
    assert layer.count("data-trend-month") == 12
    assert "data-trend-announce" in layer, "the arrow keys are read out"


@pytest.mark.django_db
def test_lines_take_their_colour_in_a_way_every_browser_reads(client, users, slow_then_fast):
    """The colours are CSS custom properties, which a presentation attribute
    does not resolve everywhere; and white dots glared on the dark theme."""
    body = _dashboard(client, users).content.decode()
    svg = body[body.index('class="trend-svg"'):body.index("</svg>", body.index('class="trend-svg"'))]

    assert 'stroke="var(' not in svg
    assert 'fill="#fff"' not in svg
    assert 'style="stroke:var(--status-pending)"' in svg
    assert 'pathLength="1"' in svg, "drawn in from its start"


@pytest.mark.django_db
def test_the_legend_says_what_each_line_measures(client, users, slow_then_fast):
    body = " ".join(_dashboard(client, users).content.decode().split())

    for words in ("Receipt</strong> sent until confirmed",
                  "In Process</strong> confirmed until completed",
                  "Total lifetime</strong> created until completed"):
        assert words in body


def test_the_motion_is_skipped_for_readers_who_ask_and_never_printed():
    import pathlib
    import re

    css = pathlib.Path("static/css/doctrack.css").read_text(encoding="utf-8")
    reduced = [block for block in re.split(r"@media \(prefers-reduced-motion: reduce\)", css)[1:]
               if ".trend-line" in block[:400]]
    assert reduced, "a reduced-motion rule for the chart"
    assert ".trend-line { animation:none; stroke-dashoffset:0; }" in reduced[0][:400]
    assert ".trend-hover { display:none; }" in css


def test_touch_and_keyboard_are_handled_by_one_delegated_listener():
    import pathlib

    script = pathlib.Path("static/js/doctrack.js").read_text(encoding="utf-8")
    block = script[script.index("Turnaround chart: a month at a time"):]

    for needle in ('"ArrowLeft"', '"ArrowRight"', '"Escape"', "data-trend-announce",
                   'document.addEventListener("click"'):
        assert needle in block, needle
    assert "innerHTML" not in block
