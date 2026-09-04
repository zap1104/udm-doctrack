"""Business rules for document tracking.

Views never edit tracking models directly — they call these functions, so the
rules (append-only history, explicit receipt, audit logging) hold everywhere.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Max, Q
from django.utils import timezone

from apps.core.models import AuditLog, Notification
from apps.core.notifications import notify_office, notify_offices, resolve_for_record
from apps.core.utils import checksum_of, log_action, truncate, validate_upload

from .models import (
    ACTIVE_STATUSES,
    COMPLETED_STATUSES,
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


def refuse_viewers(user, action: str) -> None:
    """Stop a read-only account writing anything.

    Enforced here rather than only in the views, because hiding a button is not
    a permission: the endpoints stay reachable by anyone who knows the URL, and
    a service function is what every path — view, management command, background
    task — actually goes through.
    """
    if getattr(user, "is_viewer", False):
        raise PermissionDenied(
            f"Your account has view-only access and cannot {action}. "
            "Ask your office administrator if you need to make changes."
        )


def ensure_received(record) -> None:
    """Refuse a transition that presumes the document is in somebody's hands.

    A document nobody has confirmed receiving is not being worked on, whatever
    a dropdown says. Without this the status could be driven ahead of the
    custody chain, so the timeline claimed an office was processing a document
    that was, on the record's own evidence, still in transit to it.
    """
    if not record.current_step_queryset.filter(received_at__isnull=False).exists():
        raise ValidationError(
            "This document has not been received yet. Confirm receipt before "
            "marking it In process."
        )


# ---------------------------------------------------------------------------
# Tracking numbers
# ---------------------------------------------------------------------------
#: Prefix of the placeholder a draft carries until it is sent.
#:
#: The column is unique and non-null, and the file storage path is built from
#: it, so a draft cannot simply have no value — but it must not have a *real*
#: one either. See `provisional_tracking_number`.
DRAFT_NUMBER_PREFIX = "DRAFT"


def provisional_tracking_number() -> str:
    """A placeholder for a draft that has not been sent.

    Drafts used to take a real number the moment they were created, which spent
    it: a clerk who started five slips and abandoned four left four gaps in the
    office's sequence. In a records series a gap is not neutral — it reads as
    four documents that existed and cannot be found, which is exactly the
    suspicion this system is meant to remove.

    The number is now issued by `route_record` when the draft is actually sent,
    so the series has no holes and the number's date is the date it entered
    circulation. Until then the record carries one of these, which is never
    displayed (see `TrackingRecord.display_tracking_number`).
    """
    return f"{DRAFT_NUMBER_PREFIX}-{uuid4().hex[:12].upper()}"


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


def log_view(record, *, user) -> RecordActivity | None:
    """Record that somebody opened this document.

    Reading is an act on a confidential record, and for a view-only account it
    is the *only* act — without this, the people whose access is limited to
    looking are the people the history says nothing about.

    Collapsed to one entry per person per VIEW_LOG_DEDUP_MINUTES, because the
    alternative is a row per page load: a clerk refreshing while they work would
    bury the movement history under their own footprints, and the timeline is
    rendered on the page being opened, so each read would lengthen the thing
    being read.

    So these rows count *reading sessions*, not page loads, and they undercount
    on purpose. Do not present a count of them as a number of views.
    """
    if not getattr(user, "is_authenticated", False):
        return None
    already = record.activities.filter(
        event=RecordActivity.Event.VIEWED,
        actor=user,
        created_at__gte=timezone.now() - timedelta(minutes=settings.VIEW_LOG_DEDUP_MINUTES),
    ).exists()
    if already:
        return None
    return add_activity(
        record, RecordActivity.Event.VIEWED, f"{user.display_name} opened the document", actor=user
    )


def log_print(record, *, user) -> RecordActivity:
    """Record that a paper copy of the routing slip was produced.

    Never deduplicated, unlike `log_view`. Each print is a deliberate act that
    puts another copy of a confidential document outside the system, and
    repeated printing of the same record is precisely the pattern the trail
    exists to make visible — collapsing it would erase the signal on the
    grounds that there was too much of it.
    """
    return add_activity(
        record,
        RecordActivity.Event.PRINTED,
        f"{user.display_name} printed the routing slip",
        actor=user,
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
    refuse_viewers(user, "create documents")
    office = originating_office or user.office
    if office is None:
        raise ValidationError("Your account has no office, so it cannot originate a document.")

    record = TrackingRecord.objects.create(
        # Not a real number yet — `route_record` issues that on send.
        tracking_number=provisional_tracking_number(),
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
    if files:
        refuse_viewers(user, "upload files")
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
    refuse_viewers(user, "route documents")
    offices = [office for office in offices if office is not None]
    if not offices:
        raise ValidationError("Select at least one receiving office.")
    if record.status in COMPLETED_STATUSES:
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
    saved_fields = ["current_batch", "due_at", "last_movement_at", "status", "updated_at"]
    if record.status == Status.DRAFT:
        # The number is issued here, on the first send, rather than when the
        # draft was created — so an abandoned draft leaves no gap in the
        # office's series and the number's month is the month it went out.
        record.tracking_number = generate_tracking_number(record.originating_office)
        saved_fields.append("tracking_number")
        # recalculate_status() deliberately does nothing for DRAFT/COMPLETED
        # records (see its docstring) so that calling it elsewhere can never
        # accidentally pull a draft out of editing. That guard would also
        # skip the very first routing of a record — which is exactly the
        # transition out of DRAFT we need it to compute — so it is cleared
        # here first. The value is a placeholder; recalculate_status()
        # immediately below computes the real one from the routing steps.
        record.status = Status.PENDING_RECEIPT
    record.save(update_fields=saved_fields)
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
    notify_offices(
        offices,
        kind=Notification.Kind.ROUTED,
        title="A document is waiting for your office",
        message=f"{record.tracking_number} was routed to the selected office.",
        url=record.get_absolute_url(),
        tracking_record=record,
    )
    return steps


@transaction.atomic
def confirm_receipt(record, *, user, note="") -> RoutingStep:
    """Explicit receipt. Server time only — never a value typed by the user."""
    refuse_viewers(user, "confirm receipt")
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
    Notification.objects.filter(
        tracking_record=record,
        office_id=user.office_id,
        kind=Notification.Kind.ROUTED,
        resolved_at__isnull=True,
    ).update(resolved_at=timezone.now())
    # The sender's "still not received" nudge is answered by this receipt, so it
    # is resolved here rather than left for somebody to dismiss. A chase that
    # stays on screen after the thing was chased is how a queue stops being read.
    Notification.objects.filter(
        tracking_record=record,
        office=step.from_office,
        kind=Notification.Kind.UNRECEIVED,
        resolved_at__isnull=True,
    ).update(resolved_at=timezone.now())
    notify_office(
        step.from_office, kind=Notification.Kind.RECEIVED, title="A document you sent was received",
        message=f"{record.tracking_number} was received by {user.office.name}.",
        url=record.get_absolute_url(), tracking_record=record,
    )
    return step


@transaction.atomic
def bulk_confirm_receipts(records, *, user, note="") -> list[RoutingStep]:
    """Confirm an explicit selection while preserving one receipt event per record."""
    record_ids = [record.pk for record in records]
    if not record_ids:
        raise ValidationError("Select at least one document to receive.")
    locked = {
        record.pk: record
        for record in TrackingRecord.objects.select_for_update().filter(pk__in=record_ids).order_by("pk")
    }
    if len(locked) != len(set(record_ids)):
        raise ValidationError("One of the selected documents is no longer available.")
    return [confirm_receipt(locked[record_id], user=user, note=note) for record_id in record_ids]


@transaction.atomic
def add_remark(record, *, user, remark) -> RecordActivity:
    refuse_viewers(user, "add remarks")
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
def mark_in_process(record, *, user, note="") -> TrackingRecord:
    """Declare that the office holding the document has started work on it.

    Separate from the automatic promotion in `recalculate_status()` — which
    infers In process from a remark or an attachment — so that an office with
    nothing yet to add can still say it has picked the document up.
    """
    refuse_viewers(user, "change the status of documents")
    if record.status in COMPLETED_STATUSES:
        raise ValidationError("This record is completed and its status can no longer change.")
    if record.status == Status.DRAFT:
        raise ValidationError("A draft has not been sent yet, so it cannot be In process.")
    ensure_received(record)

    if record.status == Status.IN_PROCESS:
        return record
    record.status = Status.IN_PROCESS
    record.save(update_fields=["status", "updated_at"])
    add_activity(
        record,
        RecordActivity.Event.REMARK,
        f"{user.display_name} marked the document In process",
        actor=user,
        detail=note or "",
    )
    record.touch_movement()
    log_action(
        AuditLog.Action.UPDATE,
        f"{record.tracking_number} marked In process",
        actor=user,
        target=record,
    )
    return record


@transaction.atomic
def complete_record(record, *, user, note="") -> TrackingRecord:
    """Finish the work. The record stays in Tracking awaiting approval.

    This no longer files anything. Completion is now a claim by the office that
    did the work, and COMPLETED_PENDING_UPLOAD is where that claim waits for an
    administrator to check it — `approve_upload` is what turns it into a
    repository record.
    """
    refuse_viewers(user, "complete documents")
    if record.status in COMPLETED_STATUSES:
        return record
    record.status = Status.COMPLETED_PENDING_UPLOAD
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
    # Finishing the work answers the deadline and the chase alike: neither is
    # outstanding any more, whoever they were addressed to.
    resolve_for_record(
        record,
        kinds=[
            Notification.Kind.RECEIVED,
            Notification.Kind.OVERDUE,
            Notification.Kind.UNRECEIVED,
        ],
    )
    notify_office(
        record.originating_office, kind=Notification.Kind.COMPLETED, title="A document you originated is complete",
        message=f"{record.tracking_number} has been marked completed.",
        url=record.get_absolute_url(), tracking_record=record,
    )
    return record


def approve_upload(record, *, user, tag_names=None, description=""):
    """Approve a finished record into the Document Repository.

    Thin on purpose: the work is `apps.documents.services.archive_tracking_record`,
    which owns the repository side. This exists so that Tracking has the verb —
    approval is a tracking-lifecycle act, and callers here should not have to
    know that filing lives in another app. Imported inside the function because
    the documents app imports this module.
    """
    from apps.documents.services import archive_tracking_record

    refuse_viewers(user, "approve documents into the repository")
    if not record.can_user_approve_upload(user):
        raise PermissionDenied(
            "Only an administrator for this document's office can approve it "
            "into the Document Repository."
        )
    return archive_tracking_record(
        record, user=user, tag_names=tag_names, description=description
    )


@transaction.atomic
def reopen_record(record, *, user, reason="") -> TrackingRecord:
    """Send a completed record back into active tracking. History is kept intact.

    Refuses a record that has already been filed. `is_archived` is cleared all
    the same rather than trusted: a record carrying that flag is excluded from
    `active()`, so reopening one without clearing it would return the record to
    Tracking and leave it invisible there — the same limbo this is meant to
    undo.

    `completion_note` is deliberately left alone. The note explains a
    completion that genuinely happened, and this timeline does not rewrite
    itself; the reopening is recorded as its own entry beneath it.
    """
    refuse_viewers(user, "return documents to tracking")
    if record.status == Status.COMPLETED or record.is_archived or getattr(record, "archived_document", None):
        raise ValidationError(
            "This record has already been approved into the Document Repository "
            "and cannot be returned to tracking."
        )
    if record.status != Status.COMPLETED_PENDING_UPLOAD:
        return record
    record.status = Status.RECEIVED
    record.completed_at = None
    record.completed_by = None
    record.is_archived = False
    record.archived_at = None
    record.save(
        update_fields=[
            "status", "completed_at", "completed_by", "is_archived", "archived_at", "updated_at",
        ]
    )
    # Restores the real state from the routing steps — received, in process, or
    # still awaiting a receipt — rather than assuming the value set above.
    record.recalculate_status()
    add_activity(
        record,
        RecordActivity.Event.REMARK,
        f"Returned to tracking by {user.display_name}",
        actor=user,
        detail=reason,
    )
    # Without this the record keeps the movement time of its completion and
    # sorts to wherever that falls, instead of surfacing as freshly reopened.
    record.touch_movement()
    log_action(
        AuditLog.Action.UPDATE,
        f"Returned {record.tracking_number} to tracking",
        actor=user,
        target=record,
    )
    return record


@transaction.atomic
def grant_access(record, *, user, office=None, target_user=None, reason="") -> RecordAccessGrant:
    refuse_viewers(user, "share documents")
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
        if office:
            notify_office(
                office, kind=Notification.Kind.SHARED, title="A document was shared with your office",
                message=f"Access was granted to {record.tracking_number}.",
                url=record.get_absolute_url(), tracking_record=record,
            )
    return grant


# ---------------------------------------------------------------------------
# Queue helpers used by the dashboard
# ---------------------------------------------------------------------------
def inbox_for(user):
    """Documents waiting for this user's office to confirm receipt.

    NOT a dashboard queue, and narrower than every one of them. This is the set
    somebody may actually press Confirm on, which is what bulk receipt needs and
    what `selfcheck` asserts against; the Incoming card counts
    `apply_scope(..., SCOPE_INCOMING, ...)`, which also holds what has already
    been received and what is being worked on.

    Two sibling helpers stood here — `in_custody_for` and `outgoing_for` — and
    the dashboard counted its cards with them while linking to the scopes, so
    every card disagreed with the page it opened. They are gone; `apply_scope`
    is the one definition of those queues. This one survives because confirming
    receipt is a different question from listing a queue.
    """
    if not user.is_authenticated or not user.office_id:
        return TrackingRecord.objects.none()
    return (
        TrackingRecord.objects.visible_to(user)
        .filter(
            routing_steps__to_office_id=user.office_id,
            routing_steps__received_at__isnull=True,
            routing_steps__batch=F("current_batch"),
        )
        .exclude(status__in=COMPLETED_STATUSES)
        .with_related()
        .distinct()
    )


def overdue_for(user):
    return TrackingRecord.objects.visible_to(user).overdue().with_related().distinct()


def completed_this_year_for(user):
    """Counts the work finished this year, whether or not it has been approved
    into the repository yet — approval is an administrative step that can lag by
    weeks, and a count of completed work that waits on it would understate the
    year every time somebody is slow to file."""
    return (
        TrackingRecord.objects.visible_to(user)
        .filter(status__in=COMPLETED_STATUSES, completed_at__year=timezone.localdate().year)
        .distinct()
    )


def pending_upload_for(user):
    """Finished records waiting for an administrator to approve them."""
    return TrackingRecord.objects.visible_to(user).pending_filing().with_related().distinct()


def active_for(user):
    return TrackingRecord.objects.visible_to(user).filter(status__in=ACTIVE_STATUSES).with_related().distinct()


#: Queue names the Tracking page's `?scope=` links use.
#:
#: Incoming and Outgoing are derived here rather than stored on the record,
#: because direction is not a property of a document — it is a property of a
#: document *and an office*. The batch that is outgoing for Supply is incoming
#: for HR at the same instant, so there is no single value a status column could
#: hold. Keeping them out of the enum also keeps the audit trail honest: the
#: timeline records acts, and "incoming" is not an act anybody performed.
SCOPE_INCOMING = "incoming"
SCOPE_OUTGOING = "outgoing"
SCOPE_PENDING_RECEIPT = "pending-receipt"
SCOPE_RECEIVED = "received"
SCOPE_OVERDUE = "overdue"
#: The completed-but-unapproved queue. It lives on this page rather than on the
#: repository page because the records in it have not reached the repository —
#: approving them is what puts them there.
SCOPE_PENDING_UPLOAD = "pending-upload"
#: Older names, kept only so saved bookmarks still resolve. Nothing in the app
#: links to them any more.
#:
#: They are NOT synonyms for the queues that replaced them, and a comment here
#: used to say they were:
#:
#:   inbox   is narrower than incoming — unconfirmed steps only, where incoming
#:           spans pending receipt, received and in process
#:   sent    is narrower than outgoing — drops a document the moment somebody
#:           confirms it, where outgoing keeps what left the office
#:   custody is *wider* than received — every record whose current_office is
#:           this office, completed and filed ones included
#:
#: Measured on the demo data, one office: inbox 9 against incoming 5, sent 15
#: against outgoing 7. Anything pointed at an alias by mistake shows a
#: different queue from the one its label promises.
SCOPE_INBOX = "inbox"
SCOPE_AWAITING = "awaiting"
SCOPE_CUSTODY = "custody"
SCOPE_SENT = "sent"
SCOPE_MINE = "mine"

#: Statuses that count as "the document is here, with us" for the Received
#: queue. In process belongs to Incoming, per the queue definitions, so it is
#: not a separate top-level queue of its own.
_HELD_STATUSES = (Status.RECEIVED, Status.IN_PROCESS)


#: Records per page wherever tracking records are listed. Lives here rather
#: than on one of the two views that page them, so the Tracking workspace and
#: the unified search page cannot drift to different page sizes.
PAGE_SIZE = 20


def filter_records(records, *, query=None, status=None, offices=None):
    """Free-text, status and originating-office filtering for tracking records.

    Kept here rather than inline in a view because two pages need it: the
    Tracking workspace, and the tracking half of the unified search page. The
    second one is why the text branch exists at all — the workspace has no
    search box, having traded it for the queue pills, so `query` is only ever
    passed by search.

    Scope narrowing is deliberately *not* here. It depends on the user, this
    does not, and the queues compose with these filters rather than replacing
    them — callers apply `apply_scope()` afterwards.

    A draft matches on its placeholder tracking number, which is never
    displayed. That is not a leak: `visible_to` already limits a draft to the
    person writing it, so the only person who can match one that way is its
    author searching their own unsent work.
    """
    if query:
        records = records.filter(
            Q(tracking_number__icontains=query)
            | Q(subject__icontains=query)
            | Q(originating_office__name__icontains=query)
            | Q(originating_office__code__icontains=query)
            | Q(current_office__name__icontains=query)
        )
    if status == "OVERDUE":
        # Overdue is derived, never stored — see TrackingRecord.display_status.
        records = records.filter(due_at__lt=timezone.now()).exclude(status__in=COMPLETED_STATUSES)
    elif status:
        records = records.filter(status=status)
    if offices:
        records = records.filter(originating_office__in=offices)
    return records


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
    if scope == SCOPE_OVERDUE:
        # Not office-scoped: `visible_to` has already limited the set to records
        # this user has something to do with, and an overdue document sitting in
        # another office is precisely what the person who sent it needs to see.
        return records.filter(due_at__lt=timezone.now()).exclude(status__in=COMPLETED_STATUSES)
    if scope == SCOPE_PENDING_UPLOAD:
        return records.filter(
            status=Status.COMPLETED_PENDING_UPLOAD, archived_document__isnull=True
        )

    office_scoped = {
        SCOPE_INBOX, SCOPE_CUSTODY, SCOPE_SENT,
        SCOPE_INCOMING, SCOPE_OUTGOING, SCOPE_PENDING_RECEIPT, SCOPE_RECEIVED,
    }
    if scope not in office_scoped:
        return records
    if not user.office_id:
        return records.none()

    if scope == SCOPE_INCOMING:
        # Everything addressed to this office in the current batch, whether or
        # not the receipt has been confirmed — Pending receipt, Received and
        # In process together, which is what "our incoming" means to a clerk.
        return records.filter(
            routing_steps__to_office_id=user.office_id,
            routing_steps__batch=F("current_batch"),
        ).exclude(status__in=COMPLETED_STATUSES)
    if scope == SCOPE_OUTGOING:
        # Everything this office sent onward in the current batch. Unlike the
        # older "sent" queue this does not drop a document the moment somebody
        # confirms it: what left the office is still what left the office.
        return records.filter(
            routing_steps__from_office_id=user.office_id,
            routing_steps__batch=F("current_batch"),
        ).exclude(status__in=COMPLETED_STATUSES)
    if scope == SCOPE_PENDING_RECEIPT:
        # Both directions: what this office owes a receipt on, and what it sent
        # that nobody has signed for yet. The second half was missing, so the
        # one queue whose whole job is "who has not confirmed" could not answer
        # it for the office doing the asking.
        #
        # One .filter() call, not two. Split across two calls each condition
        # binds to a *different* routing step, so a record with any unconfirmed
        # step and any step to or from this office would match — which is a
        # wider queue than either half.
        #
        # The Confirm Receipt button stays correct on its own: annotate_can_confirm
        # checks to_office_id independently, so the outgoing rows joining this
        # queue show no button.
        return records.filter(
            Q(routing_steps__to_office_id=user.office_id)
            | Q(routing_steps__from_office_id=user.office_id),
            status=Status.PENDING_RECEIPT,
            routing_steps__received_at__isnull=True,
            routing_steps__batch=F("current_batch"),
        )
    if scope == SCOPE_RECEIVED:
        return records.filter(
            status__in=_HELD_STATUSES,
            routing_steps__to_office_id=user.office_id,
            routing_steps__received_at__isnull=False,
            routing_steps__batch=F("current_batch"),
        )
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
    ).exclude(status__in=COMPLETED_STATUSES)


#: Office badges shown in a "Receiving office" cell before it collapses to "+N".
RECEIVING_SHOWN = 3


def annotate_receiving_offices(records) -> None:
    """Attach the current batch's destination offices to each record.

    One grouped query rather than `record.receiving_offices` per row, which on a
    twenty-row page is twenty queries for something one `IN` clause answers.

    `.only()` names every field the badge renders — code, name and colour — as
    well as the two used to group. Leaving one out would defer it and fetch it a
    row at a time, which is the per-row query this exists to avoid, reappearing
    somewhere harder to spot.
    """
    if not records:
        return
    steps = (
        RoutingStep.objects.filter(record__in=records)
        .select_related("to_office")
        .only("record_id", "batch", "to_office__code", "to_office__name", "to_office__colour")
    )
    by_record: dict[int, list] = {}
    for step in steps:
        by_record.setdefault(step.record_id, []).append(step)

    for record in records:
        seen, offices = set(), []
        for step in by_record.get(record.pk, []):
            if step.batch != record.current_batch or step.to_office_id in seen:
                continue
            seen.add(step.to_office_id)
            offices.append(step.to_office)
        record.receiving_offices_shown = offices[:RECEIVING_SHOWN]
        record.receiving_more = max(0, len(offices) - RECEIVING_SHOWN)


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
