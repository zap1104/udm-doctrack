"""Folding the record history.

The fold exists so a long-running record does not open as a wall of text. It
must never cost anyone the history itself, which is the whole point of the
page — so the tests here are mostly about what is still on the page.
"""

from __future__ import annotations

import re

import pytest

from apps.tracking.models import QUIET_EVENTS, RoutingStep
from apps.tracking.services import add_remark, confirm_receipt, create_draft_record, route_record
from apps.tracking.views import TIMELINE_VISIBLE

FOLD_RE = re.compile(r'<details class="fold" open>\s*<summary>([^<]+)</summary>')


def shown_activities(record):
    """The entries the page actually renders.

    Opening a record writes a VIEWED entry of its own, and those are logged but
    deliberately not displayed — see QUIET_EVENTS. Counting raw activities here
    would mean every one of these tests measured its own page load.
    """
    return record.activities.exclude(event__in=QUIET_EVENTS)


@pytest.fixture
def long_record(users, offices):
    """A record that has been round several offices, gathering history."""
    record = create_draft_record(
        user=users["med"], subject="A long-running purchase request", instructions="For action."
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    holder, actor = offices["SUP"], users["sup"]
    for destination, next_actor in [
        (offices["HR"], users["hr"]),
        (offices["SUP"], users["sup"]),
        (offices["HR"], users["hr"]),
    ]:
        confirm_receipt(record, user=actor)
        add_remark(record, user=actor, remark=f"Reviewed at {holder.code}.")
        route_record(record, [destination], user=actor, action=RoutingStep.Action.FORWARD)
        holder, actor = destination, next_actor
    record.refresh_from_db()
    return record


@pytest.mark.django_db
def test_a_long_history_is_folded(client, users, long_record):
    client.force_login(users["admin"])
    body = client.get(long_record.get_absolute_url()).content.decode()

    assert FOLD_RE.search(body), "a record with a long history should offer a fold"


@pytest.mark.django_db
def test_the_fold_says_how_many_it_is_hiding(client, users, long_record):
    """"Read more" tells the reader nothing. A count tells them whether to look."""
    client.force_login(users["admin"])
    body = client.get(long_record.get_absolute_url()).content.decode()

    for label in FOLD_RE.findall(body):
        assert re.match(r"\d+ earlier ", label.strip()), label


@pytest.mark.django_db
def test_folding_never_drops_or_repeats_an_entry(client, users, long_record):
    """The fold hides history behind a click; it must not lose it, and the
    split must not render the same entry in both halves."""
    client.force_login(users["admin"])
    body = client.get(long_record.get_absolute_url()).content.decode()

    expected = long_record.routing_steps.count() + shown_activities(long_record).count()
    assert body.count('class="t-title"') == expected


@pytest.mark.django_db
def test_the_newest_entries_stay_open(client, users, long_record):
    """Where the document is now is the question this page is opened to answer,
    so the recent end is never the part that gets hidden."""
    client.force_login(users["admin"])
    body = client.get(long_record.get_absolute_url()).content.decode()

    newest = shown_activities(long_record).order_by("-created_at", "-id").first()
    after_last_fold = body.rsplit("</details>", 1)[-1]
    assert newest.message in after_last_fold


@pytest.mark.django_db
def test_the_fold_ships_open_so_a_blocked_script_hides_nothing(client, users, long_record):
    """doctrack.js closes it on load. Rendering it closed instead would mean a
    browser with JavaScript off — or a script that failed to load — silently
    withheld part of an append-only record."""
    client.force_login(users["admin"])
    body = client.get(long_record.get_absolute_url()).content.decode()

    assert '<details class="fold" open>' in body
    assert '<details class="fold">' not in body


@pytest.mark.django_db
def test_a_short_record_is_not_folded_at_all(client, users, offices):
    """Nothing to gain from a control that hides two lines."""
    record = create_draft_record(
        user=users["med"], subject="A brand new request", instructions="For action."
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    client.force_login(users["admin"])

    body = client.get(record.get_absolute_url()).content.decode()

    assert shown_activities(record).count() <= TIMELINE_VISIBLE
    assert 'class="fold"' not in body
