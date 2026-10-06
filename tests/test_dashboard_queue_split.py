"""The two dashboard previews use existing, permission-scoped Tracking queues."""
import re
from html import unescape

import pytest
from django.contrib.messages import get_messages

from apps.tracking.models import COMPLETED_STATUSES, Status, TrackingRecord
from apps.tracking.services import complete_record, confirm_receipt, create_draft_record, route_record
from tests.test_filter_agreement import page_records, traffic  # noqa: F401


@pytest.fixture
def completed_traffic(traffic, users):  # noqa: F811
    complete_record(traffic["received"], user=users["sup"])
    confirm_receipt(traffic["elsewhere"], user=users["hr"])
    complete_record(traffic["elsewhere"], user=users["hr"])
    return traffic


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["admin", "med_admin", "sup", "hr", "viewer"])
def test_split_previews_match_visible_full_lists(client, users, offices, completed_traffic, role):
    client.force_login(users[role])
    path = f"/?office={offices['SUP'].pk}" if role == "admin" else "/"
    response = client.get(path)
    pending = response.context["pending_filing"]
    listed = page_records(client, pending["tracking_url"])
    assert pending["count"] == len(listed)
    assert {r.pk for r in pending["records"]} <= listed
    assert len(pending["records"]) == min(5, len(listed))
    assert all(r.status == Status.COMPLETED_PENDING_UPLOAD for r in pending["records"])
    for queue in response.context["desk_queues"]:
        assert queue["slug"] != "pending-upload"
        assert queue["count"] == len(page_records(client, queue["tracking_url"]))
        selected = client.get(queue["href"])
        assert all(r.status not in COMPLETED_STATUSES for r in selected.context["attention_records"])
    assert {r.pk for r in response.context["attention_records"]}.isdisjoint(listed)


@pytest.mark.django_db
def test_pending_preview_cannot_be_widened_by_office_user(client, users, offices, completed_traffic):
    client.force_login(users["sup"])
    own = client.get("/").context["pending_filing"]
    crafted = client.get(f"/?office={offices['HR'].pk}").context["pending_filing"]
    assert {r.pk for r in own["records"]} == {completed_traffic["received"].pk}
    assert {r.pk for r in crafted["records"]} == {r.pk for r in own["records"]}
    assert crafted["count"] == own["count"]


@pytest.mark.django_db
def test_all_offices_and_selected_office_pending_counts_agree(client, users, offices, completed_traffic):
    client.force_login(users["admin"])
    for office in ["all", offices["MED"].pk, offices["SUP"].pk, offices["HR"].pk]:
        pending = client.get(f"/?office={office}").context["pending_filing"]
        assert pending["count"] == len(page_records(client, pending["tracking_url"]))


@pytest.mark.django_db
def test_pending_card_includes_received_work_and_matches_its_destination(client, users, offices, memo_type):
    expected = set()
    for index in range(11):
        creator, receiver = (users["hr"], users["sup"]) if index < 3 else (
            (users["med"], users["hr"]) if index < 10 else (users["sup"], users["med"])
        )
        record = create_draft_record(user=creator, subject=f"Filing cohort {index}",
                                     instructions="For review", document_type=memo_type)
        route_record(record, [receiver.office], user=creator)
        confirm_receipt(record, user=receiver)
        complete_record(record, user=receiver)
        if index < 10:
            expected.add(record.pk)
    client.force_login(users["admin"])
    response = client.get("/", {"office": offices["HR"].pk})
    assert response.context["tracking_pending_filing_count"] == 10
    card = re.search(r'<a\b[^>]*\bid="dashboard-pending-filing"[^>]*>.*?</a>', response.content.decode(), re.S).group()
    value = re.search(r'<div class="value">(\d+)</div>', card).group(1)
    assert int(value) == 10
    href = unescape(re.search(r'href="([^"]+)"', card).group(1))
    assert page_records(client, href) == expected
    assert "Created by HR" not in card


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("role", "office"),
    [("admin", "all"), ("admin", "MED"), ("admin", "SUP"), ("admin", "HR"),
     ("med_admin", None), ("sup", None), ("hr", None), ("viewer", None), ("sup", "HR")],
)
def test_workload_breakdown_matches_tracking_and_pending_lists(
    client, users, offices, memo_type, completed_traffic, role, office,
):
    create_draft_record(user=users["med"], subject="Work still being prepared",
                        instructions="For review", document_type=memo_type)
    client.force_login(users[role])
    params = {"office": "all" if office == "all" else offices[office].pk} if office else {}
    response = client.get("/", params)
    context = response.context
    listed = page_records(client, context["tracking_total_url"])
    pending = page_records(client, context["pending_filing"]["tracking_url"])
    current = set(TrackingRecord.objects.filter(pk__in=listed).exclude(
        status=Status.COMPLETED_PENDING_UPLOAD,
    ).values_list("pk", flat=True))
    assert current.isdisjoint(pending)
    assert current | pending == listed
    assert context["tracking_current_count"] == len(current)
    assert context["tracking_pending_filing_count"] == len(pending)
    filing_card = re.search(r'<a\b[^>]*\bid="dashboard-pending-filing"[^>]*>.*?</a>', response.content.decode(), re.S).group()
    filing_value = re.search(r'<div class="value">(\d+)</div>', filing_card).group(1)
    filing_url = unescape(re.search(r'href="([^"]+)"', filing_card).group(1))
    assert int(filing_value) == len(pending)
    assert page_records(client, filing_url) == pending
    assert context["tracking_total_count"] == len(listed) == len(current) + len(pending)
    body = response.content.decode()
    card = re.search(r'<a\b[^>]*\bdata-tracking-total[^>]*>.*?</a>', body, re.S).group()
    text = re.sub(r"<[^>]+>", "", card)
    assert f"{len(current)} currently tracking + {len(pending)} pending filing = {len(listed)} documents" in text


@pytest.mark.django_db
@pytest.mark.parametrize("stage", ["empty", "draft", "pending", "filed"])
def test_workload_breakdown_handles_empty_drafts_and_completed_work(client, users, offices, memo_type, stage):
    if stage != "empty":
        record = create_draft_record(user=users["med"], subject="Workload lifecycle",
                                     instructions="For review", document_type=memo_type)
        if stage in {"pending", "filed"}:
            route_record(record, [offices["SUP"]], user=users["med"])
            confirm_receipt(record, user=users["sup"])
            complete_record(record, user=users["sup"])
        if stage == "filed":
            TrackingRecord.objects.filter(pk=record.pk).update(status=Status.COMPLETED)
    client.force_login(users["admin"])
    context = client.get("/", {"office": "all"}).context
    assert context["tracking_current_count"] == (1 if stage == "draft" else 0)
    assert context["tracking_pending_filing_count"] == (1 if stage == "pending" else 0)
    assert context["tracking_total_count"] == (1 if stage in {"draft", "pending"} else 0)


@pytest.mark.django_db
def test_old_pending_dashboard_link_keeps_both_panels_usable(client, users, completed_traffic):
    client.force_login(users["sup"])
    response = client.get("/?desk=pending-upload")
    assert response.context["desk_queue"]["slug"] == "pending-receipt"
    assert response.context["pending_filing"]["count"] == 1
    assert not list(get_messages(response.wsgi_request))
    assert 'id="dashboard-filing-title"' in response.content.decode()


@pytest.mark.django_db
def test_pending_panel_is_outside_receipt_form_and_only_opens_records(client, users, completed_traffic):
    client.force_login(users["sup"])
    body = client.get("/").content.decode()
    match = re.search(r'<aside[^>]*aria-labelledby="dashboard-filing-title"[^>]*>(.*?)</aside>', body, re.S)
    assert match
    panel = unescape(match.group(1))
    assert completed_traffic["received"].get_absolute_url() in panel
    assert "Open record" in panel
    assert "record_ids" not in panel and "<form" not in panel
    before = body[:match.start()]
    assert before.rfind("</form>") > before.rfind('<form method="post"')


@pytest.mark.django_db
def test_pending_preview_has_clear_empty_state(client, users, traffic):  # noqa: F811
    client.force_login(users["viewer"])
    response = client.get("/")
    assert response.context["pending_filing"]["count"] == 0
    assert "No completed records are waiting for filing." in response.content.decode()


@pytest.mark.django_db
def test_pending_preview_stops_at_five_and_keeps_full_count(client, users, offices, memo_type):
    made = []
    for index in range(6):
        record = create_draft_record(user=users["med"], subject=f"Completed inspection {index}",
                                     instructions="For review", document_type=memo_type)
        route_record(record, [offices["SUP"]], user=users["med"])
        confirm_receipt(record, user=users["sup"])
        complete_record(record, user=users["sup"])
        made.append(record.pk)
    client.force_login(users["sup"])
    pending = client.get("/").context["pending_filing"]
    assert pending["count"] == 6
    assert [r.pk for r in pending["records"]] == list(reversed(made[1:]))
    assert page_records(client, pending["tracking_url"]) == set(made)
