"""Receipt and work stages follow the selected office on every tracking view."""

import html
import re
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from django.urls import reverse

from apps.core.colors import STATUS_PILLS
from apps.tracking import services
from apps.tracking.models import RecordActivity, Status


@pytest.fixture
def shared_work(users, offices, memo_type):
    record = services.create_draft_record(
        user=users["sup"], subject="Shared office work", instructions="Review",
        document_type=memo_type,
    )
    services.route_record(record, [offices["MED"], offices["HR"]], user=users["sup"])
    services.confirm_receipt(record, user=users["hr"])
    services.add_remark(record, user=users["hr"], remark="HR has started its review.")
    return record


@pytest.mark.django_db
@pytest.mark.parametrize("workspace", ["tracking", "search"])
@pytest.mark.parametrize("actor,office,stage", [
    ("med", None, Status.PENDING_RECEIPT),
    ("med_admin", None, Status.PENDING_RECEIPT),
    ("viewer", None, Status.PENDING_RECEIPT),
    ("hr", None, Status.IN_PROCESS),
    ("admin", "MED", Status.PENDING_RECEIPT),
    ("admin", "HR", Status.IN_PROCESS),
    ("admin", "all", Status.PENDING_RECEIPT),
])
def test_workspace_stage_filter_and_row_follow_office(
    client, users, shared_work, workspace, actor, office, stage,
):
    client.force_login(users[actor])
    params = {"status": stage, "per_page": "all"}
    if office:
        params["office"] = office
    if workspace == "search":
        params["mode"] = "tracking"
    response = client.get(reverse("tracking:list" if workspace == "tracking" else "search:index"), params)
    assert response.status_code == 200
    records = response.context["records" if workspace == "tracking" else "results"]
    assert [record.pk for record in records] == [shared_work.pk]
    assert records[0].display_status == stage
    assert records[0].get_display_status_display() == stage.label
    assert f'class="pill {STATUS_PILLS[stage]}"' in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("workspace", ["tracking", "search"])
def test_receiving_med_does_not_inherit_hr_work(client, users, shared_work, workspace):
    services.confirm_receipt(shared_work, user=users["med"])
    client.force_login(users["med"])
    params = {"scope": "incoming", "status": Status.RECEIVED}
    if workspace == "search":
        params["mode"] = "tracking"
    path = reverse("tracking:list" if workspace == "tracking" else "search:index")
    response = client.get(path, params)
    records = response.context["records" if workspace == "tracking" else "results"]
    assert [record.pk for record in records] == [shared_work.pk]
    assert records[0].display_status == Status.RECEIVED
    params["status"] = Status.IN_PROCESS
    response = client.get(path, params)
    assert not response.context["records" if workspace == "tracking" else "results"]


@pytest.mark.django_db
@pytest.mark.parametrize("view", ["detail", "routing_slip"])
@pytest.mark.parametrize("actor,office,stage", [
    ("med", None, Status.PENDING_RECEIPT),
    ("med_admin", "HR", Status.PENDING_RECEIPT),
    ("viewer", "HR", Status.PENDING_RECEIPT),
    ("hr", None, Status.IN_PROCESS),
    ("admin", "MED", Status.PENDING_RECEIPT),
    ("admin", "HR", Status.IN_PROCESS),
    ("admin", "all", Status.PENDING_RECEIPT),
])
def test_detail_and_print_use_authorized_stage_perspective(
    client, users, shared_work, view, actor, office, stage,
):
    client.force_login(users[actor])
    response = client.get(reverse(f"tracking:{view}", args=[shared_work.pk]), {"office": office} if office else {})
    assert response.status_code == 200
    assert response.context["record"].display_status == stage
    assert f'class="pill {STATUS_PILLS[stage]}"' in response.content.decode()
    if view == "detail" and actor == "med":
        assert response.context["can_confirm"] is True
        assert response.context["can_act"] is False


@pytest.mark.django_db
def test_detail_and_print_keep_the_selected_office_linked(client, users, shared_work):
    client.force_login(users["admin"])
    response = client.get(reverse("tracking:detail", args=[shared_work.pk]), {"office": "HR"})
    slip_url = reverse("tracking:routing_slip", args=[shared_work.pk])
    assert f'href="{slip_url}?office=' in response.content.decode()
    response = client.get(reverse("tracking:routing_slip", args=[shared_work.pk]), {"office": "HR"})
    detail_url = reverse("tracking:detail", args=[shared_work.pk])
    assert f'href="{detail_url}?office=' in response.content.decode()


@pytest.mark.django_db
def test_default_stage_perspective_does_not_remove_shared_access(client, users, offices):
    record = services.create_draft_record(user=users["sup"], subject="Read by access grant", instructions="Review")
    services.route_record(record, [offices["HR"]], user=users["sup"])
    services.grant_access(record, user=users["sup"], office=offices["MED"], reason="For reference")
    client.force_login(users["med"])
    response = client.get(reverse("tracking:list"))
    assert record.pk in {row.pk for row in response.context["records"]}


@pytest.mark.django_db
@pytest.mark.parametrize("office", ["HR", "all"])
@pytest.mark.parametrize("state,actions", [
    ("active", ["confirm_receipt", "add_remark", "route", "complete", "grant_access"]),
    ("pending_filing", ["archive", "reopen"]),
])
def test_detail_forms_keep_the_selected_office(
    client, users, offices, shared_work, office, state, actions,
):
    # This administrator can confirm MED's receipt while viewing HR's stage.
    # The selected office describes the screen; it does not replace the actor.
    users["admin"].office = offices["MED"]
    users["admin"].save(update_fields=["office"])
    if state == "pending_filing":
        services.complete_record(shared_work, user=users["hr"])
    client.force_login(users["admin"])

    response = client.get(reverse("tracking:detail", args=[shared_work.pk]), {"office": office})
    assert response.status_code == 200
    form_actions = {
        urlsplit(url).path: url
        for raw in re.findall(r'<form\b[^>]*\baction="([^"]+)"', response.content.decode())
        for url in [html.unescape(raw)]
    }
    expected_office = "all" if office == "all" else str(offices["HR"].pk)
    for action in actions:
        path = reverse(f"tracking:{action}", args=[shared_work.pk])
        assert path in form_actions, f"The {action} form must be available in this state"
        assert parse_qs(urlsplit(form_actions[path]).query).get("office") == [expected_office]


@pytest.mark.django_db
@pytest.mark.parametrize("office,stage", [
    ("HR", Status.IN_PROCESS),
    ("all", Status.PENDING_RECEIPT),
])
@pytest.mark.parametrize("remark", ["Administrator checked the received copy.", ""])
def test_remark_return_keeps_the_selected_office_on_success_or_validation_error(
    client, users, offices, shared_work, office, stage, remark,
):
    client.force_login(users["admin"])
    detail_url = reverse("tracking:detail", args=[shared_work.pk])
    before = client.get(detail_url, {"office": office})
    assert before.context["record"].display_status == stage
    remarks_before = shared_work.activities.filter(event=RecordActivity.Event.REMARK).count()

    action_url = reverse("tracking:add_remark", args=[shared_work.pk])
    response = client.post(action_url + "?" + urlencode({"office": office}), {"remark": remark}, follow=True)

    assert response.status_code == 200
    assert response.redirect_chain
    destination = urlsplit(response.redirect_chain[-1][0])
    assert destination.path == detail_url
    expected_office = "all" if office == "all" else str(offices["HR"].pk)
    assert parse_qs(destination.query).get("office") == [expected_office]
    assert response.context["record"].display_status == stage
    assert shared_work.activities.filter(event=RecordActivity.Event.REMARK).count() == (
        remarks_before + bool(remark)
    )
    if not remark:
        assert response.context["remark_form"].is_bound and response.context["remark_form"].errors
    if remark:
        assert shared_work.activities.filter(
            event=RecordActivity.Event.REMARK, actor=users["admin"], detail=remark,
        ).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("actor", ["med", "med_admin", "viewer"])
@pytest.mark.parametrize("action,payload", [
    ("add_remark", {"remark": "Attempt to act as HR"}),
    ("route", {"action": "FORWARD", "offices": "REC", "instructions": "Forward as HR"}),
    ("complete", {"note": "Attempt to complete as HR"}),
    ("grant_access", {"office": "REC", "reason": "Attempt to share as HR"}),
])
def test_selected_office_does_not_authorize_an_unreceived_office_to_act(
    client, users, offices, shared_work, actor, action, payload,
):
    client.force_login(users[actor])
    payload = dict(payload)
    if action == "route":
        payload["offices"] = str(offices["REC"].pk)
    elif action == "grant_access":
        payload["office"] = str(offices["REC"].pk)
    activities_before = shared_work.activities.count()
    steps_before = shared_work.routing_steps.count()
    grants_before = shared_work.grants.count()
    original_status = shared_work.status
    action_url = reverse(f"tracking:{action}", args=[shared_work.pk])

    response = client.post(action_url + "?office=HR", payload)

    assert response.status_code == 403
    shared_work.refresh_from_db()
    assert shared_work.status == original_status
    assert shared_work.activities.count() == activities_before
    assert shared_work.routing_steps.count() == steps_before
    assert shared_work.grants.count() == grants_before
