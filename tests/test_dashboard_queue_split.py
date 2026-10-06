"""The two dashboard previews use existing, permission-scoped Tracking queues."""
import re
from html import unescape

import pytest
from django.contrib.messages import get_messages

from apps.tracking.models import COMPLETED_STATUSES, Status
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
