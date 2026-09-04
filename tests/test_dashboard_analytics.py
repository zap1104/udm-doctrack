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
from apps.documents.models import Document
from apps.tracking.models import TrackingRecord
from apps.tracking.services import (
    complete_record,
    confirm_receipt,
    create_draft_record,
    route_record,
)

DASHBOARD = "/"
REPORTS = "/reports/"


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

    calls = []
    original = analytics.monthly_volume

    def spy(records):
        calls.append(records)
        return original(records)

    views.analytics.monthly_volume = spy
    try:
        client.force_login(users["admin"])
        client.get(REPORTS)
    finally:
        views.analytics.monthly_volume = original

    assert calls, "ReportsView did not call analytics.monthly_volume"


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
    """The per-office list is capped at the longest few queues. Summing it would
    quietly under-report the total the banner exists to state."""
    records = TrackingRecord.objects.visible_to(users["admin"])
    rows = analytics.overdue_offices(records, limit=0)
    summary = analytics.overdue_summary(records, rows, total_documents=10)

    assert rows == [], "the cap is doing its job for this test"
    assert summary["total"] == 1, "the total survives the cap"
    assert summary["percent_of_all"] == 10


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
    """Incoming excludes anything already counted as overdue, so the slices sum
    to the whole instead of past it."""
    records = TrackingRecord.objects.visible_to(users["admin"])
    documents = Document.objects.visible_to(users["admin"])

    totals = analytics.combined_totals(records, documents)
    tracking = (
        totals["incoming"] + totals["pending_receipt"] + totals["in_process"]
        + totals["overdue"] + totals["pending_upload"]
    )

    assert totals["overdue"] == 1
    assert tracking <= records.distinct().count() + totals["overdue"]
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
    "overdue_offices", "overdue_summary", "tracking_donut", "repository_donut", "monthly",
    "turnaround_trend", "turnaround_trend_points", "turnaround_trend_geometry",
    "turnaround",
    "uploads_by_office", "live_by_status", "memo", "scope",
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

    for key in ("inbox_count", "outgoing_count", "overdue_count",
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
    assert everything["live_by_status"] is not None


# --- the rings -------------------------------------------------------------
# One ring per domain. The combined ring could show the split between tracking
# and the repository but not the shape of either, and tracking is the half
# somebody acts on.
DONUTS = ["tracking_donut", "repository_donut"]


@pytest.mark.django_db
@pytest.mark.parametrize("key", DONUTS)
def test_each_ring_closes_at_one_hundred_percent(client, users, filed_record, key):
    """Independently rounded values leave a hairline gap or an overlap, and a
    ring with a slit in it reads as a rendering fault."""
    client.force_login(users["admin"])
    donut = client.get(DASHBOARD).context[key]

    assert donut["stops"], f"{key} drew nothing"
    assert donut["stops"].rstrip().endswith("100%")


@pytest.mark.django_db
@pytest.mark.parametrize("key", DONUTS)
def test_each_ring_is_measured_against_its_own_domain(client, users, filed_record, key):
    """The whole point of splitting them. Reusing the grand-total percentages
    would leave each ring summing to its share of everything rather than to
    100%, so a Repository ring covering a third of all documents would be drawn
    as a third of a circle with two thirds of it blank."""
    client.force_login(users["admin"])
    donut = client.get(DASHBOARD).context[key]

    assert sum(row["percent"] for row in donut["slices"]) == 100
    assert donut["total"] == sum(row["total"] for row in donut["slices"])


@pytest.mark.django_db
def test_the_two_rings_together_are_the_whole(client, users, filed_record):
    """Two rings replace one; between them they still account for everything."""
    client.force_login(users["admin"])
    context = client.get(DASHBOARD).context

    assert (
        context["tracking_donut"]["total"] + context["repository_donut"]["total"]
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
def test_each_ring_is_painted_from_the_brand_tokens(client, users, filed_record, key):
    """Not the mockup's forest-green and gold."""
    client.force_login(users["admin"])
    donut = client.get(DASHBOARD).context[key]

    for slice_ in donut["slices"]:
        assert slice_["colour"].startswith("var(--"), slice_["key"]


@pytest.mark.django_db
@pytest.mark.parametrize("key", DONUTS)
def test_an_empty_domain_draws_no_ring(client, users, key):
    client.force_login(users["admin"])
    donut = client.get(DASHBOARD).context[key]

    assert donut["stops"] == ""
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

    assert "What this shows" not in response.content.decode()
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
    assert body.index("trend-legend") > body.index("Turnaround by month")


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

    assert memo_headings(memo) == [
        "Overview", "Needs attention", "Turnaround", "Repository activity this month",
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

    for number, depth, line in trace:
        if re.search(r'<div class="row\b', line):
            assert depth == 0, f"line {number}: .row nested at depth {depth}"
        elif re.search(r'<div class="col-', line):
            assert depth == 1, f"line {number}: .col- at depth {depth}, not inside a .row"


@pytest.mark.django_db
def test_the_panels_all_render_inside_the_page_container(client, users, filed_record):
    """The rendered page, not just the template — every panel heading has to
    come before the content block closes."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    for heading in ("Action Centre", "Newest in the Document Repository",
                    "Monthly volume", "Turnaround by month"):
        assert f"<h2>{heading}</h2>" in body, heading

    # The last panel must still precede the memo dialog, which is the final
    # thing in the content block.
    assert body.index("Turnaround by month") < body.index('id="dashboard-memo"')


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

    assert "Needs action" in body
    assert "Recently moved" in body
    assert body.index("Needs action") < body.index("Recently moved")


@pytest.mark.django_db
def test_the_block_titles_sit_below_the_panel_title(client, users, awaiting_receipt):
    """The panel is the h2; the blocks inside it are h3. Nesting level is what
    tells a screen reader the two tables belong to one panel."""
    client.force_login(users["sup"])
    body = client.get(DASHBOARD).content.decode()

    assert '<h3 class="desk-block-title">Needs action</h3>' in body
    assert '<h3 class="desk-block-title">Recently moved</h3>' in body


@pytest.mark.django_db
def test_the_desk_still_reads_from_the_same_two_context_keys(client, users, awaiting_receipt):
    """Merging the panels is a template change. Renaming the context would make
    it a view change nobody asked for."""
    client.force_login(users["sup"])
    context = client.get(DASHBOARD).context

    assert "attention_records" in context
    assert "recent_records" in context


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
    assert "Needs action" in form.group(0)
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


# ------------------------------------------------------- the column rule
#: Top to bottom. A pair is (left, right); a lone string is a full-width row.
#: Left is tracking, right is the repository, wherever a row is split.
EXPECTED_ROWS = [
    ("Tracking", "Repository"),
    ("Action Centre", "Newest in the Document Repository"),
    ("Monthly volume", "Added to the repository"),
    "Turnaround by month",
]


@pytest.mark.django_db
def test_the_panels_run_in_the_order_the_layout_specifies(client, users, filed_record):
    """The donut row teaches "left is what is moving, right is what is filed".
    The page used to drop that immediately, so a reader who had just learned it
    was wrong by the next row."""
    import re

    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    headings = [
        re.sub(r"<[^>]+>|\s+", " ", m.group(1) or m.group(2)).strip()
        for m in re.finditer(r"<h2>(.*?)</h2>", body, re.S)
    ]
    expected = []
    for row in EXPECTED_ROWS:
        expected.extend(row if isinstance(row, tuple) else [row])

    # "Added to the repository &mdash; September" carries the month, as the
    # HTML entity rather than the character.
    normalised = [re.split(r"&mdash;| — ", h)[0].strip() for h in headings]
    assert normalised == expected


@pytest.mark.django_db
def test_the_repository_column_reaches_the_bottom_of_the_page(client, users, filed_record):
    """Newest in the Document Repository used to be stacked inside the same
    column as Office Flow Today, which is why the repository side of the page
    simply stopped after the donut."""
    import re

    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    columns = re.findall(r'<div class="col-xl-6">(.*?)(?=<div class="col-xl-6">|</div>\s*</div>\s*$)', body, re.S)
    assert any("Newest in the Document Repository" in c and "Office Flow Today" not in c
               for c in columns), "the two are still sharing one column"


def test_the_turnaround_panel_is_full_width_and_comes_last():
    """It is the one chart spanning both domains, so it closes the page under
    the four half-width rows rather than sitting in one of them. Three series
    over twelve months does not fit in half a row either — that is what commit
    4b082e8 was about.

    Asserted as <div> depth: a panel in a col-xl-6 sits at the same depth as one
    in a col-12, so the class is checked directly.
    """
    import pathlib

    html = pathlib.Path("templates/core/dashboard.html").read_text(encoding="utf-8")
    head = html.index("<h2>Turnaround by month</h2>")
    column = html.rindex('<div class="col-', 0, head)

    assert html[column:].startswith('<div class="col-12">'), "not full width"
    assert "<h2>" not in html[head + 1:html.index("{% comment %}\n  Generate memo.")], "not last"


@pytest.mark.django_db
def test_the_page_carries_its_two_column_labels(client, users, filed_record):
    """Said once, above the first split row: every row below reads tracking on
    the left and the repository on the right."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert '<div class="eyebrow">tracking</div>' in body
    assert '<div class="eyebrow">repository</div>' in body


@pytest.mark.django_db
def test_held_by_your_office_kept_a_home_when_its_panel_went(client, users, filed_record):
    """custody_count was a line inside Office Flow Today, which the wireframe
    drops. Of the four figures that panel carried it is the one that says how
    much work is sitting with you, so it took a stat card rather than going with
    the others."""
    client.force_login(users["sup"])
    response = client.get(DASHBOARD)
    body = response.content.decode()

    assert "Held by your office" in body
    assert str(response.context["custody_count"]) in body


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
    still reads the overdue figures."""
    client.force_login(users["admin"])
    context = client.get(DASHBOARD).context

    for key in ("overdue_offices", "overdue_summary", "live_by_status",
                "custody_count", "received_today"):
        assert key in context, key


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
def test_ctrl_p_on_the_dashboard_is_not_recorded(client, users, finished_record):
    """An accepted gap, asserted so it stays a decision rather than a surprise.

    Nothing can stop the browser's own print dialog. What the app controls is
    whether that printout is entered in the audit log, and it is not: the
    marker that logs one belongs to a document, and this page is not one. The
    memo's print page carries the marker instead.
    """
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert "data-print-log" not in body


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
