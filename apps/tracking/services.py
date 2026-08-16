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
    MAX_INSTRUCTIONS_CHARS,
    MAX_NOTE_CHARS,
    MAX_REMARK_CHARS,
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
def _one_of(value, choices, field_name: str, default: str) -> str:
    """Reject a code that is not on the model's own choice list.

    `Model.objects.create()` does not check choices, so an unrecognised value is
    stored happily and only shows itself later as a raw code where a label
    should be — `get_priority_display()` echoing "HIGH" back at the reader
    because HIGH was never a priority this system has. Catching it here means
    the caller that got it wrong is the thing that fails, not the screen.
    """
    if not value:
        return default
    valid = {code for code, _label in choices}
    if value not in valid:
        raise ValidationError(
            f"“{value}” is not a valid {field_name}. Choose one of: {', '.join(sorted(valid))}."
        )
    return value


@transaction.atomic
def create_draft_record(*, user, subject, instructions, document_type=None, remarks="",
                        classification=None, priority=None, due_at=None, originating_office=None,
                        requested_action=""):
    office = originating_office or user.office
    if office is None:
        raise ValidationError("Your account has no office, so it cannot originate a document.")

    record = TrackingRecord.objects.create(
        tracking_number=generate_tracking_number(office),
        subject=subject.strip(),
        document_type=document_type,
        classification=_one_of(
            classification,
            TrackingRecord.Classification.choices,
            "classification",
            TrackingRecord.Classification.INTERNAL,
        ),
        priority=_one_of(
            priority, TrackingRecord.Priority.choices, "priority", TrackingRecord.Priority.NORMAL
        ),
        requested_action=requested_action or "",
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
def sending_office(record, user):
    """Which office a forward or a return is actually leaving.

    Not simply `user.office`. Ordinary staff can only act on a document their
    own office has confirmed receipt of (see `TrackingRecord.can_user_act`), so
    for them the two are always the same answer. A system administrator can act
    for *any* office, and taking their own office there wrote a step claiming
    the document left Records when it was in fact sitting in Supply — a false
    entry in the one history this system exists to keep honest, and it moved
    `current_office` to the administrator's office as a side effect.

    An administrator with no office at all was worse still: `from_office` came
    out None, which erased the record's location entirely (the Outgoing queue
    joins on it) and slipped past the guard that stops a document being sent to
    the office already holding it.
    """
    if user.office_id and record.has_custody(user.office):
        return user.office
    return record.current_office or user.office


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

    if action == RoutingStep.Action.SEND:
        if record.status != Status.DRAFT:
            raise ValidationError("Only a draft can be sent for the first time.")
        from_office = record.originating_office
    else:
        from_office = sending_office(record, user)

    if from_office is None:
        raise ValidationError(
            "This record has no office to send it from. Assign the acting user to an office, "
            "or have the office currently holding the document route it."
        )

    # Guard against a step whose from_office and to_office are the same office.
    # This used to fire only when the sender was the *sole* recipient, so
    # picking "own office + another office" slipped through and wrote a
    # self-addressed step: the office then sat in its own inbox waiting to
    # receive a document it had never let go of. It also used to be skipped
    # entirely whenever from_office came out None, which is precisely when the
    # sender was unknown and the guard was needed most.
    if any(office.pk == from_office.pk for office in offices):
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
                instructions=(instructions or record.instructions)[:MAX_INSTRUCTIONS_CHARS],
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
        record.status = Status.PENDING_RECEIPT
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
    step.receipt_note = (note or "")[:MAX_NOTE_CHARS]
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
    # Backstop for callers that are not RemarkForm — seed data, the self check,
    # anything added later. The detail column is a TextField, so without this a
    # remark had no ceiling at all and a pasted document would be replayed on
    # the record page for the rest of the record's life.
    remark = remark[:MAX_REMARK_CHARS]
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
    record.completion_note = (note or "")[:MAX_NOTE_CHARS]
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


def outgoing_for(user):
    """Documents this office sent that nobody has confirmed yet.

    Named for the queue it fills ("Outgoing") rather than for the status the
    records carry, which is what `in_transit_from` used to do — the status is
    called Pending receipt now, and the phrase "in transit" is no longer
    vocabulary this system uses anywhere a reader can see.
    """
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


#: Queue names the Tracking page's `?scope=` links use.
SCOPE_INBOX = "inbox"
SCOPE_AWAITING = "awaiting"
SCOPE_CUSTODY = "custody"
SCOPE_SENT = "sent"
SCOPE_MINE = "mine"


def apply_scope(records, scope, user):
    """Narrow a record queryset to one of the Tracking page's queues.

    Kept here rather than inline in the view so the queues have one definition,
    and so every office-based queue gets the same guard: a user with no office
    matches nothing, instead of `to_office_id=None` quietly matching no rows in
    a way that looks like an empty database.
    """
    if scope == SCOPE_AWAITING:
        return awaiting_receipt(records, user)
    if scope == SCOPE_MINE:
        return records.filter(created_by=user)
    if scope not in {SCOPE_INBOX, SCOPE_CUSTODY, SCOPE_SENT}:
        return records

    if not user.office_id:
        return records.none()
    if scope == SCOPE_INBOX:
        return records.filter(
            routing_steps__to_office_id=user.office_id,
            routing_steps__received_at__isnull=True,
            routing_steps__batch=F("current_batch"),
        )
    if scope == SCOPE_CUSTODY:
        return records.filter(current_office_id=user.office_id)
    return records.filter(
        routing_steps__from_office_id=user.office_id,
        routing_steps__received_at__isnull=True,
        routing_steps__batch=F("current_batch"),
    )


def awaiting_receipt(records, user):
    """Documents somebody still has to confirm receipt of.

    One definition for everybody: anything the user can see that still has an
    unconfirmed recipient in its current batch. `visible_to` has already
    narrowed the set to records their office actually touched, so this needs no
    role split of its own.

    It used to have one, and that is what made this queue useless for most
    people: ordinary users were quietly redirected to their own inbox, so
    "Pending Receipt" returned exactly the same rows as "Incoming" and the two
    links sat next to each other doing the same thing. The office waiting on a
    document it *sent* could not see that it was still unconfirmed — which is
    the one question this queue exists to answer.

    Broader than filtering on AWAITING_RECEIPT_STATUSES, deliberately: when a
    batch goes to several offices and only one confirms, the record reads
    RECEIVED while a recipient still owes a receipt. That record belongs here.
    """
    if not user.is_authenticated:
        return records.none()
    return records.filter(
        routing_steps__received_at__isnull=True,
        routing_steps__batch=F("current_batch"),
    ).exclude(status=Status.COMPLETED)


def annotate_can_confirm(records, user) -> None:
    """Set `can_confirm_now` on each record in one query.

    `record.can_user_confirm_receipt(user)` asks the database per record, which
    on a twenty-row page is twenty queries for a question one `IN` clause can
    answer. The lists that show a Confirm Receipt button use this instead.
    """
    if not records:
        return
    if not user.is_authenticated or not user.office_id:
        for record in records:
            record.can_confirm_now = False
        return

    # A record is confirmable when the *current* batch still has an unreceived
    # step addressed to this office — the same rule as pending_step_for_office.
    pending = set(
        RoutingStep.objects.filter(
            record__in=records,
            to_office_id=user.office_id,
            received_at__isnull=True,
            batch=F("record__current_batch"),
        ).values_list("record_id", flat=True)
    )
    for record in records:
        record.can_confirm_now = record.pk in pending
