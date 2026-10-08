"""The Tracking table and its CSV must contain the same filtered records."""

from __future__ import annotations

import csv
import html
import io
import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.tracking import services
from apps.tracking.models import RoutingStep, TrackingRecord


@pytest.fixture
def export_records(users, offices, memo_type):
    records = {}

    def draft(name, user="med"):
        record = services.create_draft_record(
            user=users[user], subject=name, instructions="For action", document_type=memo_type,
        )
        records[name] = record
        return record

    def sent(name, recipients=("SUP",), user="med"):
        record = draft(name, user)
        services.route_record(record, [offices[code] for code in recipients], user=users[user])
        return record

    draft("draft-med")
    draft("draft-sup", "sup")
    sent("pending")
    partial = sent("partial", ("SUP", "HR"))
    services.confirm_receipt(partial, user=users["sup"])
    received = sent("received")
    services.confirm_receipt(received, user=users["sup"])
    processing = sent("processing")
    services.confirm_receipt(processing, user=users["sup"])
    services.add_remark(processing, user=users["sup"], remark="Work started")
    passed = sent("passed")
    services.confirm_receipt(passed, user=users["sup"])
    services.route_record(passed, [offices["HR"]], user=users["sup"], action=RoutingStep.Action.FORWARD)
    pending_filing = sent("pending-filing")
    services.confirm_receipt(pending_filing, user=users["sup"])
    services.complete_record(pending_filing, user=users["sup"])
    filed = sent("filed")
    services.confirm_receipt(filed, user=users["sup"])
    services.complete_record(filed, user=users["sup"])
    services.approve_upload(filed, user=users["admin"])
    sent("foreign", ("HR",), user="sup")
    TrackingRecord.objects.filter(pk__in=[records["pending"].pk, passed.pk]).update(
        due_at=timezone.now() - timedelta(days=1),
    )
    return records


def csv_records(response):
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    header = next(index for index, row in enumerate(rows) if row and row[0] == "Tracking number")
    return rows, rows[header + 1:]


ALL_ACTIVE = {
    "draft-med", "draft-sup", "pending", "partial", "received", "processing", "passed", "pending-filing", "foreign",
}
MED_VISIBLE = ALL_ACTIVE - {"draft-sup", "foreign"}


@pytest.mark.django_db
@pytest.mark.parametrize(("actor", "params", "expected"), [
    ("admin", {}, ALL_ACTIVE),
    ("med", {}, MED_VISIBLE),
    ("viewer", {}, MED_VISIBLE - {"draft-med"}),
    ("med_admin", {"office": "SUP"}, MED_VISIBLE - {"draft-med"}),
    ("admin", {"office": "SUP", "scope": "incoming"}, {"pending", "partial", "received", "processing"}),
    ("admin", {"office": "MED", "scope": "outgoing"}, {"pending", "partial", "received", "processing"}),
    ("admin", {"office": "SUP", "scope": "awaiting"}, {"pending", "passed", "foreign"}),
    ("admin", {"office": "SUP", "scope": "received"}, {"partial", "received"}),
    ("admin", {"office": "SUP", "scope": "in-process"}, {"processing"}),
    ("admin", {"office": "all", "scope": "incoming"}, ALL_ACTIVE),
    ("med", {"office": "HR"}, MED_VISIBLE),
    ("admin", {"status": ["PENDING_RECEIPT", "RECEIVED"]}, {"pending", "partial", "received", "passed", "foreign"}),
    ("admin", {"status": "DRAFT"}, {"draft-med", "draft-sup"}),
    ("admin", {"scope": "pending-upload"}, {"pending-filing"}),
    ("admin", {"offices": ["SUP"]}, {"draft-sup", "foreign"}),
    ("med", {"owner": "mine", "overdue": "yes"}, {"pending", "passed"}),
    ("med", {"owner": "custody"}, {"draft-med", "pending"}),
    ("admin", {"status": "RECEIVED", "overdue": "no", "office": "SUP"}, {"partial", "received"}),
])
def test_tracking_csv_link_exports_exact_filtered_population(
    client, users, offices, export_records, actor, params, expected,
):
    client.force_login(users[actor])
    params = {
        key: [str(offices.get(value, value).pk) if value in offices else value for value in values]
        if isinstance(values, list) else str(offices[values].pk) if values in offices else values
        for key, values in params.items()
    }
    response = client.get(reverse("tracking:list"), {**params, "per_page": "all"})
    assert response.status_code == 200
    assert {record.subject for record in response.context["records"]} == expected
    link = re.search(r'href="([^"]+)"[^>]*>Export CSV</a>', response.content.decode())
    assert link is not None
    rows, exported = csv_records(client.get(html.unescape(link.group(1))))
    subjects = [row[1] for row in exported]
    assert set(subjects) == expected
    assert len(subjects) == len(expected), "Multi-recipient routing must not duplicate exported records"
    assert next(row for row in rows if row and row[0] == "Exported rows")[1] == str(len(expected))


@pytest.mark.django_db
def test_tracking_export_includes_every_page(client, users, offices):
    for n in range(31):
        services.create_draft_record(user=users["med"], subject=f"Draft {n}", instructions="Work")
    client.force_login(users["med"])
    response = client.get(reverse("tracking:list"), {"per_page": "10", "page": "2", "owner": "mine"})
    assert len(response.context["records"]) == 10
    link = re.search(r'href="([^"]+)"[^>]*>Export CSV</a>', response.content.decode())
    assert link is not None
    assert "source=tracking" in html.unescape(link.group(1))
    _rows, exported = csv_records(client.get(html.unescape(link.group(1))))
    assert {row[1] for row in exported} == {f"Draft {n}" for n in range(31)}


@pytest.mark.django_db
def test_legacy_export_keeps_filed_history_and_legacy_filters(client, users, export_records):
    client.force_login(users["admin"])
    _rows, exported = csv_records(client.get(reverse("core:report_export"), {"record_status": "COMPLETED"}))
    assert [row[1] for row in exported] == ["filed"]


@pytest.mark.django_db
def test_native_tracking_export_keeps_csv_formula_escaping(client, users, offices):
    services.create_draft_record(user=users["med"], subject="=1+1", instructions="Work")
    client.force_login(users["med"])
    _rows, exported = csv_records(client.get(reverse("core:report_export"), {"source": "tracking"}))
    assert [row[1] for row in exported] == ["'=1+1"]


@pytest.mark.django_db
def test_native_export_metadata_describes_applied_filters(client, users, export_records):
    client.force_login(users["med"])
    rows, exported = csv_records(client.get(reverse("core:report_export"), {
        "source": "tracking", "status": "BOGUS", "scope": "BOGUS", "owner": "BOGUS",
        "overdue": "BOGUS", "=1+1": "unsafe",
    }))
    metadata = next(row for row in rows if row and row[0] == "Record filters")
    assert metadata == ["Record filters", "office=All visible offices"]
    assert {row[1] for row in exported} == MED_VISIBLE


@pytest.mark.django_db
@pytest.mark.parametrize("sort", ["", "deadline", "deadline-desc"])
@pytest.mark.parametrize("path,key", [("/tracking/", "records"), ("/search/?mode=tracking", "results")])
def test_tracking_pages_partition_records_with_tied_sort_values(client, users, sort, path, key):
    records = [
        services.create_draft_record(user=users["med"], subject=f"Tied {n}", instructions="Work")
        for n in range(31)
    ]
    TrackingRecord.objects.filter(pk__in=[record.pk for record in records]).update(
        due_at=timezone.now() + timedelta(days=1), last_movement_at=timezone.now(),
    )
    client.force_login(users["med"])
    seen = []
    for page in range(1, 5):
        response = client.get(path.split("?")[0], {"mode": "tracking", "per_page": "10", "page": str(page), "sort": sort})
        assert response.status_code == 200
        seen.extend(record.pk for record in response.context[key])
    assert seen == sorted((record.pk for record in records), reverse=True)
    assert len(set(seen)) == 31
