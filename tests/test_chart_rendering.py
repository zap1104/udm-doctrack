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
def test_every_column_carries_its_own_value(client, users, charted, page):
    """One number per bar, not one per month. A single number said nothing
    about the two columns it did not sit on, and on a phone there is no hover,
    so a value only in a title attribute could not be read at all."""
    client.force_login(users["admin"])
    groups = _column_groups(client.get(page).content.decode())

    drawn = [group for group in groups if 'class="column column--' in group]
    assert drawn, "the fixture puts work in the current month"
    for group in drawn:
        assert group.count('class="column-value') == group.count('class="column column--'), group


@pytest.mark.django_db
@pytest.mark.parametrize("page", ["/", "/reports/"])
def test_an_empty_month_has_no_columns_and_no_labels(client, users, charted, page):
    client.force_login(users["admin"])
    groups = _column_groups(client.get(page).content.decode())

    empty = [group for group in groups if 'class="column column--' not in group]
    assert empty, "eleven of the twelve months are empty"
    assert all("column-value" not in group for group in empty)


@pytest.mark.django_db
def test_each_label_sits_at_the_height_of_its_own_bar(users, charted):
    """Not at the group's tallest. A label at any other height is a number in
    the wrong place on the axis."""
    from apps.tracking.models import TrackingRecord

    volume = analytics.monthly_volume(TrackingRecord.objects.visible_to(users["admin"]))

    for row in volume["rows"]:
        assert [column["label"] for column in row["columns"]] == [
            "Created", "Handovers", "Completed",
        ]
        for column in row["columns"]:
            assert column["value"] == row[
                {"Created": "created", "Handovers": "transferred", "Completed": "completed"}[
                    column["label"]
                ]
            ]
            assert column["percent"] == analytics.bar(column["value"], volume["ceiling"])
            assert column["percent"] <= 100


@pytest.mark.django_db
def test_handovers_are_drawn_and_named_as_handovers(client, users, charted):
    """They count moves between offices where the other two count documents —
    one document endorsed four times is four handovers — so the bar is there,
    labelled with its own number, and the legend and the note say what it
    counts rather than leaving the reader to assume documents."""
    client.force_login(users["admin"])
    body = client.get("/").content.decode()
    chart = body[body.index('class="column-chart"'):body.index('class="chart-table"')]
    text = " ".join(body.split())

    assert "column--three" in chart, "the handovers bar is drawn"
    assert "Handovers</span>" in text, "and named in the legend"
    assert "Handovers count moves between offices" in text
    assert ">Handovers</th>" in body, "the table calls it the same thing"


@pytest.mark.django_db
def test_the_repository_chart_labels_both_of_its_bars(client, users, charted):
    client.force_login(users["admin"])

    rows = client.get("/reports/").context["document_months"]["rows"]

    for row in rows:
        assert [column["label"] for column in row["columns"]] == ["Completed", "Historical"]
        assert [column["value"] for column in row["columns"]] == [
            row["completed"], row["historical"],
        ]


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
    assert f"Top of the chart = {ceiling} " in response.content.decode()
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


# --- legible at every width ---------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("page", ["/", "/reports/"])
def test_every_column_chart_is_sized_by_its_own_width(client, users, charted, page):
    """Each chart, its scale line and its table sit inside one frame, because
    the frame is what the width queries measure and what the narrow tier opens
    the table inside."""
    client.force_login(users["admin"])
    body = client.get(page).content.decode()

    frames = body.split('<div class="column-chart-frame">')[1:]
    assert len(frames) == body.count('class="column-chart"') >= 1
    for frame in frames:
        assert frame.index('class="column-chart"') < frame.index('class="chart-table"')


def test_the_width_tiers_are_container_queries_not_viewport_ones():
    css = _css()

    assert ".column-chart-frame { container:column-chart / inline-size; }" in css
    assert "@container column-chart (max-width:419.98px)" in css
    assert "@container column-chart (max-width:359.98px)" in css
    # Nothing sizes the column plot off the viewport any more.
    import re

    for block in re.findall(r"@media[^{]*\{((?:[^{}]*\{[^{}]*\})*)", css):
        assert ".column-chart-plot" not in block, block


def test_below_the_narrow_tier_the_plot_gives_way_to_the_open_table():
    css = _css()
    narrow = css[css.index("@container column-chart (max-width:359.98px) {"):]
    narrow = narrow[: narrow.index("\n}")]

    assert ".column-chart,.chart-scale { display:none; }" in narrow
    assert ".chart-table::details-content { content-visibility:visible; }" in css
    assert ".chart-table > summary { display:none; }" in css


def test_a_chart_table_is_not_held_to_the_record_list_minimum_width():
    """720px inside a 435px card scrolled the Completed column out of sight."""
    assert ".chart-table .table-responsive > .table-udm { min-width:0; }" in _css()


@pytest.mark.django_db
def test_the_narrow_table_has_a_short_month_to_switch_to(client, users, charted):
    client.force_login(users["admin"])
    body = client.get("/reports/").content.decode()

    assert body.count('class="chart-month-short"') == body.count('class="chart-month-long"') >= 24


def test_a_legend_or_a_switch_in_a_card_head_wraps_rather_than_clips():
    """The head is one flex row inside a card that hides overflow, so a long
    caption squeezed the legend into a column at 1280px and cut it off at
    375px. Both the chart legends and the rings' Status | Overdue switch sit
    there."""
    css = _css()
    wrap = next(
        rule for rule in css.split("}")
        if "flex-wrap:wrap" in rule and "card-udm-head" in rule
    )

    assert ":has(> .chart-legend)" in wrap
    assert ":has(> .segmented)" in wrap
    assert ".card-udm-head:has(> .segmented) > div:first-child { flex:1 1 260px; }" in css


def test_print_keeps_the_axis_and_the_value_labels():
    """Print hides the tables, so the numbers on paper are the ones on the
    chart. No rule that hides something when printing may name them."""
    import re

    css = _css()
    hiding = []
    for block in re.split(r"@media print\s*\{", css)[1:]:
        depth, end = 1, 0
        while depth and end < len(block):
            depth += {"{": 1, "}": -1}.get(block[end], 0)
            end += 1
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", block[:end]):
            if re.search(r"display\s*:\s*none", body):
                hiding.append(selectors)
    assert hiding, "the print blocks do hide things, so this is not vacuous"
    for selectors in hiding:
        for kept in ("column-value", "column-chart-axis", "column-chart-grid", "column-chart"):
            assert not re.search(rf"\.{kept}(?![-\w])", selectors), selectors


# --- the ring, as arcs --------------------------------------------------------
def test_a_segment_with_no_width_draws_no_path():
    assert analytics.ring_arc(40, 40) == ""


def test_a_ring_of_one_slice_is_drawn_as_a_whole_annulus():
    """An SVG arc whose ends coincide draws nothing, so a single 0-100% arc
    would render an empty box. Two half circles on each radius instead."""
    path = analytics.ring_arc(0, 100)

    assert path.count("M ") == 2, "outer circle and the hole"
    assert path.count(" A ") == 4


def test_the_arc_starts_at_twelve_o_clock_and_runs_clockwise():
    """Where a conic-gradient starts and the way it runs, so the redraw puts
    every slice where the gradient had it."""
    size, outer = analytics.RING_SIZE, analytics.RING_OUTER
    path = analytics.ring_arc(0, 25)

    start = path.split(" A ")[0].removeprefix("M ").split()
    assert [float(v) for v in start] == [size / 2, size / 2 - outer]
    quarter = path.split(" A ")[1].split()[5:7]
    assert [float(v) for v in quarter] == [size / 2 + outer, size / 2]


@pytest.mark.parametrize("span, large", [(49, "0"), (51, "1")])
def test_a_slice_past_half_the_ring_takes_the_long_way(span, large):
    arc = analytics.ring_arc(0, span).split(" A ")[1].split()
    assert arc[3] == large


@pytest.mark.django_db
def test_one_record_is_one_slice_that_closes_the_ring(client, users, offices, memo_type):
    from apps.tracking.services import create_draft_record, route_record

    record = create_draft_record(
        user=users["med"], subject="Alone", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    client.force_login(users["admin"])

    response = client.get("/")
    (ring,) = [ring["status"] for ring in response.context["tracking_rings"]["rings"]]

    assert [(s["arc_start"], s["arc_end"], s["percent"]) for s in ring["slices"]] == [(0, 100, 100)]
    assert ring["slices"][0]["path"] == analytics.ring_arc(0, 100)
    assert f'd="{analytics.ring_arc(0, 100)}"' in response.content.decode()


# --- nothing the Content-Security-Policy would refuse -------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("page", ["/", "/reports/", "/notifications/"])
def test_no_page_carries_an_inline_event_handler(client, users, charted, page):
    """CSP allows no inline script, so an onclick= is not slow behaviour but
    none: the browser refuses it and the control does nothing."""
    import re

    client.force_login(users["admin"])
    response = client.get(page)

    assert response.status_code == 200
    handlers = re.findall(r"<[a-zA-Z][^<>]*?\s(on[a-z]+)\s*=", response.content.decode())
    assert handlers == []


def test_the_template_check_refuses_an_inline_handler(tmp_path):
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "check_templates", pathlib.Path("scripts/check_templates.py")
    )
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)

    problems = []
    checker.check_inline_handlers(
        "x.html",
        '<p>Turn on= the lights</p>\n{% comment %}<a onclick="no">{% endcomment %}\n'
        '<button type="button"\n        onclick="go()">Go</button>',
        problems,
    )

    assert len(problems) == 1 and "x.html:4: inline onclick=" in problems[0]


# --- pointing at a slice ------------------------------------------------------
def _slices(body):
    import re

    return re.findall(r"<path class=\"donut-slice\"([^>]*)>(.*?)</path>", body, re.S)


@pytest.mark.django_db
def test_every_slice_has_a_title_and_the_figures_its_tooltip_shows(client, users, charted):
    """<title> is the fallback where script does not run. It is not enough by
    itself: a touch screen never shows one, which is why the data is there."""
    import re

    client.force_login(users["admin"])
    response = client.get("/")
    body = response.content.decode()

    slices = _slices(body)
    rings = [ring["status"] for ring in response.context["tracking_rings"]["rings"]]
    rings.append(response.context["repository_donut"])
    drawn = [s for ring in rings for s in ring["slices"] if s["path"]]
    assert len(slices) == len(drawn) >= 2
    for (attributes, inner), expected in zip(slices, drawn, strict=True):
        assert re.search(r"<title>[^<]+</title>", inner), inner
        assert f'data-count="{expected["total"]}"' in attributes
        assert f'data-percent="{expected["percent"]}"' in attributes
        assert f'data-slice="{expected["key"]}"' in attributes


@pytest.mark.django_db
def test_a_slice_follows_the_same_link_as_its_legend_row(client, users, charted):
    """The ring adds no destination of its own and no second set of links for
    a screen reader: the path is aria-hidden, the legend is the link."""
    import html
    import re

    client.force_login(users["admin"])
    body = client.get("/").content.decode()

    for attributes, _inner in _slices(body):
        key = re.search(r'data-slice="([^"]+)"', attributes).group(1)
        href = html.unescape(re.search(r'data-href="([^"]*)"', attributes).group(1))
        legend = re.search(rf'<a class="breakdown-item" href="([^"]*)" data-slice="{key}"', body)
        assert legend and html.unescape(legend.group(1)) == href
    assert "<a " not in body[body.index('<svg class="donut-ring"'):body.index("</svg>")]


def test_the_tooltip_never_prints():
    assert ".donut-tooltip { display:none !important; }" in _css()


def test_the_slice_dimming_respects_reduced_motion():
    """The hover is an opacity change with a short transition, and the global
    reduced-motion rule collapses every transition."""
    css = _css()
    assert ".donut-slice { cursor:pointer; transition:opacity 120ms ease; }" in css
    assert "@media (prefers-reduced-motion: reduce) {\n  * { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }" in css


def test_the_ring_listener_is_delegated_and_writes_text_not_markup():
    import pathlib

    script = pathlib.Path("static/js/doctrack.js").read_text(encoding="utf-8")
    ring = script[script.index("Ring slices"):]

    assert "innerHTML" not in ring
    assert 'document.addEventListener("click"' in ring
    assert ".textContent =" in ring


# --- nothing to draw ----------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("who", ["admin", "med_admin", "sup", "viewer"])
@pytest.mark.parametrize("page", ["/", "/reports/", "/?ring=overdue"])
def test_an_empty_database_draws_every_chart_as_empty(client, users, who, page):
    """No records and no documents: no ZeroDivisionError, no max() of nothing,
    and the rings say they are empty rather than drawing a blank circle."""
    client.force_login(users[who])
    response = client.get(page)

    assert response.status_code == 200
    body = response.content.decode()
    assert '<path class="donut-slice"' not in body
    if page != "/reports/":
        for ring in response.context["tracking_rings"]["rings"]:
            assert ring["status"] == {"slices": [], "total": 0}
            assert ring["overdue"] == {"slices": [], "total": 0}
        assert response.context["tracking_rings"]["overdue_total"] == 0
        assert response.context["monthly"]["ticks"] == [{"value": 0, "offset_percent": 0.0}]
    else:
        assert response.context["document_months"]["ticks"] == [{"value": 0, "offset_percent": 0.0}]


@pytest.mark.django_db
def test_the_dashboard_context_names_no_ring_it_does_not_draw(client, users, charted):
    """The old single-ring key is gone for good, and the rings carry both views
    so the switch never has to ask the server."""
    client.force_login(users["sup"])
    context = client.get("/").context

    assert "tracking_donut" not in context
    for ring in context["tracking_rings"]["rings"]:
        assert set(ring) == {"key", "title", "status", "overdue"}



# --- room for three numbers over one month ------------------------------------
def test_the_labels_turn_on_their_side_when_a_month_is_narrow():
    """Three numbers side by side need about 70px of month, which only a chart
    with the page to itself has. Turned, each is no wider than the bar it
    labels, so every bar keeps its number instead of some losing it."""
    css = _css()
    narrow = css[css.index("@container column-chart (max-width:699.98px) {"):]
    narrow = narrow[: narrow.index("\n}")]

    assert "writing-mode:vertical-rl" in narrow
    assert "--column-value-band:34px" in narrow, "room above the bars for a turned label"


@pytest.mark.django_db
@pytest.mark.parametrize("page, template", [("/", "core/dashboard.html"), ("/reports/", "reports/reports.html")])
def test_a_column_chart_has_the_page_to_itself(client, users, charted, page, template):
    """Half a row is 34px a month, which three numbers cannot sit in."""
    import pathlib
    import re

    markup = pathlib.Path("templates", template).read_text(encoding="utf-8")
    for match in re.finditer(r'<div class="(col[^"]*)">(?:(?!<div class="col).)*?column-chart-frame', markup, re.S):
        assert match.group(1) == "col-12", match.group(1)
