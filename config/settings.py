"""
Django settings for UDM DocTrack / UDM RDMS.

Everything is driven by environment variables (see .env.example).
Optional third-party packages (django-axes, django-csp, django-q2, whitenoise,
django-storages, argon2) are enabled ONLY if they are actually installed, so the
project still starts on a teammate's machine with a partial install.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# .env loading + helpers (no external dependency)
# ---------------------------------------------------------------------------
def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


_load_env_file(BASE_DIR / ".env")


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(str(env(name, default)))
    except (TypeError, ValueError):
        return default


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in str(env(name, default) or "").split(",") if item.strip()]


def has_package(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS",
                         "localhost,127.0.0.1,[::1],testserver")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

SITE_NAME = env("SITE_NAME", "UDM DocTrack")
SITE_LONG_NAME = env(
    "SITE_LONG_NAME", "UDM Records and Document Management System")


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.humanize",
    "django.contrib.postgres",
]

if DEBUG and has_package("whitenoise"):
    INSTALLED_APPS.append("whitenoise.runserver_nostatic")
INSTALLED_APPS.append("django.contrib.staticfiles")

ENABLE_AXES = env_bool("ENABLE_AXES", True) and has_package("axes")
ENABLE_CSP = env_bool("ENABLE_CSP", False) and has_package("csp")
ENABLE_BACKGROUND_TASKS = env_bool(
    "ENABLE_BACKGROUND_TASKS", False) and has_package("django_q")

if ENABLE_AXES:
    INSTALLED_APPS.append("axes")
if ENABLE_BACKGROUND_TASKS:
    INSTALLED_APPS.append("django_q")

INSTALLED_APPS += [
    "apps.core",
    "apps.accounts",
    "apps.tracking",
    "apps.documents",
    "apps.search",
]


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware"]

if has_package("whitenoise"):
    MIDDLEWARE.append("whitenoise.middleware.WhiteNoiseMiddleware")

MIDDLEWARE += [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.CurrentRequestMiddleware",
]

if ENABLE_CSP:
    MIDDLEWARE.append("csp.middleware.CSPMiddleware")
if ENABLE_AXES:
    # django-axes must be the LAST middleware entry.
    MIDDLEWARE.append("axes.middleware.AxesMiddleware")


TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site_context",
            ],
        },
    },
]


# ---------------------------------------------------------------------------
# Database (PostgreSQL)
# ---------------------------------------------------------------------------
def _database_from_url(url: str) -> dict:
    parsed = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or ""),
        "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
        "OPTIONS": {"sslmode": env("DB_SSLMODE", "prefer")},
    }


DATABASE_URL = env("DATABASE_URL")
if DATABASE_URL:
    DATABASES = {"default": _database_from_url(DATABASE_URL)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", "doctrack"),
            "USER": env("POSTGRES_USER", "doctrack"),
            "PASSWORD": env("POSTGRES_PASSWORD", "doctrack"),
            "HOST": env("POSTGRES_HOST", "127.0.0.1"),
            "PORT": env("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
            "OPTIONS": {"sslmode": env("DB_SSLMODE", "prefer")},
        }
    }


# ---------------------------------------------------------------------------
# Cache
#
# Django's default is a per-process in-memory cache. That is the wrong shape
# for this project twice over: the dev server's auto-reloader empties it on
# every code change, and the three Gunicorn workers in render.yaml would each
# keep their own private copy. Anything cached about sign-in lockouts would
# quietly disappear or disagree between workers.
#
# So the default here is a cache table in PostgreSQL — shared by every worker
# and untouched by restarts, with no extra service to pay for or run. The
# table is created by the apps/core migration, so `manage.py migrate` (which
# the Procfile release step already runs) is all that is needed.
#
# Set REDIS_URL to switch to Redis, which is faster; Django 5 ships the
# backend, so no additional package is required.
# ---------------------------------------------------------------------------
CACHE_TABLE_NAME = "doctrack_cache"
REDIS_URL = env("REDIS_URL")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": CACHE_TABLE_NAME,
            "TIMEOUT": env_int("CACHE_TIMEOUT_SECONDS", 300),
            # The backend's default cap is 300 rows, which it culls a third of
            # at a time — far too small to hold lockout state for a campus.
            "OPTIONS": {"MAX_ENTRIES": env_int("CACHE_MAX_ENTRIES", 20000), "CULL_FREQUENCY": 4},
        }
    }


# ---------------------------------------------------------------------------
# Authentication & passwords
# ---------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = []
if ENABLE_AXES:
    AUTHENTICATION_BACKENDS.append("axes.backends.AxesStandaloneBackend")
AUTHENTICATION_BACKENDS.append("django.contrib.auth.backends.ModelBackend")

PASSWORD_HASHERS = []
if has_package("argon2"):
    PASSWORD_HASHERS.append("django.contrib.auth.hashers.Argon2PasswordHasher")
PASSWORD_HASHERS += [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": env_int("MIN_PASSWORD_LENGTH", 10)},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 60 * 60 * 8)
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool(
    "SESSION_EXPIRE_AT_BROWSER_CLOSE", False)
SESSION_COOKIE_HTTPONLY = True
# HTMX reads the token from the DOM, not the cookie.
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"


# ---------------------------------------------------------------------------
# django-axes (login lockout)
# ---------------------------------------------------------------------------
if ENABLE_AXES:
    AXES_FAILURE_LIMIT = env_int("AXES_FAILURE_LIMIT", 5)
    # Cool-off is computed dynamically so it doubles on each successive lockout
    # for the same account/IP (see apps.accounts.axes_hooks). These two settings
    # are the base and ceiling that callable uses.
    AXES_COOLOFF_BASE_MINUTES = env_int("AXES_COOLOFF_MINUTES", 15)
    AXES_COOLOFF_MAX_MINUTES = env_int("AXES_COOLOFF_MAX_MINUTES", 24 * 60)
    AXES_COOLOFF_TIME = "apps.accounts.axes_hooks.get_progressive_cooloff"
    # A quiet spell forgives the escalation, so an honest user who fumbled
    # their password months ago does not start at a long wait.
    AXES_ESCALATION_DECAY_DAYS = env_int("AXES_ESCALATION_DECAY_DAYS", 7)
    # Off by default: the wait keeps growing per lockout even if a sign-in
    # succeeds in between, and is only forgiven by the decay window above or by
    # `manage.py fix_login`. Turn on to wipe an account's escalation as soon as
    # its owner completes a full sign-in (including any forced password change).
    AXES_ESCALATION_RESET_ON_LOGIN = env_bool("AXES_ESCALATION_RESET_ON_LOGIN", False)
    AXES_RESET_ON_SUCCESS = True
    # axes' own default (True) pushes the lockout's unlock time forward on every
    # retry made *while already locked out* — so a countdown just reflects
    # whenever you last tried again, from wherever you tried it, rather than a
    # fixed wait tied to the username/IP. False freezes the block at the moment
    # it started: retries (from any device) are still refused, but they stop
    # extending it, so the same countdown shows everywhere.
    AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT = False
    AXES_LOCKOUT_TEMPLATE = "accounts/lockout.html"
    # Redirects to accounts:lockout instead of rendering the template inline as
    # the body of the failed POST, so the page has a URL that can be reloaded
    # without resending the login or replaying a cached, frozen countdown.
    AXES_LOCKOUT_CALLABLE = "apps.accounts.axes_hooks.lockout_response"
    AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
    AXES_ENABLE_ADMIN = True
    AXES_VERBOSE = True


# ---------------------------------------------------------------------------
# Content Security Policy (django-csp 3.x style settings)
# ---------------------------------------------------------------------------
if ENABLE_CSP:
    CSP_DEFAULT_SRC = ("'self'",)
    CSP_SCRIPT_SRC = ("'self'", "cdn.jsdelivr.net", "unpkg.com")
    CSP_STYLE_SRC = ("'self'", "'unsafe-inline'", "cdn.jsdelivr.net")
    CSP_FONT_SRC = ("'self'", "cdn.jsdelivr.net", "data:")
    CSP_IMG_SRC = ("'self'", "data:", "blob:")
    CSP_CONNECT_SRC = ("'self'",)
    CSP_FRAME_ANCESTORS = ("'none'",)


# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "Asia/Manila")
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Static & media files
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))

FILE_STORAGE_BACKEND = env("FILE_STORAGE_BACKEND", "local").lower()

_default_storage = {"BACKEND": "django.core.files.storage.FileSystemStorage"}
if FILE_STORAGE_BACKEND == "s3" and has_package("storages"):
    # Cloudflare R2 (S3 compatible) or AWS S3.
    _default_storage = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("S3_BUCKET_NAME", ""),
            "access_key": env("S3_ACCESS_KEY_ID", ""),
            "secret_key": env("S3_SECRET_ACCESS_KEY", ""),
            "endpoint_url": env("S3_ENDPOINT_URL", ""),
            "region_name": env("S3_REGION_NAME", "auto"),
            "default_acl": None,
            "querystring_auth": True,
            "querystring_expire": env_int("SIGNED_URL_TTL_SECONDS", 900),
            "file_overwrite": False,
        },
    }
elif FILE_STORAGE_BACKEND == "azure" and has_package("storages"):
    _default_storage = {
        "BACKEND": "storages.backends.azure_storage.AzureStorage",
        "OPTIONS": {
            "account_name": env("AZURE_ACCOUNT_NAME", ""),
            "account_key": env("AZURE_ACCOUNT_KEY", ""),
            "azure_container": env("AZURE_CONTAINER", "doctrack"),
            "expiration_secs": env_int("SIGNED_URL_TTL_SECONDS", 900),
            "overwrite_files": False,
        },
    }

_static_storage = {
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
if not DEBUG and has_package("whitenoise"):
    _static_storage = {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}

STORAGES = {"default": _default_storage, "staticfiles": _static_storage}

DATA_UPLOAD_MAX_MEMORY_SIZE = env_int("MAX_UPLOAD_MB", 25) * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
MAX_UPLOAD_MB = env_int("MAX_UPLOAD_MB", 25)
ALLOWED_UPLOAD_EXTENSIONS = env_list(
    "ALLOWED_UPLOAD_EXTENSIONS",
    "pdf,doc,docx,xls,xlsx,ppt,pptx,txt,csv,jpg,jpeg,png,tif,tiff",
)
SIGNED_URL_TTL_SECONDS = env_int("SIGNED_URL_TTL_SECONDS", 900)


# ---------------------------------------------------------------------------
# Background jobs (django-q2, ORM broker: no Redis required)
# ---------------------------------------------------------------------------
if ENABLE_BACKGROUND_TASKS:
    Q_CLUSTER = {
        "name": "doctrack",
        "workers": env_int("Q_WORKERS", 2),
        "timeout": 600,
        "retry": 900,
        "queue_limit": 50,
        "bulk": 5,
        "orm": "default",
        "save_limit": 250,
        "catch_up": False,
    }


# ---------------------------------------------------------------------------
# Email (password resets / notifications)
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL",
                         "UDM DocTrack <no-reply@udm.edu.ph>")


# ---------------------------------------------------------------------------
# Production hardening (only when DEBUG is off)
# ---------------------------------------------------------------------------
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"


# ---------------------------------------------------------------------------
# DocTrack domain settings
# ---------------------------------------------------------------------------
TRACKING_NUMBER_PREFIX = env("TRACKING_NUMBER_PREFIX", "UDM-OVPA")
TRACKING_NUMBER_SEQUENCE_WIDTH = env_int("TRACKING_NUMBER_SEQUENCE_WIDTH", 4)
DEFAULT_ACTION_DUE_DAYS = env_int("DEFAULT_ACTION_DUE_DAYS", 3)

# Search tuning — every number here is documented in docs/SEARCH_DESIGN.md
SEARCH_MIN_RELEVANCE_DEFAULT = env_int("SEARCH_MIN_RELEVANCE_DEFAULT", 75)
SEARCH_RANK_SATURATION_K = float(env("SEARCH_RANK_SATURATION_K", "0.06"))
SEARCH_WEIGHT_TEXT = float(env("SEARCH_WEIGHT_TEXT", "0.55"))
SEARCH_WEIGHT_FUZZY = float(env("SEARCH_WEIGHT_FUZZY", "0.20"))
SEARCH_WEIGHT_FIELD = float(env("SEARCH_WEIGHT_FIELD", "0.25"))
SEARCH_ENABLE_TRIGRAM = env_bool("SEARCH_ENABLE_TRIGRAM", True)
SEARCH_RESULT_LIMIT = env_int("SEARCH_RESULT_LIMIT", 200)
SEARCH_CONFIG = env("SEARCH_CONFIG", "english")

# Text extraction / OCR
# auto | textlayer | ocrspace | azure | none
OCR_BACKEND = env("OCR_BACKEND", "auto")
OCR_SPACE_API_KEY = env("OCR_SPACE_API_KEY", "")
OCR_SPACE_ENDPOINT = env("OCR_SPACE_ENDPOINT",
                         "https://api.ocr.space/parse/image")
AZURE_DOCINT_ENDPOINT = env("AZURE_DOCINT_ENDPOINT", "")
AZURE_DOCINT_KEY = env("AZURE_DOCINT_KEY", "")
OCR_MAX_CHARS = env_int("OCR_MAX_CHARS", 200000)

# Metadata suggestion engine
# rules | none | ai (future)
SUGGESTION_ENGINE = env("SUGGESTION_ENGINE", "rules")
SUGGESTION_MIN_CONFIDENCE = float(env("SUGGESTION_MIN_CONFIDENCE", "0.35"))


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "doctrack": {"level": env("LOG_LEVEL", "INFO"), "handlers": ["console"], "propagate": False},
    },
}

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"
