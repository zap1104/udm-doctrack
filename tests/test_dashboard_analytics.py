"""The dashboard's analytics panels, and the shared module behind them.

Group A of the redesign moved the aggregations Reports had grown privately into
`apps.core.analytics` so the dashboard could show the same figures without a
second implementation drifting away from the first. These tests cover the
functions directly, then the context the dashboard builds from them, then the
things the redesign brief said must *not* appear on the page.

`tests/test_reports_and_dashboard.py` covers the Reports side and must keep
passing unchanged — the extraction was a refactor, not a behaviour change.
"""

from __future__ import annotations

import pathlib
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core import analytics
from apps.core.views import DASHBOARD_ROWS
from apps.documents.models import Document
from apps.tracking.models import TrackingRecord
from apps.tracking.services import (
    complete_record,
    confirm_receipt,
    create_draft_record,
    route_record,
)

DASHBOARD = "/"
REPORTS = "/tracking/reports/"


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def overdue_record(users, offices, memo_type):
    """MED raises it, routes it to SUP with a deadline that has already passed.

    Left unreceived on purpose: overdue is a deadline condition, not a status,
    and it has to be counted regardless of where in the flow the record sits.
    """
    record = create_draft_record(
        user=users["med"], subject="Late request", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    # Written directly rather than routed with a negative deadline: the service
    # refuses a due date in the past, which is correct, and the condition under
    # test is a deadline that has since gone by.
    TrackingRecord.objects.filter(pk=record.pk).update(
        due_at=timezone.now() - timedelta(days=6)
    )
    record.refresh_from_db()
    return record


@pytest.fixture
def filed_record(users, offices, memo_type, finished_record):
    """A completed record that has also been filed.

    `finished_record` stops at completion, which leaves the document awaiting an
    administrator's approval — tracking has it, the repository does not. The
    Repository ring needs something actually on its side of the line.
    """
    from django.utils import timezone as django_timezone

    from apps.documents.models import Document, Source

    Document.objects.create(
        title="A filed document",
        office=offices["SUP"],
        document_type=memo_type,
        year=django_timezone.localdate().year,
        source=Source.UPLOAD,
        uploaded_by=users["sup"],
    )
    return finished_record


@pytest.fixture
def finished_record(users, offices, memo_type):
    """MED raises it, SUP receives and completes it."""
    record = create_draft_record(
        user=users["med"], subject="A finished request", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    complete_record(record, user=users["sup"])
    record.refresh_from_db()
    return record


# ============================================================== Group A
# --- extraction ------------------------------------------------------------
def test_the_aggregations_live_in_one_shared_module():
    """Reports and the dashboard call the same functions. A second copy growing
    inside DashboardView would drift the moment either page was touched."""
    for name in (
        "by_status", "overdue_offices", "monthly_volume", "turnaround",
        "turnaround_by_month", "uploads_by_office", "combined_totals",
        "live_records_by_status", "bar", "percent", "month_window", "month_series",
    ):
        assert callable(getattr(analytics, name)), name


@pytest.mark.django_db
def test_reports_delegates_rather_than_keeping_its_own_copy(client, users, finished_record):
    """The refactor is only worth having if Reports actually reads the shared
    module — a delegation that still computed its own answer would be two
    implementations wearing one name."""
    import apps.core.views as views

    # turnaround(), the aggregation both pages still share: monthly_volume was
    # the one watched here, until Reports stopped drawing the running totals.
    calls = []
    original = analytics.turnaround

    def spy(records, *args, **kwargs):
        calls.append(records)
        return original(records, *args, **kwargs)

    views.analytics.turnaround = spy
    try:
        client.force_login(users["admin"])
        client.get(REPORTS)
    finally:
        views.analytics.turnaround = original

    assert calls, "ReportsView did not call analytics.turnaround"


# --- oldest days -----------------------------------------------------------
@pytest.mark.django_db
def test_overdue_offices_report_the_age_of_the_oldest_item(overdue_record, users):
    """A count alone cannot tell twelve documents one day late apart from three
    that have been late a month, and the second is the one to go and see."""
    rows = analytics.overdue_offices(TrackingRecord.objects.visible_to(users["admin"]))

    assert rows, "the overdue record should be attributed to an office"
    assert rows[0]["oldest_days"] == 6
    assert rows[0]["total"] == 1
    assert rows[0]["name"], "the office is named, not just coded"


@pytest.mark.django_db
def test_oldest_days_is_floored_not_rounded_up(users, offices, memo_type):
    """"3 days" must mean the deadline is three full days behind, never "some
    part of a third day"."""
    record = create_draft_record(
        user=users["med"], subject="Barely late", instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    TrackingRecord.objects.filter(pk=record.pk).update(
        due_at=timezone.now() - timedelta(days=2, hours=23)
    )

    rows = analytics.overdue_offices(TrackingRecord.objects.visible_to(users["admin"]))

    assert rows[0]["oldest_days"] == 2


@pytest.mark.django_db
def test_a_completed_record_is_never_overdue(finished_record, users):
    """Work that is finished cannot be late, whatever its deadline said."""
    TrackingRecord.objects.filter(pk=finished_record.pk).update(
        due_at=timezone.now() - timedelta(days=30)
    )

    rows = analytics.overdue_offices(TrackingRecord.objects.visible_to(users["admin"]))

    assert rows == []


@pytest.mark.django_db
def test_the_overdue_summary_counts_the_whole_queryset_not_the_listed_rows(
    overdue_record, users
):
    """The summary counts the whole queryset, never the rows it was handed.

    It was asserted by capping the list to nothing and checking the total stood
    anyway. The cap now leaves a remainder row behind, so "nothing" is no longer
    reachable — and that is the point of the remainder: the rows a reader can
    add up now equal the total the banner states, instead of being a subset of
    it with nothing saying so.

    Both halves are asserted here: the summary is still independent of the rows,
    and the rows now sum to it.
    """
    records = TrackingRecord.objects.visible_to(users["admin"])
    rows = analytics.overdue_offices(records, limit=0)
    summary = analytics.overdue_summary(records, rows, total_documents=10)

    assert summary["total"] == 1, "the total is counted over the queryset"
    assert summary["percent_of_all"] == 10
    assert [row["name"] for row in rows] == ["Other (1 office)"]
    assert sum(row["total"] for row in rows) == summary["total"]


# --- monthly turnaround ----------------------------------------------------
@pytest.mark.django_db
def test_turnaround_by_month_returns_one_point_per_month(finished_record, users):
    trend = analytics.turnaround_by_month(TrackingRecord.objects.visible_to(users["admin"]))

    assert len(trend["rows"]) == analytics.REPORT_MONTHS
    assert trend["has_data"]
    assert trend["rows"][-1]["lifetime"] is not None, "completed this month"


@pytest.mark.django_db
def test_a_month_with_nothing_completed_plots_nothing_rather_than_zero(
    finished_record, users
):
    """A month in which nothing was finished did not take zero days to finish
    things — a line dropping to the axis would say exactly that."""
    trend = analytics.turnaround_by_month(TrackingRecord.objects.visible_to(users["admin"]))

    assert trend["rows"][0]["lifetime"] is None
    assert trend["rows"][0]["has_on_time"] is False


@pytest.mark.django_db
def test_the_monthly_trend_is_counted_in_office_hours_like_reports(
    finished_record, users
):
    """The dashboard and Reports must not measure the same metric differently:
    a calendar-time chart beside office-hours figures reads as a contradiction
    rather than as a second view."""
    records = TrackingRecord.objects.visible_to(users["admin"])
    trend = analytics.turnaround_by_month(records)

    assert trend["office_hours_caveat"] == analytics.turnaround(records)["office_hours_caveat"]
    # Eight office hours to the day, so a same-day completion is well under one.
    assert trend["rows"][-1]["lifetime"] <= 1


@pytest.mark.django_db
def test_the_trend_ceiling_is_never_zero(users):
    """A zero-height axis has nothing to plot against."""
    trend = analytics.turnaround_by_month(TrackingRecord.objects.visible_to(users["admin"]))

    assert trend["ceiling"] >= 1
    assert trend["has_data"] is False


# --- uploads by office -----------------------------------------------------
@pytest.mark.django_db
def test_uploads_by_office_adds_filing_to_uploading(finished_record, users, offices):
    """One combined figure: a document uploaded and a record completed and filed
    are both an office adding to the repository."""
    uploads = analytics.uploads_by_office(
        Document.objects.visible_to(users["admin"]),
        TrackingRecord.objects.visible_to(users["admin"]),
    )

    row = next(r for r in uploads["rows"] if r["code"] == offices["SUP"].code)
    assert row["filed"] == 1
    assert row["total"] == row["uploaded"] + row["filed"]


@pytest.mark.django_db
def test_a_tie_names_no_leader(users, offices, memo_type):
    """Calling a tie "the top office" hands out a distinction the numbers did
    not award."""
    for office, actor in ((offices["SUP"], users["sup"]), (offices["HR"], users["hr"])):
        record = create_draft_record(
            user=users["med"], subject=f"For {office.code}", instructions="x",
            document_type=memo_type,
        )
        route_record(record, [office], user=users["med"])
        confirm_receipt(record, user=actor)
        record.refresh_from_db()
        complete_record(record, user=actor)

    uploads = analytics.uploads_by_office(
        Document.objects.visible_to(users["admin"]),
        TrackingRecord.objects.visible_to(users["admin"]),
    )

    assert uploads["rows"][0]["total"] == uploads["rows"][1]["total"], "a genuine tie"
    assert uploads["leader"] is None


@pytest.mark.django_db
def test_one_office_clearly_ahead_is_named(finished_record, users, offices):
    uploads = analytics.uploads_by_office(
        Document.objects.visible_to(users["admin"]),
        TrackingRecord.objects.visible_to(users["admin"]),
    )

    assert uploads["leader"]["code"] == offices["SUP"].code


@pytest.mark.django_db
def test_uploads_covers_this_month_only(finished_record, users):
    """A cumulative version would rank offices by how long they have existed."""
    TrackingRecord.objects.filter(pk=finished_record.pk).update(
        completed_at=timezone.now() - timedelta(days=400)
    )

    uploads = analytics.uploads_by_office(
        Document.objects.visible_to(users["admin"]),
        TrackingRecord.objects.visible_to(users["admin"]),
    )

    assert uploads["total"] == 0


# --- combined totals -------------------------------------------------------
@pytest.mark.django_db
def test_combined_totals_do_not_double_count(overdue_record, finished_record, users):
    """The name was always the intent and never the behaviour.

    Overdue used to sit among the slices, and it is a deadline condition lying
    across all three live stages rather than a stage of its own — on the demo
    data the ring reported 109 slice entries over 44 records. The old assertion
    could not catch it: it allowed the total to exceed the record count by
    exactly the overdue figure, which is the double-count it was named for.

    The slices are statuses now, and statuses are mutually exclusive by
    construction, so the sum is exact rather than bounded.
    """
    from apps.tracking.models import ACTIVE_STATUSES, Status

    records = TrackingRecord.objects.visible_to(users["admin"])
    documents = Document.objects.visible_to(users["admin"])

    totals = analytics.combined_totals(records, documents)
    tracking = (
        totals["pending_receipt"] + totals["received"]
        + totals["in_process"] + totals["pending_upload"]
    )
    live = (
        records.filter(status__in=ACTIVE_STATUSES)
        .exclude(status=Status.DRAFT)
        .distinct()
        .count()
    )

    assert totals["overdue"] == 1, "still computed, for the stat card"
    assert tracking == live, "every live record counted exactly once"
    assert totals["historical"] + totals["completed"] == documents.distinct().count()


@pytest.mark.django_db
def test_live_by_status_leaves_overdue_out(overdue_record, users):
    """Overdue is a deadline condition on top of a status, not a status. It has
    the banner at the top of the page; counting it again here would move records
    out of the status they are actually in."""
    rows = analytics.live_records_by_status(TrackingRecord.objects.visible_to(users["admin"]))

    assert "OVERDUE" not in {row["status"] for row in rows}
    assert rows, "the overdue record still appears under its real status"


# ============================================================== Group B
# --- context ---------------------------------------------------------------
NEW_KEYS = [
    "overdue_offices", "overdue_summary", "tracking_rings", "repository_donut", "monthly",
    "turnaround_trend", "turnaround_trend_points", "turnaround_trend_geometry",
    "turnaround",
    "uploads_by_office", "memo", "scope",
]


@pytest.mark.django_db
@pytest.mark.parametrize("username", ["admin", "med"])
def test_every_panel_is_present_for_both_roles(client, users, finished_record, username):
    """Records staff see office-to-office columns and an office user does not,
    but both get the whole analytics page."""
    client.force_login(users[username])
    context = client.get(DASHBOARD).context

    for key in NEW_KEYS:
        assert key in context, f"{key} missing for {username}"


@pytest.mark.django_db
def test_the_existing_panels_were_added_to_not_replaced(client, users, finished_record):
    client.force_login(users["admin"])
    context = client.get(DASHBOARD).context

    for key in ("incoming_count", "outgoing_count", "overdue_count",
                "attention_records", "recent_records", "breakdown"):
        assert key in context, key


@pytest.mark.django_db
def test_show_office_columns_still_governs_the_office_columns(client, users, finished_record):
    """The redesign adds no permission model of its own."""
    client.force_login(users["admin"])
    assert client.get(DASHBOARD).context["show_office_columns"] is True

    # `is_records_staff` is everyone except a viewer, so an ordinary office
    # user still gets the columns. The viewer is the one who does not.
    client.force_login(users["viewer"])
    assert client.get(DASHBOARD).context["show_office_columns"] is False


@pytest.mark.django_db
def test_the_panels_respect_visibility(client, users, finished_record):
    """HR had nothing to do with the record, so it is not in HR's figures."""
    client.force_login(users["hr"])
    hr = client.get(DASHBOARD).context["overdue_summary"]["total"]

    client.force_login(users["admin"])
    everything = client.get(DASHBOARD).context

    assert hr <= everything["overdue_summary"]["total"]


# --- the rings -------------------------------------------------------------
# One ring per domain. The combined ring could show the split between tracking
# and the repository but not the shape of either, and tracking is the half
# somebody acts on. Tracking is now itself one ring per direction when the page
# answers for an office, and one ring under every office; `_rings` reads them
# all, so every property below holds for each.
DONUTS = ["tracking", "repository_donut"]


def _rings(context, key):
    if key == "tracking":
        return [ring["status"] for ring in context["tracking_rings"]["rings"]]
    return [context[key]]


@pytest.mark.django_db
@pytest.mark.parametrize("key", DONUTS)
@pytest.mark.parametrize("who", ["admin", "sup"])
def test_each_ring_closes_at_one_hundred_percent(
    client, users, overdue_record, filed_record, key, who
):
    """Independently rounded values leave a hairline gap or an overlap, and a
    ring with a slit in it reads as a rendering fault.

    `overdue_record` for a live document: `filed_record` alone is completed,
    which is the figure beside the tracking rings and not a slice of them."""
    client.force_login(users[who])
    drawn = [ring for ring in _rings(client.get(DASHBOARD).context, key) if ring["slices"]]

    assert drawn, f"{key} drew nothing for {who}"
    for ring in drawn:
        slices = ring["slices"]
        assert slices[0]["arc_start"] == 0
        assert slices[-1]["arc_end"] == 100
        for before, after in zip(slices, slices[1:], strict=False):
            assert before["arc_end"] == after["arc_start"], "no slit and no overlap"


@pytest.mark.django_db
@pytest.mark.parametrize("key", DONUTS)
def test_each_ring_is_measured_against_its_own_domain(client, users, filed_record, key):
    """The whole point of splitting them. Reusing the grand-total percentages
    would leave each ring summing to its share of everything rather than to
    100%, so a Repository ring covering a third of all documents would be drawn
    as a third of a circle with two thirds of it blank."""
    client.force_login(users["admin"])

    for donut in _rings(client.get(DASHBOARD).context, key):
        if donut["slices"]:
            assert sum(row["percent"] for row in donut["slices"]) == 100
        assert donut["total"] == sum(row["total"] for row in donut["slices"])


@pytest.mark.django_db
def test_every_office_ring_and_the_figure_beside_it_still_account_for_everything(
    client, users, filed_record
):
    """Under every office, the tracking ring, the pending-upload figure beside
    it and the repository ring are the whole breakdown.

    Rewritten when the tracking ring split by direction. "The two rings
    together are the whole" cannot survive a split for one office: Incoming and
    Outgoing leave out what the office has passed on. It still holds where there
    is one ring, and that is where it is asserted."""
    client.force_login(users["admin"])
    context = client.get(DASHBOARD).context
    tracking = context["tracking_rings"]

    assert tracking["split"] is False
    (ring,) = tracking["rings"]
    assert (
        ring["status"]["total"]
        + tracking["pending_upload"]["total"]
        + context["repository_donut"]["total"]
        == context["breakdown"]["total"]
    )


@pytest.mark.django_db
def test_splitting_the_ring_did_not_rewrite_the_shared_slices(client, users, filed_record):
    """The per-domain pass copies rather than mutates: the write-up and the memo
    read the grand-total percentages off these same dicts, and rewriting them
    would silently change what the prose beneath the rings says."""
    client.force_login(users["admin"])
    context = client.get(DASHBOARD).context

    assert sum(row["percent"] for row in context["breakdown"]["slices"]) == 100


@pytest.mark.django_db
@pytest.mark.parametrize("key", DONUTS)
@pytest.mark.parametrize("who", ["admin", "sup"])
def test_each_ring_is_painted_from_the_brand_tokens(client, users, filed_record, key, who):
    """Not the mockup's forest-green and gold."""
    client.force_login(users[who])

    for donut in _rings(client.get(DASHBOARD).context, key):
        for slice_ in donut["slices"]:
            assert slice_["colour"].startswith("var(--"), slice_["key"]


@pytest.mark.django_db
@pytest.mark.parametrize("key", DONUTS)
def test_an_empty_domain_draws_no_ring(client, users, key):
    client.force_login(users["admin"])

    for donut in _rings(client.get(DASHBOARD).context, key):
        assert donut["slices"] == []
        assert donut["total"] == 0


@pytest.mark.django_db
def test_a_domain_with_nothing_in_it_says_so_rather_than_drawing_an_empty_circle(
    client, users
):
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert "Nothing is in tracking." in body
    assert "Nothing has been filed yet." in body


@pytest.mark.django_db
def test_the_combined_stacked_bar_was_replaced_not_kept_alongside(
    client, users, filed_record
):
    """Two rings replace the one bar; a third combined view would say the same
    thing a third time."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert "breakdown-bar" not in body
    assert "<h2>All documents</h2>" not in body


@pytest.mark.django_db
def test_the_write_up_is_gone_from_the_page_and_from_the_context(
    client, users, filed_record
):
    """The panel went when the page was rebuilt to the wireframe, and the
    figures behind it followed once the dashboard stopped being printable.

    It existed so a printed copy said something in words rather than only in
    colour. Nothing prints the dashboard any more, and the memo — which does
    print — says more than it did.
    """
    client.force_login(users["admin"])
    response = client.get(DASHBOARD)

    assert "What this shows" not in response.content.decode(), "the panel's heading"
    assert "dashboard-writeup" not in response.content.decode()
    assert "breakdown_summary" not in response.context


# --- the trend line --------------------------------------------------------
@pytest.mark.django_db
def test_the_trend_line_is_built_server_side(client, users, finished_record):
    """No JS charting library: the polyline arrives as coordinates."""
    client.force_login(users["admin"])
    series = client.get(DASHBOARD).context["turnaround_trend_points"]

    assert series
    for line in series:
        assert line["polyline"], line["key"]
        assert line["colour"].startswith("var(--"), line["key"]
        assert line["dots"]


def _view():
    from apps.core.views import DashboardView

    return DashboardView()


def test_the_grid_lines_are_placed_by_the_same_constants_as_the_data():
    """They were literals in the template while the plot geometry lived in
    Python, so the two agreed only because they had been matched by hand and
    changing the box moved the rules off the data without saying so."""
    view = _view()
    geometry = view._trend_geometry()
    plot_h = view.TREND_HEIGHT - view.TREND_PAD_TOP - view.TREND_PAD_BOTTOM

    assert geometry["grid"][0]["y"] == view.TREND_PAD_TOP, "top rule is not the ceiling"
    assert geometry["grid"][-1]["y"] == view.TREND_PAD_TOP + plot_h, "baseline is not zero"
    assert geometry["view_box"] == f"0 0 {view.TREND_WIDTH} {view.TREND_HEIGHT}"


def test_only_the_baseline_is_drawn_as_an_axis():
    grid = _view()._trend_geometry()["grid"]

    assert grid[-1]["axis"] is True
    assert not any(line["axis"] for line in grid[:-1])


def test_a_ceiling_value_lands_on_the_top_rule_and_a_zero_on_the_baseline():
    """What makes the rules readable: a point level with a rule means that
    value, and a rule the data never touches is decoration."""
    view = _view()
    geometry = view._trend_geometry()
    trend = {
        "rows": [{"receipt": 4.0, "processing": 0.0, "lifetime": None}] * 12,
        "ceiling": 4,
        "has_data": True,
    }

    series = {row["key"]: row for row in view._trend_points(trend)}

    assert {dot["y"] for dot in series["receipt"]["dots"]} == {geometry["grid"][0]["y"]}
    assert {dot["y"] for dot in series["processing"]["dots"]} == {geometry["grid"][-1]["y"]}


def test_the_plot_uses_the_whole_width():
    """It used to reserve a left gutter for y-axis labels that were never drawn,
    which cost the plot 5% of a column that is now half as wide as it was."""
    view = _view()
    trend = {
        "rows": [{"receipt": 1.0, "processing": None, "lifetime": None}] * 12,
        "ceiling": 4,
        "has_data": True,
    }

    xs = [dot["x"] for row in view._trend_points(trend) for dot in row["dots"]]

    assert view.TREND_PAD_LEFT == 0
    assert min(xs) < view.TREND_WIDTH * 0.06
    assert max(xs) > view.TREND_WIDTH * 0.94


def test_the_plot_is_deep_enough_to_read_at_the_width_it_gets():
    """The panel spans the page, and the chart takes roughly 630px of it beside
    the summary. Twelve months of three series need enough depth there to show
    one line crossing another — the half-width version resolved to about 110px
    and could not."""
    view = _view()

    assert 630 * view.TREND_HEIGHT / view.TREND_WIDTH > 140


def test_the_half_width_compensation_went_with_the_width_that_caused_it():
    """640x240 was a response to the panel being squeezed into a column. It is
    full width again, so the flatter box is correct and the taller one would
    just be wasted vertical space."""
    view = _view()

    assert (view.TREND_WIDTH, view.TREND_HEIGHT) == (640, 170)


def test_the_stylesheet_declares_the_same_ratio_the_view_plots_at():
    """The box is declared in two places — the viewBox from the view, the
    aspect-ratio in CSS — and a mismatch letterboxes the plot inside its own
    card without erroring anywhere."""
    import pathlib

    view = _view()
    css = pathlib.Path("static/css/doctrack.css").read_text(encoding="utf-8")

    assert f"aspect-ratio:{view.TREND_WIDTH}/{view.TREND_HEIGHT}" in css


@pytest.mark.django_db
def test_the_legend_sits_with_the_chart_not_in_the_heading(client, users, finished_record):
    """In the head it shared one line with the title and the office-hours
    caveat, which at this column width left three keys fighting a sentence for
    the same few centimetres."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert "trend-legend" in body
    assert body.index("trend-legend") > body.index("Turnaround Time for the Month of")


@pytest.mark.django_db
def test_nothing_measured_means_nothing_plotted(client, users):
    client.force_login(users["admin"])

    assert client.get(DASHBOARD).context["turnaround_trend_points"] == []


# --- the memo --------------------------------------------------------------
def memo_text(memo):
    """Every label and value in the memo as one string, for substring checks."""
    return " ".join(
        "{} {}".format(line["label"], line["value"]).strip()
        for section in memo
        for line in section["lines"]
    )


def memo_headings(memo):
    return [section["heading"] for section in memo]


@pytest.mark.django_db
def test_the_memo_is_composed_server_side(client, users, finished_record):
    """Composition stays out of the template, like the rest of the app.

    The memo is a list of sections, each a heading and a list of label/value
    lines, so the template renders headings and rows rather than deciding what
    the memo says.
    """
    client.force_login(users["admin"])
    memo = client.get(DASHBOARD).context["memo"]

    month = f"{timezone.localdate():%B %Y}"
    assert memo_headings(memo) == [
        "Overview", "Needs attention", f"Turnaround Time for the Month of {month}",
        "Repository activity this month",
    ]
    for section in memo:
        assert section["lines"], section["heading"]
        for line in section["lines"]:
            assert set(line) == {"label", "value"}
            assert isinstance(line["label"], str) and isinstance(line["value"], str)


@pytest.mark.django_db
def test_the_memo_agrees_with_the_figures_beside_it(client, users, overdue_record):
    client.force_login(users["admin"])
    response = client.get(DASHBOARD)
    memo = memo_text(response.context["memo"])

    assert str(response.context["breakdown"]["total"]) in memo
    assert str(response.context["overdue_summary"]["total"]) in memo


@pytest.mark.django_db
def test_an_empty_system_still_says_something(client, users):
    """Inherited from `_breakdown_summary`, which is gone.

    That method existed so a printed copy said something in words rather than
    only in colour, and its one behaviour the memo did not already cover was
    this: a system with nothing in it says so, instead of printing four rows of
    zeroes. `_memo` states zero in words everywhere else — this section was the
    last place still printing one as a figure.
    """
    client.force_login(users["admin"])
    memo = client.get(DASHBOARD).context["memo"]

    assert memo_headings(memo) == ["Overview"], "nothing else has anything to say"
    assert {"label": "", "value": "There are no documents in tracking or in the repository yet."} in memo[0]["lines"]
    assert "0 document" not in memo_text(memo)


@pytest.mark.django_db
def test_the_memo_says_so_when_nothing_is_late(client, users, finished_record):
    client.force_login(users["admin"])
    memo = client.get(DASHBOARD).context["memo"]
    attention = next(s for s in memo if s["heading"] == "Needs attention")

    # Stated in words, not as a figure: "0 documents are past the deadline"
    # reads as a finding, "Nothing is past its deadline" as the absence of one.
    assert attention["lines"] == [{"label": "", "value": "Nothing is past its deadline."}]
    assert "0" not in memo_text([attention])


@pytest.mark.django_db
def test_the_memo_uses_office_language_not_a_bare_decimal(client, users, finished_record):
    """"An average of 0.0 working days" is not a sentence anybody would write.
    The chart plots days because an axis needs a number; the prose beside it
    gets the same wording Reports uses."""
    client.force_login(users["admin"])
    memo = memo_text(client.get(DASHBOARD).context["memo"])

    assert "0.0 working day" not in memo
    assert "counted in office hours" in memo


@pytest.mark.django_db
def test_the_monthly_rows_carry_both_the_number_and_the_wording(finished_record, users):
    trend = analytics.turnaround_by_month(TrackingRecord.objects.visible_to(users["admin"]))
    latest = trend["latest"]

    assert isinstance(latest["lifetime"], float), "days, for the axis"
    assert isinstance(latest["lifetime_label"], str), "office language, for the prose"


@pytest.mark.django_db
def test_the_memo_names_the_scope_it_describes(client, users, offices, finished_record):
    """A memo that does not say which office it covers is not evidence of
    anything once it leaves the screen."""
    client.force_login(users["admin"])

    everything = client.get(DASHBOARD).context["memo"]
    assert {"label": "Scope", "value": "All offices"} in everything[0]["lines"]

    narrowed = client.get(f"{DASHBOARD}?office={offices['SUP'].pk}").context["memo"]
    assert {"label": "Scope", "value": offices["SUP"].name} in narrowed[0]["lines"]


@pytest.mark.django_db
def test_the_memo_lists_every_office_holding_something_late(
    client, users, offices, memo_type
):
    """The per-office breakdown was computed on this request and thrown away.

    `_memo` read one field of it — the single oldest figure across all offices —
    so a memo about twelve overdue documents could not say where any of them
    were. It now carries a line per office, in the order `overdue_offices`
    returns them, which is by count descending: a queue to work through, not a
    ranking. Two offices here, so the ordering is actually exercised.
    """
    # Received, not merely routed: overdue_offices groups by `current_office`,
    # which only moves to the destination once receipt is confirmed. Routing
    # alone would leave every record attributed to MED, which is the office
    # that raised them rather than the one holding them.
    for office, holder, days, count in (
        (offices["SUP"], users["sup"], 9, 2),
        (offices["HR"], users["hr"], 3, 1),
    ):
        for index in range(count):
            record = create_draft_record(
                user=users["med"], subject=f"Late {office.code} {index}",
                instructions="For action.", document_type=memo_type,
            )
            route_record(record, [office], user=users["med"])
            confirm_receipt(record, user=holder)
            TrackingRecord.objects.filter(pk=record.pk).update(
                due_at=timezone.now() - timedelta(days=days)
            )

    client.force_login(users["admin"])
    response = client.get(DASHBOARD)
    attention = next(
        s for s in response.context["memo"] if s["heading"] == "Needs attention"
    )
    rows = response.context["overdue_offices"]

    assert [row["name"] for row in rows] == [
        offices["SUP"].name, offices["HR"].name
    ], "busiest queue first"
    # The office lines are whatever overdue_offices returned, in that order,
    # after the two headline lines.
    assert attention["lines"][-len(rows):] == [
        {"label": offices["SUP"].name, "value": "2 overdue, oldest 9 days"},
        {"label": offices["HR"].name, "value": "1 overdue, oldest 3 days"},
    ]


@pytest.mark.django_db
def test_the_memo_lists_every_office_that_added_to_the_repository(
    client, users, offices, filed_record
):
    """`uploads_by_office` returns every contributing office and the memo read
    only `leader`. Naming one office and dropping the rest turned a record of
    who contributed into an award."""
    client.force_login(users["admin"])
    response = client.get(DASHBOARD)
    activity = next(
        s for s in response.context["memo"]
        if s["heading"] == "Repository activity this month"
    )
    rows = response.context["uploads_by_office"]["rows"]

    assert rows, "the fixture should have put something in the repository"
    assert [line["label"] for line in activity["lines"]] == [row["name"] for row in rows]


@pytest.mark.django_db
def test_the_memo_names_the_office_rather_than_saying_your_office(
    client, users, finished_record
):
    """A printed memo has left the building.

    `_scope()` calls a non-picker's scope "Your office", which answers a reader
    looking at their own screen and answers nobody on paper. The header meta
    line already resolved this to the real office name; the memo body did not,
    so one page carried two different answers to the same question.

    Resolved once in `_scope` as `display` rather than by each template
    deciding for itself — there were three copies of that ternary and this
    would have been a fourth.
    """
    client.force_login(users["med"])
    response = client.get("/memo/print/")
    body = response.content.decode()
    overview = response.context["memo"][0]["lines"]

    assert response.context["scope"]["can_pick"] is False
    assert {"label": "Scope", "value": users["med"].office_label} in overview
    assert "Your office" not in body


@pytest.mark.django_db
def test_an_admin_scope_still_reads_as_the_picker_names_it(
    client, users, offices, finished_record
):
    """The other half: for a user who has the picker, `display` is the picker's
    own label, so the memo and the control agree."""
    client.force_login(users["admin"])

    everything = client.get("/memo/print/").context["memo"][0]["lines"]
    assert {"label": "Scope", "value": "All offices"} in everything

    narrowed = client.get(f"/memo/print/?office={offices['SUP'].pk}").context["memo"][0]["lines"]
    assert {"label": "Scope", "value": offices["SUP"].name} in narrowed


@pytest.mark.django_db
def test_the_memo_never_compares_month_to_month(client, users, finished_record):
    """Descriptive, never comparative — a printed memo carrying a verdict on an
    office outlives the context that produced it."""
    client.force_login(users["admin"])
    memo = memo_text(client.get(DASHBOARD).context["memo"]).lower()

    for word in ("increase", "decrease", "improved", "worse", "better than", "up from", "down from"):
        assert word not in memo, word


@pytest.mark.django_db
def test_the_memo_is_offered_in_a_dialog(client, users, finished_record):
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert "Generate memo" in body
    assert 'id="dashboard-memo"' in body


# --- scope -----------------------------------------------------------------
@pytest.mark.django_db
def test_records_staff_may_narrow_the_page_to_one_office(client, users, offices, finished_record):
    client.force_login(users["admin"])
    scope = client.get(f"{DASHBOARD}?office={offices['SUP'].pk}").context["scope"]

    assert scope["can_pick"] is True
    assert scope["office"] == offices["SUP"]
    assert scope["label"] == offices["SUP"].name


@pytest.mark.django_db
def test_an_office_user_gets_no_picker(client, users, offices):
    """Gated on `is_admin`. `is_records_staff` would have included this user,
    which is exactly who the picker is not for."""
    client.force_login(users["med"])
    scope = client.get(DASHBOARD).context["scope"]

    assert scope["can_pick"] is False
    assert scope["offices"] == []


@pytest.mark.django_db
def test_the_scope_can_only_narrow_never_widen(client, users, offices, finished_record):
    """An office user editing the address bar gets their own records back, not
    somebody else's: the filter is applied on top of the visibility rules rather
    than instead of them."""
    client.force_login(users["hr"])
    unfiltered = client.get(DASHBOARD).context["breakdown"]["total"]
    forced = client.get(f"{DASHBOARD}?office={offices['SUP'].pk}").context["breakdown"]["total"]

    assert forced == unfiltered, "the office parameter did nothing for a non-admin"

    client.force_login(users["admin"])
    everything = client.get(DASHBOARD).context["breakdown"]["total"]
    narrowed = client.get(
        f"{DASHBOARD}?office={offices['SUP'].pk}"
    ).context["breakdown"]["total"]

    assert narrowed <= everything


@pytest.mark.django_db
@pytest.mark.parametrize("raw", ["", "abc", "0", "999999", "-4", "9" * 40])
def test_a_nonsense_office_parameter_falls_back_to_everything(client, users, raw):
    """Anything a user can reach by editing the address bar has to be a page,
    not a 500."""
    client.force_login(users["admin"])
    response = client.get(f"{DASHBOARD}?office={raw}")

    assert response.status_code == 200
    assert response.context["scope"]["office"] is None
    assert response.context["scope"]["label"] == "All offices"


@pytest.mark.django_db
def test_the_scope_reaches_the_printed_copy(client, users, offices, finished_record):
    """A stack of printouts with no office named on them is indistinguishable."""
    client.force_login(users["admin"])
    body = client.get(f"{DASHBOARD}?office={offices['SUP'].pk}").content.decode()

    assert offices["SUP"].name in body


# ============================================================== Group C
# --- what the brief said must not appear -----------------------------------
@pytest.mark.django_db
def test_there_is_no_self_service_role_toggle(client, users, finished_record):
    """The mockup carried Admin/Office-user buttons only so a static file could
    preview both states. The real distinction is is_records_staff."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    for marker in ("Access level", "Office user</button>", "setAdmin", "setOffice"):
        assert marker not in body, marker


@pytest.mark.django_db
def test_the_undecided_turnaround_cap_was_not_shipped(client, users, finished_record):
    """The mockup labelled it "Placeholder · not final" itself."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    for marker in ("Turnaround cap", "Placeholder", "not final"):
        assert marker not in body, marker


@pytest.mark.django_db
def test_no_placeholder_offices_from_the_mockup(client, users, finished_record):
    """Every office name on the page comes from a real Office record."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    for name in ("Office of the Registrar", "Graduate School", "Accounting Office",
                 "Research & Extension", "College of Engineering"):
        assert name not in body, name


@pytest.mark.django_db
def test_the_mockup_palette_did_not_come_across(client, users, finished_record):
    """Forest green and gold belong to the prototype, not to this app."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode().lower()

    for hexcode in ("#0f6e4c", "#d4af6a", "#0c1f18", "#2f9e6b", "#c0392b"):
        assert hexcode not in body, hexcode


def test_the_new_panels_use_only_brand_tokens():
    """The stylesheet for the new panels introduces no palette of its own."""
    import pathlib
    import re

    css = pathlib.Path("static/css/doctrack.css").read_text(encoding="utf-8")
    block = css[css.index("   Dashboard analytics panels"):css.index("repository folders */")]

    for hexcode in re.findall(r"#[0-9a-fA-F]{3,6}", block):
        # White is the surface behind the ring's hole, not a brand colour.
        assert hexcode.lower() in {"#fff", "#ffffff"}, hexcode


def test_the_turnaround_figures_cannot_overlap_when_the_panel_narrows():
    """The summary beside the plot overlapped itself on a magnified screen.

    Two causes, both about a minimum that would not give way. `white-space:
    nowrap` on the figure inherits into the caption under it, so "4 days 9 hrs
    on the calendar" became one unbreakable ~145px box in a 190px column that
    also had to hold a label; and `minmax(190px, 1fr)` is a floor, so once the
    panel itself was under 190px the column stopped shrinking and its contents
    hung over the neighbouring figure.

    Nothing caught it: the page returns 200, the divs balance, and no test here
    resolves a width. This one reads the two declarations directly, which is
    the shape of the bug rather than the rendering of it.

    FIXME: still not a layout test — it asserts the CSS says the right thing,
    not that nothing overlaps at a given viewport. Measuring that needs a
    headless browser at several widths and zoom levels.
    """
    import pathlib
    import re

    css = pathlib.Path("static/css/doctrack.css").read_text(encoding="utf-8")

    def rule(selector):
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert m, f"{selector} is gone — this guard needs rewriting"
        return re.sub(r"\s+", " ", m.group(1))

    # The figure stays intact; the caption under it is a phrase and may wrap.
    assert "white-space:nowrap" in rule(".report-metric strong").replace(" ", "")
    assert "white-space:normal" in rule(".report-metric small").replace(" ", "")

    # The track minimum has to yield to a container narrower than itself.
    columns = rule(".trend-summary-figures")
    assert "minmax(min(190px,100%)" in columns.replace(" ", ""), columns


def test_no_javascript_charting_library_was_added():
    """Bootstrap 5 + HTMX + Django templates only."""
    import pathlib

    base = pathlib.Path("templates/base.html").read_text(encoding="utf-8").lower()

    for library in ("chart.js", "chartjs", "d3.", "recharts", "plotly", "apexcharts"):
        assert library not in base, library


def _div_depth(html):
    """Running <div> depth per line, and the depth left over at the end."""
    import re

    depth, trace = 0, []
    for number, line in enumerate(html.splitlines(), 1):
        trace.append((number, depth, line))
        depth += len(re.findall(r"<div\b", line)) - len(re.findall(r"</div>", line))
    return depth, trace


def test_the_dashboard_template_closes_every_div_it_opens():
    """Guards the re-flow, which moved whole panels between columns.

    scripts/check_templates.py validates Django tag nesting and passes happily
    on markup whose <div>s do not balance, so a lifted block that carried its
    old row-closing tag with it sailed through every gate: the suite was green,
    ruff was clean, and the page still returned 200 — with every panel after the
    stray tag rendered outside the container, stacked in a column a few
    characters wide.
    """
    import pathlib

    html = pathlib.Path("templates/core/dashboard.html").read_text(encoding="utf-8")
    leftover, _ = _div_depth(html)

    assert leftover == 0, f"{leftover:+d} unbalanced <div> in dashboard.html"


def test_every_dashboard_row_and_column_sits_at_the_depth_it_should():
    """Balanced totals are not enough on their own: an extra <div> and a missing
    one cancel out in the sum while leaving the page mis-nested. Bootstrap's
    grid only works when a .row is at container level and every .col- is
    directly inside one, so those two depths are checked outright."""
    import pathlib
    import re

    html = pathlib.Path("templates/core/dashboard.html").read_text(encoding="utf-8")
    _, trace = _div_depth(html)

    # Rows now also sit inside a side (the stat cards' row), so a row may be
    # nested; what Bootstrap needs is that every .col- is directly inside one.
    rows = []
    for number, depth, line in trace:
        if re.search(r'<div class="row\b', line):
            rows.append(depth)
        elif re.search(r'<div class="col-', line):
            assert rows, f"line {number}: .col- before any .row"
            assert depth == rows[-1] + 1, f"line {number}: .col- at depth {depth}, not inside its .row"


@pytest.mark.django_db
def test_the_panels_all_render_inside_the_page_container(client, users, filed_record):
    """The rendered page, not just the template — every panel heading has to
    come before the content block closes."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert "Newest in the Document Repository" not in body, "removed in the consultation"
    for heading in ("Action Centre",
                    "Created, handed over and completed &mdash; running totals",
                    "Turnaround Time for the Month of"):
        assert f"<h2>{heading}" in body, heading

    # The last panel must still precede the memo dialog, which is the final
    # thing in the content block.
    assert body.index("Turnaround Time for the Month of") < body.index('id="dashboard-memo"')


# ============================================================== Action Centre
@pytest.fixture
def awaiting_receipt(users, offices, memo_type):
    """Three documents routed to SUP and waiting for it to confirm receipt."""
    records = []
    for index in range(3):
        record = create_draft_record(
            user=users["med"], subject=f"For receipt {index}", instructions="x",
            document_type=memo_type,
        )
        route_record(record, [offices["SUP"]], user=users["med"])
        records.append(record)
    return records


@pytest.mark.django_db
def test_the_two_desk_panels_became_one(client, users, awaiting_receipt):
    """"Pending Receipt" and "Recent Tracking Activity" sat in different rows
    answering versions of the same question, so the reader had to look in two
    places to know whether the desk was clear."""
    client.force_login(users["sup"])
    body = client.get(DASHBOARD).content.decode()

    assert "<h2>Action Centre</h2>" in body
    assert "<h2>Pending Receipt</h2>" not in body
    assert "<h2>Recent Tracking Activity</h2>" not in body


@pytest.mark.django_db
def test_the_desk_keeps_both_blocks_and_puts_action_first(client, users, awaiting_receipt):
    """A list of what already happened above a list of what has not is a filing
    cabinet, not a desk."""
    client.force_login(users["sup"])
    body = client.get(DASHBOARD).content.decode()

    # The queue block is titled by the queue picked, Pending Receipt until
    # another chip is chosen, and it still comes first.
    import re

    queue = re.search(r'<h3 class="desk-block-title">\s*Pending Receipt', body)
    assert queue
    assert "Recently moved" in body
    assert queue.start() < body.index("Recently moved")


@pytest.mark.django_db
def test_the_block_titles_sit_below_the_panel_title(client, users, awaiting_receipt):
    """The panel is the h2; the blocks inside it are h3. Nesting level is what
    tells a screen reader the two tables belong to one panel."""
    client.force_login(users["sup"])
    body = client.get(DASHBOARD).content.decode()

    import re

    assert re.search(r'<h3 class="desk-block-title">\s*Pending Receipt', body)
    assert '<h3 class="desk-block-title">Recently moved</h3>' in body


@pytest.mark.django_db
def test_every_stat_card_opens_the_list_it_counts(client, users, overdue_record):
    """Overdue used to open Reports on the argument that "why are these late"
    is a report rather than a list. True of the question, not of the click: a
    card counting documents is opened to see the documents, and one card
    behaving unlike the three beside it is a surprise every time.

    As an office user: under every office the Incoming and Outgoing cards are
    disabled and open nothing."""
    client.force_login(users["sup"])
    body = client.get(DASHBOARD).content.decode()

    for scope in ("incoming", "outgoing"):
        assert f"/tracking/?scope={scope}" in body, scope
    # Overdue opens `?overdue=`, not `?scope=overdue`. It is a deadline
    # condition lying across the stages rather than a stage of its own — the
    # same reason the Tracking page moved it out of its queue nav and into the
    # Deadline row, where it composes with a queue instead of replacing one.
    assert "/tracking/?overdue=yes" in body
    assert "/tracking/?scope=overdue" not in body
    assert "/tracking/reports/?status=OVERDUE" not in body
    # `custody` is every record whose current_office is this office, completed
    # ones included, so a card counting it read 1 over a page of 9.
    assert "?scope=custody" not in body


@pytest.mark.django_db
def test_the_overdue_card_and_the_list_it_opens_agree(client, users, overdue_record):
    """The number on the card has to be the number of rows behind it. Pointing
    a count at a list filtered even slightly differently is worse than pointing
    it somewhere else entirely — the reader has no reason to doubt it."""
    client.force_login(users["admin"])
    counted = client.get(DASHBOARD).context["overdue_count"]
    listed = client.get("/tracking/?scope=overdue").context["page_obj"].paginator.count

    assert counted == listed


@pytest.mark.django_db
def test_the_two_desk_blocks_are_told_apart(client, users, awaiting_receipt):
    """They were reading as one list with a line through it.

    Same grey, same weight, same size on both titles meant the half that needs
    doing looked like the half that already happened. Each block carries a
    modifier so the stylesheet can say which is which; without them the card is
    two identical tables stacked.
    """
    client.force_login(users["sup"])
    body = client.get(DASHBOARD).content.decode()

    assert "desk-block desk-block--primary" in body
    assert "desk-block desk-block--secondary" in body


def test_the_two_blocks_carry_different_colours():
    """A band each, not two greys. The card is two identical-looking tables and
    only one of them is work, so the difference has to survive being glanced at.

    Asserted as the pairing rather than the exact hex: both reuse the
    soft-background / ink-text tokens the status pills already use, so the card
    introduces no colour the page has not already taught.

    Not red — red is overdue here, a property of individual rows and not of the
    block, and some rows waiting for action are not late at all.
    """
    import pathlib as _pathlib
    import re as _re

    css = _pathlib.Path("static/css/doctrack.css").read_text(encoding="utf-8")
    block = css[css.index("/* ------------------------------------------------------------ Action Centre */"):
                css.index("/* --------------------------------------------------------- scope picker */")]

    def rule(selector):
        m = _re.search(_re.escape(selector) + r"\s*\{([^}]*)\}", block)
        assert m, f"{selector} is gone — this guard needs rewriting"
        return m.group(1)

    primary = rule(".desk-block--primary .desk-block-title")
    secondary = rule(".desk-block--secondary .desk-block-title")

    assert "var(--udm-gold-soft)" in primary and "var(--udm-gold-ink-dark)" in primary
    assert "var(--udm-teal-soft)" in secondary and "var(--udm-teal-ink-dark)" in secondary
    assert primary != secondary, "the two blocks would look identical again"
    assert "var(--udm-red" not in block

    # The plain inks miss AA at this size on their own tint — gold-ink on
    # gold-soft is 4.34:1 and the titles run at 11.5px.
    assert "var(--udm-gold-ink)" not in primary
    assert "var(--udm-teal-ink)" not in secondary


@pytest.mark.django_db
def test_the_desk_still_reads_from_the_same_two_context_keys(client, users, awaiting_receipt):
    """Merging the panels is a template change. Renaming the context would make
    it a view change nobody asked for."""
    client.force_login(users["sup"])
    context = client.get(DASHBOARD).context

    assert "attention_records" in context
    assert "recent_records" in context


@pytest.mark.django_db
def test_every_dashboard_panel_stops_at_the_same_five_rows(client, users, offices, memo_type):
    """Recently moved carried eight rows against Needs action's five and the
    Repository panel's five. The three sit in a two-column row, so the tall one
    dragged the card beside it out with it and the row was always ragged."""
    for index in range(9):
        record = create_draft_record(
            user=users["med"], subject=f"Long subject number {index} " + "x" * 90,
            instructions="x", document_type=memo_type,
        )
        route_record(record, [offices["SUP"]], user=users["med"])

    client.force_login(users["sup"])
    context = client.get(DASHBOARD).context

    assert len(context["attention_records"]) == DASHBOARD_ROWS
    assert len(context["recent_records"]) == DASHBOARD_ROWS


@pytest.mark.django_db
def test_a_long_subject_is_capped_rather_than_left_to_set_the_width(client, users, offices, memo_type):
    """These panels list documents whose subject is written by whoever filed
    them. Left to size itself, one long subject set the width of the column and
    therefore of the card, and the panel beside it got what was left — so the
    same dashboard was a different shape depending on what had been filed."""
    record = create_draft_record(
        user=users["med"],
        subject="Quarterly consolidated procurement " + "and supplementary " * 6,
        instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    record.refresh_from_db()

    client.force_login(users["sup"])
    body = client.get(DASHBOARD).content.decode()

    assert "desk-cell-title" in body
    # The cap is in CSS, so the full subject can stay on the title attribute —
    # the reader who needs the rest hovers rather than opening the record.
    assert f'title="{record.subject}"' in body


@pytest.mark.django_db
def test_the_panel_caps_are_css_not_just_truncation():
    """A `truncatechars` alone cuts every subject at the same character count
    whatever the column is worth. The width has to be carried by the stylesheet
    or the two panels go back to disagreeing about it."""
    css = pathlib.Path("static/css/doctrack.css").read_text(encoding="utf-8")

    assert ".desk-cell {" in css and "max-width" in css
    assert "-webkit-line-clamp" in css
    # A row that is a fixed height whether its title is one line or two.
    assert ".newest-row" in css


@pytest.mark.django_db
def test_the_desk_keeps_both_empty_states(client, users):
    """A fresh account has nothing in either block, and an empty panel that says
    nothing looks broken rather than clear."""
    client.force_login(users["hr"])
    body = client.get(DASHBOARD).content.decode()

    assert "No incoming documents are waiting" in body
    assert "No active records yet." in body


@pytest.mark.django_db
def test_the_desk_comes_before_the_memo_dialog(client, users, awaiting_receipt):
    client.force_login(users["sup"])
    body = client.get(DASHBOARD).content.decode()

    assert body.index("Action Centre") < body.index('id="dashboard-memo"')


# ------------------------------------------------------------ quick actions
@pytest.mark.django_db
def test_the_dashboard_no_longer_offers_the_two_start_work_buttons(client, users):
    """Removed on request.

    They were added so somebody landing on the dashboard could start a document
    without going out to a list page first. Taking them off reverses that: the
    dashboard now links to neither view for anybody, including a user who is
    allowed to use them, and starting work is reached from the page that owns
    the act. That is the trade the removal makes, not an oversight.
    """
    from django.urls import reverse

    client.force_login(users["med"])
    response = client.get(DASHBOARD)
    body = response.content.decode()

    assert response.context["can_start_work"] is True, "still permitted, just not offered"
    assert reverse("tracking:create") not in body
    assert reverse("documents:upload") not in body
    assert "New Tracking Slip" not in body
    assert "Upload to Repository" not in body


def test_the_tracking_list_keeps_its_own_create_button():
    """The removal was from the dashboard. The list page's button is that
    page's own primary action and predates the dashboard ever offering one."""
    listing = pathlib.Path("templates/tracking/list.html").read_text(encoding="utf-8")

    assert "+ New Tracking Slip" in listing


@pytest.mark.django_db
def test_the_gate_still_reads_false_for_a_viewer(client, users):
    """Asserting the flag, not the markup.

    This used to check that a viewer was offered neither button, because both
    target views refuse them and a button promising a redirect is worse than no
    button. Now that nobody is offered them the markup half of that would pass
    whatever the gate did, so only the gate is worth asserting — it is what
    those buttons would be restored behind.
    """
    client.force_login(users["viewer"])

    assert client.get(DASHBOARD).context["can_start_work"] is False


@pytest.mark.django_db
def test_hiding_the_button_is_not_the_permission(client, users):
    """The endpoint stays reachable to anyone who knows the URL, so the view has
    to refuse on its own — the hidden button is a courtesy, not a control."""
    client.force_login(users["viewer"])

    assert client.get("/tracking/new/").status_code in (302, 403)


@pytest.mark.django_db
def test_an_account_with_no_office_is_not_offered_them_either(client, users, offices):
    """`OfficeAssignedMixin` sends these accounts back to the dashboard with a
    warning, which is a poor answer to a button on the dashboard."""
    from django.contrib.auth import get_user_model

    orphan = get_user_model().objects.create_user(
        username="unassigned", password="TestPass123!", office=None, role="USER",
    )
    client.force_login(orphan)

    assert client.get(DASHBOARD).context["can_start_work"] is False


# ---------------------------------------------------------- bulk receipt
@pytest.mark.django_db
def test_the_desk_offers_bulk_receipt_when_something_can_be_received(
    client, users, awaiting_receipt
):
    client.force_login(users["sup"])
    response = client.get(DASHBOARD)
    body = response.content.decode()

    assert response.context["can_bulk_receive"] is True
    assert 'name="record_ids"' in body
    assert 'name="confirm_custody"' in body


@pytest.mark.django_db
def test_the_custody_attestation_is_asked_for_in_the_same_words_as_the_list(
    client, users, awaiting_receipt
):
    """It is a custody assertion landing in an append-only audit trail. The
    dashboard does not get to ask for it more casually than the tracking list
    does, and it is not defaulted or dropped to save a click."""
    client.force_login(users["sup"])
    body = client.get(DASHBOARD).content.decode()
    listing = pathlib.Path("templates/tracking/list.html").read_text(encoding="utf-8")

    wording = "I confirm the selected physical or digital documents are present in my office's custody."
    assert wording in body
    assert wording in listing


@pytest.mark.django_db
def test_the_custody_box_is_required_not_pre_ticked(client, users, awaiting_receipt):
    import re

    client.force_login(users["sup"])
    body = client.get(DASHBOARD).content.decode()

    box = re.search(r'<input[^>]*name="confirm_custody"[^>]*>', body).group(0)
    assert "required" in box
    assert "checked" not in box


@pytest.mark.django_db
def test_the_bulk_form_covers_the_needs_action_block_only(client, users, awaiting_receipt):
    """Recently moved is read-only. A form spanning both would put rows nobody
    can act on inside the thing that submits."""
    import re

    client.force_login(users["sup"])
    body = client.get(DASHBOARD).content.decode()

    form = re.search(r'<form method="post" action="[^"]*bulk-receipt[^"]*".*?</form>', body, re.S)
    assert form, "no bulk receipt form rendered"
    assert 'class="desk-block desk-block--primary"' in form.group(0)
    assert "Recently moved" not in form.group(0)
    assert "csrfmiddlewaretoken" in form.group(0)


@pytest.mark.django_db
def test_no_bulk_footer_when_nothing_on_the_page_can_be_received(client, users, awaiting_receipt):
    """MED raised these and cannot receive them. Showing the attestation to
    somebody with nothing to attest to is an invitation to tick it anyway."""
    client.force_login(users["med"])
    response = client.get(DASHBOARD)

    assert response.context["can_bulk_receive"] is False
    assert 'name="confirm_custody"' not in response.content.decode()


@pytest.mark.django_db
def test_the_dashboard_adds_no_write_path_of_its_own(client, users, awaiting_receipt):
    """It posts to the tracking app's existing view, which is the one place
    receipt is recorded."""
    from django.urls import reverse

    client.force_login(users["sup"])
    body = client.get(DASHBOARD).content.decode()

    assert reverse("tracking:bulk_confirm_receipt") in body


@pytest.mark.django_db
def test_bulk_receipt_from_the_dashboard_actually_records_it(client, users, awaiting_receipt):
    """The markup is only worth having if the post it builds is accepted."""
    from apps.tracking.models import Status

    client.force_login(users["sup"])
    response = client.post(
        "/tracking/bulk-receipt/",
        {
            "record_ids": [r.pk for r in awaiting_receipt[:2]],
            "confirm_custody": "on",
            "note": "",
        },
    )

    assert response.status_code == 302
    for record in awaiting_receipt[:2]:
        record.refresh_from_db()
        assert record.status == Status.RECEIVED
    awaiting_receipt[2].refresh_from_db()
    assert awaiting_receipt[2].status == Status.PENDING_RECEIPT, "unticked row untouched"


@pytest.mark.django_db
def test_the_post_is_refused_without_the_attestation(client, users, awaiting_receipt):
    """The whole reason the dashboard cannot offer a one-click receive."""
    from apps.tracking.models import Status

    client.force_login(users["sup"])
    client.post(
        "/tracking/bulk-receipt/",
        {"record_ids": [r.pk for r in awaiting_receipt], "note": ""},
    )

    for record in awaiting_receipt:
        record.refresh_from_db()
        assert record.status == Status.PENDING_RECEIPT


@pytest.mark.django_db
def test_the_desk_adds_no_inline_event_handlers(client, users, awaiting_receipt):
    """django-csp is enforced. The one `onchange` on the page is the scope
    picker, which predates this panel and is left alone deliberately."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert "onclick=" not in body
    assert "onsubmit=" not in body
    assert body.count("onchange=") <= 1


# ------------------------------------------------------- the side rule
#: Each side's panels, top to bottom. Document Tracking is the left, wide side;
#: Document Repository the right. The page ran as full-width rows before, with
#: the one repository chart between two tracking charts, so a figure's module
#: could not be told from where it sat.
TRACKING_SIDE = ["Tracking", "Action Centre", "Created, handed over and completed", "Turnaround Time"]
REPOSITORY_SIDE = ["Repository", "Added to the repository"]


def _side(body, name):
    """The rendered markup of one side, and its panel headings in order."""
    import re

    marker = f'class="col-xl-{8 if name == "tracking" else 4} dashboard-side dashboard-side--{name}"'
    start = body.rindex("<section", 0, body.index(marker))
    depth, end = 0, start
    for tag in re.finditer(r"<section\b|</section>", body[start:]):
        depth += 1 if tag.group(0) == "<section" else -1
        if depth == 0:
            end = start + tag.end()
            break
    markup = body[start:end]
    headings = [
        re.split(r"&mdash;| — | for the Month of ", re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip())[0].strip()
        for m in re.finditer(r"<h2>(.*?)</h2>", markup, re.S)
    ]
    return markup, headings


@pytest.mark.django_db
def test_every_panel_sits_on_its_own_side(client, users, filed_record):
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert _side(body, "tracking")[1] == TRACKING_SIDE
    assert _side(body, "repository")[1] == REPOSITORY_SIDE
    assert body.index("dashboard-side--tracking") < body.index("dashboard-side--repository"), (
        "tracking first, so it leads when the sides stack on a narrow screen"
    )


@pytest.mark.django_db
def test_each_side_is_headed_and_opens_its_module(client, users, offices, filed_record):
    client.force_login(users["admin"])
    office = offices["SUP"].pk
    body = client.get(f"{DASHBOARD}?office={office}").content.decode()

    tracking, _ = _side(body, "tracking")
    repository, _ = _side(body, "repository")
    assert 'id="side-tracking-title">Document Tracking</div>' in tracking
    assert 'id="side-repository-title">Document Repository</div>' in repository
    assert f'href="/tracking/?office={office}"' in tracking, "the office picked rides along"
    assert f'href="/documents/?office={office}"' in repository
    assert '<div class="eyebrow">tracking</div>' not in body, "the headings replace the old labels"


def test_the_sides_split_two_thirds_to_a_third_and_the_charts_are_on_the_wide_one():
    """The twelve-month charts need width: they sit on the tracking side,
    which is the wide one, and size themselves to it (container queries)."""
    import pathlib

    html = pathlib.Path("templates/core/dashboard.html").read_text(encoding="utf-8")
    tracking = html.index('<section class="col-xl-8 dashboard-side dashboard-side--tracking"')
    repository = html.index('<section class="col-xl-4 dashboard-side dashboard-side--repository"')

    for chart in ('class="column-chart-frame"', 'class="trend-svg"'):
        assert tracking < html.index(chart) < repository, chart
    last = html.rindex("<h2>", tracking, repository)
    assert html.startswith("<h2>Turnaround Time for the Month of", last), "turnaround closes its side"


@pytest.mark.django_db
def test_the_card_row_is_three_queues_the_reader_acts_on(client, users, filed_record):
    """"Held by your office" was a fourth card counting ?scope=received — a
    stage of the same pile Incoming beside it already counts, one click away
    from it, and a fifth count query on every load."""
    client.force_login(users["sup"])
    response = client.get(DASHBOARD)
    body = response.content.decode()

    assert "Held by your office" not in body
    assert "received_count" not in response.context
    for label in ("Incoming", "Outgoing", "Overdue"):
        assert f'<div class="label">{label}</div>' in body, label


@pytest.mark.django_db
def test_the_removed_panels_are_gone_from_the_page(client, users, filed_record):
    """Four panels were removed by explicit approval, one yes each."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    for heading in ("Needs attention now", "Records by status", "Office Flow Today"):
        assert f"<h2>{heading}</h2>" not in body, heading


@pytest.mark.django_db
def test_removing_the_panels_left_the_helpers_behind_them_alone(client, users, overdue_record):
    """Markup only. Reports reads several of the same helpers, and the memo
    still reads the overdue figures.

    `received_today` was in this list. It protected a helper that outlived its
    panel, and nothing has read it since the redesign; it has now been deleted
    with its two siblings, so the assertion moves to the other side — see
    test_the_unrendered_today_counters_are_gone.
    """
    client.force_login(users["admin"])
    context = client.get(DASHBOARD).context

    # `live_by_status` left this list when it was deleted — nothing rendered
    # it. The two overdue aggregates stay: the memo reads them.
    for key in ("overdue_offices", "overdue_summary"):
        assert key in context, key


@pytest.mark.django_db
def test_the_unrendered_today_counters_are_gone(client, users, overdue_record):
    """Three queries per load for a panel that no longer exists — and each wrong
    if ever reinstated: two ignored the office picker, the third had no office
    filter at all."""
    client.force_login(users["admin"])
    context = client.get(DASHBOARD).context

    for key in ("received_today", "forwarded_today", "completed_today"):
        assert key not in context, key


@pytest.mark.django_db
def test_the_dashboard_no_longer_offers_to_print_itself(client, users, finished_record):
    """The dashboard is a screen for reading, not a document.

    Printing it produced a chopped-up screenshot whose panels meant nothing off
    the page. The memo is the thing worth putting on paper and it prints from a
    page of its own, so the only print-related control left here is the one
    that opens the memo.
    """
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert "Print dashboard" not in body
    assert "Generate memo" in body


@pytest.mark.django_db
def test_the_dashboard_has_no_print_letterhead(client, users, finished_record):
    """The header was hidden on screen and shown only to Ctrl+P.

    That is the worse half of both options: a letterheaded, timestamped sheet
    that looks official and is recorded nowhere, reachable only by a route the
    app does not advertise. The memo is the document; this page is not, so it
    prints plainly or not at all.
    """
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert "dashboard-print-header" not in body
    assert "— Dashboard" not in body, "the letterhead line"


@pytest.mark.django_db
def test_ctrl_p_on_the_dashboard_is_recorded_and_says_what_it_is(client, users, finished_record):
    """It was an accepted gap: nothing can stop the browser's own print
    dialog, and the dashboard carried no marker, so its paper left no trace.
    The consultation asked for it to be audited like Reports and the memo, and
    the printout now says it is a view of the screen and where the formal
    record comes from."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert 'data-print-log="the dashboard"' in body
    assert "data-print-log-url" in body and "data-print-log-csrf" in body
    note = body[body.index('class="dashboard-print-note'):]
    assert note.startswith('class="dashboard-print-note d-none d-print-block">'), "on paper only"
    assert "Generate memo" in note[:400]


@pytest.mark.django_db
def test_no_change_arrows_on_any_of_the_new_panels(client, users, overdue_record):
    """Still explicitly refused: an arrow on a records backlog reads as a
    verdict on the office."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    for marker in ("▲", "▼", "trend-up", "trend-down", "change-indicator"):
        assert marker not in body, marker


@pytest.mark.django_db
def test_the_real_sidebar_was_left_alone(client, users):
    """Nothing from the mockup's own sidebar was carried over."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    for marker in ("Create Routing Slip", "Upload &amp; Archive", "Offices &amp; Users",
                   "Incoming &amp; Outgoing"):
        assert marker not in body, marker


# ------------------------------------------------------ the memo print page
MEMO_PRINT = "/memo/print/"


@pytest.mark.django_db
def test_the_print_page_renders_the_same_figures_as_the_dashboard(
    client, users, overdue_record
):
    """The whole point of sharing the assembly rather than repeating it.

    If the print page computed its own answer the two could disagree — a
    document changing status between opening the dialog and pressing Print is
    enough — and a printed memo that contradicts the screen it came from is
    worse than no memo.
    """
    client.force_login(users["admin"])
    dashboard = client.get(DASHBOARD)
    printed = client.get(MEMO_PRINT)

    assert printed.status_code == 200
    assert printed.context["memo"] == dashboard.context["memo"]
    assert printed.context["breakdown"]["total"] == dashboard.context["breakdown"]["total"]
    assert str(dashboard.context["breakdown"]["total"]) in printed.content.decode()


@pytest.mark.django_db
def test_the_print_page_carries_none_of_the_app_chrome(client, users, finished_record):
    """A page that exists to be printed, not a dashboard with a stylesheet over
    it. The sidebar, topbar and dashboard grid are not hidden here — they are
    not on the page."""
    client.force_login(users["admin"])
    body = client.get(MEMO_PRINT).content.decode()

    for chrome in ("app-sidebar", "app-topbar", "app-footer", "card-udm", "memo-sheet"):
        assert chrome not in body, chrome
    assert "memo-print" in body


@pytest.mark.django_db
def test_the_print_page_logs_the_copy_it_produces(client, users, finished_record):
    """Printing happens in the browser, so a paper copy leaves no trace unless
    the page asks for one to be recorded. PRINT events are never deduplicated,
    which makes the marker not optional on a page whose purpose is paper."""
    client.force_login(users["admin"])
    body = client.get(MEMO_PRINT).content.decode()

    assert 'data-print-log="the dashboard memo"' in body
    assert "data-print-log-url" in body
    assert "data-print-log-csrf" in body


@pytest.mark.django_db
def test_the_print_page_opens_its_own_dialog_without_inline_script(
    client, users, finished_record
):
    """django-csp allows no inline script, so the behaviour is declared as data
    and read by doctrack.js — the same way every other behaviour on the site is
    wired."""
    client.force_login(users["admin"])
    body = client.get(MEMO_PRINT).content.decode()

    assert "data-auto-print" in body
    assert "<script>" not in body, "inline script would be blocked by CSP"
    assert "onload=" not in body


@pytest.mark.django_db
def test_the_print_page_ignores_an_office_a_user_may_not_pick(
    client, users, offices, finished_record
):
    """The URL is not a second authorization path.

    An ordinary user gets no office picker on the dashboard, so `?office=` must
    do nothing here either. Were it honoured, anybody could read another
    office's figures by typing an id into the query string.
    """
    client.force_login(users["med"])
    plain = client.get(MEMO_PRINT)
    with_param = client.get(f"{MEMO_PRINT}?office={offices['SUP'].pk}")

    assert with_param.context["scope"]["office"] is None
    assert with_param.context["scope"]["can_pick"] is False
    assert with_param.context["memo"] == plain.context["memo"]


@pytest.mark.django_db
def test_the_print_page_honours_an_office_an_admin_may_pick(
    client, users, offices, finished_record
):
    """The other half of the same rule: the scope the dashboard showed has to
    travel to the paper, or the printed memo covers a different set of offices
    than the one it was generated from."""
    client.force_login(users["admin"])
    narrowed = client.get(f"{MEMO_PRINT}?office={offices['SUP'].pk}")

    assert narrowed.context["scope"]["office"] == offices["SUP"]
    assert {"label": "Scope", "value": offices["SUP"].name} in narrowed.context["memo"][0]["lines"]


@pytest.mark.django_db
def test_the_print_page_needs_a_login(client):
    """It reads office figures, so it is behind the same gate as everything
    else that does."""
    response = client.get(MEMO_PRINT)

    assert response.status_code in (302, 403)


@pytest.mark.django_db
def test_the_dashboard_accepts_the_all_offices_parameter(client, users, finished_record):
    """`/?office=all` was a 500: `_scope` called `.name` on the ALL_OFFICES
    sentinel. Reports and Tracking accept it, and Tracking's picker sends it."""
    client.force_login(users["admin"])

    response = client.get(f"{DASHBOARD}?office=all")

    assert response.status_code == 200
    assert response.context["scope"]["all_offices"] is True
    assert response.context["scope"]["office"] is None
