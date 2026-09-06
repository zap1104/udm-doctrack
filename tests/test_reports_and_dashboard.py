"""The report panels and the dashboard breakdown.

Covers the reworked reports (office-hours turnaround kept beside calendar time,
cumulative series, the office leaderboard, selection by name, print isolation)
and the dashboard's combined percentage.

The report is the dashboard's figures at full detail, split into the two corpora
the app keeps apart everywhere else: what is moving, and what has been filed. The
panel list is named here rather than inferred from the template — see
TRACKING_PANELS and REPOSITORY_PANELS.
"""

from __future__ import annotations

import pytest

from apps.tracking.services import (
    complete_record,
    confirm_receipt,
    create_draft_record,
    route_record,
)

REPORTS = "/reports/"
DASHBOARD = "/"


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


# --- 3.1 office hours beside calendar time ---------------------------------
@pytest.mark.django_db
def test_turnaround_reports_office_hours_and_calendar_time_together(
    client, finished_record, users
):
    """Neither replaces the other: office hours judge the office fairly,
    calendar time is what the requester actually waited."""
    client.force_login(users["admin"])
    turnaround = client.get(REPORTS).context["turnaround"]

    for key in ("receipt", "processing", "lifetime"):
        assert turnaround[key], key
        assert turnaround[f"{key}_calendar"], f"{key}_calendar"


@pytest.mark.django_db
def test_the_page_says_the_figure_excludes_weekends_but_not_holidays(
    client, finished_record, users
):
    """Labelled honestly rather than presented as exact."""
    client.force_login(users["admin"])
    body = client.get(REPORTS).content.decode()

    assert "Office hours only" in body
    assert "holidays not" in body


# --- the panels the report is specified to carry ----------------------------
#: Document Tracking Reports, in order. The page is the dashboard's figures at
#: full detail, so it is a named list rather than whatever the template happens
#: to hold: a panel arriving without a decision behind it is how this page grew
#: a per-office turnaround table and an "Archive quality" card that restated
#: three stat cards verbatim.
TRACKING_PANELS = (
    "Cumulative tracking volume",
    "Records by status",
    "Turnaround",
    "Overdue documents by holding office",
    "Documents handled by office",
    "Transferred vs received by office",
)

#: Document Repository Report, in order.
REPOSITORY_PANELS = (
    "Monthly repository volume",
    "Documents by type",
    "Most used searches",
)


@pytest.mark.django_db
def test_the_report_carries_exactly_the_specified_panels(client, finished_record, users):
    client.force_login(users["admin"])
    body = client.get(REPORTS).content.decode()

    headings = [
        line.split("<h2>", 1)[1].split("</h2>", 1)[0]
        for line in body.splitlines()
        if "<h2>" in line and "</h2>" in line
    ]

    assert headings == list(TRACKING_PANELS) + list(REPOSITORY_PANELS)


@pytest.mark.django_db
def test_the_two_sections_stay_apart(client, finished_record, users):
    """Tracking answers "where is the work"; the repository answers "what did we
    file". They are different corpora and the page keeps them in two panels."""
    client.force_login(users["admin"])
    body = client.get(REPORTS).content.decode()

    tracking = body.index('data-report-panel="tracking"')
    documents = body.index('data-report-panel="documents"')

    assert tracking < body.index(TRACKING_PANELS[-1]) < documents
    assert documents < body.index(REPOSITORY_PANELS[0])


@pytest.mark.django_db
def test_the_dropped_panels_are_gone_from_the_page_and_the_context(
    client, finished_record, users
):
    """"Turnaround by office" is not in the specified list, and "Archive
    quality" restated three of the stat cards above it word for word. Their
    view code went with them rather than being left computing figures nothing
    renders — the per-office table cost two queries and a Python aggregation."""
    client.force_login(users["admin"])
    response = client.get(REPORTS)
    body = response.content.decode()

    assert "Turnaround by office" not in body
    assert "Archive quality" not in body
    assert "turnaround_by_office" not in response.context


# --- 3.2 overdue by holding office -----------------------------------------
@pytest.mark.django_db
def test_overdue_offices_report_a_share_of_the_whole_backlog(
    client, finished_record, users
):
    """Code, count and share. The bar cannot say the share: it is scaled against
    the *longest* queue so the widest one fills its track, so two offices
    holding 6 and 5 draw almost the same bar whether that is half the backlog
    each or a tenth of it."""
    client.force_login(users["admin"])
    rows = client.get(REPORTS).context["overdue_offices"]

    for row in rows:
        assert row["share"] <= 100
    assert sum(row["share"] for row in rows) <= 100


# --- 3.3 cumulative three-series -------------------------------------------
@pytest.mark.django_db
def test_the_monthly_chart_has_three_cumulative_series(client, finished_record, users):
    client.force_login(users["admin"])
    monthly = client.get(REPORTS).context["monthly"]

    row = monthly["rows"][-1]
    for key in ("created", "transferred", "completed"):
        assert key in row, key


@pytest.mark.django_db
def test_the_series_never_decrease(client, finished_record, users):
    """A running total that falls is not a running total."""
    client.force_login(users["admin"])
    rows = client.get(REPORTS).context["monthly"]["rows"]

    for key in ("created", "transferred", "completed"):
        values = [row[key] for row in rows]
        assert values == sorted(values), f"{key} went down"


@pytest.mark.django_db
def test_the_gap_between_created_and_completed_is_what_is_still_open(
    client, users, offices, memo_type
):
    open_one = create_draft_record(
        user=users["med"], subject="Still going", instructions="x", document_type=memo_type,
    )
    route_record(open_one, [offices["SUP"]], user=users["med"])

    client.force_login(users["admin"])
    monthly = client.get(REPORTS).context["monthly"]
    last = monthly["rows"][-1]

    assert monthly["outstanding"] == last["created"] - last["completed"]
    assert monthly["outstanding"] >= 1


# --- 3.4 office leaderboard ------------------------------------------------
@pytest.mark.django_db
def test_the_office_leaderboard_counts_receipts(client, finished_record, users, offices):
    """"Handled" means received — crediting what an office sent would reward a
    pass-through desk over the office that did the work."""
    client.force_login(users["admin"])
    volume = client.get(REPORTS).context["office_volume"]

    codes = {row["code"] for row in volume["rows"]}
    assert offices["SUP"].code in codes
    assert offices["MED"].code not in codes, "MED sent it but never received it"


@pytest.mark.django_db
def test_the_leaderboard_shows_cumulative_and_this_month_together(
    client, finished_record, users
):
    client.force_login(users["admin"])
    rows = client.get(REPORTS).context["office_volume"]["rows"]

    row = rows[0]
    assert row["cumulative"] >= row["this_month"]
    assert "cumulative_percent" in row and "this_month_percent" in row

    body = client.get(REPORTS).content.decode()
    assert "Documents handled by office" in body


# --- 3.10 naming -----------------------------------------------------------
@pytest.mark.django_db
def test_the_two_report_sections_are_named_exactly(client, users):
    client.force_login(users["admin"])
    body = client.get(REPORTS).content.decode()

    assert "Document Tracking Reports" in body
    assert "Document Repository Report" in body
    assert "Document Management Reports" not in body


# --- who gets the filter at all ---------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("who", ["admin", "med_admin"])
def test_an_administrator_gets_the_office_filter(client, users, who):
    client.force_login(users[who])
    body = client.get(REPORTS).content.decode()

    assert 'id="report-office"' in body
    assert 'id="report-office-name"' in body


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["med", "viewer"])
def test_an_ordinary_account_goes_straight_to_its_own_office(client, users, who):
    """No filter row at all. The dropdown they used to be shown went through
    `scope_office`, which drops a pick from an account without the picker — so
    it was a control that appeared to work and did nothing."""
    client.force_login(users[who])
    response = client.get(REPORTS)
    body = response.content.decode()

    assert 'id="report-office"' not in body
    assert 'id="report-office-name"' not in body
    assert response.context["filters"]["can_pick"] is False
    assert users[who].office.name in body, "the heading names the office it describes"


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["med", "viewer"])
def test_an_ordinary_account_cannot_name_another_office_by_hand(
    client, users, offices, who
):
    """The two office controls disagreed about who may use them: the dropdown
    was gated and the name box was not, so `?office_name=Supply` filtered this
    page to another office for an account with no picker at all."""
    client.force_login(users[who])
    filters = client.get(
        f"{REPORTS}?office={offices['SUP'].pk}&office_name=Supply and Property Management"
    ).context["filters"]

    assert filters["office"] is None
    assert filters["office_name"] == ""


@pytest.mark.django_db
def test_the_all_offices_sentinel_never_reaches_an_office_lookup(client, users):
    """`scope_office` answers "all" with a string sentinel. Reports had never
    been given the option, so it had never hit it — and would have, on a
    hand-typed URL, with Q(originating_office="__all__")."""
    client.force_login(users["admin"])

    response = client.get(f"{REPORTS}?office=all")

    assert response.status_code == 200
    assert response.context["filters"]["office"] is None
    assert response.context["filters"]["all_offices"] is True


@pytest.mark.django_db
def test_the_report_filters_down_to_the_office_only(client, users):
    """Year, status and document type are gone. The panels below already break
    the same records down by month, by status and by type, so those three
    filtered a chart into agreeing with itself."""
    client.force_login(users["admin"])
    body = client.get(REPORTS).content.decode()

    for gone in ('id="report-year"', 'id="report-status"', 'id="report-document-type"'):
        assert gone not in body, gone


@pytest.mark.django_db
def test_the_export_button_carries_the_office_it_was_pressed_under(
    client, finished_record, users, offices
):
    """A bare URL meant the export re-read its filters from its own empty query
    string, so exporting a one-office report handed back everything."""
    client.force_login(users["admin"])
    body = client.get(f"{REPORTS}?office={offices['SUP'].pk}").content.decode()

    assert f"/reports/export/?office={offices['SUP'].pk}" in body


@pytest.mark.django_db
def test_the_export_names_the_office_it_covers(client, finished_record, users, offices):
    """A CSV attached to a memo is read by somebody who cannot re-run the
    query, so the scope travels with it."""
    client.force_login(users["admin"])

    body = client.get(f"/reports/export/?office={offices['SUP'].pk}").content.decode()

    assert offices["SUP"].name in body.splitlines()[0]


# --- 3.11 selection by name ------------------------------------------------
@pytest.mark.django_db
def test_an_office_report_can_be_pulled_by_name(client, finished_record, users, offices):
    client.force_login(users["admin"])
    filters = client.get(f"{REPORTS}?office_name=Supply and Property Management").context["filters"]

    assert filters["office"] == offices["SUP"]


@pytest.mark.django_db
def test_an_office_report_can_be_pulled_by_code(client, users, offices):
    client.force_login(users["admin"])
    filters = client.get(f"{REPORTS}?office_name=SUP").context["filters"]

    assert filters["office"] == offices["SUP"]


@pytest.mark.django_db
def test_a_unique_prefix_resolves(client, users, offices):
    client.force_login(users["admin"])
    filters = client.get(f"{REPORTS}?office_name=Human").context["filters"]

    assert filters["office"] == offices["HR"]


@pytest.mark.django_db
def test_an_ambiguous_name_resolves_to_nothing_and_says_so(client, users, offices):
    """Quietly picking the first match would hand somebody another office's
    report under the name they typed — and generating one notifies nobody, so
    there is no second pair of eyes to catch it."""
    from apps.accounts.models import Office

    Office.objects.create(code="SUP2", name="Supply Annex", cluster="OVPA")

    client.force_login(users["admin"])
    response = client.get(f"{REPORTS}?office_name=Supply")

    assert response.context["filters"]["office"] is None
    assert response.context["filters"]["office_name_unmatched"] is True
    assert "No single office matches" in response.content.decode()


@pytest.mark.django_db
def test_the_dropdown_wins_over_a_stale_name(client, users, offices):
    client.force_login(users["admin"])
    filters = client.get(
        f"{REPORTS}?office={offices['HR'].pk}&office_name=SUP"
    ).context["filters"]

    assert filters["office"] == offices["HR"]


@pytest.mark.django_db
def test_generating_a_report_notifies_nobody(client, finished_record, users, offices):
    """An anti-tampering requirement: an office must not learn it is being
    reviewed."""
    from apps.core.models import Notification

    before = Notification.objects.count()
    client.force_login(users["admin"])
    client.get(f"{REPORTS}?office_name=SUP")

    assert Notification.objects.count() == before


# --- 3.5 / 3.6 / 3.7 / 3.8 dashboard ---------------------------------------
@pytest.mark.django_db
def test_the_dashboard_shows_one_percentage_across_both_modules(
    client, finished_record, users
):
    client.force_login(users["admin"])
    breakdown = client.get(DASHBOARD).context["breakdown"]

    keys = {row["key"] for row in breakdown["slices"]}
    # "incoming" was the label on a slice computing status=RECEIVED, and
    # "overdue" was a condition sitting among stages it overlapped. The slices
    # are the four live statuses now, which partition the tracking side.
    assert {"pending_receipt", "received", "in_process", "pending_upload"} <= keys, "tracking slices"
    assert {"historical", "completed"} <= keys, "repository slices"
    assert "overdue" not in keys, "a condition, not a stage — it has a stat card"
    assert breakdown["total"] == breakdown["tracking_total"] + breakdown["repository_total"]


@pytest.mark.django_db
def test_the_slices_add_up_to_the_whole(client, finished_record, users, offices, memo_type):
    """Overlapping slices would make the percentages sum past 100."""
    other = create_draft_record(
        user=users["med"], subject="Another", instructions="x", document_type=memo_type,
    )
    route_record(other, [offices["SUP"]], user=users["med"])

    client.force_login(users["admin"])
    breakdown = client.get(DASHBOARD).context["breakdown"]

    assert sum(row["total"] for row in breakdown["slices"]) == breakdown["total"]


@pytest.mark.django_db
def test_every_slice_links_through_to_its_list(client, finished_record, users):
    """A percentage nobody can open is a number the reader has to take on trust."""
    client.force_login(users["admin"])
    breakdown = client.get(DASHBOARD).context["breakdown"]

    for row in breakdown["slices"]:
        assert row["url"], row["key"]


@pytest.mark.django_db
def test_every_tracking_slice_opens_the_tracking_list(client, users):
    """Overdue was the exception and is no longer.

    It pointed at Reports on the argument that "why are these late and whose
    are they" is a report rather than a list of rows. That is true of the
    question and not of the click: a slice counting documents is opened to see
    the documents, and one slice in the ring leaving for a different page is a
    surprise every time it happens. The per-office breakdown is still a click
    away in Reports, which the dashboard links to in its own right.
    """
    client.force_login(users["admin"])
    breakdown = client.get(DASHBOARD).context["breakdown"]

    tracking = [row for row in breakdown["slices"] if row["group"] == "tracking"]
    assert tracking, "the ring should have tracking slices"
    for row in tracking:
        assert row["url"].startswith("/tracking/"), row["key"]


@pytest.mark.django_db
def test_the_dashboard_offers_a_way_into_reports(client, users):
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert "Open Reports" in body


@pytest.mark.django_db
def test_the_memo_is_what_the_dashboard_offers_to_print(client, users):
    """The dashboard prints nothing itself any more.

    It used to carry a print button and a `data-print-trigger` in the memo
    dialog, both calling window.print() on the dashboard. The dialog one was
    the bug: the print stylesheet hides dialogs, so pressing Print inside the
    memo printed the dashboard behind it. Both are gone, and the memo's Print
    is a link to a page that contains only the memo.
    """
    from django.urls import reverse

    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    assert "data-print-trigger" not in body
    assert reverse("core:dashboard_memo_print") in body


@pytest.mark.django_db
def test_no_change_arrows_anywhere_on_the_dashboard(client, finished_record, users):
    """Explicitly refused: a month-on-month arrow on a records backlog reads as
    a verdict on the office."""
    client.force_login(users["admin"])
    body = client.get(DASHBOARD).content.decode()

    for marker in ("▲", "▼", "trend-up", "trend-down", "change-indicator"):
        assert marker not in body, marker


@pytest.mark.django_db
def test_the_breakdown_respects_visibility(client, finished_record, users):
    """HR had nothing to do with the record, so it is not in HR's total."""
    client.force_login(users["hr"])
    breakdown = client.get(DASHBOARD).context["breakdown"]

    client.force_login(users["admin"])
    admin_breakdown = client.get(DASHBOARD).context["breakdown"]

    assert breakdown["total"] < admin_breakdown["total"]


# --- 3.9 print isolation ---------------------------------------------------
def _print_block(css: str) -> str:
    """The @media print block that governs the report panels."""
    start = css.index(".report-panel { display:none; }", css.index("@media print {\n  @page { size:A4 portrait"))
    return css[start : start + 400]


def test_printing_emits_only_the_panel_that_is_on_screen():
    """The rule used to be `.report-panel { display:block !important }`, so
    every print job contained both the tracking and the repository report and
    neither could be printed alone. The !important also beat the rule that does
    the tab switching, so screen and paper disagreed about the selection."""
    import pathlib

    css = pathlib.Path("static/css/doctrack.css").read_text(encoding="utf-8")
    block = _print_block(css)

    assert ".report-panel { display:none; }" in block
    assert ".report-panel.active { display:block;" in block
    assert "display:block !important" not in block


def test_the_screen_rule_still_hides_the_inactive_panel():
    import pathlib

    css = pathlib.Path("static/css/doctrack.css").read_text(encoding="utf-8")

    assert ".report-panel { display:none; }" in css
    assert ".report-panel.active { display:block; }" in css


@pytest.mark.django_db
def test_both_panels_are_present_in_the_markup_with_one_active(client, users):
    """Print isolation is done in CSS off the active class, so the markup must
    carry exactly one active panel for it to have something to select."""
    client.force_login(users["admin"])
    body = client.get(REPORTS).content.decode()

    assert body.count('class="report-panel active"') == 1
    assert body.count('class="report-panel"') == 1
