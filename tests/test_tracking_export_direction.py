"""A Tracking CSV keeps the table's office perspective and record population."""

import csv
import html
import io
import re

import pytest
from django.urls import reverse

from tests.test_filter_agreement import traffic as traffic

SUP_INCOMING = {"arriving": "Incoming", "received": "Incoming", "working": "Incoming"}
SUP_DIRECTIONS = {**SUP_INCOMING, "sent": "Outgoing"}
ALL_DIRECTIONS = {key: "—" for key in ("arriving", "received", "working", "sent", "elsewhere")}


@pytest.mark.django_db
@pytest.mark.parametrize(("actor", "params", "perspective", "filter_office", "expected"), [
    ("sup", {}, "SUP", "All visible offices", SUP_DIRECTIONS),
    ("sup", {"scope": "incoming"}, "SUP", "All visible offices", SUP_INCOMING),
    ("sup", {"office": "HR", "scope": "incoming"}, "SUP", "All visible offices", SUP_INCOMING),
    ("sup_admin", {"scope": "incoming"}, "SUP", "SUP", SUP_INCOMING),
    ("sup_admin", {"office": "HR", "scope": "incoming"}, "SUP", "SUP", SUP_INCOMING),
    ("viewer", {"scope": "outgoing"}, "MED", "All visible offices", {
        "arriving": "Outgoing", "received": "Outgoing", "elsewhere": "Outgoing",
    }),
    ("admin", {"office": "SUP"}, "SUP", "SUP", SUP_DIRECTIONS),
    ("admin", {"office": "all"}, None, "All visible offices", ALL_DIRECTIONS),
    ("admin", {}, None, "All visible offices", ALL_DIRECTIONS),
])
def test_tracking_csv_keeps_effective_direction_office(
    client, users, offices, traffic, actor, params, perspective, filter_office, expected,
):
    # Give the system administrator an office involved in the traffic so the
    # all-offices cases detect an incorrect fallback to their assigned office.
    if actor == "admin":
        users[actor].office = offices["MED"]
        users[actor].save(update_fields=["office"])
    client.force_login(users[actor])
    response = client.get(reverse("tracking:list"), {**params, "per_page": "all"})
    assert response.status_code == 200
    expected_ids = {traffic[key].pk for key in expected}
    assert {record.pk for record in response.context["records"]} == expected_ids
    direction_labels = {"incoming": "Incoming", "outgoing": "Outgoing", "": "—"}
    assert {
        record.pk: direction_labels[record.direction] for record in response.context["records"]
    } == {traffic[key].pk: label for key, label in expected.items()}

    link = re.search(r'href="([^"]+)"[^>]*>Export CSV</a>', response.content.decode())
    assert link is not None
    exported = client.get(html.unescape(link.group(1)))
    assert exported.status_code == 200
    rows = list(csv.reader(io.StringIO(exported.content.decode())))
    header_index = next(index for index, row in enumerate(rows) if row and row[0] == "Tracking number")
    records = [dict(zip(rows[header_index], row, strict=True)) for row in rows[header_index + 1:]]
    ids_by_number = {record.tracking_number: record.pk for record in traffic.values()}
    assert {ids_by_number[row["Tracking number"]] for row in records} == expected_ids
    assert len(records) == len(expected_ids)
    assert {ids_by_number[row["Tracking number"]]: row["Direction"] for row in records} == {
        traffic[key].pk: label for key, label in expected.items()
    }
    metadata = {row[0]: row[1:] for row in rows[:header_index] if row}
    assert metadata["Direction measured from"] == [
        offices[perspective].name if perspective else "no office — Direction not available"
    ]
    # Direction is relative to a desk; the visible population is not an
    # own-office filter unless the resolved filters actually impose one.
    assert f"office={filter_office}" in metadata["Record filters"]
