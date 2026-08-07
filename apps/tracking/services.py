"""Business rules for document tracking.

Views never edit tracking models directly — they call these functions, so the
rules (append-only history, explicit receipt, audit logging) hold everywhere.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.utils import checksum_of, log_action, truncate, validate_upload

from .models import (
    ACTIVE_STATUSES,
    Attachment,
    RecordAccessGrant,
    RecordActivity,
    RoutingStep,
    Status,
    TrackingNumberSequence,
    TrackingRecord,
)

logger = logging.getLogger("doctrack")

#: Distinguishes "caller said nothing about a deadline" (fall back to due_days)
#: from "caller explicitly said there is no deadline" (due_at=None). A plain
#: None default would silently turn the latter into the former.
_UNSET = object()


# ---------------------------------------------------------------------------
# Tracking numbers
# ---------------------------------------------------------------------------
@transaction.atomic
def generate_tracking_number(office, when=None) -> str:
    """UDM-OVPA-<OFFICE>-<YEAR>-<MONTH>-<SEQ>, unique even with simultaneous users."""
    when = when or timezone.localtime()
    sequence, _ = TrackingNumberSequence.objects.select_for_update().get_or_create(
        office=office, year=when.year, month=when.month
    )
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    width = settings.TRACKING_NUMBER_SEQUENCE_WIDTH
    return (
        f"{settings.TRACKING_NUMBER_PREFIX}-{office.code}-{when.year}-"
        f"{when.month:02d}-{sequence.last_number:0{width}d}"
    )


# ---------------------------------------------------------------------------
# Timeline helper
# ---------------------------------------------------------------------------
def add_activity(record, event, message, *, actor=None, detail="", batch=None) -> RecordActivity:
    return RecordActivity.objects.create(
        record=record,
        event=event,
        batch=record.current_batch if batch is None else batch,
        actor=actor,
        actor_office=getattr(actor, "office", None),
        message=message[:255],
        detail=detail or "",
    )


# ---------------------------------------------------------------------------
# Create / attach
# ---------------------------------------------------------------------------
@transaction.atomic
def create_draft_record(*, user, subject, instructions, document_type=None, remarks="",
                        classification=None, priority=None, due_at=None, originating_office=None):
    office = originating_office or user.office
    if office is None:
        raise ValidationError("Your account has no office, so it cannot originate a document.")

    record = TrackingRecord.objects.create(
        tracking_number=generate_tracking_number(office),
        subject=subject.strip(),
        document_type=document_type,
        classification=classification or TrackingRecord.Classification.INTERNAL,
        priority=priority or TrackingRecord.Priority.NORMAL,
        originating_office=office,
        created_by=user,
        instructions=instructions.strip(),
        remarks=(remarks or "").strip(),
        status=Status.DRAFT,
        current_office=office,
        current_holder=user,
        current_batch=0,
        due_at=due_at,
    )
    add_activity(record, RecordActivity.Event.CREATED, f"Record created by {user.display_name}", actor=user)
    log_action(AuditLog.Action.CREATE, f"Created {record.tracking_number}", actor=user, target=record)
    return record


@transaction.atomic
def attach_files(record, files, *, user, note="", routing_step=None) -> list[Attachment]:
    created: list[Attachment] = []
    for uploaded in files:
        validate_upload(uploaded)
        attachment = Attachment.objects.create(
            record=record,
            routing_step=routing_step,
            file=uploaded,
            original_name=uploaded.name[:255],
            content_type=getattr(uploaded, "content_type", "") or "",
            size=uploaded.size or 0,
            checksum=checksum_of(uploaded),
            note=note[:255],
            uploaded_by=user,
        )
        created.append(attachment)
        add_activity(
            record,
            RecordActivity.Event.ATTACHMENT,
            f"{user.display_name} attached {attachment.original_name}",
            actor=user,
        )
    if created:
        record.touch_movement()
    return created


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
@transaction.atomic
def route_record(record, offices, *, user, instructions="", action=RoutingStep.Action.SEND,
                 due_days=None, due_at=_UNSET, remark="") -> list[RoutingStep]:
    """Send the record to one or more offices. Creates a new batch of steps.

    The deadline can arrive either way: `due_at` is an exact datetime picked
    from a calendar (pass None for "no deadline"), `due_days` is the older
    relative form. `due_at` wins when both are given.
    """
    offices = [office for office in offices if office is not None]
    if not offices:
        raise ValidationError("Select at least one receiving office.")
    if record.status == Status.COMPLETED:
        raise ValidationError("This record is completed. Reopen it before routing again.")

    from_office = user.office if action != RoutingStep.Action.SEND else record.originating_office
    if action == RoutingStep.Action.SEND and record.status != Status.DRAFT:
        raise ValidationError("Only a draft can be sent for the first time.")

    # Guard against a step whose from_office and to_office are the same office.
    # This used to fire only when the sender was the *sole* recipient, so
    # picking "own office + another office" slipped through and wrote a
    # self-addressed step: the office then sat in its own inbox waiting to
    # receive a document it had never let go of.
    from_office_pk = getattr(from_office, "pk", None)
    if from_office_pk is not None and any(office.pk == from_office_pk for office in offices):
        raise ValidationError("A document cannot be routed to the office that is sending it.")

    batch = (record.routing_steps.aggregate(value=Max("batch"))["value"] or 0) + 1
    sequence = record.routing_steps.aggregate(value=Max("sequence"))["value"] or 0

    if due_at is _UNSET:
        due_days = settings.DEFAULT_ACTION_DUE_DAYS if due_days is None else due_days
        due_at = timezone.now() + timedelta(days=int(due_days)) if due_days else None

    steps: list[RoutingStep] = []
    for office in offices:
        sequence += 1
        steps.append(
            RoutingStep.objects.create(
                record=record,
                sequence=sequence,
                batch=batch,
                action=action,
                from_office=from_office,
                to_office=office,
                sent_by=user,
                instructions=(instructions or record.instructions)[:5000],
                due_at=due_at,
            )
        )

    record.current_batch = batch
    record.due_at = due_at
    record.last_movement_at = timezone.now()
    if record.status == Status.DRAFT:
        # recalculate_status() deliberately does nothing for DRAFT/COMPLETED
        # records (see its docstring) so that calling it elsewhere can never
        # accidentally pull a draft out of editing. That guard would also
        # skip the very first routing of a record — which is exactly the
        # transition out of DRAFT we need it to compute — so it is cleared
        # here first. The value is a placeholder; recalculate_status()
        # immediately below computes the real one from the routing steps.
        record.status = Status.IN_TRANSIT
    record.save(update_fields=["current_batch", "due_at", "last_movement_at", "status", "updated_at"])
    # recalculate_status() is the single source of truth for status,
    # current_office and current_holder — computing them again here
    # previously duplicated that logic and had drifted out of sync with it.
    record.recalculate_status()

    office_labels = ", ".join(office.code for office in offices)
    verb = {"SEND": "Routed", "FORWARD": "Forwarded", "RETURN": "Returned"}[action]
    add_activity(
        record,
        {
            RoutingStep.Action.SEND: RecordActivity.Event.SENT,
            RoutingStep.Action.FORWARD: RecordActivity.Event.FORWARDED,
            RoutingStep.Action.RETURN: RecordActivity.Event.RETURNED,
        }[action],
        f"{verb} to {office_labels} by {user.display_name}",
        actor=user,
        detail=remark or instructions,
        batch=batch,
    )
    log_action(
        AuditLog.Action.ROUTE,
        f"{verb} {record.tracking_number} to {office_labels}",
        actor=user,
        target=record,
        extra={"offices": office_labels, "action": action},
    )
    return steps


@transaction.atomic
def confirm_receipt(record, *, user, note="") -> RoutingStep:
    """Explicit receipt. Server time only — never a value typed by the user."""
    if not user.office_id:
        raise PermissionDenied("Your account has no office, so it cannot receive documents.")

    step = (
        RoutingStep.objects.select_for_update()
        .filter(record=record, batch=record.current_batch, to_office_id=user.office_id, received_at__isnull=True)
        .first()
    )
    if step is None:
        raise ValidationError("There is nothing to confirm for your office on this record.")

    step.received_by = user
    step.received_at = timezone.now()
    step.receipt_note = (note or "")[:2000]
    step.save(update_fields=["received_by", "received_at", "receipt_note"])

    if record.first_received_at is None:
        record.first_received_at = step.received_at
        record.save(update_fields=["first_received_at", "updated_at"])

    add_activity(
        record,
        RecordActivity.Event.RECEIVED,
        f"Receipt confirmed by {user.display_name} ({user.office.code})",
        actor=user,
        detail=note or "",
    )
    record.recalculate_status()
    record.touch_movement()
    log_action(
        AuditLog.Action.RECEIVE,
        f"{user.office.code} confirmed receipt of {record.tracking_number}",
        actor=user,
        target=record,
    )
    return step


@transaction.atomic
def add_remark(record, *, user, remark) -> RecordActivity:
    remark = (remark or "").strip()
    if not remark:
        raise ValidationError("Write the remark before saving.")
    activity = add_activity(
        record, RecordActivity.Event.REMARK, f"{user.display_name} added a remark", actor=user, detail=remark
    )
    record.recalculate_status()
    record.touch_movement()
    log_action(
        AuditLog.Action.UPDATE,
        f"Remark on {record.tracking_number}: {truncate(remark, 80)}",
        actor=user,
        target=record,
    )
    return activity


@transaction.atomic
def complete_record(record, *, user, note="") -> TrackingRecord:
    if record.status == Status.COMPLETED:
        return record
    record.status = Status.COMPLETED
    record.completed_at = timezone.now()
    record.completed_by = user
    record.completion_note = (note or "")[:2000]
    record.last_movement_at = record.completed_at
    record.save(
        update_fields=[
            "status", "completed_at", "completed_by", "completion_note", "last_movement_at", "updated_at",
        ]
    )
    add_activity(
        record,
        RecordActivity.Event.COMPLETED,
        f"Marked completed by {user.display_name}",
        actor=user,
        detail=note or "",
    )
    log_action(AuditLog.Action.COMPLETE, f"Completed {record.tracking_number}", actor=user, target=record)
    return record


@transaction.atomic
def reopen_record(record, *, user, reason="") -> TrackingRecord:
    """Administrators can reopen a completed record; the history is kept intact."""
    if record.status != Status.COMPLETED:
        return record
    record.status = Status.RECEIVED
    record.completed_at = None
    record.completed_by = None
    record.save(update_fields=["status", "completed_at", "completed_by", "updated_at"])
    record.recalculate_status()
    add_activity(record, RecordActivity.Event.REMARK, f"Reopened by {user.display_name}", actor=user, detail=reason)
    log_action(AuditLog.Action.UPDATE, f"Reopened {record.tracking_number}", actor=user, target=record)
    return record


@transaction.atomic
def grant_access(record, *, user, office=None, target_user=None, reason="") -> RecordAccessGrant:
    grant, created = RecordAccessGrant.objects.get_or_create(
        record=record,
        office=office,
        user=target_user,
        defaults={"granted_by": user, "reason": reason[:255]},
    )
    if created:
        add_activity(
            record,
            RecordActivity.Event.ACCESS,
            f"Access granted to {office or target_user} by {user.display_name}",
            actor=user,
            detail=reason,
        )
        log_action(
            AuditLog.Action.PERMISSION,
            f"Granted access to {record.tracking_number} for {office or target_user}",
            actor=user,
            target=record,
        )
    return grant


# ---------------------------------------------------------------------------
# Queue helpers used by the dashboard
# ---------------------------------------------------------------------------
def inbox_for(user):
    """Documents waiting for this user's office to confirm receipt."""
    if not user.is_authenticated or not user.office_id:
        return TrackingRecord.objects.none()
    return (
        TrackingRecord.objects.visible_to(user)
        .filter(
            routing_steps__to_office_id=user.office_id,
            routing_steps__received_at__isnull=True,
            routing_steps__batch=F("current_batch"),
        )
        .exclude(status=Status.COMPLETED)
        .with_related()
        .distinct()
    )


def in_custody_for(user):
    """Documents this office has received and still has to act on."""
    if not user.is_authenticated or not user.office_id:
        return TrackingRecord.objects.none()
    return (
        TrackingRecord.objects.visible_to(user)
        .filter(
            status__in=[Status.RECEIVED, Status.IN_PROCESS],
            routing_steps__to_office_id=user.office_id,
            routing_steps__received_at__isnull=False,
            routing_steps__batch=F("current_batch"),
        )
        .filter(current_office_id=user.office_id)
        .with_related()
        .distinct()
    )


def in_transit_from(user):
    """Documents this office sent that nobody has confirmed yet."""
    if not user.is_authenticated or not user.office_id:
        return TrackingRecord.objects.none()
    return (
        TrackingRecord.objects.visible_to(user)
        .filter(
            routing_steps__from_office_id=user.office_id,
            routing_steps__received_at__isnull=True,
            routing_steps__batch=F("current_batch"),
        )
        .exclude(status=Status.COMPLETED)
        .with_related()
        .distinct()
    )


def overdue_for(user):
    return TrackingRecord.objects.visible_to(user).overdue().with_related().distinct()


def completed_this_year_for(user):
    return (
        TrackingRecord.objects.visible_to(user)
        .filter(status=Status.COMPLETED, completed_at__year=timezone.localdate().year)
        .distinct()
    )


def active_for(user):
    return TrackingRecord.objects.visible_to(user).filter(status__in=ACTIVE_STATUSES).with_related().distinct()
