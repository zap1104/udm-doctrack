"""Reports is a tab of Document Tracking, not an item of its own.

It lives at /tracking/reports/, the sidebar highlights Document Tracking on
it, and a Tracking | Reports strip joins the two pages. The old address
redirects permanently with its query string, so a bookmarked or printed link
to a report still opens that report.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse


def test_the_names_are_unchanged_and_point_under_tracking():
    assert reverse("core:reports") == "/tracking/reports/"
    assert reverse("core:report_export") == "/tracking/reports/export/"


@pytest.mark.django_db
@pytest.mark.parametrize(("old", "new"), [
    ("/reports/", "/tracking/reports/"),
    ("/reports/?office=all&month=2026-08", "/tracking/reports/?office=all&month=2026-08"),
    ("/reports/export/?office=7", "/tracking/reports/export/?office=7"),
])
def test_the_old_address_redirects_permanently_with_its_query(client, users, old, new):
    client.force_login(users["admin"])
    response = client.get(old)

    assert response.status_code == 301
    assert response["Location"] == new


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["admin", "med_admin", "sup", "viewer"])
def test_reports_highlights_document_tracking_and_its_own_tab(client, users, role):
    client.force_login(users[role])
    body = client.get("/tracking/reports/").content.decode()

    sidebar = body[body.index('aria-label="Main navigation"'):body.index("</nav>", body.index('aria-label="Main navigation"'))]
    assert ">Reports</a>" not in sidebar, "no sidebar item of its own"
    assert re.search(r'href="/tracking/"\s*aria-current="page"', sidebar), "Document Tracking is the section"
    assert '<a class="subtab is-active" href="/tracking/reports/" aria-current="page">Reports</a>' in body


@pytest.mark.django_db
def test_the_tracking_page_offers_the_reports_tab(client, users):
    client.force_login(users["sup"])
    body = client.get("/tracking/").content.decode()

    assert '<a class="subtab is-active" href="/tracking/" aria-current="page">Tracking</a>' in body
    assert '<a class="subtab" href="/tracking/reports/">Reports</a>' in body
