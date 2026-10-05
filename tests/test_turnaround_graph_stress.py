"""Stress the real chart geometry, including sparse, tied and tiny values."""

from datetime import date, timedelta
from html.parser import HTMLParser
from itertools import product
from math import isfinite

import pytest
from django.template.loader import render_to_string
from django.utils import translation

from apps.core import analytics
from apps.core.business_time import humanise_business_seconds
from apps.core.views import DashboardView

KEYS = ("receipt", "processing", "lifetime")
VALUES = (None, 0, 1 / 28800, 0.125, 1, 1.00001, 7.25, 100000)
WINDOWS = (1, 2, 3, 12, 28, 29, 30, 31)


def chart(values, length=1):
    rows = []
    for index in range(length):
        row = {"month": date(2026, 8, 1) + timedelta(days=index)}
        # Rotate the series so crossings, missing dates and ties occur at
        # different positions instead of testing three flat lines only.
        for position, key in enumerate(KEYS):
            value = values[(position + index) % len(values)]
            samples = 0 if value is None else index + 1
            row.update({key: value, f"{key}_samples": samples,
                        f"{key}_documents": samples, f"{key}_zero_working_time": samples if value == 0 else 0,
                        f"{key}_label": humanise_business_seconds(None if value is None else value * 28800),
                        f"{key}_calendar": "1 day",
                        f"{key}_office_seconds": None if value is None else value * 28800,
                        f"{key}_calendar_seconds": None if value is None else 86400})
        rows.append(row)
    present = [row[key] for row in rows for key in KEYS if row[key] is not None]
    ceiling = max(1, int(max(present, default=0)) + 1)
    return {"rows": rows, "ceiling": ceiling, "has_data": bool(present), "ticks": analytics.axis_ticks(ceiling),
            "working_day_hours": 8}


@pytest.mark.parametrize("values", list(product(VALUES, repeat=3)))
@pytest.mark.parametrize("length", WINDOWS)
def test_geometry_population_order_alignment_and_bounds(values, length):
    trend = chart(values, length)
    view = DashboardView()
    geometry = view._trend_geometry(trend)
    lines = view._trend_points(trend)
    expected_keys = [key for key in KEYS if any(row[key] is not None for row in trend["rows"])]
    assert [line["key"] for line in lines] == expected_keys
    assert len(geometry["months"]) == (length if trend["has_data"] else 0)
    for line in lines:
        observations = [(index, row[line["key"]]) for index, row in enumerate(trend["rows"]) if row[line["key"]] is not None]
        assert len(line["dots"]) == len(observations)
        assert len(line["polyline"].split()) == len(observations)
        for dot, (index, value) in zip(line["dots"], observations, strict=True):
            assert isfinite(dot["x"]) and isfinite(dot["y"])
            assert 0 <= dot["x"] <= view.TREND_WIDTH
            assert view.TREND_PAD_TOP <= dot["y"] <= view.TREND_HEIGHT - view.TREND_PAD_BOTTOM
            expected_x = (index + 0.5) * view.TREND_WIDTH / length
            expected_y = view.TREND_PAD_TOP + (1 - value / trend["ceiling"]) * (view.TREND_HEIGHT - view.TREND_PAD_TOP - view.TREND_PAD_BOTTOM)
            assert abs(dot["x"] - expected_x) <= 0.051
            assert abs(dot["y"] - expected_y) <= 0.051
            target = geometry["months"][index]
            point = next(point for point in target["points"] if point["key"] == line["key"])
            assert point["top_percent"] * geometry["height"] / 100 == pytest.approx(dot["y"], abs=1e-10)
            assert point["text"] == trend["rows"][index][line["key"] + "_label"]
            assert point["samples"] == index + 1
            centre = target["left"] + target["width"] / 2
            assert abs(centre * view.TREND_WIDTH / 100 - dot["x"]) < 0.06
    for index, target in enumerate(geometry["months"]):
        expected = sorted((key for key in KEYS if trend["rows"][index][key] is not None),
                          key=lambda key: trend["rows"][index][key], reverse=True)
        assert [point["key"] for point in target["points"]] == expected
    assert [line["value"] for line in geometry["grid"]] == [tick["value"] for tick in reversed(trend["ticks"])]
    assert geometry["grid"][-1]["axis"] is True


def test_equal_values_keep_their_shared_coordinate_without_enlargement():
    trend = chart((1, 1, 1), 2)
    lines = DashboardView()._trend_points(trend)
    assert len({(line["dots"][0]["x"], line["dots"][0]["y"]) for line in lines}) == 1
    assert all(set(line["dots"][0]) == {"x", "y"} for line in lines)
    points = DashboardView()._trend_months(trend)[0]["points"]
    assert {point["key"] for point in points} == set(KEYS)
    assert len({point["top_percent"] for point in points}) == 1


def test_three_near_zero_values_remain_three_visible_colours():
    trend = chart((0, 0, 3 / 480), 1)
    view = DashboardView()
    lines = view._trend_points(trend)
    dots = [line["dots"][0] for line in lines]
    assert len({line["colour"] for line in lines}) == 3
    assert dots[2]["y"] != dots[0]["y"], "The three-minute measurement must not become zero."
    points = view._trend_months(trend)[0]["points"]
    assert {point["key"] for point in points} == set(KEYS)
    assert points[0]["outside_seconds"] == points[0]["calendar_seconds"] - points[0]["office_seconds"]


def test_hover_marker_uses_the_rendered_svg_coordinate_exactly():
    trend = chart((0.125, 1.00001, 7.25), 3)
    view = DashboardView()
    for line in view._trend_points(trend):
        point = next(point for point in view._trend_geometry(trend)["months"][0]["points"] if point["key"] == line["key"])
        assert point["top_percent"] * view.TREND_HEIGHT / 100 == pytest.approx(line["dots"][0]["y"], abs=1e-10)


def test_no_rows_and_no_samples_have_no_hover_targets_or_fake_points():
    view = DashboardView()
    trend = {"rows": [], "ceiling": 1, "has_data": False, "ticks": analytics.axis_ticks(1)}
    assert view._trend_points(trend) == []
    assert view._trend_geometry(trend)["months"] == []
    assert view._trend_months(trend) == []


class ChartMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.coordinates = []
        self.percentages = []
        self.markers = []
        self.marker_viewboxes = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-trend-stage" in attrs:
            self.markers.append(attrs)
        if tag == "svg" and attrs.get("class") == "trend-marker":
            self.marker_viewboxes.append(attrs["viewbox"])
        if tag in ("circle", "line"):
            self.coordinates.extend(value for key, value in attrs.items() if key in ("cx", "cy", "r", "x1", "x2", "y1", "y2"))
        if tag in ("div", "span") and attrs.get("class", "").startswith("trend-"):
            self.percentages.extend(part.split(":", 1)[1].strip("%") for part in attrs.get("style", "").split(";") if "%" in part)


@pytest.mark.parametrize("language", ["en", "de", "fr", "ar"])
@pytest.mark.parametrize("length", WINDOWS)
def test_svg_and_overlay_numbers_remain_valid_in_every_locale(language, length):
    trend = chart((0.125, 1.00001, 7.25), length)
    view = DashboardView()
    with translation.override(language):
        body = render_to_string("core/_turnaround_chart.html", {
            "turnaround_trend": trend, "turnaround_trend_geometry": view._trend_geometry(trend),
            "turnaround_trend_points": view._trend_points(trend), "turnaround_table_rows": [],
        })
    parser = ChartMarkup()
    parser.feed(body)
    assert parser.coordinates and parser.percentages
    assert all(isfinite(float(value)) for value in parser.coordinates + parser.percentages)


@pytest.mark.parametrize("values", list(product((None, 0, 3 / 480, 1), repeat=3)))
@pytest.mark.parametrize("length", (1, 31))
def test_real_markup_has_one_compact_marker_per_observation_without_extra_hover_rings(values, length):
    trend = chart(values, length)
    view = DashboardView()
    geometry = view._trend_geometry(trend)
    lines = view._trend_points(trend)
    body = render_to_string("core/_turnaround_chart.html", {
        "turnaround_trend": trend, "turnaround_trend_geometry": geometry,
        "turnaround_trend_points": lines, "turnaround_table_rows": [],
    })
    parser = ChartMarkup()
    parser.feed(body)
    expected = [point for month in geometry["months"] for point in month["points"]]
    assert len(parser.markers) == len(expected)
    for marker, point in zip(parser.markers, expected, strict=True):
        assert marker["data-trend-stage"] == point["key"]
        style = dict(part.split(":", 1) for part in marker["style"].split(";"))
        assert float(style["top"].strip("%")) == pytest.approx(point["top_percent"])
        assert style["--dot"] == point["colour"]
        assert "--dot-size" not in style
    assert all(viewbox == "0 0 10 10" for viewbox in parser.marker_viewboxes)
    assert "trend-point" not in body
    assert "trend-dot--overlap" not in body
    assert "Different-size rings" not in body


@pytest.mark.parametrize("key,shape", [
    ("receipt", '<circle cx="5" cy="5" r="3">'),
    ("processing", '<path d="M5 0.8 9.2 5 5 9.2 0.8 5Z">'),
    ("lifetime", '<path d="M5 0.8V9.2M0.8 5H9.2">'),
])
def test_stage_symbols_are_distinct_in_both_legend_and_plot(key, shape):
    assert shape in render_to_string("core/_trend_marker.html", {"key": key})
