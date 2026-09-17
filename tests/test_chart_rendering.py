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
