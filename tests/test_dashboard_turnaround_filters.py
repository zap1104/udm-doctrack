"""Dashboard turnaround filters must agree across chart, popup and optional details."""
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.messages import get_messages
from django.utils import timezone

from tests.test_turnaround_period import two_months  # noqa: F401


@pytest.mark.django_db
@pytest.mark.parametrize("stage", ["receipt", "processing", "lifetime"])
def test_stage_filter_changes_every_turnaround_view_only(client, users, two_months, stage):  # noqa: F811
    client.force_login(users["admin"])
    # Compare the same snapshot; the service exposes its precise cutoff.
    with patch("django.utils.timezone.now", return_value=timezone.now()):
        baseline = client.get("/").context
        response = client.get("/", {"turnaround_stage": stage, "turnaround_window": "3"})
    context = response.context

    assert all(row["month"].replace(day=1) == context["turnaround"]["month"] for row in context["turnaround_trend"]["rows"])
    assert [item["key"] for item in context["turnaround_trend_points"]] == [stage]
    label = context["turnaround_trend_points"][0]["label"]
    for month in context["turnaround_trend_geometry"]["months"]:
        assert all(point["label"] == label for point in month["points"])
    assert [item["key"] for item in context["turnaround_panel"]["stages"]] == [stage]
    assert [item["key"] for item in context["turnaround_table_series"]] == [stage]
    assert all(len(row["values"]) == 1 for row in context["turnaround_table_rows"])
    assert context["turnaround_show_on_time"] is False
    for key in ("turnaround", "memo", "repository_donut", "breakdown", "monthly"):
        assert context[key] == baseline[key], key


@pytest.mark.django_db
def test_window_ends_at_the_picked_month_and_keeps_the_office(client, users, offices, two_months):  # noqa: F811
    client.force_login(users["admin"])
    month = two_months["month"]
    response = client.get("/", {"month": f"{month:%Y-%m}", "office": offices["SUP"].pk,
                                "turnaround_stage": "lifetime", "turnaround_window": "3"})
    context = response.context
    rows = context["turnaround_trend"]["rows"]

    assert len(rows) >= 28 and all(row["month"].replace(day=1) == month for row in rows)
    assert context["turnaround_panel"]["lifetime"] == "2 days"
    assert ("office", str(offices["SUP"].pk)) in context["turnaround_filters"]["keep"]
    assert not {"month", "turnaround_stage", "turnaround_window"} & {
        key for key, _ in context["turnaround_filters"]["keep"]
    }
    reset = urlsplit(context["turnaround_filters"]["reset_url"])
    assert parse_qs(reset.query) == {"office": [str(offices["SUP"].pk)]}
    assert reset.fragment == "turnaround-panel"


@pytest.mark.django_db
def test_statistics_are_visible_and_empty_filters_have_a_message(client, users, two_months):  # noqa: F811
    client.force_login(users["admin"])
    body = client.get("/").content.decode()
    assert '<details class="dashboard-turnaround-statistics">' not in body
    assert 'class="trend-summary dashboard-turnaround-summary"' in body
    assert '<details class="chart-table">' in body
    assert '<details class="dashboard-turnaround-statistics" open' not in body

    # The first month predates both documents; no other stage or period leaks in.
    first = client.get("/").context["month_picker"]["options"][-1]["value"]
    response = client.get("/", {"month": first, "turnaround_stage": "lifetime",
                                "turnaround_window": "3"})
    assert not response.context["turnaround_trend"]["has_data"]
    assert response.context["turnaround_trend_points"] == []
    assert "No turnaround data for the selected stage and period." in response.content.decode()
    assert 'class="trend-summary dashboard-turnaround-summary"' in response.content.decode()


@pytest.mark.django_db
def test_invalid_turnaround_filters_fall_back_with_an_explanation(client, users):
    client.force_login(users["admin"])
    response = client.get("/", {"turnaround_stage": "unknown", "turnaround_window": "999"})
    notes = [str(note) for note in get_messages(response.wsgi_request)]
    assert any("Unknown turnaround stage" in note for note in notes)
    assert response.context["turnaround_trend"]["daily"] is True
    assert 'name="turnaround_window"' not in response.content.decode()
    assert response.context["turnaround_filters"]["stages"][0]["selected"]
