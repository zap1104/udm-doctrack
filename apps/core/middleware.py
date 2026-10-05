"""Request-scoped middleware."""

import threading
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse

_state = threading.local()


def get_current_request():
    return getattr(_state, "request", None)


class CurrentRequestMiddleware:
    """Keeps the current request available to service functions (for audit logging)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _state.request = request
        try:
            return self.get_response(request)
        finally:
            _state.request = None


class OfficeScheduleMiddleware:
    """Keep every time label and calculation on one lazily loaded schedule."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .business_time import office_schedule_context

        with office_schedule_context():
            return self.get_response(request)


def idle_seconds_for(user) -> int:
    """How long this account may sit idle before it is signed out.

    One function so the middleware that enforces the window, the keep-alive
    endpoint that reports it, and the countdown the template renders cannot
    disagree — a warning timed off a different number than the expiry is how a
    "you are about to be signed out" banner ends up appearing after the fact.
    """
    admin_age = getattr(settings, "SESSION_COOKIE_AGE_ADMIN", settings.SESSION_COOKIE_AGE)
    if user is not None and user.is_authenticated and getattr(user, "is_office_admin", False):
        return admin_age
    return settings.SESSION_COOKIE_AGE


def configured_idle_windows() -> set[int]:
    """Every idle window this deployment actually enforces."""
    return {
        settings.SESSION_COOKIE_AGE,
        getattr(settings, "SESSION_COOKIE_AGE_ADMIN", settings.SESSION_COOKIE_AGE),
    }


def idle_seconds_for_request(request) -> int:
    """The window to *quote* on this page, which is not always the reader's own.

    The sign-in page is the exception, and it is the whole reason this exists:
    "you were signed out after N minutes" is rendered once the session is gone,
    so the request is anonymous and `idle_seconds_for` answers with the ordinary
    30 minutes no matter who was actually signed out. An administrator idled out
    at 15 was told 30 — a message about a security control, stating the wrong
    figure, on the one screen where it cannot be checked.

    The browser knows the right number: it counted down with it. So the
    redirect carries it back, and it is honoured only when it names a window
    this deployment really enforces. That keeps a value from the query string
    from putting an arbitrary number on the page — the worst a crafted link can
    do is quote the other real window.
    """
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return idle_seconds_for(user)

    raw = (request.GET.get("timeout") or "").strip()
    if raw.isdigit() and int(raw) in configured_idle_windows():
        return int(raw)
    return idle_seconds_for(None)


class RoleIdleTimeoutMiddleware:
    """Gives administrators a shorter idle window than ordinary users.

    SESSION_COOKIE_AGE is one number for the whole project, so the per-role
    window has to be applied to the session itself. SESSION_SAVE_EVERY_REQUEST
    is on, which means the expiry set here is rewritten on every request the
    user makes — that is what keeps the window *idle* rather than absolute,
    exactly as it works for the project-wide default.

    Must run after AuthenticationMiddleware: it reads request.user.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            seconds = idle_seconds_for(user)
            now = time.time()
            last_activity = request.session.get("_last_user_activity", now)
            if now - last_activity >= seconds:
                logout(request)
                return redirect(settings.LOGIN_URL)
            # Automatic polling is not evidence that someone is at the desk.
            polling = request.path == "/notifications/count/" or request.path.endswith("/extraction-status/")
            if not polling:
                request.session["_last_user_activity"] = now
            if seconds != settings.SESSION_COOKIE_AGE:
                request.session.set_expiry(seconds)
            elif request.session.get_expiry_age() != settings.SESSION_COOKIE_AGE:
                # Back to the default if the account stopped being an
                # administrator mid-session; without this the shorter window
                # would stick to the session until the next sign-in.
                request.session.set_expiry(seconds)
        return self.get_response(request)


class PrivateResponseMiddleware:
    """Sensitive HTML, JSON, and downloads must not survive in browser caches."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.user.is_authenticated or request.path.startswith("/accounts/"):
            if "no-store" not in response.get("Cache-Control", ""):
                response["Cache-Control"] = "private, no-store"
        return response


class SensitiveRequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST":
            from .security import allow_sensitive_request
            from .utils import client_ip

            if request.path in ("/accounts/login/", "/django-admin/login/"):
                if not allow_sensitive_request("login-ip", client_ip(request), limit=120, seconds=60):
                    response = HttpResponse("Too many sign-in requests. Try again shortly.", status=429)
                    response["Retry-After"] = "60"
                    return response
            if request.user.is_authenticated and (request.path.endswith("/re-extract/") or request.path == "/documents/upload/"):
                if not allow_sensitive_request("file-processing", request.user.pk, limit=10, seconds=600):
                    response = HttpResponse("Too many file-processing requests. Try again later.", status=429)
                    response["Retry-After"] = "600"
                    return response
            if request.user.is_authenticated and (
                request.path == "/accounts/password/"
                or (request.path.startswith("/accounts/users/") and "set_password" in request.POST)
            ):
                if not allow_sensitive_request("password-change", request.user.pk, limit=10, seconds=600):
                    response = HttpResponse("Too many password-change requests. Try again later.", status=429)
                    response["Retry-After"] = "600"
                    return response
        return self.get_response(request)


class ForcePasswordChangeMiddleware:
    """Makes `User.must_change_password` mean what its help text says.

    The field is documented as "Force a password change on the next sign-in",
    and every account the administration screen creates is stamped with it by
    default — but the only thing enforcing it was a one-off redirect in
    SignInView. Anyone who then typed a URL, pressed Back, or followed a
    bookmark carried on using the shared password the administrator had handed
    them, indefinitely. A control that is trivially stepped around is worse than
    none, because the administrator believes it happened.

    This runs on every request instead, so the password screen is the only
    place the account can go until the password is actually changed.

    Must sit after AuthenticationMiddleware (it reads `request.user`) and after
    MessageMiddleware (it adds a message).
    """

    #: Reachable while the change is outstanding. Signing out has to stay
    #: possible — trapping someone on one page with no way off is how a
    #: forced-change screen turns into a locked-out account. The lockout page
    #: and the login page are here so a redirect can never bounce between them.
    EXEMPT_URL_NAMES = (
        "accounts:password_change",
        "accounts:login",
        "accounts:logout",
        "accounts:lockout",
        # Someone stuck on the password screen is still at their desk, and the
        # idle countdown runs there too. Redirecting the keep-alive would hand
        # the script HTML instead of JSON and sign them out while they typed.
        "accounts:session_keep_alive",
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self._exempt_paths: set[str] | None = None

    def exempt_paths(self) -> set[str]:
        # Resolved on first use, not in __init__: middleware is built during
        # startup, when the URLconf may not be loaded yet.
        if self._exempt_paths is None:
            paths = set()
            for name in self.EXEMPT_URL_NAMES:
                try:
                    paths.add(reverse(name))
                except NoReverseMatch:  # e.g. accounts:lockout when axes is off
                    continue
            self._exempt_paths = paths
        return self._exempt_paths

    def _is_exempt(self, path: str) -> bool:
        if path in self.exempt_paths():
            return True
        # Static and media are served through this stack in DEBUG; redirecting
        # them would strip the stylesheet off the very page we are forcing.
        for prefix in (settings.STATIC_URL, settings.MEDIA_URL):
            if prefix and path.startswith(prefix):
                return True
        return False

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and getattr(user, "must_change_password", False)
            and not self._is_exempt(request.path)
        ):
            messages.info(request, "Set a new password before you continue.")
            return redirect("accounts:password_change")
        return self.get_response(request)
