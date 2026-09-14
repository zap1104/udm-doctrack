from datetime import timedelta

from django.conf import settings
from django.db import models
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


def chase_unreceived_and_overdue():
    """Raise the two time-based notices: unreceived documents, and overdue ones.

    Both are things that become true by the passage of time rather than by
    anybody acting, so neither can be raised at the moment of an action the way
    ROUTED and RECEIVED are. They are computed here instead, on the same daily
    schedule as the pruning.

    Both are idempotent: an office is told once per record per outstanding
    condition, not once per run. A queue that re-notifies every night trains
    people to ignore it, which costs more than the notice is worth.
    """
    from apps.tracking.models import COMPLETED_STATUSES, RoutingStep, TrackingRecord

    from .notifications import notify_office

    now = timezone.now()
    nudge_after = now - timedelta(days=settings.UNRECEIVED_NUDGE_DAYS)
    raised = {"unreceived": 0, "overdue": 0}

    # --- still not received: told to the office that sent it ----------------
    stale = (
        RoutingStep.objects.filter(
            received_at__isnull=True,
            sent_at__lt=nudge_after,
            batch=models.F("record__current_batch"),
            from_office__isnull=False,
        )
        .exclude(record__status__in=COMPLETED_STATUSES)
        .select_related("record", "from_office", "to_office")
    )
    for step in stale:
        already = Notification.objects.filter(
            tracking_record=step.record,
            office=step.from_office,
            kind=Notification.Kind.UNRECEIVED,
            resolved_at__isnull=True,
        ).exists()
        if already:
            continue
        notify_office(
            step.from_office,
            kind=Notification.Kind.UNRECEIVED,
            title="A document you sent has still not been received",
            message=(
                f"{step.record.tracking_number} was sent to {step.to_office.name} on "
                f"{timezone.localtime(step.sent_at):%d %b %Y} and nobody has confirmed it."
            ),
            url=step.record.get_absolute_url(),
            tracking_record=step.record,
        )
        raised["unreceived"] += 1

    # --- past the deadline: told to whoever is holding it -------------------
    overdue = (
        TrackingRecord.objects.filter(due_at__lt=now, current_office__isnull=False)
        .exclude(status__in=COMPLETED_STATUSES)
        .select_related("current_office")
    )
    for record in overdue:
        already = Notification.objects.filter(
            tracking_record=record,
            office=record.current_office,
            kind=Notification.Kind.OVERDUE,
            resolved_at__isnull=True,
        ).exists()
        if already:
            continue
        notify_office(
            record.current_office,
            kind=Notification.Kind.OVERDUE,
            title="A document with your office is past its deadline",
            message=(
                f"{record.tracking_number} was due "
                f"{timezone.localtime(record.due_at):%d %b %Y}."
            ),
            url=record.get_absolute_url(),
            tracking_record=record,
        )
        raised["overdue"] += 1

    return raised
