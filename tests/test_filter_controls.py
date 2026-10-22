"""GET filters declare selective updates and retain a native fallback."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory

from tests.test_template_accessibility import RenderedHTML


@pytest.mark.parametrize("template,control_id", [
    ("core/_month_picker.html", "test-month"),
    ("reports/reports.html", "trend-year"),
])
def test_report_period_selectors_opt_into_change_updates(template, control_id):
    html = RenderedHTML(render_to_string(template, {
        "request": RequestFactory().get("/tracking/reports/"),
        "user": AnonymousUser(), "scope_label": "All offices",
        "picker_id": "test-month", "picker": {"param": "month", "options": []},
    }))
    parent = html.by_id(control_id)["parent"]
    while parent["tag"] != "form":
        parent = parent["parent"]
    assert "data-auto-filter" in parent["attrs"]
    assert parent["attrs"]["data-filter-mode"] == "change"
    assert parent["attrs"]["data-filter-sync"] == "reports"


def test_shared_filter_attributes_default_to_explicit_submission():
    html = RenderedHTML('<form ' + render_to_string("partials/_auto_filter_attrs.html", {
        "request": RequestFactory().get("/search/"),
    }) + '></form>')
    attrs = html.nodes[0]["attrs"]
    assert attrs["data-filter-mode"] == "submit"
    assert "data-filter-sync" not in attrs


@pytest.mark.django_db
@pytest.mark.parametrize("path,control_id,mode,sync", [
    ("/", "dashboard-scope", "change", "dashboard"),
    ("/", "turnaround-month", "change", "dashboard"),
    ("/", "turnaround-stage", "change", "dashboard"),
    ("/tracking/", "tracking-as-office", "change", None),
    ("/tracking/", "id_q", "submit", None),
    ("/notifications/", "notification-kind", "change", None),
    ("/tracking/reports/", "report-office", "change", "reports"),
    ("/tracking/reports/", "trend-year", "change", "reports"),
    ("/tracking/reports/", "report-turnaround-month", "change", "reports"),
    ("/documents/reports/", "report-office", "change", "reports"),
    ("/documents/", "id_month", "submit", None),
    ("/documents/external/", "id_source", "submit", None),
    ("/documents/external/reports/", "id_source", "submit", None),
    ("/tracking/external/", "external-status", "submit", None),
    ("/tracking/external/reports/", "external-status", "submit", None),
    ("/search/?mode=repository", "id_source", "submit", None),
    ("/search/?mode=tracking", "search-as-office", "submit", None),
    ("/accounts/users/", "user-role", "submit", None),
    ("/administration/audit-log/", "audit-action", "submit", None),
    ("/administration/audit-log/", "access-event", "submit", None),
    ("/administration/document-types/", "masterdata-status", "submit", None),
    ("/documents/deletion-review/", "id_status", "submit", None),
    ("/documents/external/deletion-review/", "id_status", "submit", None),
])
def test_filters_declare_selective_update_policy_with_a_native_fallback(client, users, path, control_id, mode, sync):
    client.force_login(users["admin"])
    response = client.get(path)
    assert response.status_code == 200
    html = RenderedHTML(response.content.decode())
    control = html.by_id(control_id)
    parent = control["parent"]
    while parent and parent["tag"] != "form":
        parent = parent["parent"]
    assert parent is not None
    attrs = parent["attrs"]
    assert "data-auto-filter" in attrs
    assert attrs["data-filter-mode"] == mode
    assert attrs.get("data-filter-sync") == sync
    assert attrs.get("id"), f"{control_id} needs a stable form ID for pending values"
    assert attrs["hx-get"] == path.split("?")[0]
    assert attrs["hx-target"] == "#main-content"
    assert attrs["hx-select"] == "#main-content > *"
    assert attrs["hx-trigger"] == "submit, filter-change"
    assert attrs["hx-swap"] == "innerHTML show:none"
    assert attrs["hx-push-url"] == "true"
    assert attrs["hx-sync"] == "#main-content:replace"
    buttons = []
    for node in html.nodes:
        if node["tag"] != "button" or node["attrs"].get("type", "submit") != "submit":
            continue
        ancestor = node["parent"]
        inside_noscript = False
        while ancestor and ancestor is not parent:
            inside_noscript |= ancestor["tag"] == "noscript"
            ancestor = ancestor["parent"]
        if ancestor is parent and not inside_noscript:
            buttons.append(node)
    assert buttons, f"{control_id} still needs a native submit when scripts are unavailable"
    assert all(button["text"].strip() for button in buttons)
    for button in buttons:
        if "apply" in button["text"].lower() or "filter" == button["text"].strip().lower():
            assert "data-filter-fallback" in button["attrs"]
        if button["text"].strip() == "Search":
            assert "data-filter-fallback" not in button["attrs"]
    enhanced = [node for node in html.nodes if node["tag"] == "form" and "data-auto-filter" in node["attrs"]]
    assert all(node["attrs"].get("id") for node in enhanced)
    assert len({node["attrs"]["id"] for node in enhanced}) == len(enhanced)
    allowed_change_forms = {
        "/": {"dashboard-scope-filter", "dashboard-turnaround-filters"},
        "/tracking/": {"tracking-office-filter"},
        "/notifications/": {"notification-kind-filter"},
        "/tracking/reports/": {"report-scope-filter", "report-year-filter", "report-turnaround-month-filter"},
        "/documents/reports/": {"report-scope-filter"},
    }.get(path.split("?")[0], set())
    assert {node["attrs"]["id"] for node in enhanced if node["attrs"].get("data-filter-mode") == "change"} == allowed_change_forms
    for node in enhanced:
        if node["attrs"]["id"] not in allowed_change_forms:
            assert node["attrs"]["data-filter-mode"] == "submit"
            assert "data-filter-sync" not in node["attrs"]


@pytest.mark.django_db
@pytest.mark.parametrize("path,region_ids", [
    ("/", ["turnaround-panel"]),
    ("/tracking/", ["tracking-results"]),
    ("/tracking/reports/", ["report-document-stages", "report-annual-turnaround", "report-month-turnaround"]),
    ("/documents/reports/", ["repository-report-results"]),
    ("/documents/", ["repository-results"]),
    ("/documents/external/", ["external-repository-results"]),
    ("/documents/external/reports/", ["external-repository-report-results"]),
    ("/tracking/external/", ["external-tracking-results"]),
    ("/tracking/external/reports/", ["external-tracking-results"]),
    ("/search/?mode=repository", ["search-repository-results"]),
    ("/search/?mode=tracking", ["search-tracking-results"]),
    ("/administration/audit-log/", ["audit-system-results", "audit-access-results"]),
])
def test_updated_results_have_stable_filter_regions(client, users, path, region_ids):
    client.force_login(users["admin"])
    response = client.get(path)
    assert response.status_code == 200
    html = RenderedHTML(response.content.decode())
    for region_id in region_ids:
        assert "data-filter-region" in html.by_id(region_id)["attrs"]


@pytest.mark.django_db
@pytest.mark.parametrize("path", [
    "/?month=2026-08", "/tracking/?q=paper", "/documents/?q=paper",
    "/documents/external/?q=paper", "/tracking/external/?q=paper",
    "/search/?mode=repository", "/search/?mode=tracking", "/search/?mode=tracking&q=paper&scope=incoming",
    "/accounts/users/?q=paper", "/administration/document-types/?q=paper",
    "/documents/deletion-review/?q=paper",
    "/documents/external/deletion-review/?q=paper",
])
def test_clear_reset_and_search_mode_links_explicitly_discard_pending_filters(client, users, path):
    client.force_login(users["admin"])
    response = client.get(path)
    assert response.status_code == 200
    html = RenderedHTML(response.content.decode())
    links = [node for node in html.nodes if node["tag"] == "a" and (
        node["text"].strip() in {"Clear", "Clear filters", "Reset"}
        or node["parent"] and node["parent"]["parent"] and node["parent"]["parent"]["attrs"].get("aria-label") == "Search modes"
    )]
    assert links
    assert all("data-filter-discard" in link["attrs"] for link in links)


@pytest.mark.django_db
def test_audit_clear_actions_discard_only_their_own_pending_form(client, users):
    client.force_login(users["admin"])
    response = client.get("/administration/audit-log/?q=paper&record=paper")
    html = RenderedHTML(response.content.decode())
    clear_links = [node for node in html.nodes if node["tag"] == "a" and node["text"].strip() == "Clear these filters"]
    assert [node["attrs"].get("data-filter-discard") for node in clear_links] == [
        "#audit-system-filters", "#audit-access-filters",
    ]


@pytest.mark.django_db
def test_deletion_queue_tabs_keep_their_immediate_link_policy(client, users):
    client.force_login(users["admin"])
    html = RenderedHTML(client.get("/documents/deletion-review/").content.decode())
    tabs = [node for node in html.nodes if node["tag"] == "a" and node["parent"] and node["parent"]["attrs"].get("aria-label") == "Deletion review queues"]
    assert len(tabs) == 4
    assert all("data-filter-reset" in node["attrs"] and "data-filter-discard" not in node["attrs"] for node in tabs)


@pytest.mark.django_db
def test_filter_loading_status_stays_outside_the_content_being_replaced(client, users):
    client.force_login(users["admin"])
    html = RenderedHTML(client.get("/").content.decode())
    status = html.by_id("filter-update-status")
    assert status["attrs"].get("role") == "status"
    assert status["attrs"].get("aria-live") == "polite"
    parent = status["parent"]
    while parent:
        assert parent["attrs"].get("id") != "main-content"
        parent = parent["parent"]
