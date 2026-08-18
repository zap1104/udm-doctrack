from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import Notification


def prune_notifications():
    """Resolve stale informational alerts and prune only old resolved UI rows."""
    now = timezone.now()
    informational_cutoff = now - timedelta(days=settings.NOTIFICATION_INFO_RESOLVE_DAYS)
    resolved_count = Notification.objects.filter(
        kind__in=[Notification.Kind.RECEIVED, Notification.Kind.COMPLETED, Notification.Kind.SHARED],
        resolved_at__isnull=True,
        created_at__lt=informational_cutoff,
    ).update(resolved_at=now)

    retention_cutoff = now - timedelta(days=settings.NOTIFICATION_RETENTION_DAYS)
    deleted_count, _details = Notification.objects.filter(
        resolved_at__isnull=False,
        resolved_at__lt=retention_cutoff,
    ).delete()
    return {"resolved": resolved_count, "deleted": deleted_count}
