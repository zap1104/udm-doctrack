"""The dashboard and Reports have to agree with each other.

`test_filter_agreement.py` proves each page agrees with the pages its own cards
open. Nothing compared the dashboard to Reports, and that absence is why the
dashboard's office filter matched two relationships while Reports matched four
for fifty commits, under a comment on the dashboard claiming they were "the same
pairing Reports filters on".

The rule this file enforces: where both pages ask the same question they must
give the same answer, and where they ask different questions — overdue by custody
on the dashboard memo, overdue by accountability on Reports — each says so on
screen, which is tested in `test_reports_and_dashboard.py`.

Two of the assertions proposed for this file were false by construction, and are
written here as the relations that actually hold:

- The awaiting-receipt headline is not `ours + theirs`. A document MED raised,
  which SUP forwarded to PRC and PRC has not signed for, is in MED's report and is
  awaiting receipt, but the receipt is owed between two *other* offices. The
  headline is `ours + theirs + between others`, and that is asserted exactly.
- Whole-number shares cannot be promised to sum to 100: three equal offices are
  33 + 33 + 33 = 99. The counts sum exactly; the shares sum to within rounding.

Its first run found two faults nobody had listed, both fixed on this branch: the
awaiting headline under SUP exceeded ours + theirs + between others, because an
`.exclude()` across routing steps dropped what SUP had signed for and passed on;
and the upload rows did not sum to the uploads counted over every office,
because a `.distinct()` queryset grouped per document. See
`test_overdue_accountability.py` and `test_grouped_counts.py`.
"""

from __future__ import annotations

import pathlib
import re
from datetime import timedelta

import pytest
from django.db import connection
from django.db.models import F
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import Office
from apps.core.views import DashboardView, apply_report_filters
from apps.documents.models import Document, OcrStatus
from apps.tracking.models import COMPLETED_STATUSES, RoutingStep, TrackingRecord
from apps.tracking.services import (
    awaiting_receipt,
    confirm_receipt,
    create_draft_record,
    route_record,
)
from tests.test_filter_agreement import traffic  # noqa: F401 — fixture, used by name

DASHBOARD = "/"
REPORTS = "/tracking/reports/"


@pytest.fixture
def agreement(users, offices, memo_type, traffic):  # noqa: F811
    """`traffic` plus the shapes that separate the definitions this file tests.

    Each exists because a fault turned on it:
      forwarded   passed through SUP to PRC, unreceived — visible to the
                  four-relationship office filter, invisible to the two
      stale       an unconfirmed step in an earlier batch, current batch signed
                  for — the awaiting-receipt headline counted it forever
      between     raised by MED, pending between SUP and PRC — why the headline
                  is not simply ours + theirs
      overdue     so the overdue figures are never compared at zero
      a draft     the completion-rate denominator
      13 offices  uploads, so the top-N cap of 12 actually truncates
      6 states    one document per text-extraction status
    """
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    prc = Office.objects.create(code="PRC", name="Procurement", cluster="OVPA")
    prc_user = user_model.objects.create_user(
        username="prc", password="TestPass123!", office=prc, role="USER"
    )
    made = {"PRC": prc}

    def raised(subject, by="med"):
        return create_draft_record(
            user=users[by], subject=subject, instructions="x", document_type=memo_type,
        )

    forwarded = raised("Forwarded through SUP to PRC")
    route_record(forwarded, [offices["SUP"]], user=users["med"])
    confirm_receipt(forwarded, user=users["sup"])
    route_record(forwarded, [prc], user=users["sup"], action="FORWARD")
    made["forwarded"] = forwarded

    stale = raised("Earlier hop never confirmed")
    route_record(stale, [offices["SUP"], offices["HR"]], user=users["med"])
    confirm_receipt(stale, user=users["sup"])
    route_record(stale, [prc], user=users["sup"], action="FORWARD")
    confirm_receipt(stale, user=prc_user)
    made["stale"] = stale

    late = raised("Overdue in SUP")
    route_record(late, [offices["SUP"]], user=users["med"])
    TrackingRecord.objects.filter(pk=late.pk).update(due_at=timezone.now() - timedelta(days=4))
    made["late"] = late

    made["draft"] = raised("Never sent")

    extra = [
        Office.objects.create(code=f"X{index:02d}", name=f"Extra office {index:02d}", cluster="OVPA")
        for index in range(13)
    ]
    for index, office in enumerate(extra):
        for copy in range(index + 1):
            Document.objects.create(
                title=f"{office.code} upload {copy}", office=office, document_type=memo_type,
                source="UPLOAD", uploaded_by=users["admin"],
            )
    for status in OcrStatus:
        Document.objects.create(
            title=f"extraction {status.value}", office=offices["REC"], document_type=memo_type,
            source="UPLOAD", uploaded_by=users["admin"], ocr_status=status.value,
        )
    return made


#: Offices to compare the two pages under, plus "every office". Codes, resolved
#: to primary keys inside each test because the fixture creates PRC.
SCOPES = ["MED", "SUP", "PRC", "HR", "all"]


def _office_param(offices, agreement, code):
    if code == "all":
        return "all"
    return str((agreement["PRC"] if code == "PRC" else offices[code]).pk)


def _both(client, param):
    dashboard = client.get(f"{DASHBOARD}?office={param}")
    reports = client.get(f"{REPORTS}?office={param}")
    assert dashboard.status_code == 200
    assert reports.status_code == 200
    return dashboard.context, reports.context


def _dashboard_records(user, office):
    """The dashboard's own scoped set, through its own method."""
    records, _documents = DashboardView()._scoped(user, office)
    return records


def _report_records(user, filters):
    return apply_report_filters(TrackingRecord.objects.visible_to(user), filters).distinct()


# --- 1 and 2: the same office means the same records --------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("code", SCOPES)
def test_both_pages_resolve_the_same_office(client, users, offices, agreement, code):
    client.force_login(users["admin"])
    dashboard, reports = _both(client, _office_param(offices, agreement, code))

    assert dashboard["scope"]["office"] == reports["filters"]["office"]
    assert dashboard["scope"]["all_offices"] == reports["filters"]["all_offices"]


@pytest.mark.django_db
@pytest.mark.parametrize("code", SCOPES)
def test_the_overdue_totals_agree(client, users, offices, agreement, code):
    client.force_login(users["admin"])
    dashboard, reports = _both(client, _office_param(offices, agreement, code))

    assert dashboard["overdue_summary"]["total"] == reports["overdue_all"]


@pytest.mark.django_db
@pytest.mark.parametrize("code", SCOPES)
def test_the_two_pages_count_the_same_records_not_merely_as_many(
    client, users, offices, agreement, code
):
    """Sets, not counts. Two definitions can land on the same number over
    different records, which is exactly how the drift stayed invisible."""
    client.force_login(users["admin"])
    dashboard, reports = _both(client, _office_param(offices, agreement, code))

    dashboard_pks = set(
        _dashboard_records(users["admin"], dashboard["scope"]["office"]).values_list("pk", flat=True)
    )
    report_pks = set(
        _report_records(users["admin"], reports["filters"]).values_list("pk", flat=True)
    )
    assert dashboard_pks == report_pks


@pytest.mark.django_db
def test_an_office_sees_what_it_forwarded_and_what_was_forwarded_to_it(
    client, users, offices, agreement
):
    """The case the two-relationship filter missed, on both pages at once."""
    client.force_login(users["admin"])
    for code in ("SUP", "PRC"):
        dashboard, reports = _both(client, _office_param(offices, agreement, code))
        for records in (
            _dashboard_records(users["admin"], dashboard["scope"]["office"]),
            _report_records(users["admin"], reports["filters"]),
        ):
            assert agreement["forwarded"] in records, code


# --- 3 and 4: awaiting receipt ------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("code", ["MED", "SUP", "PRC", "HR"])
def test_the_awaiting_headline_is_ours_theirs_and_between_others(
    client, users, offices, agreement, code
):
    """Not `ours + theirs`: a document this office is involved in can be waiting
    on a receipt owed between two *other* offices. That third part is counted
    independently here, so the headline has to equal the three exactly."""
    client.force_login(users["admin"])
    _dashboard, reports = _both(client, _office_param(offices, agreement, code))
    office = reports["filters"]["office"]
    records = _report_records(users["admin"], reports["filters"])

    current_unreceived = RoutingStep.objects.filter(
        record__in=records, received_at__isnull=True, batch=F("record__current_batch"),
    ).exclude(record__status__in=COMPLETED_STATUSES)
    involved = set(
        current_unreceived.filter(to_office=office).values_list("record_id", flat=True)
    ) | set(
        current_unreceived.filter(from_office=office).values_list("record_id", flat=True)
    )
    between_others = set(current_unreceived.values_list("record_id", flat=True)) - involved

    split = reports["awaiting_split"]
    assert reports["awaiting_receipt"] == split["ours"] + split["theirs"] + len(between_others)


@pytest.mark.django_db
@pytest.mark.parametrize("code", SCOPES)
def test_the_awaiting_headline_is_the_one_definition(client, users, offices, agreement, code):
    client.force_login(users["admin"])
    _dashboard, reports = _both(client, _office_param(offices, agreement, code))
    records = _report_records(users["admin"], reports["filters"])

    assert reports["awaiting_receipt"] == awaiting_receipt(records, users["admin"]).distinct().count()


@pytest.mark.django_db
def test_a_superseded_unconfirmed_hop_is_named_not_counted(client, users, agreement):
    """HR never signed for the first batch, and the record has moved on and been
    received since. It is no longer awaiting receipt, and it is not dropped
    silently either."""
    client.force_login(users["admin"])
    reports = client.get(f"{REPORTS}?office=all").context

    assert agreement["stale"] not in awaiting_receipt(
        TrackingRecord.objects.filter(pk=agreement["stale"].pk), users["admin"]
    )
    assert reports["stale_receipts"] >= 1


# --- 5 to 9: every capped list and every partition sums ----------------------
@pytest.mark.django_db
@pytest.mark.parametrize("code", SCOPES)
def test_overdue_office_rows_sum_to_the_overdue_total(client, users, offices, agreement, code):
    client.force_login(users["admin"])
    dashboard, _reports = _both(client, _office_param(offices, agreement, code))

    rows = dashboard["overdue_offices"]
    assert sum(row["total"] for row in rows) == dashboard["overdue_summary"]["total"]


@pytest.mark.django_db
def test_upload_rows_sum_to_a_total_counted_over_every_office(client, users, agreement):
    """Thirteen offices uploaded and the cap is twelve, so this truncates."""
    client.force_login(users["admin"])
    uploads = client.get(f"{DASHBOARD}?office=all").context["uploads_by_office"]

    assert any(row.get("is_remainder") for row in uploads["rows"]), "the cap truncated"
    assert sum(row["total"] for row in uploads["rows"]) == uploads["total"]

    since = timezone.localdate().replace(day=1)
    added = Document.objects.visible_to(users["admin"]).filter(created_at__date__gte=since).count()
    filed = TrackingRecord.objects.visible_to(users["admin"]).filter(
        status__in=COMPLETED_STATUSES, completed_at__date__gte=since
    ).count()
    assert uploads["total"] == added + filed, "not the sum of the rows that survived the cap"


@pytest.mark.django_db
def test_upload_shares_sum_to_a_hundred_within_rounding(client, users, agreement):
    """Whole percentages cannot promise exactly 100 — 33 + 33 + 33 is 99 — so
    the bound is the rounding: at most half a point per row."""
    client.force_login(users["admin"])
    rows = client.get(f"{DASHBOARD}?office=all").context["uploads_by_office"]["rows"]

    assert abs(sum(row["percent"] for row in rows) - 100) <= len(rows) / 2


@pytest.mark.django_db
@pytest.mark.parametrize("code", SCOPES)
def test_document_type_rows_sum_to_the_repository(client, users, offices, agreement, code):
    client.force_login(users["admin"])
    _dashboard, reports = _both(client, _office_param(offices, agreement, code))

    assert sum(row["total"] for row in reports["document_types"]) == reports["total_documents"]


@pytest.mark.django_db
@pytest.mark.parametrize("code", SCOPES)
def test_extraction_states_sum_to_the_repository(client, users, offices, agreement, code):
    client.force_login(users["admin"])
    _dashboard, reports = _both(client, _office_param(offices, agreement, code))

    assert sum(reports["extraction"]["by_status"].values()) == reports["total_documents"]


# --- 10: nothing is computed that nothing reads -------------------------------
#: Every key the dashboard view puts in context. Named, so that adding one is a
#: decision rather than a side effect: a key that is not on this list fails, and
#: a key on this list that no template reads fails below unless it is one of the
#: memo aggregates. This is what would have caught the three today counters and
#: `live_by_status`, each computed on every load for a panel that no longer
#: existed.
DASHBOARD_CONTEXT = {
    "attention_records", "breakdown", "can_bulk_receive", "can_start_work", "desk_clear_href",
    "desk_queue", "desk_queues", "desk_target", "greeting",
    "incoming_count", "incoming_new_today", "memo", "month_picker", "monthly", "outgoing_count",
    "overdue_count", "overdue_offices", "overdue_summary", "printed_at",
    "recent_records", "repository_donut", "scope",
    "show_office_columns", "tracking_rings", "turnaround", "turnaround_trend",
    "turnaround_trend_geometry", "turnaround_trend_points", "uploads_by_office", "view",
}

#: Read in Python rather than by a template. `get_memo_context` computes them once
#: and shares them on purpose — the memo builder reads both, and `overdue_count`
#: reads the summary's total — so they are not dead, and removing them from
#: context would mean computing them twice.
MEMO_AGGREGATES = {"overdue_offices", "overdue_summary"}


@pytest.mark.django_db
def test_the_dashboard_context_is_exactly_the_named_keys(client, users, agreement):
    client.force_login(users["admin"])
    response = client.get(DASHBOARD)

    assert set(response.context_data) == DASHBOARD_CONTEXT


@pytest.mark.django_db
def test_every_dashboard_context_key_is_read(client, users, agreement):
    client.force_login(users["admin"])
    response = client.get(DASHBOARD)
    # str(): a template name can arrive as a SafeString, and Python 3.11's
    # pathlib interns path parts, which refuses a str subclass.
    names = [str(template.name) for template in response.templates if template.name]
    source = "".join(
        pathlib.Path("templates", name).read_text(encoding="utf-8")
        for name in names
        if pathlib.Path("templates", name).exists()
    )

    unread = {
        key for key in response.context_data
        if not re.search(r"\b" + re.escape(key) + r"\b", source)
    }
    assert unread <= MEMO_AGGREGATES, unread - MEMO_AGGREGATES


# --- 11: the recent panels stay inside the picked office ----------------------
@pytest.mark.django_db
@pytest.mark.parametrize("code", ["MED", "SUP", "PRC", "HR"])
def test_the_recent_panels_list_only_the_picked_office(client, users, offices, agreement, code):
    client.force_login(users["admin"])
    dashboard, _reports = _both(client, _office_param(offices, agreement, code))
    office = dashboard["scope"]["office"]

    scoped = set(_dashboard_records(users["admin"], office).values_list("pk", flat=True))
    assert {record.pk for record in dashboard["recent_records"]} <= scoped


# --- 12: an office with nothing ----------------------------------------------
@pytest.mark.django_db
def test_an_office_with_no_records_renders_both_pages_at_zero(client, users, db):
    """Every rate here divides by something; an empty office must not find out
    which one raises."""
    empty = Office.objects.create(code="NIL", name="Empty office", cluster="OVPA")
    client.force_login(users["admin"])
    dashboard, reports = _both(client, str(empty.pk))

    assert dashboard["overdue_summary"]["total"] == 0
    assert reports["total_records"] == 0
    assert reports["total_documents"] == 0
    assert reports["overdue_all"] == 0
    assert reports["awaiting_receipt"] == 0
    assert reports["completion_rate"] == 0
    assert sum(reports["extraction"]["by_status"].values()) == 0


# --- 13: the query count is pinned --------------------------------------------
#: Measured against the `agreement` fixture after every fix in this branch, as a
#: system administrator viewing every office. A change that adds a panel query
#: moves these, and the failure says by how much. Raise them deliberately, with
#: the reason in the commit.
#: 48 on feature/chart-legibility. The Status | Overdue switch added one: under
#: every office the single tracking ring counts its stages and their overdue
#: documents in a grouped query of its own, where it used to read stage counts
#: off the breakdown, which had no overdue counts. Disabling the Incoming and
#: Outgoing cards under every office took three away: their two counts and
#: "moved today", for a direction that does not exist there.
#: 49: holidays. Each turnaround calculation reads the holiday table once,
#: and the dashboard makes two, the monthly trend and the memo's averages.
#: 43: turnaround became one service over two queries of intervals, where it
#: was eight — an aggregate and a value list per stage, and two deadline counts.
#: 49: the Action Centre's chips. Five queue counts (Pending Receipt, Received,
#: In Process, Completed - Pending Upload, Overdue; Incoming and Outgoing read
#: the rings' counts, and are disabled under every office anyway), and the
#: office badges looked up for the queue's rows and for Recently moved
#: separately, since the queue is now also rendered on its own.
#: 47: "Newest in the Document Repository" left the dashboard, and its
#: document list and the office badges it drew went with it.
DASHBOARD_QUERIES = 47
#: 51: the repository section gained its three retention counts (due, due in
#: 90 days, never scheduled), each one query, for every reader.
#: 51: holidays, one read for the page's one turnaround calculation.
#: 45: the same six, on the same service.
REPORTS_QUERIES = 45


@pytest.mark.django_db
def test_the_dashboard_query_count_is_pinned(
    client, users, agreement, django_assert_num_queries
):
    client.force_login(users["admin"])
    client.get(f"{REPORTS}?office=all")  # session and permission caches warm

    with django_assert_num_queries(DASHBOARD_QUERIES):
        client.get(f"{DASHBOARD}?office=all")


@pytest.mark.django_db
def test_the_reports_query_count_is_pinned(
    client, users, agreement, django_assert_num_queries
):
    client.force_login(users["admin"])
    client.get(f"{DASHBOARD}?office=all")

    with django_assert_num_queries(REPORTS_QUERIES):
        client.get(f"{REPORTS}?office=all")


#: All four fell by one on the turnaround fixes: the average receipt time and the
#: number of handovers it is taken over now come from one query, where the count
#: was a query of its own.
#: The same two pages with an office picked, which is where the dashboard draws
#: two direction rings instead of one. Pinned separately because the pin under
#: every office cannot see them: there, the rings do not exist. Both views of
#: the rings are one pass, so asking for the overdue view costs nothing extra.
#: Both up with holidays, by the same reads as the two pins above.
DASHBOARD_OFFICE_QUERIES = 49
#: 52: with an office picked, the two office rankings are no longer computed
#: (their rows would have been built from that office's documents only) and one
#: grouped query gives that office's own handover figures instead; the three
#: retention counts are added.
REPORTS_OFFICE_QUERIES = 46


@pytest.mark.django_db
@pytest.mark.parametrize("ring", ["", "&ring=overdue"])
def test_the_dashboard_query_count_is_pinned_for_an_office(
    client, users, offices, agreement, django_assert_num_queries, ring
):
    client.force_login(users["admin"])
    pk = offices["SUP"].pk
    client.get(f"{REPORTS}?office={pk}")

    with django_assert_num_queries(DASHBOARD_OFFICE_QUERIES):
        client.get(f"{DASHBOARD}?office={pk}{ring}")


@pytest.mark.django_db
def test_the_reports_query_count_is_pinned_for_an_office(
    client, users, offices, agreement, django_assert_num_queries
):
    client.force_login(users["admin"])
    pk = offices["SUP"].pk
    client.get(f"{DASHBOARD}?office={pk}")

    with django_assert_num_queries(REPORTS_OFFICE_QUERIES):
        client.get(f"{REPORTS}?office={pk}")


@pytest.mark.django_db
@pytest.mark.parametrize("page", [DASHBOARD, REPORTS])
@pytest.mark.parametrize("scope", ["all", "SUP"])
def test_neither_page_runs_a_query_per_record(
    client, users, offices, memo_type, agreement, page, scope
):
    """What the pinned numbers stand in for. A pin moves for a new panel too;
    this only moves for a loop — fifteen more routed records, same count."""
    office = "all" if scope == "all" else offices[scope].pk
    url = f"{page}?office={office}"
    client.force_login(users["admin"])
    client.get(url)

    def queries():
        with CaptureQueriesContext(connection) as captured:
            client.get(url)
        return len(captured)

    before = queries()
    for n in range(15):
        record = create_draft_record(
            user=users["med"], subject=f"More traffic {n}", instructions="x",
            document_type=memo_type,
        )
        route_record(record, [offices["SUP"], offices["HR"]], user=users["med"])

    assert queries() == before
