"""Every document counted once, across Tracking and the Repository.

The dashboard's two rings split everything in scope six ways: four live
tracking stages (Pending Receipt, Received, In Process, Completed - Pending
Upload) and the repository's two origins (completed through tracking,
historical). They must add up to the total the page states, and every segment
must open a page listing exactly its count — with an office picked as well as
across every office, and with a scanned document in the repository, which the
historical segment used to leave out of the page it opened.
"""

from __future__ import annotations

import pytest

from apps.documents.models import Document, Source
from tests.test_dashboard_reports_agreement import agreement  # noqa: F401 — fixture, used by name
from tests.test_filter_agreement import traffic  # noqa: F401 — fixture, used by name

TRACKING = ("pending_receipt", "received", "in_process", "pending_upload")
REPOSITORY = ("historical", "completed")


@pytest.fixture
def scanned(users, offices, memo_type):
    return Document.objects.create(
        title="Scanned letter", office=offices["SUP"], document_type=memo_type,
        source=Source.SCAN, uploaded_by=users["admin"],
    )


def _breakdown(client, office):
    return client.get(f"/?office={office}").context["breakdown"]


@pytest.mark.django_db
@pytest.mark.parametrize("scope", ["all", "SUP"])
def test_the_six_segments_add_up_to_the_total(client, users, offices, agreement, scanned, scope):  # noqa: F811
    client.force_login(users["admin"])
    office = "all" if scope == "all" else offices["SUP"].pk
    breakdown = _breakdown(client, office)
    totals = {row["key"]: row["total"] for row in breakdown["slices"]}

    assert set(totals) == set(TRACKING) | set(REPOSITORY)
    assert sum(totals.values()) == breakdown["total"]
    assert sum(totals[key] for key in TRACKING) == breakdown["tracking_total"]
    assert sum(totals[key] for key in REPOSITORY) == breakdown["repository_total"]


@pytest.mark.django_db
@pytest.mark.parametrize("scope", ["all", "SUP"])
def test_every_segment_opens_a_page_listing_its_count(client, users, offices, agreement, scanned, scope):  # noqa: F811
    client.force_login(users["admin"])
    office = "all" if scope == "all" else offices["SUP"].pk
    for row in _breakdown(client, office)["slices"]:
        listed = client.get(row["url"]).context["total"]
        assert listed == row["total"], (scope, row["key"], row["url"])


@pytest.mark.django_db
def test_a_scanned_document_is_historical_on_the_ring_and_on_the_page(client, users, offices, scanned):
    client.force_login(users["admin"])
    historical = next(
        row for row in _breakdown(client, offices["SUP"].pk)["slices"] if row["key"] == "historical"
    )
    page = client.get(historical["url"]).context

    assert historical["total"] == 1
    assert [document.title for document in page["documents"]] == ["Scanned letter"]


@pytest.mark.django_db
def test_an_office_user_sees_the_same_reconciliation(client, users, offices, agreement, scanned):  # noqa: F811
    client.force_login(users["sup"])
    breakdown = client.get("/").context["breakdown"]

    assert sum(row["total"] for row in breakdown["slices"]) == breakdown["total"]
    for row in breakdown["slices"]:
        assert client.get(row["url"]).context["total"] == row["total"], row["key"]
