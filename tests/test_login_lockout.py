"""Progressive sign-in lockouts and the countdown shown on the lockout page.

The bug these guard against: every lockout looked like the first one, because
the escalation lived in a per-process cache that the dev server's auto-reloader
wiped, and because axes re-fires ``user_locked_out`` on every failed attempt
made *during* a lockout.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.accounts.axes_hooks import (
    SESSION_USERNAME_KEY,
    _record_lockout,
    clear_escalation,
    cooloff_for_stage,
    current_stage,
)
from apps.accounts.models import LoginLockout

User = get_user_model()

USERNAME = LoginLockout.Kind.USERNAME


@override_settings(AXES_COOLOFF_BASE_MINUTES=15, AXES_COOLOFF_MAX_MINUTES=24 * 60)
def test_each_stage_doubles_the_wait_and_then_stops_at_the_ceiling():
    assert cooloff_for_stage(0) == timedelta(minutes=15)
    assert cooloff_for_stage(1) == timedelta(minutes=15)
    assert cooloff_for_stage(2) == timedelta(minutes=30)
    assert cooloff_for_stage(3) == timedelta(hours=1)
    assert cooloff_for_stage(4) == timedelta(hours=2)
    assert cooloff_for_stage(99) == timedelta(hours=24)


@override_settings(AXES_COOLOFF_BASE_MINUTES=15, AXES_ESCALATION_DECAY_DAYS=7)
def test_repeated_failures_during_one_lockout_do_not_escalate_it(db):
    """Axes fires user_locked_out on every attempt while locked, not just the first."""
    start = timezone.now()
    _record_lockout(USERNAME, "mallory", start)
    assert current_stage("mallory", None) == 1

    # Hammering the login form during the 15-minute lockout.
    for minute in (1, 2, 5, 14):
        _record_lockout(USERNAME, "mallory", start + timedelta(minutes=minute))

    assert current_stage("mallory", None) == 1, "retries inside one lockout must not escalate"


@override_settings(AXES_COOLOFF_BASE_MINUTES=15, AXES_ESCALATION_DECAY_DAYS=7)
def test_a_fresh_lockout_after_the_last_one_expired_waits_longer(db):
    start = timezone.now()
    _record_lockout(USERNAME, "mallory", start)
    assert cooloff_for_stage(current_stage("mallory", None)) == timedelta(minutes=15)

    # Second lockout, after the first 15-minute window has passed.
    _record_lockout(USERNAME, "mallory", start + timedelta(minutes=20))
    assert current_stage("mallory", None) == 2
    assert cooloff_for_stage(current_stage("mallory", None)) == timedelta(minutes=30)

    # Third, after the 30-minute window has passed.
    _record_lockout(USERNAME, "mallory", start + timedelta(minutes=60))
    assert current_stage("mallory", None) == 3
    assert cooloff_for_stage(current_stage("mallory", None)) == timedelta(hours=1)


@override_settings(AXES_ESCALATION_DECAY_DAYS=7)
def test_escalation_is_forgiven_after_a_quiet_spell(db):
    LoginLockout.objects.create(
        kind=USERNAME,
        key="mallory",
        stage=5,
        last_lockout_at=timezone.now() - timedelta(days=8),
        locked_until=timezone.now() - timedelta(days=8),
    )
    assert current_stage("mallory", None) == 0

    _record_lockout(USERNAME, "mallory", timezone.now())
    assert current_stage("mallory", None) == 1, "a forgiven history restarts at the base wait"


def test_escalation_is_recorded_per_username_and_per_ip(db):
    now = timezone.now()
    _record_lockout(USERNAME, "mallory", now)
    _record_lockout(LoginLockout.Kind.IP, "10.0.0.9", now)

    assert current_stage("mallory", None) == 1
    assert current_stage(None, "10.0.0.9") == 1
    assert current_stage("someone-else", "10.0.0.9") == 1, "the IP alone still carries a stage"

    assert clear_escalation(username="mallory") == 1
    assert current_stage("mallory", None) == 0
    assert current_stage(None, "10.0.0.9") == 1, "clearing a user must not clear the shared IP"


@override_settings(AXES_FAILURE_LIMIT=3, AXES_COOLOFF_BASE_MINUTES=15)
def test_locked_out_user_is_redirected_to_a_reloadable_page_with_a_countdown(client, db):
    User.objects.create_user(username="mallory", password="RealPass123!")

    for _ in range(3):
        response = client.post("/accounts/login/", {"username": "mallory", "password": "nope"})

    assert response.status_code == 302
    assert response["Location"] == "/accounts/locked/"
    assert client.session[SESSION_USERNAME_KEY] == "mallory"

    page = client.get("/accounts/locked/")
    assert page.status_code == 429
    assert page["Cache-Control"] == "no-store"

    seconds = int(page.context["cooloff_seconds_remaining"])
    assert 0 < seconds <= 15 * 60

    # Reloading must not restart the countdown.
    again = client.get("/accounts/locked/")
    assert int(again.context["cooloff_seconds_remaining"]) <= seconds

    assert LoginLockout.objects.filter(kind=USERNAME, key="mallory", stage=1).exists()


@override_settings(AXES_FAILURE_LIMIT=3, AXES_COOLOFF_BASE_MINUTES=15)
def test_retrying_while_locked_does_not_push_the_deadline_back(client, db):
    """The reported bug: any retry made the countdown jump back up to full."""
    from django.test import Client

    User.objects.create_user(username="mallory", password="RealPass123!")
    ip = "198.51.100.10"

    for _ in range(3):
        client.post("/accounts/login/", {"username": "mallory", "password": "nope"}, REMOTE_ADDR=ip)
    first = int(client.get("/accounts/locked/", REMOTE_ADDR=ip).context["cooloff_seconds_remaining"])

    # Retry on the same device, wrong password again.
    client.post("/accounts/login/", {"username": "mallory", "password": "still-nope"}, REMOTE_ADDR=ip)
    after_retry = int(client.get("/accounts/locked/", REMOTE_ADDR=ip).context["cooloff_seconds_remaining"])
    assert after_retry <= first, "a retry on the same device pushed the deadline out"

    # Retry with the CORRECT password — still refused, still must not reset.
    client.post("/accounts/login/", {"username": "mallory", "password": "RealPass123!"}, REMOTE_ADDR=ip)
    after_correct = int(client.get("/accounts/locked/", REMOTE_ADDR=ip).context["cooloff_seconds_remaining"])
    assert after_correct <= first, "a correct-password retry while locked pushed the deadline out"

    # Same username, checked from a different computer entirely.
    other_device = Client()
    other_device.post("/accounts/login/", {"username": "mallory", "password": "nope"}, REMOTE_ADDR="203.0.113.9")
    from_elsewhere = int(
        other_device.get("/accounts/locked/", REMOTE_ADDR="203.0.113.9").context["cooloff_seconds_remaining"]
    )
    assert abs(from_elsewhere - first) <= 2, "checking from another computer showed a different countdown"


@override_settings(AXES_FAILURE_LIMIT=3)
def test_lockout_page_sends_you_back_to_login_once_it_expires(client, db):
    User.objects.create_user(username="mallory", password="RealPass123!")
    for _ in range(3):
        client.post("/accounts/login/", {"username": "mallory", "password": "nope"})

    # Cool-off elapsed: axes drops the attempts it counts against you.
    from axes.models import AccessAttempt

    AccessAttempt.objects.all().delete()

    page = client.get("/accounts/locked/")
    assert page.status_code == 302
    assert page["Location"] == "/accounts/login/"
    assert SESSION_USERNAME_KEY not in client.session


def sign_in(client, username="mallory", password="RealPass123!", **extra):
    """Sign in through the real view.

    Not ``client.login()``: that calls ``authenticate()`` with no request, and
    the axes backend raises ``AxesBackendRequestParameterRequired`` (a
    ValueError, which ``authenticate()`` does not swallow) when the request is
    missing. Going through the view is also what exercises our signal handlers.
    """
    return client.post("/accounts/login/", {"username": username, "password": password}, **extra)


@override_settings(AXES_FAILURE_LIMIT=3, AXES_ESCALATION_RESET_ON_LOGIN=True)
def test_full_sign_in_forgives_the_escalation_when_that_is_enabled(client, db):
    User.objects.create_user(username="mallory", password="RealPass123!")
    _record_lockout(USERNAME, "mallory", timezone.now() - timedelta(days=1))
    assert current_stage("mallory", None) == 1

    sign_in(client)
    assert client.session.get("_auth_user_id"), "the sign-in did not go through"
    assert current_stage("mallory", None) == 0


@override_settings(AXES_FAILURE_LIMIT=3, AXES_ESCALATION_RESET_ON_LOGIN=False)
def test_by_default_signing_in_does_not_forgive_the_escalation(client, db):
    """The reported bug: sign in, get locked out again, and the wait had reset."""
    User.objects.create_user(username="mallory", password="RealPass123!")
    _record_lockout(USERNAME, "mallory", timezone.now() - timedelta(days=1))

    sign_in(client)
    assert client.session.get("_auth_user_id"), "the sign-in did not go through"
    assert current_stage("mallory", None) == 1

    _record_lockout(USERNAME, "mallory", timezone.now())
    assert current_stage("mallory", None) == 2
    assert cooloff_for_stage(current_stage("mallory", None)) == timedelta(minutes=30)


@override_settings(AXES_ESCALATION_RESET_ON_LOGIN=True)
def test_a_forced_password_change_is_what_completes_the_sign_in(client, db):
    """A user parked on the forced-password-change screen is not 'fully logged in' yet."""
    user = User.objects.create_user(username="mallory", password="RealPass123!")
    User.objects.filter(pk=user.pk).update(must_change_password=True)
    _record_lockout(USERNAME, "mallory", timezone.now() - timedelta(days=1))

    client.force_login(User.objects.get(pk=user.pk))
    assert current_stage("mallory", None) == 1, "still stuck at the password change"

    client.post(
        "/accounts/password/",
        {
            "old_password": "RealPass123!",
            "new_password1": "BrandNewPass456!",
            "new_password2": "BrandNewPass456!",
        },
    )
    assert User.objects.get(pk=user.pk).must_change_password is False
    assert current_stage("mallory", None) == 0


@pytest.mark.parametrize("stage,expected_minutes", [(1, 15), (2, 30), (3, 60), (4, 120)])
@override_settings(AXES_COOLOFF_BASE_MINUTES=15, AXES_COOLOFF_MAX_MINUTES=24 * 60)
def test_stage_to_wait_mapping(stage, expected_minutes):
    assert cooloff_for_stage(stage) == timedelta(minutes=expected_minutes)


# ---------------------------------------------------------------------------
# The cache in front of LoginLockout
# ---------------------------------------------------------------------------
@override_settings(AXES_ESCALATION_DECAY_DAYS=7)
def test_flushing_the_cache_cannot_reset_the_escalation(db):
    """The whole point of keeping the database as the source of truth."""
    _record_lockout(USERNAME, "mallory", timezone.now())
    _record_lockout(USERNAME, "mallory", timezone.now() + timedelta(hours=2))
    assert current_stage("mallory", None) == 2

    cache.clear()

    assert current_stage("mallory", None) == 2, "a cold cache must re-read, not restart"


@override_settings(AXES_ESCALATION_DECAY_DAYS=7)
def test_a_new_lockout_invalidates_the_cached_stage(db):
    start = timezone.now()
    _record_lockout(USERNAME, "mallory", start)
    assert current_stage("mallory", None) == 1  # warms the cache

    _record_lockout(USERNAME, "mallory", start + timedelta(hours=2))
    assert current_stage("mallory", None) == 2, "the cache served a stale stage"


@override_settings(AXES_ESCALATION_DECAY_DAYS=7)
def test_clearing_rows_in_bulk_invalidates_the_cache(db):
    """fix_login and the admin action both delete via a queryset."""
    _record_lockout(USERNAME, "mallory", timezone.now())
    assert current_stage("mallory", None) == 1  # warms the cache

    LoginLockout.objects.all().delete()

    assert current_stage("mallory", None) == 0, "bulk delete must not leave a stale stage cached"


@override_settings(AXES_ESCALATION_DECAY_DAYS=7)
def test_a_missing_record_is_cached_without_going_stale_on_write(db):
    assert current_stage("nobody", None) == 0  # caches the "nothing recorded" answer

    _record_lockout(USERNAME, "nobody", timezone.now())

    assert current_stage("nobody", None) == 1


@override_settings(AXES_ESCALATION_DECAY_DAYS=7)
def test_decay_is_applied_to_cached_records_too(db):
    """Decay is time-based, so it must be re-evaluated on read, not cached in."""
    _record_lockout(USERNAME, "mallory", timezone.now())
    assert current_stage("mallory", None) == 1  # warms the cache

    with override_settings(AXES_ESCALATION_DECAY_DAYS=0):
        assert current_stage("mallory", None) == 0, "a cached stage outlived its decay window"
