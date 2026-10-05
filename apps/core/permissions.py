"""Checks for service callers, including stale account and record instances."""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.db import transaction


def active_writer(user):
    """Reload permissions instead of trusting an account cached before suspension."""
    from apps.accounts.models import User

    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_records_staff", False):
        raise PermissionDenied("An active account with permission to make changes is required.")
    actor = User.objects.select_for_update().filter(pk=user.pk, is_active=True).first()
    if actor is None or not actor.can_start_work:
        raise PermissionDenied("Your account cannot make changes. Contact your office administrator.")
    return actor


def locked_mutation(function):
    """Authorize against the current database state and serialize record writes.

    Keep the caller's instance up to date because existing workflows use it for
    their next transition. Never use its old office, batch or status to authorize.
    """
    @wraps(function)
    def wrapped(record, *args, user, **kwargs):
        with transaction.atomic():
            actor = active_writer(user)
            locked = type(record).objects.select_for_update().get(pk=record.pk)
            result = function(locked, *args, user=actor, **kwargs)
            record.refresh_from_db()
            return record if result is locked else result
    return wrapped
