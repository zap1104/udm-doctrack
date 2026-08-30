"""Request-scoped middleware."""

import threading

from django.conf import settings
from django.contrib import messages
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
            if seconds != settings.SESSION_COOKIE_AGE:
                request.session.set_expiry(seconds)
            elif request.session.get_expiry_age() != settings.SESSION_COOKIE_AGE:
                # Back to the default if the account stopped being an
                # administrator mid-session; without this the shorter window
                # would stick to the session until the next sign-in.
                request.session.set_expiry(seconds)
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
