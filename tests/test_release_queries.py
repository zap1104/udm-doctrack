"""Release audit regressions for filters with no free-text query and returned work."""

import re
from datetime import timedelta
from html import unescape

import pytest
from django.utils import timezone

from apps.documents.models import Document
from apps.tracking.models import RoutingStep, TrackingRecord
from apps.tracking.services import complete_record, confirm_receipt, create_draft_record, route_record


@pytest.fixture
def deadline_records(users, offices, memo_type):
    made = {}
    for name, sender, recipient in (
        ("late", "med", "SUP"),
        ("current", "med", "SUP"),
        ("other_office", "hr", "SUP"),
    ):
        record = create_draft_record(
            user=users[sender], subject=f"Filter only {name}", instructions="For action",
            document_type=memo_type,
        )
        route_record(record, [offices[recipient]], user=users[sender])
        if name != "current":
            TrackingRecord.objects.filter(pk=record.pk).update(due_at=timezone.now() - timedelta(days=1))
        made[name] = record
    return made


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["admin", "med", "med_admin", "viewer"])
@pytest.mark.parametrize("deadline", ["yes", "no"])
def test_deadline_only_search_renders_the_filtered_records(client, users, deadline_records, role, deadline):
    client.force_login(users[role])
    response = client.get("/search/", {"mode": "tracking", "overdue": deadline})
    expected = {deadline_records["late" if deadline == "yes" else "current"].pk}
    if role == "admin" and deadline == "yes":
        expected.add(deadline_records["other_office"].pk)
    assert response.status_code == 200
    assert response.context["has_searched"]
    assert {record.pk for record in response.context["results"]} == expected
    assert "tracking record" in response.content.decode()
    for record in response.context["results"]:
        assert record.tracking_number in response.content.decode()


@pytest.mark.django_db
def test_office_only_tracking_search_renders_the_selected_office(client, users, offices, deadline_records):
    client.force_login(users["admin"])
    response = client.get("/search/", {"mode": "tracking", "office": offices["MED"].pk})
    assert response.context["has_searched"]
    assert {record.pk for record in response.context["results"]} == {
        deadline_records["late"].pk, deadline_records["current"].pk,
    }
    assert deadline_records["late"].tracking_number in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["admin", "med", "med_admin", "viewer"])
def test_an_unfiltered_tracking_search_still_shows_the_search_prompt(client, users, deadline_records, role):
    client.force_login(users[role])
    response = client.get("/search/", {"mode": "tracking"})
    assert not response.context["has_searched"]
    assert "Search tracking records" in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("scope", ["incoming", "pending-receipt", "inbox", "received"])
def test_returned_work_can_be_in_its_creators_receiving_queue(client, users, offices, memo_type, scope):
    record = create_draft_record(
        user=users["med"], subject="Creator receives returned work", instructions="For action",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    route_record(record, [offices["MED"]], user=users["sup"], action=RoutingStep.Action.RETURN)
    if scope == "received":
        confirm_receipt(record, user=users["med"])
    client.force_login(users["med"])
    response = client.get("/tracking/", {"scope": scope, "owner": "mine"})
    assert {row.pk for row in response.context["records"]} == {record.pk}
    assert not response.context["impossible_reason"]


@pytest.mark.django_db
def test_senders_own_work_can_await_the_recipients_receipt(client, users, deadline_records):
    client.force_login(users["med"])
    response = client.get("/tracking/", {"scope": "pending-receipt", "owner": "mine"})
    assert {record.pk for record in response.context["records"]} == {
        deadline_records["late"].pk, deadline_records["current"].pk,
    }
    assert not response.context["impossible_reason"]


@pytest.mark.django_db
@pytest.mark.parametrize("scope", ["incoming", "received", "inbox"])
def test_empty_own_receiving_queues_do_not_claim_returns_are_impossible(client, users, deadline_records, scope):
    client.force_login(users["sup"])
    response = client.get("/tracking/", {"scope": scope, "owner": "mine"})
    assert response.context["total"] == 0
    assert "created by another office" not in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["admin", "sup_admin", "med_admin", "sup"])
def test_dashboard_pending_filing_card_matches_its_destination(client, users, offices, memo_type, role):
    record = create_draft_record(
        user=users["med"], subject="Other office completed our document", instructions="For action",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    complete_record(record, user=users["sup"])
    client.force_login(users[role])
    params = {"office": offices["SUP"].pk} if role == "admin" else {}
    body = client.get("/", params).content.decode()
    card = re.search(r'<a\b[^>]*\bid="dashboard-pending-filing"[^>]*>.*?</a>', body, re.S).group()
    counted = int(re.search(r'class="value">\s*(\d+)', card).group(1))
    destination = unescape(re.search(r'href="([^"]+)"', card).group(1))
    assert counted == client.get(destination).context["total"]


@pytest.mark.django_db
def test_draft_only_dashboard_memo_acknowledges_tracking_work(client, users, memo_type):
    create_draft_record(
        user=users["med"], subject="Draft awaiting preparation", instructions="For action",
        document_type=memo_type,
    )
    client.force_login(users["med"])
    context = client.get("/").context
    overview = context["memo"][0]
    assert {"label": "Total", "value": "1 document"} in overview["lines"]
    assert any("draft" in line["value"].lower() for line in overview["lines"])
    assert "There are no documents" not in str(overview)
    assert context["tracking_total_count"] == 1


@pytest.mark.django_db
def test_overdue_percentage_includes_drafts_in_its_denominator(client, users, offices, memo_type):
    for number in range(2):
        draft = create_draft_record(
            user=users["med"], subject=f"Deadline passed while drafting {number}", instructions="For action",
            document_type=memo_type, due_at=timezone.now() + timedelta(days=1),
        )
        TrackingRecord.objects.filter(pk=draft.pk).update(due_at=timezone.now() - timedelta(days=1))
    circulating = create_draft_record(
        user=users["med"], subject="One document in circulation", instructions="For action", document_type=memo_type,
    )
    route_record(circulating, [offices["SUP"]], user=users["med"])
    client.force_login(users["med"])
    context = client.get("/").context
    assert context["overdue_count"] == 2
    assert context["overdue_summary"]["percent_of_all"] == 67
    overview = context["memo"][0]
    assert {"label": "Total", "value": "3 documents"} in overview["lines"]
    assert {"label": "In Tracking", "value": "3 (100%)"} in overview["lines"]


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["admin", "med", "med_admin", "viewer"])
def test_autocomplete_excludes_inactive_documents(client, users, offices, role):
    Document.objects.create(title="Releaseactive title", office=offices["MED"])
    Document.objects.create(title="Releaseinactive title", office=offices["MED"], is_active=False)
    client.force_login(users[role])
    response = client.get("/search/autocomplete/", {"q": "Release"})
    assert response.status_code == 200
    assert response.json()["results"] == ["Releaseactive title"]


@pytest.mark.django_db
def test_tracking_search_finds_the_current_office_by_its_code(client, users, offices, memo_type):
    record = create_draft_record(
        user=users["med"], subject="Electrical panel inspection", instructions="For action", document_type=memo_type,
    )
    route_record(record, [offices["HR"]], user=users["med"])
    confirm_receipt(record, user=users["hr"])
    client.force_login(users["med"])
    response = client.get("/search/", {"mode": "tracking", "q": "HR"})
    assert {result.pk for result in response.context["results"]} == {record.pk}
    assert record.tracking_number in response.content.decode()


@pytest.mark.django_db
def test_search_renders_valid_stages_when_another_selected_stage_is_invalid(client, users, deadline_records):
    confirm_receipt(deadline_records["late"], user=users["sup"])
    client.force_login(users["med"])
    response = client.get("/search/", {"mode": "tracking", "status": ["RECEIVED", "BOGUS"]})
    assert response.context["has_searched"]
    assert {result.pk for result in response.context["results"]} == {deadline_records["late"].pk}
    assert deadline_records["late"].tracking_number in response.content.decode()
    assert "status" in response.context["form"].errors


@pytest.mark.django_db
def test_all_office_overdue_ring_includes_drafts_and_matches_its_list(client, users, memo_type):
    draft = create_draft_record(
        user=users["med"], subject="Overdue preparation", instructions="For action", document_type=memo_type,
    )
    TrackingRecord.objects.filter(pk=draft.pk).update(due_at=timezone.now() - timedelta(days=1))
    client.force_login(users["admin"])
    response = client.get("/", {"office": "all", "ring": "overdue"})
    context = response.context
    assert context["overdue_count"] == 1
    assert context["tracking_rings"]["overdue_total"] == context["overdue_count"]
    overdue = context["tracking_rings"]["rings"][0]["overdue"]
    draft_slice = next(row for row in overdue["slices"] if row["key"] == "draft")
    assert draft_slice["total"] == 1
    destination = client.get(draft_slice["url"])
    assert {record.pk for record in destination.context["records"]} == {draft.pk}
    assert "needs action in Tracking" in response.content.decode()
