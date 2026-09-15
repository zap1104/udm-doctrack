"""A grouped count is one row per group, whatever queryset it is handed.

`TrackingRecord` orders by `-last_movement_at, -created_at` and `Document` by
`-document_date, -created_at`. Django leaves `Meta.ordering` out of a GROUP BY —
until the queryset is `.distinct()`, when the ordering columns have to be
selected and so land in the GROUP BY too. The dashboard and Reports both hand
their panels `.distinct()` querysets, so `values("office").annotate(Count)`
came back as one row per *record*, each counting 1, and a dict comprehension
over those rows kept the last: every office read 1.

`overdue_accountability`'s holding column broke on a plain queryset as well, for
a neighbouring reason: an `Exists` annotated before `.values()` is grouped by
the columns it references from outside, which is the record's primary key.

Every function here is asked the same question twice, once per queryset shape,
with three records or documents behind each answer — one would hide the bug.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import analytics
from apps.core.views import ReportsView
from apps.documents.models import Document, Source
from apps.tracking.models import Status, TrackingRecord
from apps.tracking.services import (
    by_status_direction,
    confirm_receipt,
    create_draft_record,
    direction_totals,
    overdue_accountability,
    route_record,
)

SHAPES = {
    "plain": lambda queryset: queryset,
    "distinct": lambda queryset: queryset.distinct(),
}


@pytest.fixture
def three_held_by_sup(users, offices, memo_type):
    """Three overdue records, each received and held by SUP."""
    for n in range(3):
        record = create_draft_record(
            user=users["med"], subject=f"Late {n}", instructions="x",
            document_type=memo_type,
        )
        route_record(record, [offices["SUP"]], user=users["med"])
        confirm_receipt(record, user=users["sup"])
        TrackingRecord.objects.filter(pk=record.pk).update(
            due_at=timezone.now() - timedelta(days=2)
        )


@pytest.fixture
def three_uploads_by_med(users, offices, memo_type):
    for n in range(3):
        Document.objects.create(
            title=f"Upload {n}", office=offices["MED"], document_type=memo_type,
            year=timezone.localdate().year, source=Source.UPLOAD,
            uploaded_by=users["med"],
        )


@pytest.mark.django_db
@pytest.mark.parametrize("shape", SHAPES)
def test_uploads_are_counted_per_office(three_uploads_by_med, users, offices, shape):
    uploads = analytics.uploads_by_office(
        SHAPES[shape](Document.objects.visible_to(users["admin"])),
        SHAPES[shape](TrackingRecord.objects.visible_to(users["admin"])),
    )

    row = next(r for r in uploads["rows"] if r["code"] == offices["MED"].code)
    assert row["uploaded"] == 3
    assert uploads["total"] == 3


@pytest.mark.django_db
@pytest.mark.parametrize("shape", SHAPES)
def test_statuses_are_counted_without_a_point_of_view(three_held_by_sup, users, shape):
    """The branch a system administrator viewing every office takes."""
    rows = by_status_direction(
        SHAPES[shape](TrackingRecord.objects.visible_to(users["admin"])), None
    )

    assert {row["status"]: row["total"] for row in rows} == {Status.RECEIVED: 3}


@pytest.mark.django_db
@pytest.mark.parametrize("shape", SHAPES)
def test_statuses_are_counted_from_an_office(three_held_by_sup, users, offices, shape):
    rows = by_status_direction(
        SHAPES[shape](TrackingRecord.objects.visible_to(users["admin"])), offices["SUP"]
    )

    assert [(row["status"], row["total"], row["incoming"]) for row in rows] == [
        (Status.RECEIVED, 3, 3)
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("shape", SHAPES)
def test_directions_are_counted_per_direction(three_held_by_sup, users, offices, shape):
    counts = direction_totals(
        SHAPES[shape](TrackingRecord.objects.visible_to(users["admin"])), offices["SUP"]
    )

    assert counts == {"incoming": 3, "outgoing": 0, "other": 0}


@pytest.mark.django_db
@pytest.mark.parametrize("shape", SHAPES)
def test_holding_is_counted_per_office(three_held_by_sup, users, offices, shape):
    rows = overdue_accountability(
        SHAPES[shape](TrackingRecord.objects.visible_to(users["admin"]))
    )

    assert [(row["code"], row["awaiting"], row["holding"]) for row in rows] == [
        (offices["SUP"].code, 0, 3)
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("shape", SHAPES)
def test_extraction_states_are_one_row_each(three_uploads_by_med, users, shape):
    """The totals were right before, by summing per-row groups; the query is
    asserted too, because a grouped count returning a row per document is a
    full scan dressed as an aggregate."""
    documents = SHAPES[shape](Document.objects.visible_to(users["admin"]))

    with CaptureQueriesContext(connection) as queries:
        state = ReportsView()._extraction_state(documents)

    assert state["total"] == 3
    assert sum(state["by_status"].values()) == 3
    group_by = queries.captured_queries[-1]["sql"].split("GROUP BY", 1)[1]
    assert "created_at" not in group_by, group_by


# --- what a reader of each page actually saw ---------------------------------
@pytest.mark.django_db
def test_reports_counts_every_status_for_every_office(client, three_held_by_sup, users):
    client.force_login(users["admin"])

    rows = client.get("/reports/").context["by_status"]

    assert [(row["status"], row["total"]) for row in rows] == [(Status.RECEIVED, 3)]


@pytest.mark.django_db
def test_reports_charges_the_holder_for_every_record_it_holds(
    client, three_held_by_sup, users, offices
):
    client.force_login(users["admin"])

    rows = client.get("/reports/").context["overdue_accountability"]["rows"]

    assert [(row["code"], row["holding"]) for row in rows] == [(offices["SUP"].code, 3)]


@pytest.mark.django_db
def test_the_dashboard_credits_every_upload(client, three_uploads_by_med, users, offices):
    client.force_login(users["admin"])

    uploads = client.get("/").context["uploads_by_office"]

    assert [(row["code"], row["uploaded"]) for row in uploads["rows"]] == [
        (offices["MED"].code, 3)
    ]
    assert uploads["total"] == 3
