"""Sign-in lockout history and a reloadable countdown for django-axes.

Equal base and ceiling settings give every lockout the same wait. The stage
records successive lockouts for the same username or client IP.

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
   ``locked_until`` once it is set. Both enforcement and the countdown use
   that deadline, even when earlier failures from another device age out.

Office IP addresses here are shared by many staff behind NAT, so a successful
sign-in never clears the IP-side escalation — that would let one person's login
wipe the backoff protecting everyone else on the same connection.
"""

from __future__ import annotations

from datetime import timedelta
from logging import getLogger
from math import ceil

from axes.attempts import clean_expired_user_attempts, get_user_attempts
from axes.handlers.database import AxesDatabaseHandler
from axes.handlers.proxy import AxesProxyHandler
from axes.helpers import (
    get_client_ip_address,
    get_client_session_hash,
    get_client_username,
    get_cool_off,
    get_cool_off_iso8601,
    get_credentials,
    get_failure_limit,
)
from axes.models import AccessAttempt, AccessLog
from axes.signals import user_locked_out
from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.core.cache import cache
from django.db import connections, router, transaction
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
            .values("stage", "last_lockout_at")
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


@receiver(post_save, sender=LoginLockout, dispatch_uid="doctrack_lockout_cache_save")
@receiver(post_delete, sender=LoginLockout, dispatch_uid="doctrack_lockout_cache_delete")
def _invalidate_cached_record(sender, instance, **kwargs):
    """Keep the cache honest no matter who writes.

    Hanging this off the model rather than calling it from our own helpers
    means the admin, `manage.py fix_login` and any future code all invalidate
    correctly. Registering a post_delete receiver also stops Django taking its
    fast-delete shortcut, so bulk `queryset.delete()` still notifies us.
    """
    cache.delete(cache_key(instance.kind, instance.key))


def _active_lockout_ip(request):
    """Return the IP only when configuration deliberately uses it for blocking."""
    if getattr(settings, "AXES_LOCKOUT_PARAMETERS", None) == ["username"]:
        return None
    return get_client_ip_address(request)


def get_progressive_cooloff() -> timedelta:
    """``AXES_COOLOFF_TIME`` callable.

    Axes uses the same duration for the lockout and the attempt-counting window.
    """
    if settings.AXES_COOLOFF_BASE_MINUTES == settings.AXES_COOLOFF_MAX_MINUTES:
        return cooloff_for_stage(0)
    request = get_current_request()
    if request is None:  # management commands, shell, tests
        return cooloff_for_stage(0)
    return cooloff_for_stage(current_stage(get_client_username(request), _active_lockout_ip(request)))


def clear_escalation(username: str | None = None, ip_address: str | None = None) -> int:
    """Forget lockout history while preserving an active username deadline."""
    alias = router.db_for_write(LoginLockout)
    with transaction.atomic(using=alias):
        if username and _fixed_lockout_enabled():
            _lock_username(alias, username)
            if _fixed_username_deadline(username, get_cool_off(), timezone.now()) is not None:
                username = None
        query = Q()
        if username:
            query |= Q(kind=LoginLockout.Kind.USERNAME, key=username)
        if ip_address:
            query |= Q(kind=LoginLockout.Kind.IP, key=ip_address)
        if not query:
            return 0
        count, _ = LoginLockout.objects.using(alias).filter(query).delete()
        return count


def _fixed_lockout_enabled():
    return (settings.AXES_LOCKOUT_PARAMETERS == ["username"]
            and settings.AXES_COOLOFF_BASE_MINUTES == settings.AXES_COOLOFF_MAX_MINUTES)


def _lock_username(alias, username):
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext('doctrack.lockout'), hashtext(%s))", [username])


def _fixed_username_deadline(username, duration, attempt_time):
    """Return a current policy deadline backed by an Axes attempt.

    Keeping the attempt requirement preserves Axes' manual reset tools. Older
    progressive rows are left intact and expire via the legacy Axes counts.
    """
    if not username or not _fixed_lockout_enabled() or not settings.AXES_LOCK_OUT_AT_FAILURE:
        return None
    row = (LoginLockout.objects.filter(kind=LoginLockout.Kind.USERNAME, key=username,
                                       locked_until__gt=attempt_time)
           .values_list("last_lockout_at", "locked_until").first())
    if row and row[0] and row[1] - row[0] == duration:
        if AccessAttempt.objects.filter(username=username, attempt_time=row[0]).exists():
            return row[1]
    return None


class FixedLockoutDatabaseHandler(AxesDatabaseHandler):
    """Freeze the username lock when the threshold is reached across devices."""

    def is_locked(self, request, credentials=None):
        if not _fixed_lockout_enabled():
            return super().is_locked(request, credentials)
        if not settings.AXES_LOCK_OUT_AT_FAILURE:
            return False
        return _seconds_remaining(request, credentials, get_cool_off(), get_failure_limit(request, credentials)) > 0

    def user_login_failed(self, sender, credentials, request=None, **kwargs):
        username = get_client_username(request, credentials) if request is not None else None
        if not username or not _fixed_lockout_enabled() or not settings.AXES_LOCK_OUT_AT_FAILURE:
            return super().user_login_failed(sender, credentials, request, **kwargs)

        alias = router.db_for_write(LoginLockout)
        with transaction.atomic(using=alias):
            # Axes locks separate attempt rows per client. Serialize the
            # threshold transition for this username even across devices.
            _lock_username(alias, username)
            request.axes_attempt_time = timezone.now()
            if request.axes_locked_out or not self.is_allowed(request, credentials):
                request.axes_locked_out = True
                request.doctrack_lockout_retry = True
            else:
                # Axes' inclusive cutoff would reuse the old attempts on the
                # exact expiry boundary, even though sign-in already reopened.
                AccessAttempt.objects.using(alias).filter(
                    username=username, attempt_time=request.axes_attempt_time - get_cool_off()
                ).delete()
            return super().user_login_failed(sender, credentials, request, **kwargs)

    def user_logged_in(self, sender, request, user, **kwargs):
        if not _fixed_lockout_enabled() or not settings.AXES_RESET_ON_SUCCESS:
            return super().user_logged_in(sender, request, user, **kwargs)

        username = user.get_username()
        alias = router.db_for_write(LoginLockout)
        with transaction.atomic(using=alias):
            _lock_username(alias, username)
            now = timezone.now()
            if _fixed_username_deadline(username, get_cool_off(), now) is None:
                return super().user_logged_in(sender, request, user, **kwargs)

            # A password accepted before a later lockout may still finish OTP.
            # Record its login without deleting the attempt that backs the lock.
            clean_expired_user_attempts(now)
            if not settings.AXES_DISABLE_ACCESS_LOG:
                AccessLog.objects.create(
                    username=username,
                    ip_address=request.axes_ip_address,
                    user_agent=request.axes_user_agent,
                    http_accept=request.axes_http_accept,
                    path_info=request.axes_path_info,
                    session_hash=get_client_session_hash(request),
                )


def _record_lockout(kind: str, key: str, attempt_time, *, new_episode=False) -> None:
    """Advance the stage, but only when this is a genuinely new lockout.

    ``locked_until`` is set once, when a lockout begins, and then left alone.
    A retry made while already locked (from this device, a reload, or someone
    checking from a different computer) must not push it further out — axes
    itself is told not to (``AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT =
    False``), but it still re-fires the signal that gets us here on every such
    retry, so this has to refuse to extend it independently too.

    The row is locked for the read-modify-write. Failed sign-ins arrive
    concurrently by their nature — a script hitting the form, or simply the
    several workers in render.yaml — and without this two of them can both
    read the same stage and each add one, doubling the wait twice over for a
    single lockout.
    """
    with transaction.atomic(using=router.db_for_write(LoginLockout)):
        row, _created = LoginLockout.objects.select_for_update().get_or_create(kind=kind, key=key)

        if not new_episode and row.locked_until is not None and attempt_time < row.locked_until:
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


@receiver(user_locked_out, dispatch_uid="doctrack_lockout_escalate")
def on_user_locked_out(sender, request, username, ip_address, **kwargs):
    attempt_time = getattr(request, "axes_attempt_time", None) or timezone.now()
    if username and not getattr(request, "doctrack_lockout_retry", False):
        if _fixed_lockout_enabled():
            # An AccessAttempt insert gets its own auto_now_add timestamp.
            # Tie the episode to that actual row so an Axes reset cannot
            # resurrect this deadline when the next failed attempt arrives.
            threshold_time = (AccessAttempt.objects.filter(username=username, ip_address=ip_address,
                                                           user_agent=request.axes_user_agent)
                              .values_list("attempt_time", flat=True).first())
            if threshold_time is not None:
                _record_lockout(LoginLockout.Kind.USERNAME, username, threshold_time, new_episode=True)
        else:
            _record_lockout(LoginLockout.Kind.USERNAME, username, attempt_time)
    if ip_address:
        _record_lockout(LoginLockout.Kind.IP, ip_address, attempt_time)


@receiver(user_logged_in, dispatch_uid="doctrack_lockout_on_login")
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
def _seconds_remaining(request, credentials, cool_off, failure_limit) -> int:
    """Use the same expiry as Axes, including for locks created under an older policy."""
    unlock_at = _fixed_username_deadline(get_client_username(request, credentials), cool_off, request.axes_attempt_time)
    if unlock_at is not None:
        return max(0, ceil((unlock_at - request.axes_attempt_time).total_seconds()))
    for attempts in get_user_attempts(request, credentials):
        failures_remaining = 0
        # Find when this group drops below the failure limit as its attempts expire.
        by_recency = attempts.order_by("-attempt_time").values_list("attempt_time", "failures_since_start")
        for attempt_time, failures in by_recency.iterator():
            failures_remaining += failures
            if failures_remaining >= failure_limit:
                expires_at = attempt_time + cool_off
                if unlock_at is None or expires_at > unlock_at:
                    unlock_at = expires_at
                break
    if unlock_at is None:
        return 0
    return max(0, ceil((unlock_at - request.axes_attempt_time).total_seconds()))


def lockout_response(request, credentials=None):
    """``AXES_LOCKOUT_CALLABLE`` — hand the browser a URL it can safely reload."""
    username = get_client_username(request, credentials)

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        # Keep axes' own JSON contract for non-browser callers.
        cool_off = get_cool_off()
        response = JsonResponse(
            {
                "failure_limit": get_failure_limit(request, credentials),
                "username": username or "",
                "cooloff_time": get_cool_off_iso8601(cool_off),
                "cooloff_seconds": int(cool_off.total_seconds()),
            },
            status=settings.AXES_HTTP_RESPONSE_CODE,
        )
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

    cool_off = get_cool_off()
    failure_limit = get_failure_limit(request, credentials)
    context = {
        "failure_limit": failure_limit,
        "username": username or "",
        "cooloff_time": get_cool_off_iso8601(cool_off),
        "cooloff_timedelta": cool_off,
        "cooloff_seconds_remaining": _seconds_remaining(request, credentials, cool_off, failure_limit),
    }
    response = render(
        request, settings.AXES_LOCKOUT_TEMPLATE, context, status=settings.AXES_HTTP_RESPONSE_CODE
    )
    response["Cache-Control"] = "no-store"
    return response
