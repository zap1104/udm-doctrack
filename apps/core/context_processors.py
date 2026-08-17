from pathlib import Path

from django.conf import settings

from .colors import PALETTE

_VENDOR_DIR = Path(settings.BASE_DIR) / "static" / "vendor"
_LOCAL_VENDOR = (_VENDOR_DIR / "bootstrap.min.css").exists() and (_VENDOR_DIR / "bootstrap.bundle.min.js").exists()


NAV_MAP = (
    ("/tracking", "tracking"),
    ("/documents", "documents"),
    ("/search", "search"),
    ("/reports", "reports"),
    ("/administration", "administration"),
    ("/accounts/users", "administration"),
    ("/accounts/offices", "administration"),
)


def _nav_active(path: str) -> str:
    for prefix, name in NAV_MAP:
        if path.startswith(prefix):
            return name
    return "dashboard"


def site_context(request):
    """Values every template needs."""
    unread_notifications = 0
    if getattr(request.user, "is_authenticated", False):
        from .notifications import unread_count
        unread_notifications = unread_count(request.user)
    return {
        "nav_active": _nav_active(request.path or "/"),
        "unread_notifications": unread_notifications,
        "PALETTE": PALETTE,
        "SITE_NAME": settings.SITE_NAME,
        "SITE_LONG_NAME": settings.SITE_LONG_NAME,
        "USE_LOCAL_VENDOR": _LOCAL_VENDOR,
        "SEARCH_MIN_RELEVANCE_DEFAULT": settings.SEARCH_MIN_RELEVANCE_DEFAULT,
        "MAX_UPLOAD_MB": settings.MAX_UPLOAD_MB,
        "DEBUG": settings.DEBUG,
        "EMAIL_CONFIGURED": settings.EMAIL_CONFIGURED,
        # The page is rendered inside a request, and the session middleware
        # rewrites the expiry on the way out, so a signed-in reader always has
        # the full window from the moment this page arrives.
        "SESSION_IDLE_SECONDS": settings.SESSION_COOKIE_AGE,
        "SESSION_WARNING_SECONDS": settings.SESSION_WARNING_SECONDS,
        # Derived from the real cookie age, not from SESSION_IDLE_MINUTES, so
        # the sign-in page cannot quote a figure the server is not enforcing
        # when a deployment overrides SESSION_COOKIE_AGE directly.
        "SESSION_IDLE_MINUTES": max(1, round(settings.SESSION_COOKIE_AGE / 60)),
    }
