"""The charts on the dashboard and Reports, as drawn.

`test_filter_agreement.py` proves each number opens a page that agrees with it.
This file covers the drawing: that the scale a reader measures against is
stated, that every column carries its own value, that the ring closes, and that
none of it depends on script the Content Security Policy would refuse.
"""

from __future__ import annotations

import pytest

from apps.core import analytics


# --- axis ticks ---------------------------------------------------------------
@pytest.mark.parametrize("ceiling", [1, 2, 3, 4, 5, 7, 12, 99, 180, 1001])
def test_ticks_climb_to_the_ceiling(ceiling):
    ticks = analytics.axis_ticks(ceiling)
    values = [tick["value"] for tick in ticks]
    offsets = [tick["offset_percent"] for tick in ticks]

    assert values == sorted(set(values)), "monotonic and never repeated"
    assert offsets == sorted(set(offsets))
    assert values[0] == 0 and offsets[0] == 0
    assert values[-1] == ceiling and offsets[-1] == 100


@pytest.mark.parametrize("ceiling", [0, 1])
def test_a_tiny_or_empty_scale_has_no_duplicate_ticks(ceiling):
    values = [tick["value"] for tick in analytics.axis_ticks(ceiling)]

    assert len(values) == len(set(values))
    assert values[-1] == ceiling


def test_an_empty_chart_is_one_tick_at_zero():
    """Rather than a division by zero."""
    assert analytics.axis_ticks(0) == [{"value": 0, "offset_percent": 0.0}]


def test_ticks_are_round_numbers_below_the_ceiling():
    """0, 50, 100, 150 is a scale a reader measures against; 45, 90, 135 is one
    they do arithmetic on."""
    assert [tick["value"] for tick in analytics.axis_ticks(180)] == [0, 50, 100, 150, 180]
    assert [tick["value"] for tick in analytics.axis_ticks(1001)] == [0, 250, 500, 750, 1001]


def test_the_gridlines_below_the_ceiling_are_evenly_spaced():
    for ceiling in (5, 7, 12, 41, 99, 180, 1001):
        values = [tick["value"] for tick in analytics.axis_ticks(ceiling)][:-1]
        gaps = {b - a for a, b in zip(values, values[1:], strict=False)}
        assert len(gaps) <= 1, (ceiling, values)


def test_a_round_tick_crowding_the_ceiling_is_dropped():
    """40 would print on top of 41."""
    assert [tick["value"] for tick in analytics.axis_ticks(41)] == [0, 10, 20, 30, 41]


@pytest.mark.parametrize("ceiling", [1, 3, 7, 41, 99, 180, 1001, 9999])
def test_no_more_than_six_labels_whatever_the_scale(ceiling):
    assert len(analytics.axis_ticks(ceiling)) <= 6


def test_each_tick_sits_at_the_height_of_the_value_it_prints():
    """The top interval is usually shorter than the rest, so a label placed at
    an evenly divided height would be a label in the wrong place."""
    for ceiling in (3, 7, 41, 180):
        for tick in analytics.axis_ticks(ceiling):
            assert tick["offset_percent"] == round(100 * tick["value"] / ceiling, 2)


# --- a value on every column group --------------------------------------------
@pytest.fixture
def charted(users, offices, memo_type):
    """A year of nothing and one month with work in it: a routed record, a
    completed one, and a document uploaded, so every chart has a group that
    must be labelled and groups that must not."""
    from django.utils import timezone

    from apps.documents.models import Document, Source
    from apps.tracking.services import (
        complete_record,
        confirm_receipt,
        create_draft_record,
        route_record,
    )

    for subject in ("Moving", "Finished"):
        record = create_draft_record(
            user=users["med"], subject=subject, instructions="x", document_type=memo_type,
        )
        route_record(record, [offices["SUP"]], user=users["med"])
        if subject == "Finished":
            confirm_receipt(record, user=users["sup"])
            record.refresh_from_db()
            complete_record(record, user=users["sup"])
    Document.objects.create(
        title="Scanned ledger", office=offices["MED"], document_type=memo_type,
        year=timezone.localdate().year, source=Source.UPLOAD, uploaded_by=users["med"],
    )


def _column_groups(body):
    """Each column group's markup, from its opening tag to its month label."""
    groups = []
    for chunk in body.split('class="column-group"')[1:]:
        groups.append(chunk.split('class="column-label"', 1)[0])
    return groups


@pytest.mark.django_db
@pytest.mark.parametrize("page", ["/", "/reports/"])
def test_every_group_with_a_column_carries_its_value(client, users, charted, page):
    """On a phone there is no hover, so a value only in a title attribute could
    not be read at all."""
    client.force_login(users["admin"])
    groups = _column_groups(client.get(page).content.decode())

    drawn = [group for group in groups if 'class="column column--' in group]
    assert drawn, "the fixture puts work in the current month"
    for group in drawn:
        assert group.count('class="column-value') == 1, group


@pytest.mark.django_db
@pytest.mark.parametrize("page", ["/", "/reports/"])
def test_an_empty_month_has_no_label(client, users, charted, page):
    client.force_login(users["admin"])
    groups = _column_groups(client.get(page).content.decode())

    empty = [group for group in groups if 'class="column column--' not in group]
    assert empty, "eleven of the twelve months are empty"
    assert all("column-value" not in group for group in empty)


@pytest.mark.django_db
def test_the_tracking_label_is_the_tallest_column_not_the_sum(users, charted):
    """Running totals in different units do not add. The label is the value the
    axis can read, so it never exceeds the ceiling."""
    from apps.tracking.models import TrackingRecord

    volume = analytics.monthly_volume(TrackingRecord.objects.visible_to(users["admin"]))

    for row in volume["rows"]:
        assert row["label_value"] == max(row["created"], row["transferred"], row["completed"])
        assert row["label_value"] <= volume["ceiling"]
    assert volume["rows"][-1]["label_percent"] == 100


@pytest.mark.django_db
def test_the_repository_label_is_the_month_total(client, users, charted):
    client.force_login(users["admin"])

    rows = client.get("/reports/").context["document_months"]["rows"]

    for row in rows:
        assert row["label_value"] == row["completed"] + row["historical"]


# --- a labelled y-axis --------------------------------------------------------
def _axis_values(body):
    import re

    values = []
    for axis in re.findall(r'class="column-chart-axis[^"]*"[^>]*>(.*?)</div>', body, re.S):
        values.append([int(v) for v in re.findall(r">(\d+)</span>", axis)])
    return values


@pytest.mark.django_db
@pytest.mark.parametrize("page, charts", [("/", 1), ("/reports/", 2)])
def test_every_column_chart_has_a_labelled_axis_topped_by_its_ceiling(
    client, users, charted, page, charts
):
    client.force_login(users["admin"])
    response = client.get(page)
    body = response.content.decode()

    axes = _axis_values(body)
    assert len(axes) == charts == body.count('class="column-chart"')
    context = response.context
    ceilings = [context["monthly"]["ceiling"]]
    if page == "/reports/":
        ceilings.append(context["document_months"]["ceiling"])
    for values, ceiling in zip(axes, ceilings, strict=True):
        assert values[0] == 0
        assert values[-1] == ceiling
        assert values == sorted(set(values))


@pytest.mark.django_db
def test_the_axis_and_the_scale_line_agree(client, users, charted):
    """The prose stays; it must name the same top the axis does."""
    client.force_login(users["admin"])
    response = client.get("/")

    ceiling = response.context["monthly"]["ceiling"]
    assert f"Tallest column = {ceiling} document" in response.content.decode()
    assert _axis_values(response.content.decode())[0][-1] == ceiling


# --- horizontal bar rows ------------------------------------------------------
def _css():
    import pathlib

    return pathlib.Path("static/css/doctrack.css").read_text(encoding="utf-8")


def test_a_row_with_a_share_has_a_column_for_it():
    """The share was a fourth child in a three-column grid and wrapped onto a
    line of its own under the office name."""
    import pathlib

    markup = pathlib.Path("templates/core/dashboard.html").read_text(encoding="utf-8")
    row = markup[markup.index("{% for row in uploads_by_office.rows %}"):]
    row = row[: row.index("{% endfor %}")]

    assert "status-share" in row
    assert "report-series-item--share" in row
    assert ".report-series-item--share { grid-template-columns:minmax(0,190px) minmax(0,1fr) 44px 40px; }" in _css()


def test_the_series_label_column_fits_awaiting_receipt():
    """At 82px the overdue panel read "Awaiting recei…"."""
    assert ".report-series-item { display:grid; grid-template-columns:100px " in _css()
    assert ".report-series-item { grid-template-columns:96px " in _css()
