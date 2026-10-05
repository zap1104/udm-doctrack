"""Independent calendar arithmetic and graph/count checks against real records."""
import calendar
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.core import analytics
from apps.core.business_time import NO_HOLIDAYS, business_seconds_between
from apps.core.checks import office_hours_configuration
from apps.tracking.models import RoutingStep, Status, TrackingRecord


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def quarter_hour_reference(start, end):
    """Count a fixed 15-minute clock grid, independently of production clipping."""
    counted = 0
    while start < end:
        if start.weekday() < 5 and (8 <= start.hour < 12 or 13 <= start.hour < 17):
            counted += 900
        start += timedelta(minutes=15)
    return counted


@pytest.mark.parametrize("day", [date(2024, 2, 29), date(2025, 12, 31), date(2026, 8, 3), date(2026, 8, 7), date(2026, 8, 8), date(2026, 8, 9)])
@pytest.mark.parametrize("clock", [0, 7, 8, 11, 12, 13, 16, 17, 23])
@pytest.mark.parametrize("hours", [0, 0.25, 1, 8, 24, 48, 96])
def test_working_time_matches_an_independent_clock_grid(day, clock, hours, settings):
    settings.OFFICE_DAY_START, settings.OFFICE_DAY_END = time(8), time(17)
    settings.OFFICE_LUNCH_START, settings.OFFICE_LUNCH_END = time(12), time(13)
    settings.OFFICE_WEEK_DAYS = 5
    with timezone.override("Asia/Manila"):
        start = at(day, clock)
        end = start + timedelta(hours=hours)
        assert business_seconds_between(start, end, NO_HOLIDAYS) == quarter_hour_reference(start, end)


@pytest.mark.django_db
@pytest.mark.parametrize("year", [2024, 2025, 2026, 2028])
@pytest.mark.parametrize("month", range(1, 13))
def test_daily_chart_covers_exact_calendar_days_including_leap_years(year, month):
    trend = analytics.turnaround_by_day(TrackingRecord.objects.none(), date(year, month, 1), now=at(date(2030, 1, 1), 0))
    assert len(trend["rows"]) == calendar.monthrange(year, month)[1]
    assert [row["month"].day for row in trend["rows"]] == list(range(1, calendar.monthrange(year, month)[1] + 1))
    assert not trend["has_data"]
    assert all(row[key] is None for row in trend["rows"] for key in ("receipt", "processing", "lifetime"))


@pytest.mark.django_db
@pytest.mark.parametrize("day,hour,expected", [(1, 0, 1), (1, 8, 1), (2, 0, 2), (28, 23, 28)])
def test_current_month_stops_today_and_future_month_is_empty(day, hour, expected):
    now = at(date(2026, 2, day), hour)
    query = TrackingRecord.objects.none()
    assert len(analytics.turnaround_by_day(query, date(2026, 2, 1), now=now)["rows"]) == expected
    future = analytics.turnaround_by_day(query, date(2026, 3, 1), now=now)
    assert future["rows"] == [] and not future["has_data"]


def test_configuration_rejects_a_zero_length_working_day(settings):
    settings.OFFICE_DAY_START, settings.OFFICE_DAY_END = time(8), time(17)
    settings.OFFICE_LUNCH_START, settings.OFFICE_LUNCH_END = time(8), time(17)
    assert "doctrack.E017" in [error.id for error in office_hours_configuration(None)]


@pytest.mark.parametrize("days", [0, -1, 8])
def test_configuration_rejects_invalid_working_weekdays(settings, days):
    settings.OFFICE_WEEK_DAYS = days
    assert "doctrack.E018" in [error.id for error in office_hours_configuration(None)]


@pytest.mark.django_db
def test_daily_monthly_summary_table_and_permissions_agree(client, offices, memo_type):
    users = {}
    for code, role in [("MED", "USER"), ("SUP", "ADMIN"), ("HR", "VIEWER"), ("REC", "SYSTEM_ADMIN")]:
        users[code] = get_user_model().objects.create_user(username=f"graph-{code}", office=offices[code], role=role)
    expected = {}
    for code, count in [("MED", 3), ("SUP", 5), ("HR", 7)]:
        population = {key: [] for key in ("receipt", "processing", "lifetime")}
        for index in range(count):
            day = date(2026, 8, 8) if index % 3 == 0 else date(2026, 8, 3)
            created, received, end = at(day, 8), at(day, 9), at(day, 17)
            record = TrackingRecord.objects.create(
                tracking_number=f"GRAPH-{code}-{index}", subject=f"Graph {code} {index}",
                originating_office=offices[code], current_office=offices[code],
                created_by=users[code], document_type=memo_type,
                status=Status.COMPLETED_PENDING_UPLOAD if index % 2 else Status.COMPLETED,
                first_received_at=received, completed_at=end, due_at=end,
            )
            TrackingRecord.objects.filter(pk=record.pk).update(created_at=created)
            for sequence, start, finish in [(1, created, received), (2, received, at(day, 11))]:
                RoutingStep.objects.create(record=record, sequence=sequence, batch=sequence,
                                           from_office=offices[code], to_office=offices[code],
                                           sent_by=users[code], sent_at=start, received_at=finish)
                population["receipt"].append(quarter_hour_reference(start, finish))
            population["processing"].append(quarter_hour_reference(received, end))
            population["lifetime"].append(quarter_hour_reference(created, end))
        expected[code] = population
    expected["REC"] = {key: sum((population[key] for population in expected.values()), []) for key in ("receipt", "processing", "lifetime")}
    now = at(date(2026, 10, 2), 12)
    for code, user in users.items():
        visible = TrackingRecord.objects.visible_to(user)
        daily = analytics.turnaround_by_day(visible, date(2026, 8, 1), now=now)
        monthly = analytics.turnaround_by_month(visible, year=2026, now=now)["rows"][7]
        summary = analytics.turnaround(visible, month=date(2026, 8, 1), now=now)
        for key, population in expected[code].items():
            mean = sum(population) / len(population)
            stage = next(stage for stage in summary["stages"] if stage["key"] == key)
            assert stage["samples"] == monthly[f"{key}_samples"] == len(population)
            assert stage["average_seconds"] == pytest.approx(mean)
            assert monthly[key] * 28800 == pytest.approx(mean)
            assert sum(row[f"{key}_samples"] for row in daily["rows"]) == len(population)
            weighted = sum(row[key] * 28800 * row[f"{key}_samples"] for row in daily["rows"] if row[key] is not None) / len(population)
            assert weighted == pytest.approx(mean)
            assert stage["zero_working_time"] == population.count(0)
            assert stage["documents"] == len(expected[code]["lifetime"])
        for path in ("/", "/tracking/reports/"):
            with patch("django.utils.timezone.now", return_value=now):
                # Freeze login and request together so the intentional idle
                # timeout does not expire a session created at the real clock.
                client.force_login(user)
                response = client.get(path, {"month": "2026-08", "year": "2026", "office": offices[code].pk if code != "REC" else "all"})
            assert response.status_code == 200
            assert response.context["turnaround"]["stages"] == summary["stages"], (code, path)
            table_rows = response.context["turnaround_table_rows"]
            for row in table_rows:
                for cell in row["cells"]:
                    assert cell["samples"] == row[f'{cell["key"]}_samples']
                    assert cell["documents"] == row[f'{cell["key"]}_documents']
            if code != "REC":
                foreign = "SUP" if code == "MED" else "MED"
                with patch("django.utils.timezone.now", return_value=now):
                    denied = client.get(path, {"month": "2026-08", "office": offices[foreign].pk})
                assert denied.status_code == 200
                assert denied.context["turnaround"]["receipt_samples"] <= len(expected[code]["receipt"])
                assert f"GRAPH-{foreign}-" not in denied.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("future_year", [2027, 9999])
def test_year_picker_uses_local_dates_and_future_events_cannot_break_it(client, offices, future_year):
    user = get_user_model().objects.create_user(username="graph-years", office=offices["REC"], role="SYSTEM_ADMIN")
    now = at(date(2026, 10, 2), 12)
    with timezone.override("Asia/Manila"):
        record = TrackingRecord.objects.create(
            tracking_number="GRAPH-YEAR-BOUNDARY", subject="Year boundary",
            originating_office=offices["REC"], current_office=offices["REC"], created_by=user,
            status=Status.COMPLETED, first_received_at=at(date(2026, 1, 1), 0),
            completed_at=at(date(future_year, 1, 1), 0),
        )
        # Local January 1 is still December 31 in storage (UTC).
        TrackingRecord.objects.filter(pk=record.pk).update(created_at=at(date(2026, 1, 1), 0))
        with patch("django.utils.timezone.now", return_value=now):
            client.force_login(user)
            response = client.get("/tracking/reports/", {"trend_year": str(future_year)})
    assert response.status_code == 200
    assert response.context["trend_years"] == [2026]
    assert response.context["trend_year"] == 2026
    assert response.context["turnaround_trend"]["has_data"] is False


@pytest.mark.django_db
def test_large_population_and_duplicate_joins_keep_counts_and_queries_correct(offices, django_assert_num_queries):
    user = get_user_model().objects.create_user(username="graph-load", office=offices["REC"], role="SYSTEM_ADMIN")
    day = date(2026, 8, 3)
    start, received, completed = at(day, 8), at(day, 9), at(day, 17)
    records = TrackingRecord.objects.bulk_create([
        TrackingRecord(tracking_number=f"GRAPH-LOAD-{index}", subject="Load verification",
                       originating_office=offices["REC"], current_office=offices["REC"], created_by=user,
                       status=Status.COMPLETED_PENDING_UPLOAD, first_received_at=received, completed_at=completed)
        for index in range(1000)
    ])
    TrackingRecord.objects.filter(pk__in=[record.pk for record in records]).update(created_at=start)
    RoutingStep.objects.bulk_create([
        RoutingStep(record=record, sequence=sequence, batch=sequence, from_office=offices["REC"],
                    to_office=offices["REC"], sent_by=user, sent_at=start + timedelta(minutes=sequence),
                    received_at=start + timedelta(minutes=sequence + 1))
        for record in records for sequence in range(1, 6)
    ])
    # This deliberately produces five joined rows per document. The service
    # must still count each completion once and every actual handover once.
    joined = TrackingRecord.objects.filter(routing_steps__received_at__isnull=False)
    assert joined.count() == 5000
    # One additional schedule snapshot per calculation, independent of row count.
    with django_assert_num_queries(5):
        summary = analytics.turnaround(joined, month=day.replace(day=1))
    with django_assert_num_queries(4):
        daily = analytics.turnaround_by_day(joined, day.replace(day=1))
    with django_assert_num_queries(4):
        annual = analytics.turnaround_by_month(joined, year=2026)["rows"][7]
    point = next(row for row in daily["rows"] if row["month"] == day)
    for key, count, seconds in [("receipt", 5000, 60), ("processing", 1000, 7 * 3600), ("lifetime", 1000, 8 * 3600)]:
        stage = next(stage for stage in summary["stages"] if stage["key"] == key)
        assert stage["samples"] == point[f"{key}_samples"] == annual[f"{key}_samples"] == count
        assert stage["documents"] == point[f"{key}_documents"] == annual[f"{key}_documents"] == 1000
        assert stage["average_seconds"] == seconds
        assert point[key] * 28800 == annual[key] * 28800 == seconds
