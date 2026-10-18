"""Isolated settings for the systemwide audit; never use for deployment.

Pytest creates a separate PostgreSQL database per named audit worker. Commands
and subprocesses also target that deliberately prefixed database, so they can
never fall back to the developer's business database. Credentials stay in the
existing environment.
"""

import copy
import os
import re
import tempfile
from pathlib import Path

from .settings import *  # noqa: F403

AUDIT_SLOT = os.environ.get("DOCTRACK_AUDIT_SLOT", "main")
if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", AUDIT_SLOT):
    raise ValueError("DOCTRACK_AUDIT_SLOT must be a short lowercase worker name.")
AUDIT_DATABASE = f"test_doctrack_audit_{AUDIT_SLOT}"
DATABASES = copy.deepcopy(DATABASES)  # noqa: F405
DATABASES["default"]["TEST"] = {"NAME": AUDIT_DATABASE}
DATABASES["default"]["NAME"] = AUDIT_DATABASE
DATABASES["default"].setdefault("OPTIONS", {})["connect_timeout"] = 10

SECRET_KEY = "isolated-systemwide-audit-key-not-for-production"
DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MEDIA_ROOT = Path(tempfile.gettempdir()) / "doctrack-systemwide-audit" / AUDIT_SLOT
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
# Never reuse a configured Redis service or its lockout/session cache during
# disposable tests. This table belongs to the isolated database above.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "doctrack_cache",
        "TIMEOUT": 300,
        "OPTIONS": {"MAX_ENTRIES": 20000, "CULL_FREQUENCY": 4},
    },
}
ALLOW_DEMO_SEED = False
OCR_SPACE_API_KEY = ""
AZURE_DOCINT_KEY = ""
