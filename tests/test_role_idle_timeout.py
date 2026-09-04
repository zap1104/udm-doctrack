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


# --- what the sign-in page says afterwards ---------------------------------
LOGIN = "/accounts/login/"


@pytest.mark.django_db
def test_the_timeout_message_quotes_the_window_that_actually_expired(client):
    """The message is rendered once the session is gone.

    So the request behind it is anonymous, and asking `idle_seconds_for` who is
    reading gets the ordinary 30 minutes no matter who was signed out — an
    administrator idled out at 15 was told 30. A message about a security
    control, stating the wrong figure, on the one screen where it cannot be
    checked against anything.

    The browser knows the right number because it counted down with it, so the
    redirect carries it back.
    """
    admin_window = client.get(f"{LOGIN}?timeout={settings.SESSION_COOKIE_AGE_ADMIN}")
    user_window = client.get(f"{LOGIN}?timeout={settings.SESSION_COOKIE_AGE}")

    assert "after 15 minutes" in admin_window.content.decode()
    assert "after 30 minutes" in user_window.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["9999", "1", "0", "-60", "<script>", "abc"])
def test_only_a_window_the_deployment_enforces_is_quoted(client, value):
    """`?timeout=` comes from the query string, so it is not trusted to be a
    number worth printing. Anything that does not name a configured window
    falls back, which caps the worst a crafted link can do at quoting the other
    real window — never an arbitrary figure on a page about security."""
    body = client.get(f"{LOGIN}?timeout={value}").content.decode()

    assert f"after {settings.SESSION_IDLE_MINUTES} minutes" in body
    assert "9999" not in body


@pytest.mark.django_db
def test_an_older_timeout_link_still_explains_itself(client):
    """`?timeout=1` was the flag before it carried the window. A bookmark or an
    open tab still holding one has to show the message rather than a form that
    has silently reappeared."""
    body = client.get(f"{LOGIN}?timeout=1").content.decode()

    assert "without activity" in body


@pytest.mark.django_db
def test_a_signed_in_reader_is_told_their_own_window_not_the_query_string(client, users):
    """The query string only answers for the sign-in page, where nobody is
    signed in. Anywhere else the reader's own role decides, or a crafted link
    could make the countdown on a working page disagree with the server."""
    client.force_login(users["med_admin"])
    body = client.get(f"/?timeout={settings.SESSION_COOKIE_AGE}").content.decode()

    assert f'data-session-timeout="{settings.SESSION_COOKIE_AGE_ADMIN}"' in body


@pytest.mark.django_db
def test_an_empty_timeout_flag_claims_nothing(client):
    """`?timeout=` with no value is not evidence that a session expired, so the
    page says nothing rather than asserting a sign-out that may not have
    happened. The template's own truthiness test already handles it; this pins
    it, because the obvious fix for the case above would have been to make the
    fallback answer here too."""
    body = client.get(f"{LOGIN}?timeout=").content.decode()

    assert "without activity" not in body
