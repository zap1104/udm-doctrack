"""Idle sign-out.

Shared workstations plus an append-only custody log: an unattended session lets
somebody else confirm receipt under a colleague's name, into a history that is
deliberately impossible to correct. These tests hold the window closed.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.conf import settings
from django.contrib.sessions.models import Session
from django.urls import reverse
from django.utils import timezone

KEEP_ALIVE = "/accounts/session/keep-alive/"


def expiry_of(client):
    return Session.objects.get(session_key=client.session.session_key).expire_date


def age_the_session(client, seconds):
    """Rewind the stored expiry, as if the browser had sat idle that long."""
    row = Session.objects.get(session_key=client.session.session_key)
    row.expire_date = timezone.now() + timedelta(seconds=settings.SESSION_COOKIE_AGE - seconds)
    row.save(update_fields=["expire_date"])


# ---------------------------------------------------------------------------
# The window is idle-based, not absolute
# ---------------------------------------------------------------------------
def test_the_window_is_measured_from_the_last_request_not_from_sign_in():
    """Without SESSION_SAVE_EVERY_REQUEST the clock ran from sign-in, which threw
    working staff out mid-task and left abandoned desks signed in."""
    assert settings.SESSION_SAVE_EVERY_REQUEST is True


def test_the_window_is_short_enough_to_matter():
    assert settings.SESSION_COOKIE_AGE <= 60 * 60
    assert 0 < settings.SESSION_WARNING_SECONDS < settings.SESSION_COOKIE_AGE


@pytest.mark.django_db
def test_using_the_site_pushes_the_deadline_back(client, users):
    client.force_login(users["med"])
    client.get("/")
    age_the_session(client, 60 * 20)
    before = expiry_of(client)

    client.get("/tracking/")

    assert expiry_of(client) > before


@pytest.mark.django_db
def test_an_idle_session_stops_working(client, users):
    """The server is the thing that enforces this — not the countdown on screen."""
    client.force_login(users["med"])
    assert client.get("/").status_code == 200

    row = Session.objects.get(session_key=client.session.session_key)
    row.expire_date = timezone.now() - timedelta(seconds=1)
    row.save(update_fields=["expire_date"])

    response = client.get("/")
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


# ---------------------------------------------------------------------------
# Keep-alive
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_keep_alive_extends_the_session_and_reports_the_new_window(client, users):
    client.force_login(users["med"])
    client.get("/")
    age_the_session(client, 60 * 25)
    before = expiry_of(client)

    response = client.post(KEEP_ALIVE)

    assert response.status_code == 200
    assert response.json()["seconds_remaining"] == settings.SESSION_COOKIE_AGE
    assert expiry_of(client) > before


@pytest.mark.django_db
def test_keep_alive_refuses_a_get(client, users):
    """Extending is a state change; a prefetch must not decide somebody is present."""
    client.force_login(users["med"])
    assert client.get(KEEP_ALIVE).status_code == 405


@pytest.mark.django_db
def test_keep_alive_does_nothing_for_a_signed_out_visitor(client):
    response = client.post(KEEP_ALIVE)
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


@pytest.mark.django_db
def test_keep_alive_works_while_a_password_change_is_being_forced(client, users):
    """That screen has a countdown too, and typing a new password sends nothing."""
    user = users["med"]
    type(user).objects.filter(pk=user.pk).update(must_change_password=True)
    client.force_login(user)

    response = client.post(KEEP_ALIVE)

    assert response.status_code == 200
    assert response.json()["authenticated"] is True


# ---------------------------------------------------------------------------
# What the page hands the browser
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_signed_in_page_carries_the_countdown_marker(client, users):
    client.force_login(users["med"])
    body = client.get("/").content.decode()

    assert f'data-session-timeout="{settings.SESSION_COOKIE_AGE}"' in body
    assert KEEP_ALIVE in body


@pytest.mark.django_db
def test_signing_out_really_ends_the_session(client, users):
    """The warning's "Sign out now" posts to logout rather than visiting the
    sign-in page. Visiting it does not sign anybody out — the view sends an
    already-authenticated visitor straight back to `next`, so the button
    returned the user to the page they were on, still signed in."""
    client.force_login(users["med"])
    assert client.session.get("_auth_user_id")

    client.post(reverse("accounts:logout"))

    assert client.session.get("_auth_user_id") is None


@pytest.mark.django_db
def test_visiting_the_sign_in_page_does_not_sign_anyone_out(client, users):
    """Pins the behaviour that made the button wrong, so it cannot regress
    silently into looking like a working sign-out."""
    client.force_login(users["med"])

    response = client.get(reverse("accounts:login") + "?timeout=1&next=/tracking/")

    assert response.status_code == 302
    assert client.session.get("_auth_user_id") is not None


@pytest.mark.django_db
def test_the_countdown_marker_carries_a_logout_url(client, users):
    client.force_login(users["med"])
    body = client.get("/").content.decode()
    assert f'data-session-logout-url="{reverse("accounts:logout")}"' in body


@pytest.mark.django_db
def test_the_sign_in_page_has_no_countdown(client):
    """Nothing to count down, and no session to keep alive."""
    body = client.get(reverse("accounts:login")).content.decode()
    assert "data-session-timeout" not in body


@pytest.mark.django_db
def test_the_sign_in_page_explains_an_idle_sign_out(client):
    """A form that has simply reappeared reads as a bug, or as a stolen account."""
    body = client.get(reverse("accounts:login") + "?timeout=1").content.decode()
    assert "without activity" in body


@pytest.mark.django_db
def test_the_sign_in_page_is_quiet_when_arrived_at_normally(client):
    body = client.get(reverse("accounts:login")).content.decode()
    assert "without activity" not in body
