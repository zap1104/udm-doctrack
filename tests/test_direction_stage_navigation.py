"""Direction and stage links must describe the exact same records everywhere."""

from urllib.parse import parse_qs, urlsplit

import pytest

from apps.tracking import services
from apps.tracking.models import RoutingStep, Status
from tests.test_filter_agreement import traffic  # noqa: F401


@pytest.fixture
def direction_traffic(users, offices, traffic):  # noqa: F811
    made = dict(traffic)
    for key, stage in (("sent_received", Status.RECEIVED), ("sent_working", Status.IN_PROCESS)):
        record = services.create_draft_record(user=users["sup"], subject=key, instructions="For action")
        services.route_record(record, [offices["HR"]], user=users["sup"])
        services.confirm_receipt(record, user=users["hr"])
        if stage == Status.IN_PROCESS:
            services.mark_in_process(record, user=users["hr"])
        made[key] = record
    partial = services.create_draft_record(user=users["med"], subject="Partial receipt", instructions="For action")
    services.route_record(partial, [offices["SUP"], offices["HR"]], user=users["med"])
    services.confirm_receipt(partial, user=users["hr"])
    made["partial"] = partial
    return made


def listed(client, url):
    response = client.get(url)
    assert response.status_code == 200
    return {record.pk for record in response.context["page_obj"].object_list}


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["sup", "sup_admin", "admin", "viewer"])
def test_dashboard_group_counts_and_children_match_tracking_and_search(client, users, offices, direction_traffic, role):
    # The viewer belongs to MED; other roles read SUP's direction.
    client.force_login(users[role])
    query = f"?office={offices['SUP'].pk}" if role == "admin" else ""
    response = client.get("/" + query)
    groups = response.context["desk_direction_groups"]
    assert [group["key"] for group in groups] == ["incoming", "outgoing"]
    for group in groups:
        assert group["count"] == sum(stage["count"] for stage in group["stages"])
        all_ids = listed(client, group["tracking_url"])
        assert len(all_ids) == group["count"]
        combined = set()
        for stage in group["stages"]:
            ids = listed(client, stage["tracking_url"])
            assert len(ids) == stage["count"]
            assert not combined.intersection(ids)
            combined.update(ids)
            parts = urlsplit(stage["tracking_url"])
            search_url = "/search/?mode=tracking&" + parts.query
            assert listed(client, search_url) == ids
            picked = client.get(stage["href"], HTTP_HX_REQUEST="true", HTTP_HX_TARGET="action-centre-queue")
            assert picked.context["desk_queue"]["label"] == f"{group['label']} · {stage['label']}"
            assert {record.pk for record in picked.context["attention_records"]} == ids
            assert picked.context["desk_queue"]["tracking_url"] == stage["tracking_url"]
            assert '<html' not in picked.content.decode()
        assert combined == all_ids


@pytest.mark.django_db
def test_outgoing_received_and_in_process_are_sender_filters_and_partial_receipt_is_local_stage(
    client, users, direction_traffic,
):
    client.force_login(users["sup"])
    groups = {group["key"]: group for group in client.get("/").context["desk_direction_groups"]}
    outgoing = {stage["status"]: stage for stage in groups["outgoing"]["stages"]}
    assert listed(client, outgoing[Status.RECEIVED]["tracking_url"]) == {direction_traffic["sent_received"].pk}
    assert listed(client, outgoing[Status.IN_PROCESS]["tracking_url"]) == {direction_traffic["sent_working"].pk}
    incoming = {stage["status"]: stage for stage in groups["incoming"]["stages"]}
    assert listed(client, incoming[Status.RECEIVED]["tracking_url"]) == {
        direction_traffic["received"].pk,
    }
    assert direction_traffic["partial"].pk in listed(client, incoming[Status.PENDING_RECEIPT]["tracking_url"])
    awaiting = client.get("/?desk=awaiting")
    assert direction_traffic["partial"].pk in {record.pk for record in awaiting.context["attention_records"]}


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/tracking/", "/search/?mode=tracking&"])
def test_group_links_set_both_filters_preserve_other_filters_and_reset_page(client, users, offices, direction_traffic, path):
    client.force_login(users["admin"])
    joiner = "&" if "?" in path else "?"
    response = client.get(path + joiner + (
        f"office={offices['SUP'].code.lower()}&scope=incoming&status=RECEIVED&status=IN_PROCESS"
        f"&offices={offices['MED'].pk}&q=receipt&overdue=no&owner=mine&per_page=100&page=9"
    ))
    groups = response.context["direction_filter_groups"]
    for group in groups:
        for stage in group["stages"]:
            params = parse_qs(urlsplit(stage["href"]).query)
            assert params["scope"] == [group["key"]]
            assert params["status"] == [stage["status"]]
            assert params["office"] == [str(offices["SUP"].pk)]
            assert params["offices"] == [str(offices["MED"].pk)]
            assert params["q"] == ["receipt"] and params["owner"] == ["mine"]
            assert params["overdue"] == ["no"] and params["per_page"] == ["100"]
            assert "page" not in params
    body = response.content.decode()
    assert 'aria-label="Outgoing · Received"' in body
    assert 'aria-label="Incoming · In Process"' in body
    prefix = "search-direction" if path.startswith("/search/") else "tracking-direction"
    assert f'id="{prefix}-outgoing-received"' in body
    assert f'id="{prefix}-incoming-in-process"' in body


@pytest.mark.django_db
@pytest.mark.parametrize("path,context_key", [
    ("/?office=all", "desk_direction_groups"),
    ("/tracking/?office=all", "direction_filter_groups"),
    ("/search/?mode=tracking&office=all", "direction_filter_groups"),
])
def test_all_offices_never_offers_direction_stage_links(client, users, direction_traffic, path, context_key):
    client.force_login(users["admin"])
    response = client.get(path)
    for group in response.context[context_key]:
        assert group["disabled"]
        for stage in group["stages"]:
            assert not stage.get("href")
    assert "Choose an office to use Incoming and Outgoing" in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["sup", "sup_admin", "viewer"])
def test_direction_groups_ignore_foreign_office_for_non_system_admin(client, users, offices, direction_traffic, role):
    client.force_login(users[role])
    own = client.get("/").context["desk_direction_groups"]
    foreign = client.get(f"/?office={offices['HR'].pk}").context["desk_direction_groups"]
    assert [[stage["count"] for stage in group["stages"]] for group in own] == [
        [stage["count"] for stage in group["stages"]] for group in foreign
    ]


@pytest.mark.django_db
def test_report_group_count_and_record_sets_agree_with_dashboard(client, users, offices, direction_traffic):
    client.force_login(users["admin"])
    office = offices["SUP"].pk
    dashboard = {group["key"]: group for group in client.get(f"/?office={office}").context["desk_direction_groups"]}
    response = client.get(f"/tracking/reports/?office={office}")
    for group in response.context["direction_stages"]:
        desk_group = dashboard[group["key"]]
        assert group["total"] == desk_group["count"]
        assert listed(client, group["workspace_url"]) == listed(client, desk_group["tracking_url"])
        for stage, desk_stage in zip(group["stages"], desk_group["stages"], strict=True):
            assert stage["status"] == desk_stage["status"]
            assert stage["total"] == desk_stage["count"]
            assert listed(client, stage["workspace_url"]) == listed(client, desk_stage["tracking_url"])


@pytest.mark.django_db
def test_old_batches_and_finished_work_are_not_current_direction_stages(client, users, offices, direction_traffic):
    forwarded = direction_traffic["received"]
    services.route_record(forwarded, [offices["HR"]], user=users["sup"], action=RoutingStep.Action.FORWARD)
    services.complete_record(direction_traffic["sent_received"], user=users["hr"])
    client.force_login(users["med"])
    med_groups = {group["key"]: group for group in client.get("/").context["desk_direction_groups"]}
    assert forwarded.pk not in listed(client, med_groups["outgoing"]["tracking_url"])
    client.force_login(users["sup"])
    sup_groups = {group["key"]: group for group in client.get("/").context["desk_direction_groups"]}
    assert forwarded.pk not in listed(client, sup_groups["incoming"]["tracking_url"])
    assert forwarded.pk in listed(client, sup_groups["outgoing"]["tracking_url"])
    assert direction_traffic["sent_received"].pk not in listed(client, sup_groups["outgoing"]["tracking_url"])
    for group in sup_groups.values():
        assert group["count"] == sum(stage["count"] for stage in group["stages"])


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/tracking/", "/search/?mode=tracking&"])
def test_all_tracking_clears_direction_and_stages_but_preserves_search_and_office(client, users, offices, path):
    client.force_login(users["admin"])
    joiner = "&" if "?" in path else "?"
    response = client.get(path + joiner + f"office={offices['SUP'].pk}&scope=outgoing&status=RECEIVED&q=memo&overdue=yes")
    params = parse_qs(urlsplit(response.context["all_tracking_href"]).query)
    assert "scope" not in params and "status" not in params
    assert params["q"] == ["memo"] and params["office"] == [str(offices["SUP"].pk)]
    assert params["overdue"] == ["yes"]


@pytest.mark.django_db
def test_search_keeps_global_stage_filter_visible_when_direction_is_unavailable(client, users):
    client.force_login(users["admin"])
    response = client.get("/search/?mode=tracking&office=all&scope=incoming&status=RECEIVED")
    assert response.context["resolved"].scope == ""
    assert response.context["selected_statuses"] == {Status.RECEIVED}
    body = response.content.decode()
    details = body.split('id="search-tracking-filter-options"', 1)[1].split('>', 1)[0]
    assert "open" in details
    assert "Filters applied" in body
