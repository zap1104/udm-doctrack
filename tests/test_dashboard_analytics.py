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
    "overdue_offices", "overdue_summary", "status_donut", "monthly",
    "turnaround_trend", "turnaround_trend_points", "turnaround",
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
                "attention_records", "recent_records", "breakdown", "breakdown_summary"):
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


# --- the ring --------------------------------------------------------------
@pytest.mark.django_db
def test_the_ring_always_closes_at_one_hundred_percent(client, users, finished_record):
    """Seven independently rounded values leave a hairline gap or an overlap,
    and a ring with a slit in it reads as a rendering fault."""
    client.force_login(users["admin"])
    donut = client.get(DASHBOARD).context["status_donut"]

    assert donut["stops"], "something was drawn"
    assert donut["stops"].rstrip().endswith("100%")


@pytest.mark.django_db
def test_the_ring_is_painted_from_the_brand_tokens(client, users, finished_record):
    """Not the mockup's forest-green and gold."""
    client.force_login(users["admin"])
    donut = client.get(DASHBOARD).context["status_donut"]

    for slice_ in donut["slices"]:
        assert slice_["colour"].startswith("var(--"), slice_["key"]


@pytest.mark.django_db
def test_an_empty_system_draws_no_ring(client, users):
    client.force_login(users["admin"])
    donut = client.get(DASHBOARD).context["status_donut"]

    assert donut["stops"] == ""
    assert donut["total"] == 0


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


@pytest.mark.django_db
def test_nothing_measured_means_nothing_plotted(client, users):
    client.force_login(users["admin"])

    assert client.get(DASHBOARD).context["turnaround_trend_points"] == []


# --- the memo --------------------------------------------------------------
@pytest.mark.django_db
def test_the_memo_is_composed_server_side(client, users, finished_record):
    """Sentence-building stays out of the template, like the rest of the app."""
    client.force_login(users["admin"])
    memo = client.get(DASHBOARD).context["memo"]

    assert memo and all(isinstance(line, str) for line in memo)


@pytest.mark.django_db
def test_the_memo_agrees_with_the_figures_beside_it(client, users, overdue_record):
    client.force_login(users["admin"])
    response = client.get(DASHBOARD)
    memo = " ".join(response.context["memo"])

    assert str(response.context["breakdown"]["total"]) in memo
    assert str(response.context["overdue_summary"]["total"]) in memo


@pytest.mark.django_db
def test_the_memo_says_so_when_nothing_is_late(client, users, finished_record):
    client.force_login(users["admin"])
    memo = " ".join(client.get(DASHBOARD).context["memo"])

    assert "Nothing is past its deadline." in memo


@pytest.mark.django_db
def test_the_memo_uses_office_language_not_a_bare_decimal(client, users, finished_record):
    """"An average of 0.0 working days" is not a sentence anybody would write.
    The chart plots days because an axis needs a number; the prose beside it
    gets the same wording Reports uses."""
    client.force_login(users["admin"])
    memo = " ".join(client.get(DASHBOARD).context["memo"])

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

    everything = " ".join(client.get(DASHBOARD).context["memo"])
    assert "all offices hold" in everything

    narrowed = " ".join(
        client.get(f"{DASHBOARD}?office={offices['SUP'].pk}").context["memo"]
    )
    assert f"{offices['SUP'].name} holds" in narrowed


@pytest.mark.django_db
def test_the_memo_never_compares_month_to_month(client, users, finished_record):
    """Descriptive, never comparative — a printed memo carrying a verdict on an
    office outlives the context that produced it."""
    client.force_login(users["admin"])
    memo = " ".join(client.get(DASHBOARD).context["memo"]).lower()

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


def test_no_javascript_charting_library_was_added():
    """Bootstrap 5 + HTMX + Django templates only."""
    import pathlib

    base = pathlib.Path("templates/base.html").read_text(encoding="utf-8").lower()

    for library in ("chart.js", "chartjs", "d3.", "recharts", "plotly", "apexcharts"):
        assert library not in base, library


@pytest.mark.django_db
def test_the_dashboard_still_prints(client, users, finished_record):
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert "data-print-trigger" in body
    assert "dashboard-print-header" in body


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
