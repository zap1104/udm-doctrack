"""Administrators idle out sooner than ordinary users.

An administrator session can create accounts, reset other people's passwords
and change access control, so an unattended one is worth more to whoever sits
down at it than a clerk's is. There are also far fewer administrators, so the
cost of the shorter window falls on the people best placed to absorb it.

The window has to agree in three places — what the middleware enforces, what
the keep-alive endpoint reports, and what the page's countdown renders — or a
"you are about to be signed out" banner appears after the fact.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.sessions.models import Session

from apps.core.middleware import idle_seconds_for

KEEP_ALIVE = "/accounts/session/keep-alive/"


def expiry_seconds(client):
    row = Session.objects.get(session_key=client.session.session_key)
    from django.utils import timezone

    return (row.expire_date - timezone.now()).total_seconds()


def test_the_two_windows_are_settings_driven_and_the_admin_one_is_shorter():
    assert settings.SESSION_COOKIE_AGE_ADMIN < settings.SESSION_COOKIE_AGE
    assert settings.SESSION_IDLE_MINUTES_ADMIN == 15
    assert settings.SESSION_IDLE_MINUTES == 30


@pytest.mark.django_db
def test_the_helper_picks_the_window_by_role(users):
    assert idle_seconds_for(users["med"]) == settings.SESSION_COOKIE_AGE
    assert idle_seconds_for(users["viewer"]) == settings.SESSION_COOKIE_AGE
    assert idle_seconds_for(users["med_admin"]) == settings.SESSION_COOKIE_AGE_ADMIN
    assert idle_seconds_for(users["admin"]) == settings.SESSION_COOKIE_AGE_ADMIN


@pytest.mark.django_db
def test_an_admin_session_actually_expires_sooner(client, users):
    client.force_login(users["med_admin"])
    client.get("/")

    assert expiry_seconds(client) <= settings.SESSION_COOKIE_AGE_ADMIN + 5
    assert expiry_seconds(client) < settings.SESSION_COOKIE_AGE


@pytest.mark.django_db
def test_an_ordinary_session_keeps_the_full_window(client, users):
    client.force_login(users["med"])
    client.get("/")

    assert expiry_seconds(client) > settings.SESSION_COOKIE_AGE_ADMIN + 5


@pytest.mark.django_db
def test_the_keep_alive_reports_the_role_s_own_window(client, second_client, users):
    client.force_login(users["med_admin"])
    assert client.post(KEEP_ALIVE).json()["seconds_remaining"] == settings.SESSION_COOKIE_AGE_ADMIN

    second_client.force_login(users["med"])
    assert second_client.post(KEEP_ALIVE).json()["seconds_remaining"] == settings.SESSION_COOKIE_AGE


@pytest.mark.django_db
def test_the_page_countdown_matches_what_is_enforced(client, second_client, users):
    client.force_login(users["med_admin"])
    body = client.get("/").content.decode()
    assert f'data-session-timeout="{settings.SESSION_COOKIE_AGE_ADMIN}"' in body

    second_client.force_login(users["med"])
    body = second_client.get("/").content.decode()
    assert f'data-session-timeout="{settings.SESSION_COOKIE_AGE}"' in body


@pytest.mark.django_db
def test_losing_the_admin_role_restores_the_longer_window(client, users):
    """Otherwise the shorter window sticks to the session until the next sign-in."""
    user = users["med_admin"]
    client.force_login(user)
    client.get("/")
    assert expiry_seconds(client) <= settings.SESSION_COOKIE_AGE_ADMIN + 5

    user.role = user.Role.USER
    user.save(update_fields=["role"])
    client.get("/")

    assert expiry_seconds(client) > settings.SESSION_COOKIE_AGE_ADMIN + 5
