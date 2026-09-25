"""Turnaround counted in office hours.

The bug this replaces: a document routed Friday 4PM and received Monday 9AM
reported "2 days 17 hrs", which reads as the receiving office being slow and is
really a weekend. Every turnaround figure carried that distortion, worst for the
documents that crossed a weekend — so the offices that looked slowest were often
the ones whose documents happened to arrive on a Friday.
"""

from __future__ import annotations

from datetime import datetime, time

import pytest
from django.conf import settings
from django.utils import timezone

from apps.core.business_time import (
    NO_HOLIDAYS,
    average_business_seconds,
    business_seconds_between,
    humanise_business_seconds,
    is_working_day,
    office_hours_caveat,
    working_day_hours,
    working_day_seconds,
)
from apps.core.models import Holiday

#: Every interval now asks the holiday table, so these are database tests.
pytestmark = pytest.mark.django_db


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


def test_a_whole_day_is_eight_hours_because_lunch_is_not_counted():
    """8AM to 5PM is nine clock hours; the 12PM-1PM lunch leaves eight."""
    seconds = business_seconds_between(at(2026, 8, 26, 8), at(2026, 8, 26, 17))

    assert seconds == 8 * HOUR == working_day_seconds()


@pytest.mark.parametrize(
    ("start", "end", "hours"),
    [
        ((8, 0), (12, 0), 4),      # a morning
        ((13, 0), (17, 0), 4),     # an afternoon
        ((12, 0), (13, 0), 0),     # lunch alone
        ((12, 10), (12, 50), 0),   # handed over and received inside lunch
        ((11, 30), (13, 30), 1),   # half an hour either side of lunch
        ((8, 0), (16, 0), 7),      # a cap charged this as a whole day
        ((7, 0), (18, 0), 8),      # before opening and after closing
    ],
)
def test_a_partial_day_counts_exactly_what_it_covers(start, end, hours):
    seconds = business_seconds_between(at(2026, 8, 26, *start), at(2026, 8, 26, *end))
    assert seconds == hours * HOUR


def test_several_days_are_several_working_days():
    """Wednesday 8AM to Friday 5PM: three full days, 24 office hours."""
    seconds = business_seconds_between(at(2026, 8, 26, 8), at(2026, 8, 28, 17))
    assert seconds == 3 * working_day_seconds() == 24 * HOUR


def test_a_day_is_the_same_length_all_year():
    """Asia/Manila has no daylight saving, so no date gains or loses an hour."""
    assert settings.TIME_ZONE == "Asia/Manila"
    for month in (1, 3, 6, 11):
        day = next(d for d in range(1, 8) if at(2026, month, d, 9).weekday() < 5)
        seconds = business_seconds_between(at(2026, month, day, 8), at(2026, month, day, 17))
        assert seconds == 8 * HOUR, month


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
    assert (settings.OFFICE_LUNCH_START.hour, settings.OFFICE_LUNCH_END.hour) == (12, 13)
    assert settings.OFFICE_WEEK_DAYS == 5
    assert working_day_hours() == 8


def test_the_day_length_follows_the_window_rather_than_a_second_setting(settings):
    """There is no hours-per-day setting to disagree with the window."""
    assert not hasattr(settings, "OFFICE_HOURS_PER_DAY")
    settings.OFFICE_LUNCH_END = time(12, 30)
    assert working_day_seconds() == int(8.5 * HOUR)
    settings.OFFICE_LUNCH_END = time(12, 0)  # no break at all
    assert working_day_seconds() == 9 * HOUR


def test_a_lunch_outside_the_window_is_clipped_not_subtracted(settings):
    settings.OFFICE_LUNCH_START, settings.OFFICE_LUNCH_END = time(18, 0), time(19, 0)
    assert working_day_seconds() == 9 * HOUR


def test_a_shorter_week_is_honoured(settings):
    """Four-day week: Friday stops counting."""
    settings.OFFICE_WEEK_DAYS = 4
    assert is_working_day(at(2026, 8, 28, 9).date()) is False
    assert business_seconds_between(at(2026, 8, 28, 9), at(2026, 8, 28, 16)) == 0


# --- wording ---------------------------------------------------------------
def test_a_day_means_a_working_day_not_twenty_four_hours():
    """Saying "7 hrs" where the reader means "a day" is the confusion this
    avoids — the unit has to match the thing being counted."""
    one_day = working_day_seconds()

    # A zero second unit is dropped: "1 day", not "1 day 0 hrs".
    assert humanise_business_seconds(one_day) == "1 day"
    assert humanise_business_seconds(one_day * 2) == "2 days"


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
        (at(2026, 8, 26, 8), at(2026, 8, 26, 12)),  # same day: 4 hours
    ]
    assert average_business_seconds(pairs) == 3 * HOUR


# --- what the figure says about itself --------------------------------------
def test_the_explanation_states_the_basis_the_code_counts():
    text = office_hours_caveat()
    assert "8 hours = 1 working day" in text
    assert "8:00 AM–5:00 PM" in text
    assert "lunch 12:00 PM–1:00 PM not counted" in text
    assert "excluding weekends" in text


def test_the_explanation_follows_the_settings(settings):
    settings.OFFICE_LUNCH_END = time(12, 30)
    assert "8.5 hours = 1 working day" in office_hours_caveat()


# --- holidays -----------------------------------------------------------------
def test_a_holiday_counts_no_office_time():
    """Tuesday 4PM to Thursday 9AM across Rizal Day: an hour, nothing, an hour."""
    Holiday.objects.create(date=at(2026, 12, 30, 9).date(), name="Rizal Day")

    assert is_working_day(at(2026, 12, 30, 9).date()) is False
    assert business_seconds_between(at(2026, 12, 29, 16), at(2026, 12, 31, 9)) == 2 * HOUR


def test_a_recurring_holiday_applies_every_year():
    Holiday.objects.create(date=at(2020, 6, 12, 9).date(), name="Independence Day", recurring=True)

    assert is_working_day(at(2026, 6, 12, 9).date()) is False  # a Friday
    assert is_working_day(at(2026, 6, 11, 9).date()) is True


def test_a_one_off_holiday_does_not_repeat():
    """A typhoon suspension is one day, not the same date every year."""
    Holiday.objects.create(date=at(2025, 7, 22, 9).date(), name="Work suspension")

    assert is_working_day(at(2025, 7, 22, 9).date()) is False
    assert is_working_day(at(2026, 7, 22, 9).date()) is True  # a Wednesday


def test_a_retired_holiday_counts_as_a_working_day_again():
    Holiday.objects.create(date=at(2026, 12, 30, 9).date(), name="Rizal Day", is_active=False)
    assert is_working_day(at(2026, 12, 30, 9).date()) is True


def test_a_holiday_does_not_shorten_the_working_day():
    """The length of a day is not whether one particular day is off."""
    Holiday.objects.create(date=at(2000, 1, 3, 9).date(), name="Any day", recurring=True)
    assert working_day_seconds() == 8 * HOUR


def test_many_intervals_ask_the_holiday_table_once(django_assert_num_queries):
    pairs = [(at(2026, 8, day, 9), at(2026, 8, day, 11)) for day in (24, 25, 26, 27, 28)]
    with django_assert_num_queries(1):
        assert average_business_seconds(pairs) == 2 * HOUR


def test_a_caller_can_pass_the_holidays_it_already_loaded(django_assert_num_queries):
    with django_assert_num_queries(0):
        assert business_seconds_between(at(2026, 8, 26, 9), at(2026, 8, 26, 11), NO_HOLIDAYS) == 2 * HOUR
