"""Elapsed time counted in office hours rather than in calendar hours.

A document routed at 4PM on Friday and received at 9AM on Monday sat unattended
for about one working hour, but calendar arithmetic reports "2 days 17 hrs" —
which reads as a delay by the receiving office and is really a weekend. Every
turnaround figure computed on wall-clock time carries that distortion, and it is
worst exactly where it matters most: the documents that cross a weekend or a
long holiday look like the slowest ones in the office.

So this walks the interval and counts only the parts of it that fall inside
office hours. Two consequences worth being clear about:

**A day caps at OFFICE_HOURS_PER_DAY.** The window is 8AM-5PM, which is nine
hours, but the countable day is seven — nobody is at the desk for the lunch
break and the ends of the day. Rather than model a lunch hour (which offices
here take at different times), the day's countable total is simply capped. A
document that sits from 8AM to 5PM is charged seven hours, not nine.

**Holidays are not excluded.** There is no holiday table in this system, and
inventing one that is wrong is worse than not having one — a figure people
believe is exact, that quietly mis-states every December. A document spanning
Rizal Day is over-charged by one working day. Anything showing an office-hours
figure must therefore say it excludes weekends and counts office hours only,
*not* that it is exact; `apps.core.views` labels them that way.

Calendar time is kept alongside, never replaced. It is what a requester actually
waited, and for "how long did this take from where I stand" it is the honest
number — office hours answer a different question, about how much working time
an office had to act.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.utils import timezone


def _setting(name: str, default):
    return getattr(settings, name, default)


def office_day_bounds(day: date) -> tuple[time, time]:
    """The start and end of the office day, as configured."""
    return (
        _setting("OFFICE_DAY_START", time(8, 0)),
        _setting("OFFICE_DAY_END", time(17, 0)),
    )


def is_working_day(day: date) -> bool:
    """Monday-Friday. Holidays are not known to this system — see the module
    docstring; they are counted as working days and slightly over-charge the
    intervals that span them."""
    return day.weekday() < _setting("OFFICE_WEEK_DAYS", 5)


def business_seconds_between(start, end) -> int:
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

    per_day_cap = int(_setting("OFFICE_HOURS_PER_DAY", 7) * 3600)
    tz = timezone.get_current_timezone()
    total = 0

    day = start.date()
    while day <= end.date():
        if not is_working_day(day):
            day += timedelta(days=1)
            continue

        opens_at, closes_at = office_day_bounds(day)
        opens = timezone.make_aware(datetime.combine(day, opens_at), tz)
        closes = timezone.make_aware(datetime.combine(day, closes_at), tz)

        # The slice of this office day the interval actually covers.
        window_start = max(start, opens)
        window_end = min(end, closes)
        if window_end > window_start:
            # Capped per day, not over the whole interval: a five-day wait is
            # five capped days, and capping the total instead would make every
            # long interval report the same number.
            total += min(int((window_end - window_start).total_seconds()), per_day_cap)
        day += timedelta(days=1)

    return total


def business_timedelta_between(start, end) -> timedelta:
    return timedelta(seconds=business_seconds_between(start, end))


def humanise_business_seconds(seconds) -> str:
    """Office-hours seconds as office language: '2 days 4 hrs', '3 hrs'.

    A "day" here is OFFICE_HOURS_PER_DAY of counted time, not 24 hours — saying
    "1 day" when seven working hours have passed is what the reader means by a
    day, and dividing by 86400 would report the same interval as "7 hrs".
    """
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return "under a minute"

    day_seconds = int(_setting("OFFICE_HOURS_PER_DAY", 7) * 3600)
    days, remainder = divmod(seconds, day_seconds)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    if days:
        return f"{days} day{'s' if days != 1 else ''} {hours} hr{'s' if hours != 1 else ''}"
    if hours:
        return f"{hours} hr{'s' if hours != 1 else ''} {minutes} min{'s' if minutes != 1 else ''}"
    return f"{minutes} min{'s' if minutes != 1 else ''}"


def average_business_seconds(pairs) -> float | None:
    """Mean office-hours duration over (start, end) pairs, or None if empty.

    Computed in Python rather than in the database because the office-hours rule
    is a calendar walk, not an expression the ORM can average — an SQL AVG over
    `end - start` is exactly the calendar figure this module exists to replace.
    The inputs are already narrowed by the report's filters, so the set is the
    page's own result rows rather than the whole table.
    """
    totals = [
        business_seconds_between(start, end)
        for start, end in pairs
        if start is not None and end is not None
    ]
    if not totals:
        return None
    return sum(totals) / len(totals)


#: Shown wherever an office-hours figure appears, so nobody reads it as exact.
OFFICE_HOURS_CAVEAT = (
    "Office hours only — weekends excluded, holidays not: this system has no "
    "holiday calendar, so an interval spanning one is over-counted by a day."
)
