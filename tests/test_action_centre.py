"""Direction/stage controls and attention chips count the lists they open.

Every chip is an `apply_scope` queue counted with the dashboard's office, and
its "View all in Tracking" link opens the same queue with the same office. So
for every role, and under every office as well as one, a chip's count is the
number of rows the Tracking page lists for it.
"""

from __future__ import annotations

import re
from html import escape

import pytest
from django.contrib.messages import get_messages

from tests.test_filter_agreement import traffic  # noqa: F401 — fixture, used by name

DASHBOARD = "/"
QUEUES = ["awaiting", "incoming", "pending-receipt", "received", "in-process", "overdue", "outgoing"]
RENDERED_QUEUES = [
    "incoming", "incoming-pending-receipt", "incoming-received", "incoming-in-process",
    "outgoing", "outgoing-pending-receipt", "outgoing-received", "outgoing-in-process",
    "awaiting", "overdue",
]


def _queues(response):
    queues = {}
    for group in response.context["desk_direction_groups"]:
        queues[group["key"]] = {**group, "slug": group["key"]}
        for stage in group["stages"]:
            queues[stage["slug"]] = {**stage, "disabled": group["disabled"]}
    queues.update({queue["slug"]: queue for queue in response.context["desk_attention_queues"]})
    return queues


def _listed(client, queue):
    return client.get(queue["tracking_url"]).context["total"]


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["admin", "med_admin", "sup", "hr", "viewer"])
def test_every_chip_counts_the_list_it_opens(client, users, offices, traffic, role):  # noqa: F811
    client.force_login(users[role])
    office = "" if role != "admin" else f"?office={offices['SUP'].pk}"
    queues = _queues(client.get(f"{DASHBOARD}{office}"))

    assert list(queues) == RENDERED_QUEUES
    for slug, queue in queues.items():
        assert not queue["disabled"], slug
        assert queue["count"] == _listed(client, queue), (role, slug)


@pytest.mark.django_db
def test_across_every_office_direction_is_disabled_and_the_rest_still_agree(client, users, traffic):  # noqa: F811
    client.force_login(users["admin"])
    response = client.get(f"{DASHBOARD}?office=all")
    queues = _queues(response)
    body = response.content.decode()

    for slug in RENDERED_QUEUES[:8]:
        assert queues[slug]["disabled"] and queues[slug]["count"] is None
    assert body.count('class="pill-toggle is-disabled" aria-disabled="true"') >= 2
    for slug in ("awaiting", "overdue"):
        assert queues[slug]["count"] == _listed(client, queues[slug]), slug


@pytest.mark.django_db
@pytest.mark.parametrize("slug", QUEUES)
def test_picking_a_queue_shows_its_rows_and_marks_its_chip(client, users, traffic, slug):  # noqa: F811
    client.force_login(users["sup"])
    response = client.get(f"{DASHBOARD}?desk={slug}")
    queue = response.context["desk_queue"]
    body = response.content.decode()

    assert queue["slug"] == slug
    assert len(response.context["attention_records"]) == min(queue["count"], 5)
    active = [
        anchor for anchor in re.findall(r'<a\b[^>]*>', body)
        if f'href="{escape(queue["href"])}"' in anchor
        and re.search(r'class="pill-toggle[^\"]*\bis-active\b', anchor)
        and 'aria-current="true"' in anchor
    ]
    assert active, "the picked chip says so to assistive technology"
    if slug in ("incoming", "outgoing"):
        selected = next(group for group in response.context["desk_direction_groups"] if group["key"] == slug)
        assert selected["active"]
        assert f'aria-label="All {slug} documents, {queue["count"]} documents"' in active[0]
    if slug == "awaiting":
        assert ">Reset queue</a>" not in body, "nothing to reset on the default queue"
    else:
        assert ">Reset queue</a>" in body


@pytest.mark.django_db
@pytest.mark.parametrize(("path", "reason"), [
    ("/?desk=nonsense", "is not one of the tracking queues"),
    ("/?office=all&desk=incoming", "needs an office"),
])
def test_a_queue_that_cannot_be_opened_falls_back_with_a_note(client, users, traffic, path, reason):  # noqa: F811
    client.force_login(users["admin"])
    response = client.get(path)

    assert response.status_code == 200
    assert response.context["desk_queue"]["slug"] == "awaiting"
    notes = [str(message) for message in get_messages(response.wsgi_request)]
    assert any(reason in note for note in notes), notes


@pytest.mark.django_db
def test_a_chip_clicked_with_htmx_gets_the_queue_alone(client, users, traffic):  # noqa: F811
    client.force_login(users["sup"])
    response = client.get(
        f"{DASHBOARD}?desk=received", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="action-centre-queue",
    )
    body = response.content.decode()

    assert response.status_code == 200
    assert body.lstrip().startswith('<div id="action-centre-queue"'), "only the queue, no page"
    assert "<html" not in body
    assert response.context["desk_queue"]["slug"] == "received"


@pytest.mark.django_db
def test_the_chips_work_without_script(client, users, traffic):  # noqa: F811
    """Each chip is a real link to the same dashboard with the queue in the
    query string; HTMX only upgrades it."""
    client.force_login(users["sup"])
    body = client.get(DASHBOARD).content.decode()

    for slug in ("incoming-received", "incoming-in-process", "outgoing-received", "outgoing-in-process", "overdue"):
        assert f'href="/?desk={slug}"' in body, slug
    assert 'hx-push-url="true"' in body


@pytest.mark.django_db
def test_an_office_user_cannot_widen_the_queues_by_naming_an_office(client, users, offices, traffic):  # noqa: F811
    client.force_login(users["sup"])
    own = _queues(client.get(DASHBOARD))
    other = _queues(client.get(f"{DASHBOARD}?office={offices['HR'].pk}"))

    assert {slug: q["count"] for slug, q in own.items()} == {slug: q["count"] for slug, q in other.items()}
