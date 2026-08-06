"""Progressive sign-in lockouts for django-axes, plus a real page to show them.

django-axes has one fixed cool-off for every lockout. This module makes each
successive lockout of the same username (or client IP) wait twice as long as
the one before, up to ``AXES_COOLOFF_MAX_MINUTES``, and gives the lockout page
its own URL so the countdown on it is real.

Three things need to be true for that to work, and each one is a bug we hit:

1. **The escalation has to survive a restart.** It lives in the database
   (``LoginLockout``), not the cache — see that model's docstring.

2. **Repeated failures during one lockout must not keep escalating it.** Axes
   re-fires ``user_locked_out`` on *every* failed attempt while locked, so the
   stage only moves when a lockout starts *after* the previous one expired.
   ``LoginLockout.locked_until`` is what tells those two cases apart.

3. **The lockout page needs its own GET-able URL.** Rendered inline as the body
   of the failed POST it has no address of its own, so reloading either resends
   the login (extending the real lockout) or replays a cached copy with a
   frozen countdown. ``accounts:lockout`` recomputes the time left per request.

4. **A retry must not push the deadline back.** Axes' own default resets its
   cool-off on every failed attempt made *while already locked* — so trying
   again, from this device or another one, kept showing a fresh full timer
   instead of counting down. ``AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT
   = False`` stops axes doing that, ``_record_lockout`` refuses to move
   ``locked_until`` once it is set, and the countdown reads that frozen value
   instead of axes' live attempt data — so the same number shows no matter
   which device or username/IP pairing asks.

Office IP addresses here are shared by many staff behind NAT, so a successful
sign-in never clears the IP-side escalation — that would let one person's login
wipe the backoff protecting everyone else on the same connection.
"""

from __future__ import annotations

from datetime import timedelta
from logging import getLogger

from axes.attempts import get_user_attempts
from axes.handlers.proxy import AxesProxyHandler
from axes.helpers import (
    get_client_ip_address,
    get_client_username,
    get_cool_off_iso8601,
    get_credentials,
    get_failure_limit,
)
from axes.signals import user_locked_out
from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.core.cache import cache
from django.db.models import Q
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.core.middleware import get_current_request

from .models import LoginLockout

log = getLogger(__name__)

SESSION_USERNAME_KEY = "axes_lockout_username"

# Read-through cache in front of LoginLockout. Axes asks for the cool-off
# several times per sign-in attempt, and this is on the path of every login.
# The database stays the source of truth: losing or flushing the cache costs a
# query, never an escalation. The TTL is only a backstop, because every write
# invalidates the key through the signals at the bottom of this section.
CACHE_PREFIX = "doctrack:lockout"
CACHE_TTL_SECONDS = 60 * 60
_NO_ROW: dict = {}  # cached "nothing recorded", so misses do not re-query each time


# ---------------------------------------------------------------------------
# Escalation state
# ---------------------------------------------------------------------------
def _decay_cutoff():
    return timezone.now() - timedelta(days=settings.AXES_ESCALATION_DECAY_DAYS)


def cooloff_for_stage(stage: int) -> timedelta:
    """Stage 0 and 1 both wait the base time; every stage after that doubles it."""
    base = settings.AXES_COOLOFF_BASE_MINUTES
    ceiling = settings.AXES_COOLOFF_MAX_MINUTES
    minutes = min(base * (2 ** max(stage - 1, 0)), ceiling)
    return timedelta(minutes=minutes)


def cache_key(kind: str, key: str) -> str:
    return f"{CACHE_PREFIX}:{kind}:{key}"


def _record_for(kind: str, key: str) -> dict | None:
    """The stored lockout row for one username/IP, via the cache."""
    name = cache_key(kind, key)
    record = cache.get(name)
    if record is None:  # a miss; we never store None itself, only _NO_ROW
        record = (
            LoginLockout.objects.filter(kind=kind, key=key)
            .values("stage", "last_lockout_at", "locked_until")
            .first()
            or _NO_ROW
        )
        cache.set(name, record, CACHE_TTL_SECONDS)
    return record or None


def _live_records(username: str | None, ip_address: str | None) -> list[dict]:
    """Rows for this username and IP that the decay window has not yet forgiven.

    Decay is applied here, on read, rather than baked into the cached value —
    otherwise a cached stage could outlive the quiet spell meant to forgive it.
    """
    cutoff = _decay_cutoff()
    records = []
    for kind, key in ((LoginLockout.Kind.USERNAME, username), (LoginLockout.Kind.IP, ip_address)):
        if not key:
            continue
        record = _record_for(kind, key)
        if record is None:
            continue
        last_lockout_at = record["last_lockout_at"]
        if last_lockout_at is None or last_lockout_at < cutoff:
            continue
        records.append(record)
    return records


def current_stage(username: str | None, ip_address: str | None) -> int:
    """Highest live escalation stage recorded against this username or IP."""
    return max((record["stage"] for record in _live_records(username, ip_address)), default=0)


@receiver(post_save, sender=LoginLockout)
@receiver(post_delete, sender=LoginLockout)
def _invalidate_cached_record(sender, instance, **kwargs):
    """Keep the cache honest no matter who writes.

    Hanging this off the model rather than calling it from our own helpers
    means the admin, `manage.py fix_login` and any future code all invalidate
    correctly. Registering a post_delete receiver also stops Django taking its
    fast-delete shortcut, so bulk `queryset.delete()` still notifies us.
    """
    cache.delete(cache_key(instance.kind, instance.key))


def get_progressive_cooloff() -> timedelta:
    """``AXES_COOLOFF_TIME`` callable.

    Axes uses this for two things: how long a lockout lasts, and how far back
    it counts failed attempts. Both should grow together, so both come from the
    same stage.
    """
    request = get_current_request()
    if request is None:  # management commands, shell, tests
        return cooloff_for_stage(0)
    return cooloff_for_stage(current_stage(get_client_username(request), get_client_ip_address(request)))


def clear_escalation(username: str | None = None, ip_address: str | None = None) -> int:
    """Forget the escalation for a username and/or IP. Returns rows removed."""
    query = Q()
    if username:
        query |= Q(kind=LoginLockout.Kind.USERNAME, key=username)
    if ip_address:
        query |= Q(kind=LoginLockout.Kind.IP, key=ip_address)
    if not query:
        return 0
    count, _ = LoginLockout.objects.filter(query).delete()
    return count


def _record_lockout(kind: str, key: str, attempt_time) -> None:
    """Advance the stage, but only when this is a genuinely new lockout.

    ``locked_until`` is set once, when a lockout begins, and then left alone.
    A retry made while already locked (from this device, a reload, or someone
    checking from a different computer) must not push it further out — axes
    itself is told not to (``AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT =
    False``), but it still re-fires the signal that gets us here on every such
    retry, so this has to refuse to extend it independently too.
    """
    row, _created = LoginLockout.objects.get_or_create(kind=kind, key=key)

    still_locked = row.locked_until is not None and attempt_time < row.locked_until
    if still_locked:
        row.last_lockout_at = attempt_time
        row.save(update_fields=["last_lockout_at", "updated_at"])
        return

    if row.last_lockout_at is not None and row.last_lockout_at < _decay_cutoff():
        stage = 1  # quiet for long enough that the history is forgiven
    else:
        stage = row.stage + 1
        log.warning("AXES: %s %r reached lockout stage %d", kind, key, stage)

    row.stage = stage
    row.last_lockout_at = attempt_time
    row.locked_until = attempt_time + cooloff_for_stage(stage)
    row.save(update_fields=["stage", "last_lockout_at", "locked_until", "updated_at"])


@receiver(user_locked_out)
def on_user_locked_out(sender, request, username, ip_address, **kwargs):
    attempt_time = getattr(request, "axes_attempt_time", None) or timezone.now()
    if username:
        _record_lockout(LoginLockout.Kind.USERNAME, username, attempt_time)
    if ip_address:
        _record_lockout(LoginLockout.Kind.IP, ip_address, attempt_time)


@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    if request is not None:
        request.session.pop(SESSION_USERNAME_KEY, None)

    if not settings.AXES_ESCALATION_RESET_ON_LOGIN:
        return
    if getattr(user, "must_change_password", False):
        return  # not through the door yet — cleared once the new password is set
    clear_escalation(username=user.get_username())


# ---------------------------------------------------------------------------
# The lockout page
# ---------------------------------------------------------------------------
def _seconds_remaining(request, credentials, username, ip_address, cool_off) -> int:
    """Time left until unlock — the same number regardless of who asks or from where.

    ``LoginLockout.locked_until`` is the authoritative answer: it is a single
    timestamp keyed on the username and IP, set once when the lockout began
    and frozen from then on (see ``_record_lockout``). Reading it here, rather
    than re-deriving a deadline from axes' own live attempt data, is what
    makes the countdown agree across devices and across reloads — axes'
    per-request bookkeeping is exactly the thing that used to make it drift.

    Live axes attempts are still consulted as a fallback, only for the gap
    between a lockout being detected and our signal handler recording it.
    """
    recorded = [record["locked_until"] for record in _live_records(username, ip_address) if record["locked_until"]]
    unlock_at = max(recorded, default=None)

    if unlock_at is None:
        latest = None
        for attempts in get_user_attempts(request, credentials):
            newest = attempts.order_by("-attempt_time").values_list("attempt_time", flat=True).first()
            if newest and (latest is None or newest > latest):
                latest = newest
        unlock_at = latest + cool_off if latest else None

    if unlock_at is None:
        return int(cool_off.total_seconds())
    return max(0, int((unlock_at - timezone.now()).total_seconds()))


def lockout_response(request, credentials=None):
    """``AXES_LOCKOUT_CALLABLE`` — hand the browser a URL it can safely reload."""
    username = get_client_username(request, credentials)

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        # Keep axes' own JSON contract for non-browser callers.
        cool_off = cooloff_for_stage(current_stage(username, request.axes_ip_address))
        response = JsonResponse(
            {
                "failure_limit": get_failure_limit(request, credentials),
                "username": username or "",
                "cooloff_time": get_cool_off_iso8601(cool_off),
                "cooloff_seconds": int(cool_off.total_seconds()),
            },
            status=settings.AXES_HTTP_RESPONSE_CODE,
        )
        response["Access-Control-Allow-Origin"] = settings.AXES_ALLOWED_CORS_ORIGINS
        return response

    request.session[SESSION_USERNAME_KEY] = username or ""
    return redirect("accounts:lockout")


def lockout_status_view(request):
    """GET view behind ``accounts:lockout`` — recalculated on every load."""
    username = request.session.get(SESSION_USERNAME_KEY) or None
    credentials = get_credentials(username=username) if username else None

    AxesProxyHandler.update_request(request)
    if not AxesProxyHandler.is_locked(request, credentials):
        request.session.pop(SESSION_USERNAME_KEY, None)
        return redirect("accounts:login")

    ip_address = request.axes_ip_address
    cool_off = cooloff_for_stage(current_stage(username, ip_address))

    context = {
        "failure_limit": get_failure_limit(request, credentials),
        "username": username or "",
        "cooloff_time": get_cool_off_iso8601(cool_off),
        "cooloff_timedelta": cool_off,
        "cooloff_seconds_remaining": _seconds_remaining(
            request, credentials, username, ip_address, cool_off
        ),
    }
    response = render(
        request, settings.AXES_LOCKOUT_TEMPLATE, context, status=settings.AXES_HTTP_RESPONSE_CODE
    )
    response["Cache-Control"] = "no-store"
    return response
