from pathlib import Path

from django.conf import settings
from django.utils.functional import SimpleLazyObject

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
    def badge_count():
        if not getattr(request.user, "is_authenticated", False):
            return 0
        from .notifications import in_app_enabled, unread_count
        if not in_app_enabled(request.user):
            return 0
        return unread_count(request.user)

    def in_app_preference():
        if not getattr(request.user, "is_authenticated", False):
            return False
        from .notifications import in_app_enabled
        return in_app_enabled(request.user)

    def queue_counts():
        """Per-queue totals for the Tracking disclosure in the navigation.

        Built from `active_for` and `apply_scope` — the same two functions the
        Tracking page itself uses — so a badge can never disagree with the list
        it links to. Writing the filters out again here would be faster to read
        and would drift the first time a queue definition changed.

        Cost: six COUNTs, on every page that renders the navigation, which is
        every page. They are cheap and indexed, and SimpleLazyObject means they
        only run if a template actually reads them (the sign-in and lockout
        pages replace the whole body block, so they never do). If this ever
        shows up in page timings, the fix is to cache the dict per user for a
        few seconds rather than to inline the filters.
        """
        if not getattr(request.user, "is_authenticated", False):
            return {}
        from django.utils import timezone

        from apps.tracking import services
        from apps.tracking.models import Status

        active = services.active_for(request.user)
        return {
            "inbox": services.apply_scope(active, services.SCOPE_INBOX, request.user).distinct().count(),
            "sent": services.apply_scope(active, services.SCOPE_SENT, request.user).distinct().count(),
            "awaiting": services.apply_scope(active, services.SCOPE_AWAITING, request.user).distinct().count(),
            "received": active.filter(status=Status.RECEIVED).count(),
            "in_process": active.filter(status=Status.IN_PROCESS).count(),
            # Matches the list page: overdue is a deadline condition on top of a
            # status, not a status of its own.
            "overdue": active.filter(due_at__lt=timezone.now())
            .exclude(status=Status.COMPLETED)
            .count(),
        }

    unread_notifications = SimpleLazyObject(badge_count)
    notification_in_app_enabled = SimpleLazyObject(in_app_preference)
    return {
        "nav_active": _nav_active(request.path or "/"),
        "queue_counts": SimpleLazyObject(queue_counts),
        "unread_notifications": unread_notifications,
        "notification_in_app_enabled": notification_in_app_enabled,
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
