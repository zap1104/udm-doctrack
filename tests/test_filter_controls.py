"""GET filters update the current workspace and retain a native fallback."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory

from tests.test_template_accessibility import RenderedHTML


@pytest.mark.parametrize("template,control_id", [
    ("core/_month_picker.html", "test-month"),
    ("reports/reports.html", "trend-year"),
])
def test_report_period_changes_do_not_require_a_show_button(template, control_id):
    html = RenderedHTML(render_to_string(template, {
        "request": RequestFactory().get("/tracking/reports/"),
        "user": AnonymousUser(), "scope_label": "All offices",
        "picker_id": "test-month", "picker": {"param": "month", "options": []},
    }))
    parent = html.by_id(control_id)["parent"]
    while parent["tag"] != "form":
        parent = parent["parent"]
    assert "data-auto-filter" in parent["attrs"]


@pytest.mark.django_db
@pytest.mark.parametrize("path,control_id", [
    ("/", "dashboard-scope"),
    ("/tracking/", "tracking-as-office"),
    ("/notifications/", "notification-kind"),
    ("/tracking/reports/", "report-office"),
    ("/documents/reports/", "report-office"),
    ("/", "turnaround-stage"),
    ("/documents/", "id_month"),
    ("/search/?mode=repository", "id_source"),
    ("/accounts/users/", "user-role"),
    ("/administration/audit-log/", "audit-action"),
    ("/administration/audit-log/", "access-event"),
    ("/administration/document-types/", "masterdata-status"),
])
def test_dropdown_filters_refresh_only_page_content_with_a_native_fallback(client, users, path, control_id):
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
    assert attrs["hx-get"] == path.split("?")[0]
    assert attrs["hx-target"] == "#main-content"
    assert attrs["hx-select"] == "#main-content > *"
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
