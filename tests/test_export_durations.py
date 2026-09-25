"""Office-hours durations in the Reports export.

Plain numbers of office hours, so a spreadsheet can sort and add them, by the
same definitions as the figures on the page, and blank where a stage has not
happened rather than a zero somebody would average in.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.tracking.models import RoutingStep, TrackingRecord
from apps.tracking.services import complete_record, confirm_receipt, create_draft_record, route_record

EXPORT = "/reports/export/?office=all"
COLUMNS = ("Waiting for receipt (office hrs)", "In process (office hrs)", "Lifetime (office hrs)")
MONDAY, TUESDAY = date(2026, 8, 24), date(2026, 8, 25)


def _at(day, hour):
    return timezone.make_aware(datetime.combine(day, time(hour)))


def _sheet(client):
    rows = list(csv.reader(io.StringIO(client.get(EXPORT).content.decode())))
    header_at = next(i for i, row in enumerate(rows) if row and row[0] == "Tracking number")
    header = rows[header_at]
    return rows[:header_at], [dict(zip(header, row, strict=True)) for row in rows[header_at + 1:]]


def _record(users, offices, memo_type, subject, *, finish):
    record = create_draft_record(
        user=users["med"], subject=subject, instructions="x", document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    if finish:
        confirm_receipt(record, user=users["sup"])
        record.refresh_from_db()
        complete_record(record, user=users["sup"])
        RoutingStep.objects.filter(record=record).update(sent_at=_at(MONDAY, 9), received_at=_at(MONDAY, 11))
        TrackingRecord.objects.filter(pk=record.pk).update(
            created_at=_at(MONDAY, 8), first_received_at=_at(MONDAY, 11), completed_at=_at(TUESDAY, 17),
        )
    return record


@pytest.mark.django_db
def test_a_finished_record_exports_its_three_stages_in_office_hours(client, users, offices, memo_type):
    _record(users, offices, memo_type, "Finished", finish=True)
    client.force_login(users["admin"])
    preamble, rows = _sheet(client)
    row = next(r for r in rows if r["Subject"] == "Finished")

    assert [row[column] for column in COLUMNS] == ["2.00", "13.00", "16.00"]
    basis = next(line for line in preamble if line and line[0] == "Durations")
    assert "8 hours = 1 working day" in basis[1]


@pytest.mark.django_db
def test_a_stage_not_reached_is_blank_not_zero(client, users, offices, memo_type):
    _record(users, offices, memo_type, "Still waiting", finish=False)
    client.force_login(users["admin"])
    _preamble, rows = _sheet(client)
    row = next(r for r in rows if r["Subject"] == "Still waiting")

    assert [row[column] for column in COLUMNS] == ["", "", ""]


@pytest.mark.django_db
def test_the_durations_cost_the_same_queries_however_many_rows(client, users, offices, memo_type):
    client.force_login(users["admin"])
    _record(users, offices, memo_type, "One", finish=True)
    client.get(EXPORT)  # session and caches warm
    with CaptureQueriesContext(connection) as one:
        client.get(EXPORT)
    for index in range(4):
        _record(users, offices, memo_type, f"More {index}", finish=True)
    with CaptureQueriesContext(connection) as five:
        client.get(EXPORT)

    assert len(five) == len(one)
