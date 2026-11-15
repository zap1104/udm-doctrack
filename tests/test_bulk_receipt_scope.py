"""Bulk receipt returns to the filtered queue the reader submitted."""

from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
from django.urls import reverse

from apps.tracking import services
from tests.test_count_filter_reconciliation import controls

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("workspace", ["dashboard", "tracking"])
@pytest.mark.parametrize("attest", [False, True])
def test_bulk_receipt_preserves_the_submitting_queue(client, users, offices, workspace, attest):
    admin = users["admin"]
    admin.office = offices["SUP"]
    admin.save(update_fields=["office"])
    client.force_verified_login(admin)

    def send(sender, destination, subject):
        record = services.create_draft_record(user=sender, subject=subject, instructions="Review")
        services.route_record(record, [destination], user=sender)
        return record

    pending = send(users["med"], offices["SUP"], "Receipt & papers")
    send(users["med"], offices["HR"], "Receipt & papers in another office")
    filters = [("office", str(offices["SUP"].pk))]
    if workspace == "dashboard":
        page = reverse("core:dashboard")
        filters.extend([("desk", "incoming-pending-receipt"), ("ring", "overdue")])
    else:
        page = reverse("tracking:list")
        filters.extend([
            ("scope", "incoming"), ("status", "PENDING_RECEIPT"), ("status", "RECEIVED"),
            ("offices", str(offices["MED"].pk)), ("offices", str(offices["HR"].pk)),
            ("q", "Receipt & papers"), ("per_page", "10"),
        ])
    response = client.get(f"{page}?{urlencode(filters)}")
    action, inputs = next(
        (attrs["action"], values) for attrs, values in controls(response).forms
        if urlsplit(attrs.get("action", "")).path == reverse("tracking:bulk_confirm_receipt")
    )
    assert ("record_ids", str(pending.pk)) in inputs
    submitted = {key: value for key, value in inputs if key in {"csrfmiddlewaretoken", "next"}}
    submitted["record_ids"] = [pending.pk]
    if attest:
        submitted["confirm_custody"] = "on"

    result = client.post(action, submitted)

    assert result.status_code == 302
    returned = urlsplit(result["Location"])
    assert returned.path == page
    assert sorted(parse_qsl(returned.query)) == sorted(filters)
    returned_page = client.get(result["Location"])
    assert returned_page.status_code == 200
    if workspace == "dashboard":
        assert returned_page.context["scope"]["office"] == offices["SUP"]
        expected = set() if attest else {pending.pk}
        assert {row.pk for row in returned_page.context["attention_records"]} == expected
    else:
        assert returned_page.context["resolved"].as_office == offices["SUP"]
        assert {row.pk for row in returned_page.context["records"]} == {pending.pk}
    assert (pending.routing_steps.get().received_at is not None) == attest


def test_bulk_receipt_rejects_arbitrary_return_destinations(client, users):
    client.force_verified_login(users["sup"])
    host = "https://example.invalid/"
    action = reverse("tracking:bulk_confirm_receipt")
    result = client.post(f"{action}?{urlencode({'office': 'HR', 'next': host})}", {"next": host})

    assert result.status_code == 302
    assert result["Location"] == reverse("tracking:list") + "?scope=incoming"
