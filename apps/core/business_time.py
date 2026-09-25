"""Elapsed time counted in office hours rather than in calendar hours.

A document routed at 4PM on Friday and received at 9AM on Monday sat unattended
for about one working hour, but calendar arithmetic reports "2 days 17 hrs" —
which reads as a delay by the receiving office and is really a weekend. Every
turnaround figure computed on wall-clock time carries that distortion, and it is
worst exactly where it matters most: the documents that cross a weekend or a
long holiday look like the slowest ones in the office.

So this walks the interval and counts only the parts of it that fall inside
office hours. Two consequences worth being clear about:

**A working day is eight office hours.** The window is 8AM-5PM with lunch from
12PM to 1PM, both settings, and lunch is not counted: 8AM-5PM is eight hours,
a morning 8AM-12PM is four, an afternoon 1PM-5PM is four, and a document handed
over at 12:10PM and received at 12:50PM waited no office time at all. Lunch is
modelled rather than the day capped (which this did before, at seven hours) so
that a partial day is exact: a cap charged 8AM-4PM as a full day, and charged a
wait across the lunch hour as if somebody were at the desk.

**Holidays are excluded**, from the `Holiday` table system administrators keep
under Administration. A holiday counts no office time at all, whole day. The
figures are only as right as that table: a holiday nobody entered is counted
as a working day, which is why the explanation on screen names the table.

The holiday set is loaded once per calculation, not once per interval: a
report averages hundreds of intervals, and a query each would be hundreds of
queries. Callers that measure many intervals load it with `load_holidays()`
and pass it in; a single interval loads its own.

Calendar time is kept alongside, never replaced. It is what a requester actually
waited, and for "how long did this take from where I stand" it is the honest
number — office hours answer a different question, about how much working time
an office had to act.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.utils import timezone


def _setting(name: str, default):
    return getattr(settings, name, default)


@dataclass(frozen=True)
class Holidays:
    """The closed days, as dates and as every-year day-and-month pairs."""

    dates: frozenset = frozenset()
    every_year: frozenset = frozenset()

    def __contains__(self, day: date) -> bool:
        return day in self.dates or (day.month, day.day) in self.every_year


NO_HOLIDAYS = Holidays()


def load_holidays() -> Holidays:
    """Every active holiday, in one query."""
    from apps.core.models import Holiday

    dates, every_year = set(), set()
    for day, recurring in Holiday.active.values_list("date", "recurring"):
        if recurring:
            every_year.add((day.month, day.day))
        else:
            dates.add(day)
    return Holidays(frozenset(dates), frozenset(every_year))


def office_day_bounds(day: date) -> tuple[time, time]:
    """The start and end of the office day, as configured."""
    return (
        _setting("OFFICE_DAY_START", time(8, 0)),
        _setting("OFFICE_DAY_END", time(17, 0)),
    )


def lunch_bounds(day: date) -> tuple[time, time]:
    """The lunch break, as configured. Equal start and end means no break."""
    return (
        _setting("OFFICE_LUNCH_START", time(12, 0)),
        _setting("OFFICE_LUNCH_END", time(13, 0)),
    )


def _overlap_seconds(start, end, window_start, window_end) -> int:
    """How much of [start, end) falls inside [window_start, window_end)."""
    return max(0, int((min(end, window_end) - max(start, window_start)).total_seconds()))


def is_working_day(day: date, holidays: Holidays | None = None) -> bool:
    """Monday-Friday, less the holidays."""
    if day.weekday() >= _setting("OFFICE_WEEK_DAYS", 5):
        return False
    return day not in (load_holidays() if holidays is None else holidays)


def business_seconds_between(start, end, holidays: Holidays | None = None) -> int:
    """Seconds between two datetimes that fall inside office hours.

    Walks day by day rather than trying to compute it in closed form: the
    closed-form version has to special-case the first day, the last day, the
    single-day case and the empty case, and it is the single-day case that gets
    silently wrong. Intervals here span days, not years, so the loop is cheap
    and it is obvious what it does.
    """
    if start is None or end is None:
        return 0
    start, end = timezone.localtime(start), timezone.localtime(end)
    if end <= start:
        return 0

    if holidays is None:
        holidays = load_holidays()
    tz = timezone.get_current_timezone()
    total = 0

    day = start.date()
    while day <= end.date():
        if not is_working_day(day, holidays):
            day += timedelta(days=1)
            continue

        def at(clock, day=day):
            return timezone.make_aware(datetime.combine(day, clock), tz)

        opens_at, closes_at = office_day_bounds(day)
        lunch_from, lunch_to = lunch_bounds(day)
        opens, closes = at(opens_at), at(closes_at)
        # The break only subtracts what lies inside the office window, so a
        # misconfigured lunch can never make a day count negative.
        lunch_start = min(max(at(lunch_from), opens), closes)
        lunch_end = min(max(at(lunch_to), lunch_start), closes)

        total += _overlap_seconds(start, end, opens, closes) - _overlap_seconds(
            start, end, lunch_start, lunch_end
        )
        day += timedelta(days=1)

    return total


def business_timedelta_between(start, end) -> timedelta:
    return timedelta(seconds=business_seconds_between(start, end))


def working_day_seconds() -> int:
    """The length of one working day, in counted seconds.

    The one definition every turnaround figure uses, and derived rather than
    configured: it is exactly what `business_seconds_between` counts for one
    whole office day, the window less lunch. A separate "hours per day" setting
    could disagree with the window; this cannot.
    """
    today = date(2000, 1, 3)  # a Monday; only the clock times matter
    tz = timezone.get_current_timezone()
    opens_at, closes_at = office_day_bounds(today)
    return business_seconds_between(
        timezone.make_aware(datetime.combine(today, opens_at), tz),
        timezone.make_aware(datetime.combine(today, closes_at), tz),
        holidays=NO_HOLIDAYS,  # the length of a day, not whether this one is off
    )


def working_day_hours() -> float:
    """`working_day_seconds` in hours, for the sentences that state it."""
    return round(working_day_seconds() / 3600, 2)


def humanise_business_seconds(seconds) -> str:
    """Office-hours seconds as office language: '2 days 4 hrs', '3 hrs'.

    A "day" here is one working day of counted time, not 24 hours — saying
    "1 day" when eight working hours have passed is what the reader means by a
    day, and dividing by 86400 would report the same interval as "8 hrs".
    """
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return "under a minute"

    days, remainder = divmod(seconds, working_day_seconds())
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    return _two_units(days, "day", hours, "hr", minutes, "min")


def _two_units(days, day_word, hours, hour_word, minutes, minute_word) -> str:
    """The two largest units that are not zero: "4 days", not "4 days 0 hrs".

    Shared with the calendar figure in analytics.humanise_duration, so the two
    numbers printed one above the other are worded the same way.
    """
    def unit(value, word):
        return f"{value} {word}{'s' if value != 1 else ''}"

    if days:
        return unit(days, day_word) + (f" {unit(hours, hour_word)}" if hours else "")
    if hours:
        return unit(hours, hour_word) + (f" {unit(minutes, minute_word)}" if minutes else "")
    return unit(minutes, minute_word)


def average_business_seconds(pairs, holidays: Holidays | None = None) -> float | None:
    """Mean office-hours duration over (start, end) pairs, or None if empty.

    Computed in Python rather than in the database because the office-hours rule
    is a calendar walk, not an expression the ORM can average — an SQL AVG over
    `end - start` is exactly the calendar figure this module exists to replace.
    The inputs are already narrowed by the report's filters, so the set is the
    page's own result rows rather than the whole table.
    """
    if holidays is None:
        holidays = load_holidays()
    totals = [
        business_seconds_between(start, end, holidays)
        for start, end in pairs
        if start is not None and end is not None
    ]
    if not totals:
        return None
    return sum(totals) / len(totals)


def _clock(value: time) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def office_hours_caveat() -> str:
    """The basis every office-hours figure is counted on, as one sentence.

    Built from the settings rather than written out, so the words on screen
    cannot say eight hours while the code counts seven — which is what a
    hand-written sentence did the last time the day changed.
    """
    opens, closes = office_day_bounds(date(2000, 1, 3))
    lunch_from, lunch_to = lunch_bounds(date(2000, 1, 3))
    lunch = (
        f", lunch {_clock(lunch_from)}–{_clock(lunch_to)} not counted"
        if lunch_to > lunch_from else ""
    )
    return (
        f"Turnaround is counted in office hours ({working_day_hours():g} hours = "
        f"1 working day: {_clock(opens)}–{_clock(closes)}{lunch}), excluding weekends "
        "and the holidays listed under Administration."
    )
