"""Turnaround counted in office hours.

The bug this replaces: a document routed Friday 4PM and received Monday 9AM
reported "2 days 17 hrs", which reads as the receiving office being slow and is
really a weekend. Every turnaround figure carried that distortion, worst for the
documents that crossed a weekend — so the offices that looked slowest were often
the ones whose documents happened to arrive on a Friday.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from apps.core.business_time import (
    average_business_seconds,
    business_seconds_between,
    humanise_business_seconds,
    is_working_day,
)


def at(year, month, day, hour, minute=0):
    return timezone.make_aware(
        datetime(year, month, day, hour, minute), timezone.get_current_timezone()
    )


HOUR = 3600


# --- the weekend case this exists for --------------------------------------
def test_a_weekend_costs_almost_nothing():
    """Friday 4PM to Monday 9AM: one office hour on Friday, one on Monday."""
    friday_4pm = at(2026, 8, 28, 16)
    monday_9am = at(2026, 8, 31, 9)

    assert is_working_day(friday_4pm.date()) is True
    assert business_seconds_between(friday_4pm, monday_9am) == 2 * HOUR

    calendar = (monday_9am - friday_4pm).total_seconds()
    assert calendar > 60 * HOUR, "the calendar figure is what made this misleading"


def test_the_same_gap_inside_one_day_is_counted_in_full():
    assert business_seconds_between(at(2026, 8, 26, 9), at(2026, 8, 26, 11)) == 2 * HOUR


# --- the window ------------------------------------------------------------
def test_time_before_opening_and_after_closing_does_not_count():
    # 6AM to 8AM is entirely before the office opens.
    assert business_seconds_between(at(2026, 8, 26, 6), at(2026, 8, 26, 8)) == 0
    # 5PM to 11PM is entirely after it closes.
    assert business_seconds_between(at(2026, 8, 26, 17), at(2026, 8, 26, 23)) == 0


def test_an_overnight_gap_counts_only_the_office_parts():
    """4PM Wednesday to 9AM Thursday: one hour, then one hour."""
    assert business_seconds_between(at(2026, 8, 26, 16), at(2026, 8, 27, 9)) == 2 * HOUR


def test_a_whole_day_is_capped_at_the_configured_hours():
    """8AM to 5PM is nine clock hours but seven countable ones — nobody is at
    the desk for the lunch break and the ends of the day."""
    seconds = business_seconds_between(at(2026, 8, 26, 8), at(2026, 8, 26, 17))

    assert seconds == int(settings.OFFICE_HOURS_PER_DAY * HOUR)
    assert seconds < 9 * HOUR


def test_the_cap_is_per_day_not_per_interval():
    """Three full working days are three capped days, not one."""
    seconds = business_seconds_between(at(2026, 8, 26, 8), at(2026, 8, 28, 17))
    assert seconds == 3 * int(settings.OFFICE_HOURS_PER_DAY * HOUR)


def test_a_weekend_only_interval_is_zero():
    assert business_seconds_between(at(2026, 8, 29, 9), at(2026, 8, 30, 17)) == 0


# --- degenerate inputs -----------------------------------------------------
@pytest.mark.parametrize(
    "start, end",
    [
        (None, at(2026, 8, 26, 9)),
        (at(2026, 8, 26, 9), None),
        (None, None),
        (at(2026, 8, 26, 11), at(2026, 8, 26, 9)),  # end before start
        (at(2026, 8, 26, 9), at(2026, 8, 26, 9)),   # identical
    ],
)
def test_nonsense_intervals_are_zero_rather_than_negative(start, end):
    assert business_seconds_between(start, end) == 0


# --- the settings are settings --------------------------------------------
def test_the_window_is_settings_driven():
    assert settings.OFFICE_DAY_START.hour == 8
    assert settings.OFFICE_DAY_END.hour == 17
    assert settings.OFFICE_HOURS_PER_DAY == 7
    assert settings.OFFICE_WEEK_DAYS == 5


def test_a_shorter_week_is_honoured(settings):
    """Four-day week: Friday stops counting."""
    settings.OFFICE_WEEK_DAYS = 4
    assert is_working_day(at(2026, 8, 28, 9).date()) is False
    assert business_seconds_between(at(2026, 8, 28, 9), at(2026, 8, 28, 16)) == 0


# --- wording ---------------------------------------------------------------
def test_a_day_means_a_working_day_not_twenty_four_hours():
    """Saying "7 hrs" where the reader means "a day" is the confusion this
    avoids — the unit has to match the thing being counted."""
    one_day = int(settings.OFFICE_HOURS_PER_DAY * HOUR)

    assert humanise_business_seconds(one_day) == "1 day 0 hrs"
    assert humanise_business_seconds(one_day * 2) == "2 days 0 hrs"


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (None, "—"),
        (30, "under a minute"),
        (90 * 60, "1 hr 30 mins"),
        (45 * 60, "45 mins"),
    ],
)
def test_humanising_reads_like_office_language(seconds, expected):
    assert humanise_business_seconds(seconds) == expected


# --- averaging -------------------------------------------------------------
def test_an_average_over_no_pairs_is_none_not_zero():
    """Zero would print as "under a minute", which claims a measurement that
    was never taken."""
    assert average_business_seconds([]) is None
    assert average_business_seconds([(None, None)]) is None
    assert humanise_business_seconds(average_business_seconds([])) == "—"


def test_pairs_with_a_missing_end_are_skipped_not_counted_as_zero():
    pairs = [
        (at(2026, 8, 26, 9), at(2026, 8, 26, 11)),   # 2 hours
        (at(2026, 8, 26, 9), None),                  # still open, not a zero
    ]
    assert average_business_seconds(pairs) == 2 * HOUR


def test_the_average_is_the_mean_of_office_hours_not_of_calendar_hours():
    pairs = [
        (at(2026, 8, 28, 16), at(2026, 8, 31, 9)),  # over a weekend: 2 hours
        (at(2026, 8, 26, 9), at(2026, 8, 26, 13)),  # same day: 4 hours
    ]
    assert average_business_seconds(pairs) == 3 * HOUR


# --- what the figure does not claim ---------------------------------------
def test_a_holiday_is_counted_as_a_working_day():
    """There is no holiday table, and inventing a wrong one is worse than
    having none. The UI says so rather than implying the figure is exact —
    this test exists so the limitation stays deliberate."""
    rizal_day = at(2026, 12, 30, 9)  # a Wednesday
    assert is_working_day(rizal_day.date()) is True
    assert business_seconds_between(rizal_day, rizal_day + timedelta(hours=2)) == 2 * HOUR
