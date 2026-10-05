"""Counts must describe the exact rows opened by the number's link."""

import csv
import io
from datetime import timedelta
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import pytest
from django.utils import timezone

from apps.core.analytics import overdue_offices, overdue_summary
from apps.core.models import Tag
from apps.documents.models import HISTORICAL_FILTER, Document
from apps.search.services import search_documents
from apps.tracking.models import Status, TrackingRecord
from apps.tracking.services import confirm_receipt, create_draft_record, mark_in_process, route_record
from tests.test_search import make_document


@pytest.fixture
def partly_received(users, offices, memo_type):
    result = []
    for index in range(3):
        record = create_draft_record(user=users["med"], subject=f"Count audit {index}", instructions="x", document_type=memo_type)
        route_record(record, [offices["SUP"], offices["HR"]], user=users["med"])
        if index:
            confirm_receipt(record, user=users["sup"])
        if index == 2:
            mark_in_process(record, user=users["sup"])
        TrackingRecord.objects.filter(pk=record.pk).update(due_at=timezone.now() - timedelta(days=1))
        result.append(record)
    return result


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["admin", "med_admin", "sup", "viewer"])
def test_pending_receipt_counts_the_stage_instead_of_partial_confirmations(client, users, partly_received, role):
    client.force_login(users[role])
    response = client.get("/tracking/reports/")
    context = response.context
    assert context["pending_receipt"] == 1
    assert context["partial_receipts"] == 2
    assert context["awaiting_receipt"] == 3
    listed = client.get(context["report_pending_receipt_url"]).context
    assert listed["total"] == context["pending_receipt"]
    assert {row.pk for row in listed["page_obj"]} == {partly_received[0].pk}
    assert client.get("/tracking/?status=PENDING_RECEIPT").context["total"] == 1
    assert "still await another recipient" in response.content.decode()


@pytest.mark.django_db
def test_office_overdue_headline_opens_all_overdue_records_in_its_scope(client, users, offices, partly_received):
    import re

    client.force_login(users["admin"])
    url = f"/tracking/reports/?office={offices['MED'].pk}"
    response = client.get(url)
    context = response.context
    # MED sent these on: its own next-action count is zero, while its report
    # still contains three overdue documents that require other offices.
    assert context["overdue"] == 0
    assert context["overdue_all"] == 3
    match = re.search(r'class="stat-card red" href="([^"]+)"', response.content.decode())
    target = urljoin(url, unescape(match[1]))
    listed = client.get(target).context
    assert listed["total"] == 3
    assert {row.pk for row in listed["page_obj"]} == {row.pk for row in partly_received}


@pytest.mark.django_db
@pytest.mark.parametrize("stage", [Status.PENDING_RECEIPT, Status.RECEIVED, Status.IN_PROCESS])
def test_export_uses_the_report_record_filters(client, users, partly_received, stage):
    client.force_login(users["admin"])
    response = client.get("/tracking/reports/export/", {
        "record_status": stage, "record_q": "Count audit", "record_overdue": "yes",
    })
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    header = next(index for index, row in enumerate(rows) if row and row[0] == "Tracking number")
    actual = rows[header + 1:]
    expected = {row.tracking_number for row in partly_received if row.status == stage}
    assert len(actual) == len(expected) == 1
    assert {row[0] for row in actual} == expected


def replace_filter(url, **changes):
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query))
    params.update(changes)
    params.pop("page", None)
    return parts.path + "?" + urlencode(params)


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["admin", "med_admin", "med", "viewer"])
def test_repository_badges_match_their_filtered_destinations(client, users, offices, memo_type, role):
    today = timezone.localdate()
    tag = Tag.objects.create(name="count-audit")
    for title, source in (("Match one", "DTS"), ("Match two", "UPLOAD"), ("Unrelated", "UPLOAD")):
        document = Document.objects.create(title=title, source=source, office=offices["MED"], document_type=memo_type, uploaded_by=users["med"], retention_until=today - timedelta(days=1))
        document.tags.add(tag)
    client.force_login(users[role])
    url = f"/documents/?q=Match&source={HISTORICAL_FILTER}"
    context = client.get(url).context
    assert context["total"] == 1
    assert context["filed_count"] == 3
    assert client.get(context["filed_url"]).context["total"] == 3
    for folder in context["type_folders"]:
        assert folder["total"] == client.get(folder["url"]).context["total"] == 1
    for counted_tag in context["popular_tags"]:
        assert counted_tag.visible_usage == client.get(replace_filter(url, tag=counted_tag.pk)).context["total"] == 1
    assert context["retention_due_count"] == client.get(replace_filter(url, retention="due")).context["total"] == 1
    for folder in context["smart_folders"]:
        assert folder["total"] == client.get(folder["url"]).context["total"] == 3


@pytest.mark.django_db
def test_pending_upload_type_badges_keep_search_filters(client, users, offices, memo_type):
    for title in ("Match upload", "Other upload"):
        record = create_draft_record(user=users["med"], subject=title, instructions="x", document_type=memo_type)
        TrackingRecord.objects.filter(pk=record.pk).update(status=Status.COMPLETED_PENDING_UPLOAD, completed_at=timezone.now())
    client.force_login(users["admin"])
    context = client.get("/documents/?view=pending&q=Match").context
    assert context["total"] == 1
    assert context["pending_count"] == 2
    assert client.get(context["pending_url"]).context["total"] == 2
    for folder in context["type_folders"]:
        assert folder["total"] == client.get(folder["url"]).context["total"] == 1


@pytest.mark.django_db
def test_search_totals_survive_browse_and_candidate_limits(users, offices):
    for index in range(7):
        make_document(offices["MED"], f"Metric audit policy {index}")
    browse = search_documents(user=users["admin"], limit=2, log=False)
    assert browse.total_matches == 7
    assert len(browse.results) == 2
    query = search_documents(user=users["admin"], query="Metric audit policy", limit=2, min_relevance=0, log=False)
    assert query.total_matches == 7
    assert query.evaluated_count == 4
    assert query.truncated
    assert len(query.results) == 2
    assert query.hidden_count == 0


@pytest.mark.django_db
@pytest.mark.parametrize("limit", [0, 1, 2])
def test_office_count_includes_each_office_in_the_remainder(users, offices, memo_type, limit):
    for name in ("med", "sup", "hr"):
        record = create_draft_record(user=users[name], subject="Late audit", instructions="x", document_type=memo_type)
        TrackingRecord.objects.filter(pk=record.pk).update(status=Status.IN_PROCESS, due_at=timezone.now() - timedelta(days=1))
    records = TrackingRecord.objects.visible_to(users["admin"])
    rows = overdue_offices(records, limit=limit)
    assert overdue_summary(records, rows, records.count())["office_count"] == 3


@pytest.mark.django_db
def test_a_retired_type_still_opens_its_existing_records(client, users, offices, memo_type):
    document = Document.objects.create(title="Retired type", office=offices["MED"], document_type=memo_type)
    memo_type.is_active = False
    memo_type.save(update_fields=["is_active"])
    client.force_login(users["admin"])
    context = client.get("/documents/").context
    folder = next(row for row in context["type_folders"] if row["document_type_id"] == memo_type.pk)
    listed = client.get(folder["url"]).context
    assert listed["total"] == folder["total"] == 1
    assert {row.pk for row in listed["documents"]} == {document.pk}


@pytest.mark.django_db
def test_tag_usage_is_live_and_permission_scoped(client, users, offices):
    tag = Tag.objects.create(name="shared tag", usage_count=999)
    for office, active in (("MED", True), ("HR", True), ("MED", False)):
        document = Document.objects.create(title="Tagged", office=offices[office], is_active=active)
        document.tags.add(tag)
    client.force_login(users["med_admin"])
    assert client.get("/administration/tags/").status_code == 403
    context = client.get("/documents/").context
    assert next(row for row in context["popular_tags"] if row.pk == tag.pk).visible_usage == 1
    client.force_login(users["admin"])
    context = client.get("/administration/tags/").context
    assert next(row for row in context["objects"] if row.pk == tag.pk).visible_usage == 2


@pytest.mark.django_db
def test_denied_folder_tabs_remain_empty_instead_of_resetting_to_every_record(client, users, offices):
    Document.objects.create(title="Allowed", office=offices["MED"])
    Document.objects.create(title="Hidden", office=offices["HR"])
    client.force_login(users["med"])
    context = client.get(f"/documents/?office={offices['HR'].pk}").context
    assert context["folder_unavailable"]
    assert context["selected_office"] is None
    assert context["total"] == 0
    assert client.get(context["filed_url"]).context["total"] == context["filed_count"] == 0
    assert client.get(context["pending_url"]).context["total"] == context["pending_count"] == 0
