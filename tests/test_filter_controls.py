"""Filter selections must be finishable before a native form submission."""

import pytest

from tests.test_template_accessibility import RenderedHTML


@pytest.mark.django_db
@pytest.mark.parametrize("path,control_id", [
    ("/", "dashboard-scope"),
    ("/tracking/", "tracking-as-office"),
    ("/notifications/", "notification-kind"),
    ("/tracking/reports/", "report-office"),
])
def test_scope_and_kind_filters_have_an_always_available_submit_button(client, users, path, control_id):
    client.force_login(users["admin"])
    response = client.get(path)
    assert response.status_code == 200
    html = RenderedHTML(response.content.decode())
    control = html.by_id(control_id)
    parent = control["parent"]
    while parent and parent["tag"] != "form":
        parent = parent["parent"]
    assert parent is not None
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
    assert buttons, f"{control_id} must let users finish choosing before applying"
    assert all(button["text"].strip() for button in buttons)
