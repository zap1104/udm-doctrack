"""Security helpers that do not retain submitted passwords or raw addresses."""

import hashlib
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone


def allow_sensitive_request(scope, identity, *, limit, seconds):
    from .models import SecurityThrottle

    key = hashlib.sha256(f"{scope}:{identity}".encode()).hexdigest()
    now = timezone.now()
    with transaction.atomic():
        counter, _ = SecurityThrottle.objects.select_for_update().get_or_create(key=key, defaults={"expires_at": now + timedelta(seconds=seconds)})
        if counter.expires_at <= now:
            counter.attempts = 0
            counter.expires_at = now + timedelta(seconds=seconds)
        if counter.attempts >= limit:
            return False
        counter.attempts += 1
        counter.save(update_fields=["attempts", "expires_at"])
    return True


def redact_secrets(value):
    text = str(value)
    secrets = [settings.SECRET_KEY, settings.EMAIL_HOST_PASSWORD, settings.OCR_SPACE_API_KEY, settings.AZURE_DOCINT_KEY]
    secrets.extend(db.get("PASSWORD", "") for db in settings.DATABASES.values())
    secrets.extend(settings.STORAGES["default"].get("OPTIONS", {}).get(name, "") for name in ("secret_key", "account_key"))
    for secret in sorted({str(secret) for secret in secrets if secret}, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(password|token|apikey|api_key|secret|signature|sig)([=:\s]+)[^&\s,;]+", r"\1\2[REDACTED]", text)
    text = re.sub(r"(://[^\s:/]+:)[^\s@]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"(/accounts/(?:password-reset|email/verify)/[^/\s]+/)[^/\s\"?]+", r"\1[REDACTED]", text)
    return text


def redact_audit_data(value):
    """Keep credentials out of structured audit rows as well as text logs."""
    if isinstance(value, dict):
        sensitive_keys = {"password", "token", "apikey", "api_key", "secret", "signature", "sig", "authorization"}
        return {
            key: "[REDACTED]" if str(key).lower().replace("-", "_") in sensitive_keys else redact_audit_data(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_audit_data(item) for item in value]
    return redact_secrets(value) if isinstance(value, str) else value


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact_secrets(super().format(record))
