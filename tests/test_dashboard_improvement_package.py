"""Regression coverage for the dashboard counting and interpretation fixes."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core import analytics
from apps.core.views import DashboardView
from apps.documents.models import Document, Source
from apps.tracking.models import RoutingStep, TrackingRecord
from tests.test_dashboard_analytics import finished_record  # noqa: F401


def test_partition_percentages_total_exactly_one_hundred():
    assert analytics.percent_split([1, 1, 1]) == [34, 33, 33]
    assert analytics.percent_split([0, 0]) == [0, 0]
    assert analytics.percent_split([]) == []
    assert sum(analytics.percent_split([1, 1, 1, 97, 251])) == 100


def test_chart_connects_observations_without_inventing_missing_values():
    trend = {
        "rows": [{"receipt": n, "processing": None, "lifetime": None}
                 for n in (1, 2, None, 0)],
        "ceiling": 3, "has_data": True,
    }
    receipt = DashboardView()._trend_points(trend)[0]
    assert len(receipt["polyline"].split()) == 3
    xs = [dot["x"] for dot in receipt["dots"]]
    assert xs[2] - xs[1] == pytest.approx(2 * (xs[1] - xs[0]))
    assert len(receipt["dots"]) == 3  # Real zero remains an observation.


@pytest.mark.django_db
def test_invalid_timelines_are_excluded_and_explained(finished_record):  # noqa: F811
    record = finished_record
    RoutingStep.objects.filter(record=record).update(received_at=record.created_at - timedelta(days=1))
    TrackingRecord.objects.filter(pk=record.pk).update(
        completed_at=record.created_at - timedelta(days=1), first_received_at=None,
    )
    result = analytics.turnaround(TrackingRecord.objects.filter(pk=record.pk))
    assert all(stage["samples"] == 0 for stage in result["stages"])
    assert all(stage["excluded"] == 1 for stage in result["stages"])
    assert result["has_on_time"] is False


@pytest.mark.django_db
def test_repository_month_excludes_inactive_and_next_month(users, offices):
    month = timezone.localdate().replace(day=1)
    _since, until = analytics._month_bounds(month)
    common = {"office": offices["MED"], "uploaded_by": users["med"],
              "source": Source.UPLOAD, "year": month.year}
    Document.objects.create(title="Inactive", is_active=False, **common)
    future = Document.objects.create(title="Next month", **common)
    Document.objects.filter(pk=future.pk).update(created_at=until)
    result = analytics.uploads_by_office(
        Document.objects.visible_to(users["admin"]), TrackingRecord.objects.all(),
    )
    assert result["total"] == 0


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/", "/tracking/reports/", "/documents/reports/"])
def test_private_dashboard_and_reports_are_not_cached(client, users, url):
    client.force_login(users["admin"])
    response = client.get(url)
    assert response.status_code == 200
    assert "private" in response.headers["Cache-Control"]
    assert "no-store" in response.headers["Cache-Control"]
