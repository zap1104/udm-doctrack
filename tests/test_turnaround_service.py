"""One turnaround service: a period's average, fastest and slowest per stage.

The dashboard's monthly trend, the month's summary beside it, Reports and the
memo all read `analytics.turnaround()` or `turnaround_by_month()`, and both are
built from one collection of intervals — so a figure on two pages is one figure.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from django.utils import timezone

from apps.core import analytics
from apps.tracking.models import RoutingStep, Status, TrackingRecord
from apps.tracking.services import create_draft_record, route_record

HOUR = 3600


def _at(day, hour, minute=0):
    return timezone.make_aware(datetime(day.year, day.month, day.day, hour, minute))


def _finished(users, offices, memo_type, *, created, sent, received, completed, due=None):
    """A completed record whose one handover and whose life took exactly this."""
    record = create_draft_record(
        user=users["med"], subject="Measured", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    RoutingStep.objects.filter(record=record).update(sent_at=sent, received_at=received)
    TrackingRecord.objects.filter(pk=record.pk).update(
        status=Status.COMPLETED, created_at=created, first_received_at=received,
        completed_at=completed, due_at=due,
    )
    record.refresh_from_db()
    return record


def _stage(result, key):
    return next(stage for stage in result["stages"] if stage["key"] == key)


@pytest.fixture
def august(users, offices, memo_type):
    """Three documents finished in August 2026, of 1, 2 and 3 working days."""
    made = []
    for days, start in ((1, date(2026, 8, 3)), (2, date(2026, 8, 10)), (3, date(2026, 8, 17))):
        end = date(start.year, start.month, start.day + days - 1)
        made.append(_finished(
            users, offices, memo_type,
            created=_at(start, 8), sent=_at(start, 8), received=_at(start, 9),
            completed=_at(end, 17), due=_at(end, 17),
        ))
    return made


@pytest.mark.django_db
def test_a_month_reports_average_fastest_and_slowest_for_every_stage(august):
    result = analytics.turnaround(TrackingRecord.objects.all(), month=date(2026, 8, 1))
    lifetime = _stage(result, "lifetime")

    assert [s["key"] for s in result["stages"]] == ["receipt", "processing", "lifetime"]
    assert lifetime["samples"] == 3
    assert lifetime["average_label"] == "2 days"
    assert lifetime["fastest"]["office_label"] == "1 day"
    assert lifetime["slowest"]["office_label"] == "3 days"
    assert lifetime["fastest"]["tracking_number"] == august[0].tracking_number
    assert lifetime["slowest"]["url"] == august[2].get_absolute_url()
    assert result["on_time_total"] == 3 and result["on_time_percent"] == 100


@pytest.mark.django_db
def test_the_month_and_its_point_on_the_trend_are_one_figure(august, settings):
    """Asked two ways, the same month must answer the same."""
    records = TrackingRecord.objects.all()
    month = date(2026, 8, 1)
    single = analytics.turnaround(records, month=month)
    row = next(
        r for r in analytics.turnaround_by_month(records, months_back=60)["rows"] if r["month"] == month
    )

    for key in ("receipt", "processing", "lifetime"):
        assert single[key] == row[f"{key}_label"], key
        assert single[f"{key}_calendar"] == row[f"{key}_calendar"], key
        assert single[f"{key}_samples"] == row[f"{key}_samples"], key
    assert (single["on_time"], single["on_time_total"]) == (row["on_time"], row["closed"])


@pytest.mark.django_db
def test_a_month_holds_only_what_finished_inside_it(users, offices, memo_type):
    last_of_july = _finished(
        users, offices, memo_type, created=_at(date(2026, 7, 31), 8), sent=_at(date(2026, 7, 31), 8),
        received=_at(date(2026, 7, 31), 9), completed=_at(date(2026, 7, 31), 17),
    )
    records = TrackingRecord.objects.all()

    assert _stage(analytics.turnaround(records, month=date(2026, 8, 1)), "lifetime")["samples"] == 0
    july = _stage(analytics.turnaround(records, month=date(2026, 7, 1)), "lifetime")
    assert july["samples"] == 1 and july["fastest"]["tracking_number"] == last_of_july.tracking_number
    assert analytics.turnaround(records)["lifetime_samples"] == 1, "no month: every month"


@pytest.mark.django_db
def test_work_done_entirely_outside_office_hours_is_never_fastest(users, offices, memo_type):
    """A handover sent and confirmed on a Saturday took no office time. It is
    counted in the average, and said to be outside office hours, but calling it
    the fastest would name the same weekend every month."""
    saturday, monday = date(2026, 8, 8), date(2026, 8, 10)
    _finished(
        users, offices, memo_type, created=_at(saturday, 9), sent=_at(saturday, 9),
        received=_at(saturday, 10), completed=_at(monday, 17),
    )
    weekday = _finished(
        users, offices, memo_type, created=_at(monday, 8), sent=_at(monday, 8),
        received=_at(monday, 10), completed=_at(monday, 17),
    )
    receipt = _stage(analytics.turnaround(TrackingRecord.objects.all(), month=date(2026, 8, 1)), "receipt")

    assert receipt["samples"] == 2
    assert receipt["outside_office_hours"] == 1
    assert receipt["fastest"]["tracking_number"] == weekday.tracking_number
    assert receipt["average_seconds"] == HOUR, "(0 + 2 hours) / 2: still in the average"


@pytest.mark.django_db
def test_a_tie_goes_to_whoever_finished_first(users, offices, memo_type):
    later = _finished(
        users, offices, memo_type, created=_at(date(2026, 8, 12), 8), sent=_at(date(2026, 8, 12), 8),
        received=_at(date(2026, 8, 12), 9), completed=_at(date(2026, 8, 12), 17),
    )
    earlier = _finished(
        users, offices, memo_type, created=_at(date(2026, 8, 5), 8), sent=_at(date(2026, 8, 5), 8),
        received=_at(date(2026, 8, 5), 9), completed=_at(date(2026, 8, 5), 17),
    )
    lifetime = _stage(analytics.turnaround(TrackingRecord.objects.all(), month=date(2026, 8, 1)), "lifetime")

    assert lifetime["fastest"]["office_label"] == lifetime["slowest"]["office_label"] == "1 day"
    assert lifetime["fastest"]["tracking_number"] == earlier.tracking_number
    assert lifetime["slowest"]["tracking_number"] == earlier.tracking_number
    assert later.tracking_number != earlier.tracking_number


@pytest.mark.django_db
def test_an_empty_month_says_so_rather_than_inventing_a_figure(users):
    result = analytics.turnaround(TrackingRecord.objects.all(), month=date(2026, 8, 1))

    for stage in result["stages"]:
        assert stage["samples"] == 0
        assert stage["fastest"] is None and stage["slowest"] is None
        assert stage["average_label"] == "—"
    assert result["has_on_time"] is False


@pytest.mark.django_db
def test_a_period_costs_the_same_few_queries_however_much_is_in_it(
    august, django_assert_num_queries
):
    """Holidays, handovers, documents, and the live waiting count."""
    with django_assert_num_queries(4):
        analytics.turnaround(TrackingRecord.objects.all(), month=date(2026, 8, 1))
