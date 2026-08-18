from __future__ import annotations

from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .models import Notification, NotificationPreference, NotificationRead


def _safe_url(url: str) -> str:
    value = str(url or "").strip()
    if not value or not value.startswith("/") or value.startswith("//"):
        return ""
    if not url_has_allowed_host_and_scheme(value, allowed_hosts=set(), require_https=False):
        return ""
    return value[:255]


def _notification_kwargs(*, kind, title, message, url="", tracking_record=None, document=None):
    return {
        "kind": kind,
        "title": title[:160],
        "message": message[:255],
        "url": _safe_url(url),
        "tracking_record": tracking_record,
        "document": document,
    }


def notify_office(office, *, kind, title, message, url="", tracking_record=None, document=None):
    if office is None:
        return None
    return Notification.objects.create(
        office=office,
        **_notification_kwargs(
            kind=kind,
            title=title,
            message=message,
            url=url,
            tracking_record=tracking_record,
            document=document,
        ),
    )


def notify_offices(offices, *, kind, title, message, url="", tracking_record=None, document=None):
    offices = [office for office in offices if office is not None]
    if not offices:
        return []
    kwargs = _notification_kwargs(
        kind=kind,
        title=title,
        message=message,
        url=url,
        tracking_record=tracking_record,
        document=document,
    )
    return Notification.objects.bulk_create([Notification(office=office, **kwargs) for office in offices])


def unread_for(user):
    return Notification.objects.unread_for(user)


def unread_count(user) -> int:
    return unread_for(user).count()


def in_app_enabled(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return NotificationPreference.objects.filter(user=user).values_list("in_app_enabled", flat=True).first() is not False


def mark_read(notification, user):
    if notification.office_id != user.office_id:
        return False
    NotificationRead.objects.get_or_create(notification=notification, user=user)
    return True


def mark_all_read(user, notifications=None) -> int:
    if not getattr(user, "is_authenticated", False) or not user.office_id:
        return 0
    queryset = notifications or Notification.objects.filter(office_id=user.office_id)
    notification_ids = queryset.filter(office_id=user.office_id).values_list("pk", flat=True)
    now = timezone.now()
    rows = [NotificationRead(notification_id=pk, user=user, read_at=now) for pk in notification_ids]
    NotificationRead.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def resolve_for_record(record, *, kinds=None):
    queryset = Notification.objects.filter(tracking_record=record, resolved_at__isnull=True)
    if kinds:
        queryset = queryset.filter(kind__in=kinds)
    return queryset.update(resolved_at=timezone.now())


def ensure_preferences(user):
    preference, _ = NotificationPreference.objects.get_or_create(user=user)
    return preference
