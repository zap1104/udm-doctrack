from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from .models import Notification


def chase_external_followups():
    """Chase external cases without changing OVPA routing or its notice totals."""
    from apps.tracking.models import COMPLETED_STATUSES, ExternalTrackingRecord, Status

    from .notifications import notify_office

    now = timezone.now()
    cutoff = now - timedelta(days=settings.UNRECEIVED_NUDGE_DAYS)
    kinds = [Notification.Kind.UNRECEIVED, Notification.Kind.OVERDUE]
    Notification.objects.filter(
        external_record__status__in=[Status.DRAFT, *COMPLETED_STATUSES],
        kind__in=kinds, resolved_at__isnull=True,
    ).update(resolved_at=now)
    candidates = ExternalTrackingRecord.objects.exclude(
        status__in=[Status.DRAFT, *COMPLETED_STATUSES],
    ).order_by("pk").values_list("pk", flat=True)
    raised = {"unreceived": 0, "overdue": 0}
    for record_id in candidates.iterator():
        with transaction.atomic():
            record = ExternalTrackingRecord.objects.select_for_update().filter(pk=record_id).first()
            if record is None:
                continue
            step = record.current_step
            if not step or record.status in {Status.DRAFT, *COMPLETED_STATUSES}:
                Notification.objects.filter(external_record=record, kind__in=kinds,
                                            resolved_at__isnull=True).update(resolved_at=now)
                continue
            pending = step.received_at is None
            unreceived = pending and step.sent_at < cutoff
            overdue = bool(record.due_at and record.due_at < now)
            checks = [
                (Notification.Kind.UNRECEIVED, unreceived, record.responsible_office,
                 "Follow up on an external slip awaiting receipt",
                 f"{record.tracking_number} is awaiting receipt from {step.to_name_snapshot}.", "unreceived"),
                (Notification.Kind.OVERDUE, overdue,
                 (step.to_office if pending else record.current_office) or record.responsible_office,
                 "An external slip is past its deadline",
                 f"{record.tracking_number} is past its deadline. Check its current receipt or work step.", "overdue"),
            ]
            for kind, needed, office, title, message, counter in checks:
                notices = Notification.objects.filter(external_record=record, kind=kind,
                                                       resolved_at__isnull=True)
                if not needed:
                    notices.update(resolved_at=now)
                    continue
                notices.exclude(office=office).update(resolved_at=now)
                if not notices.filter(office=office).update(title=title, message=message[:255]):
                    notify_office(office, kind=kind, title=title, message=message,
                                  url=record.get_absolute_url(), external_record=record)
                    raised[counter] += 1
    return raised


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
    from apps.tracking.models import COMPLETED_STATUSES, RoutingStep, TrackingRecord, overdue_q

    from .notifications import notify_office, overdue_notice_text

    now = timezone.now()
    nudge_after = now - timedelta(days=settings.UNRECEIVED_NUDGE_DAYS)
    raised = {"unreceived": 0, "overdue": 0}

    # Deleted records no longer have a row to lock or an action to chase.
    Notification.objects.filter(
        kind__in=[Notification.Kind.UNRECEIVED, Notification.Kind.OVERDUE],
        tracking_record__isnull=True,
        external_record__isnull=True,
        resolved_at__isnull=True,
    ).update(resolved_at=now)

    # Candidates can become stale while the task waits. Recheck and write under
    # the same record lock used by receipt, routing and completion services.
    stale = (
        RoutingStep.objects.filter(
            received_at__isnull=True,
            sent_at__lt=nudge_after,
            batch=models.F("record__current_batch"),
            from_office__isnull=False,
        )
        .exclude(record__status__in=COMPLETED_STATUSES)
        .values_list("record_id", "from_office_id")
        .order_by("record_id", "from_office_id")
        .distinct()
    )
    nudge_candidates = set(stale)
    nudge_candidates.update(
        Notification.objects.filter(
            kind=Notification.Kind.UNRECEIVED,
            tracking_record__isnull=False,
            resolved_at__isnull=True,
        ).values_list("tracking_record_id", "office_id")
    )
    for record_id, sender_id in sorted(nudge_candidates):
        with transaction.atomic():
            record = TrackingRecord.objects.select_for_update().filter(pk=record_id).first()
            if record is None:
                continue
            notices = Notification.objects.filter(
                tracking_record_id=record_id,
                office_id=sender_id,
                kind=Notification.Kind.UNRECEIVED,
                resolved_at__isnull=True,
            )
            if record.status in COMPLETED_STATUSES:
                notices.update(resolved_at=timezone.now())
                continue
            steps = list(record.current_step_queryset.filter(
                from_office_id=sender_id,
                received_at__isnull=True,
                sent_at__lt=nudge_after,
            ).select_related("from_office", "to_office"))
            if not steps:
                notices.update(resolved_at=timezone.now())
                continue
            recipient_names = ", ".join(dict.fromkeys(step.to_office.name for step in steps))
            oldest_sent = min(step.sent_at for step in steps)
            suffix = f"; oldest endorsement {timezone.localtime(oldest_sent):%d %b %Y}."
            message = f"{record.tracking_number} is awaiting receipt from {recipient_names}{suffix}"
            if len(message) > 255:
                # Codes keep larger recipient lists within the existing limit.
                recipient_codes = ", ".join(dict.fromkeys(step.to_office.code for step in steps))
                message = f"{record.tracking_number} is awaiting receipt from {recipient_codes}{suffix}"
            title = "A document you sent has still not been received"
            if notices.update(title=title, message=message[:255]):
                continue
            notify_office(
                steps[0].from_office,
                kind=Notification.Kind.UNRECEIVED,
                title=title,
                message=message,
                url=record.get_absolute_url(),
                tracking_record=record,
            )
            raised["unreceived"] += 1

    # Every current-batch recipient owes either receipt or work. The cached
    # current_office is only the latest confirmed holder, so it cannot supply
    # all notification targets for a document sent to several offices.
    overdue = TrackingRecord.objects.filter(overdue_q()).values_list("pk").order_by("pk")
    overdue_candidates = {record_id for record_id, in overdue}
    overdue_candidates.update(
        Notification.objects.filter(
            kind=Notification.Kind.OVERDUE,
            tracking_record__isnull=False,
            resolved_at__isnull=True,
        ).values_list("tracking_record_id", flat=True)
    )
    # Earlier completion paths left receipt actions visible. Reconcile only
    # terminal records, under their mutation lock, without deleting notice history.
    overdue_candidates.update(
        Notification.objects.filter(
            kind=Notification.Kind.ROUTED,
            tracking_record__status__in=COMPLETED_STATUSES,
            resolved_at__isnull=True,
        ).values_list("tracking_record_id", flat=True)
    )
    for record_id in sorted(overdue_candidates):
        with transaction.atomic():
            record = TrackingRecord.objects.select_for_update().filter(pk=record_id).first()
            if record is None:
                continue
            if record.status in COMPLETED_STATUSES:
                Notification.objects.filter(
                    tracking_record=record, kind=Notification.Kind.ROUTED,
                    resolved_at__isnull=True,
                ).update(resolved_at=timezone.now())
            notices = Notification.objects.filter(
                tracking_record=record,
                kind=Notification.Kind.OVERDUE,
                resolved_at__isnull=True,
            )
            if not record.is_overdue:
                notices.update(resolved_at=timezone.now())
                continue
            targets = {
                step.to_office_id: step
                for step in record.current_step_queryset.select_related("to_office")
            }
            notices.exclude(office_id__in=targets).update(resolved_at=timezone.now())
            for office_id, step in targets.items():
                title, message = overdue_notice_text(record, awaiting_receipt=step.received_at is None)
                if notices.filter(office_id=office_id).update(title=title, message=message):
                    continue
                notify_office(
                    step.to_office,
                    kind=Notification.Kind.OVERDUE,
                    title=title,
                    message=message,
                    url=record.get_absolute_url(),
                    tracking_record=record,
                )
                raised["overdue"] += 1

    return raised


def prune_security_throttles():
    """Run daily via django-q or the deployment's scheduler."""
    from .models import SecurityThrottle

    count, _ = SecurityThrottle.objects.filter(expires_at__lt=timezone.now()).delete()
    return count
