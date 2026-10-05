"""Readable clocks and visible arithmetic must explain the same measured intervals."""
from datetime import date, datetime, time

import pytest
from django.template.loader import render_to_string
from django.test import override_settings
from django.utils import timezone

from apps.core import analytics
from apps.core.business_time import (
    NO_HOLIDAYS,
    business_seconds_between,
    humanise_hours,
    office_schedule_explanation,
)
from apps.core.templatetags.doctrack import duration_compact, office_hours_tick
from apps.core.views import DashboardView


@pytest.mark.parametrize("seconds,label", [
    (None, "—"), (-1, "—"), (float("nan"), "—"), (float("inf"), "—"),
    (0, "0 mins"), (0.5, "under a minute"), (59, "under a minute"),
    (60, "1 min"), (3599, "59 mins"), (3600, "1 hr"),
    (28800, "8 hrs"), (86400, "24 hrs"), (90061, "25 hrs 1 min"),
])
def test_both_clocks_use_total_hours_without_ambiguous_days(seconds, label):
    assert humanise_hours(seconds) == label


@pytest.mark.parametrize("seconds,label", [(0, "0m"), (30, "<1m"), (180, "3m"),
                                         (223800, "62h 10m"), (None, "—")])
def test_hover_equations_have_explicit_hours_and_minutes(seconds, label):
    assert duration_compact(seconds) == label


def test_default_example_uses_real_office_window_and_lunch():
    example = office_schedule_explanation()
    assert example["days"] == "Monday–Friday"
    assert example["example_total_seconds"] == 9 * 3600
    assert example["example_excluded_seconds"] == 3600
    assert example["example_office_seconds"] == 8 * 3600


@pytest.mark.parametrize("days,label", [(1, "Monday"), (6, "Monday–Saturday"), (7, "Every day")])
def test_visible_schedule_follows_configuration_instead_of_assuming_eight_hours(days, label):
    with override_settings(OFFICE_WEEK_DAYS=days, OFFICE_DAY_START=time(9), OFFICE_DAY_END=time(15),
                           OFFICE_LUNCH_START=time(12), OFFICE_LUNCH_END=time(12, 30)):
        example = office_schedule_explanation()
    assert example["days"] == label
    assert example["opens"] == "9:00 AM"
    assert example["example_total_seconds"] == 6 * 3600
    assert example["example_office_seconds"] == 5.5 * 3600
    assert example["example_excluded_seconds"] == 1800


def measured_sample(start, end, record_id=1):
    return {"office": business_seconds_between(start, end, NO_HOLIDAYS),
            "calendar": (end - start).total_seconds(), "record_id": record_id,
            "tracking_number": f"EXAMPLE-{record_id}", "ended": end}


def at(day, hour):
    return timezone.make_aware(datetime(2026, 10, day, hour))


def test_overnight_calculation_has_a_popup_without_opening_help():
    # Monday 4 PM to Tuesday 9 AM is 17 elapsed hours and two office hours.
    sample = measured_sample(at(5, 16), at(6, 9))
    stage = analytics._stage("receipt", "Pending Receipt", "sent until confirmed", "handover", [sample])
    assert stage["average_calendar_seconds"] == 17 * 3600
    assert stage["average_outside_seconds"] == 15 * 3600
    assert stage["average_seconds"] == 2 * 3600
    html = render_to_string("core/_turnaround_stage.html", {"stage": stage, "id_prefix": "test"})
    assert "17 hrs total wait − 15 hrs excluded ≈ 2 hrs within office hours" in html
    assert "2 hrs counted ÷ 1 confirmed handover ≈ 2 hrs" in html
    assert "Average total wait" in html
    assert 'data-bs-target="#turnaround-calculation-dialog"' in html
    assert 'aria-label="View calculation for Pending Receipt"' in html
    assert 'class="turnaround-calculation"' not in html
    assert "data-help-title" not in html
    assert "<details" not in html


def test_average_formula_includes_zero_office_time_and_repeated_handovers():
    samples = [measured_sample(at(5, 16), at(6, 9)), measured_sample(at(10, 8), at(10, 17))]
    stage = analytics._stage("receipt", "Pending Receipt", "sent until confirmed", "handover", samples)
    assert stage["samples"] == 2 and stage["documents"] == 1
    assert stage["zero_working_time"] == 1
    assert stage["total_office_seconds"] == 2 * 3600
    assert stage["average_seconds"] == 3600
    assert stage["average_calendar_seconds"] == 13 * 3600
    assert stage["average_outside_seconds"] == 12 * 3600
    html = render_to_string("core/_turnaround_stage.html", {"stage": stage, "id_prefix": "test"})
    assert "13 hrs total wait − 12 hrs excluded ≈ 1 hr within office hours" in html
    assert "2 hrs counted ÷ 2 confirmed handovers ≈ 1 hr" in html


def test_no_samples_has_no_fake_calculation():
    stage = analytics._stage("receipt", "Pending Receipt", "sent until confirmed", "handover", [])
    assert stage["average_outside_seconds"] is None
    html = render_to_string("core/_turnaround_stage.html", {"stage": stage, "id_prefix": "test"})
    assert "Average breakdown" not in html
    assert "None confirmed" in html


def test_chart_popup_table_and_axis_use_the_same_hours_as_the_cards():
    sample = measured_sample(at(5, 16), at(6, 9))
    row = {"month": date(2026, 10, 6)}
    for key in ("receipt", "processing", "lifetime"):
        row.update(analytics._turnaround_row(key, [sample], 28800))
    view = DashboardView()
    trend = {"rows": [row], "has_data": True, "ceiling": 1}
    point = view._trend_months(trend)[0]["points"][0]
    cell = view._turnaround_table_rows([row], view.TREND_SERIES)[0]["cells"][0]
    assert point["office_seconds"] == cell["office_seconds"] == 7200
    assert point["calendar_seconds"] == cell["calendar_seconds"] == 61200
    assert "2 hrs within office hours, 17 hrs total wait" in view._trend_months(trend)[0]["summary"]
    assert row["receipt"] == 0.25  # The geometry has not been rescaled or rounded.
    assert office_hours_tick(row["receipt"], 8) == "2"
    assert office_hours_tick(2, 5.5) == "11"


def test_guide_shows_how_the_configured_schedule_works():
    with override_settings(OFFICE_DAY_START=time(9), OFFICE_DAY_END=time(15),
                           OFFICE_LUNCH_START=time(12), OFFICE_LUNCH_END=time(12)):
        html = render_to_string("core/_turnaround_explanation.html", {"turnaround": {
            "month": date(2026, 10, 1), "office_schedule": office_schedule_explanation(),
            "working_day_hours": 6,
        }})
    assert "9:00 AM to 3:00 PM = 6 hrs total wait − 0 mins excluded = 6 hrs" in html
    assert "6 hours is the length of a full working day, not a fixed deduction" in html
    assert "It does not measure hands-on work" in html
