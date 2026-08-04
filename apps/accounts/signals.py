"""Audit trail for sign-in / sign-out events."""

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.utils import log_action


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    User = user.__class__
    User.objects.filter(pk=user.pk).update(last_seen_at=timezone.now())
    log_action(AuditLog.Action.LOGIN, f"{user} signed in", actor=user, target=user, request=request)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        log_action(AuditLog.Action.LOGOUT, f"{user} signed out", actor=user, target=user, request=request)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    username = (credentials or {}).get("username", "unknown")
    log_action(
        AuditLog.Action.LOGIN_FAILED,
        f"Failed sign-in for “{username}”",
        target_type="User",
        target_id=str(username)[:64],
        request=request,
    )
