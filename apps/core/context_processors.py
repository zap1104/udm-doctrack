from pathlib import Path

from django.conf import settings

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
    return {
        "nav_active": _nav_active(request.path or "/"),
        "SITE_NAME": settings.SITE_NAME,
        "SITE_LONG_NAME": settings.SITE_LONG_NAME,
        "USE_LOCAL_VENDOR": _LOCAL_VENDOR,
        "SEARCH_MIN_RELEVANCE_DEFAULT": settings.SEARCH_MIN_RELEVANCE_DEFAULT,
        "MAX_UPLOAD_MB": settings.MAX_UPLOAD_MB,
        "DEBUG": settings.DEBUG,
    }
