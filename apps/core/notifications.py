from __future__ import annotations

from .models import Notification, NotificationPreference, NotificationRead


def notify_office(office, *, kind, title, message, url="", tracking_record=None, document=None):
    if office is None:
        return None
    return Notification.objects.create(
        office=office, kind=kind, title=title[:160], message=message[:255], url=url[:255],
        tracking_record=tracking_record, document=document,
    )


def unread_for(user):
    return Notification.objects.unread_for(user)


def unread_count(user) -> int:
    return unread_for(user).count()


def mark_read(notification, user):
    if notification.office_id != user.office_id:
        return False
    NotificationRead.objects.get_or_create(notification=notification, user=user)
    return True


def ensure_preferences(user):
    preference, _ = NotificationPreference.objects.get_or_create(user=user)
    return preference
