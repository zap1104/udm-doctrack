"""Every bar is its share of the whole it states, never of the largest row.

The dashboard's "Added to the repository" panel printed "HR 3 — 20%" beside a
bar that filled the card, because every horizontal bar in the app was measured
against the busiest row: the leader always drew a full track whatever its share.
The same was true of Reports' stages, overdue offices, receipts, handovers and
document types, and of Administration's most used searches.

Now a bar's track is 100% of the whole the panel names in its caption, the bar
is drawn at the percentage printed beside it, and the "Other" rows that account
for a capped list are drawn too, at their own share.
"""

from __future__ import annotations

import re

import pytest

from apps.core import analytics
from apps.core.models import DocumentType
from apps.core.views import ReportsView, top_searches
from apps.documents.models import Document, SearchQueryLog
from tests.test_dashboard_reports_agreement import agreement  # noqa: F401 — fixture, used by name
from tests.test_filter_agreement import traffic  # noqa: F401 — fixture, used by name

DASHBOARD = "/"
REPORTS = "/reports/"
ADMINISTRATION = "/administration/"

#: A bar and the share printed after it in the same row. The tempered dot stops
#: a row with no share from borrowing the next row's.
BAR_THEN_SHARE = re.compile(
    r'class="report-bar">\s*<i[^>]*?style="width:(\d+)%[^"]*"'
    r'(?:(?!class="report-bar").)*?class="status-share num">([^<]+)<',
    re.S,
)


def _drawn_at(text: str) -> int:
    """The width a printed share promises: "<1%" is the 1% floor that keeps a
    real value visible, ">99%" rounds to a full track."""
    return {"<1%": 1, ">99%": 100}.get(text, None) or int(text.rstrip("%"))


def _pairs(body: str) -> list[tuple[int, str]]:
    return [(int(width), share.strip()) for width, share in BAR_THEN_SHARE.findall(body)]


# --- the arithmetic ---------------------------------------------------------
def test_the_bar_that_started_this_is_a_fifth_of_the_track():
    assert analytics.bar(3, 15) == 20
    assert analytics.share_text(3, 15) == "20%"


@pytest.mark.parametrize(("part", "whole", "text"), [
    (0, 10, "0%"), (0, 0, "0%"), (5, 0, "0%"),
    (1, 300, "<1%"),
    (999, 1000, ">99%"),
    (15, 15, "100%"),
    (1, 3, "33%"),
])
def test_a_printed_share_never_contradicts_its_bar(part, whole, text):
    """"0%" beside a visible 1% bar says nothing is there; "100%" beside 999
    of 1,000 says everything is."""
    assert analytics.share_text(part, whole) == text
    assert analytics.bar(part, whole) == _drawn_at(text)


# --- every rendered bar -----------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("url", [f"{DASHBOARD}?office=all", f"{REPORTS}?office=all"])
def test_every_bar_is_drawn_at_the_share_beside_it(client, users, agreement, url):  # noqa: F811
    client.force_login(users["admin"])
    pairs = _pairs(client.get(url).content.decode())

    assert len(pairs) >= 5, "the pattern found the bars"
    for width, share in pairs:
        assert width == _drawn_at(share), (width, share)


@pytest.mark.django_db
def test_the_searches_panel_draws_its_shares_too(client, users):
    for index in range(14):
        for _ in range(index + 1):
            SearchQueryLog.objects.create(user=users["admin"], query=f"query {index:02d}")
    client.force_login(users["admin"])
    pairs = _pairs(client.get(ADMINISTRATION).content.decode())

    assert len(pairs) == analytics.TOP_N + 1, "twelve queries and the rest"
    for width, share in pairs:
        assert width == _drawn_at(share), (width, share)


# --- the leader no longer fills the track -----------------------------------
@pytest.mark.django_db
def test_the_busiest_office_is_drawn_at_its_share_not_at_full_length(
    client, users, agreement  # noqa: F811
):
    client.force_login(users["admin"])
    uploads = client.get(f"{DASHBOARD}?office=all").context["uploads_by_office"]
    leader = uploads["rows"][0]

    assert leader["total"] < uploads["total"]
    assert leader["bar_percent"] == analytics.bar(leader["total"], uploads["total"]) < 100
    for row in uploads["rows"]:
        assert row["bar_percent"] == analytics.bar(row["total"], uploads["total"])


@pytest.mark.django_db
def test_the_other_row_is_drawn_at_its_share(client, users, agreement):  # noqa: F811
    """It had no bar at all, because a long "everything else" would have set
    the scale. Against the whole it is simply how much everything else is."""
    client.force_login(users["admin"])
    uploads = client.get(f"{DASHBOARD}?office=all").context["uploads_by_office"]
    other = next(row for row in uploads["rows"] if row.get("is_remainder"))

    assert other["bar_percent"] == analytics.bar(other["total"], uploads["total"]) > 0


@pytest.mark.django_db
def test_stages_add_up_to_one_full_track(client, users, agreement):  # noqa: F811
    """Every document is in exactly one stage, so the bars partition it."""
    client.force_login(users["admin"])
    context = client.get(f"{REPORTS}?office=all").context

    rows = context["by_status"]
    for row in rows:
        assert row["bar_percent"] == analytics.bar(row["total"], context["total_records"])
    assert abs(sum(row["bar_percent"] for row in rows) - 100) <= len(rows)


@pytest.mark.django_db
def test_overdue_bars_are_shares_of_every_overdue_document(client, users, agreement):  # noqa: F811
    client.force_login(users["admin"])
    context = client.get(f"{REPORTS}?office=all").context
    panel = context["overdue_accountability"]

    assert panel["whole"] == context["overdue_all"] > 0
    for row in panel["rows"]:
        assert row["awaiting_percent"] == analytics.bar(row["awaiting"], panel["whole"])
        assert row["holding_percent"] == analytics.bar(row["holding"], panel["whole"])
    assert panel["overlaps"] == (sum(row["total"] for row in panel["rows"]) > panel["whole"])


@pytest.mark.django_db
def test_receipts_and_handovers_are_shares_of_every_one(client, users, agreement):  # noqa: F811
    client.force_login(users["admin"])
    context = client.get(f"{REPORTS}?office=all").context

    volume = context["office_volume"]
    assert volume["receipts"] == sum(row["cumulative"] for row in volume["rows"])
    for row in volume["rows"]:
        assert row["cumulative_percent"] == analytics.bar(row["cumulative"], volume["receipts"])

    flow = context["office_flow"]
    assert flow["handovers"] >= sum(row["sent"] for row in flow["rows"])
    assert flow["handovers"] >= sum(row["received"] for row in flow["rows"])
    for row in flow["rows"]:
        assert row["sent_percent"] == analytics.bar(row["sent"], flow["handovers"])
        assert row["received_percent"] == analytics.bar(row["received"], flow["handovers"])


@pytest.mark.django_db
def test_document_types_are_shares_of_every_document(users, offices):
    """Fourteen types and a cap of twelve, so the tail is drawn too."""
    for index in range(14):
        kind = DocumentType.objects.create(code=f"T{index:02d}", name=f"Type {index:02d}")
        for copy in range(index + 1):
            Document.objects.create(
                title=f"{kind.code} {copy}", office=offices["REC"], document_type=kind,
                source="UPLOAD", uploaded_by=users["admin"],
            )
    documents = Document.objects.all()
    rows = ReportsView()._document_types(documents)
    whole = documents.count()

    assert rows[-1]["is_remainder"] and rows[-1]["bar_percent"] > 0
    for row in rows:
        assert row["bar_percent"] == analytics.bar(row["total"], whole)
        assert row["percent"] == analytics.percent(row["total"], whole)


@pytest.mark.django_db
def test_search_bars_are_shares_of_every_query(users):
    for index in range(3):
        for _ in range(index + 1):
            SearchQueryLog.objects.create(user=users["admin"], query=f"query {index}")

    rows = top_searches()

    assert [row["bar_percent"] for row in rows] == [50, 33, 17]
