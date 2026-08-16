"""Active document tracking (DTS).

Design rules that the rest of the code depends on:

1. `RoutingStep` is the custody chain. One row per (batch, receiving office).
   The only fields ever written after creation are the receipt fields, and only
   once — nothing in the history is overwritten.
2. `RecordActivity` is the human-readable timeline. It is append-only.
3. `TrackingRecord.status` is a *cached* summary of the routing steps so that
   list pages stay fast; `recalculate_status()` is the single place that sets it.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.models import DocumentType, TimeStampedModel


def attachment_upload_path(instance, filename: str) -> str:
    record = instance.record
    return f"tracking/{record.created_at:%Y/%m}/{record.tracking_number}/{filename}"


class Status(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    IN_TRANSIT = "IN_TRANSIT", "In transit"
    RECEIVED = "RECEIVED", "Received"
    IN_PROCESS = "IN_PROCESS", "In process"
    FORWARDED = "FORWARDED", "Forwarded"
    RETURNED = "RETURNED", "Returned"
    COMPLETED = "COMPLETED", "Completed"


#: Ceilings for the free-text boxes, shared by the forms and the services so
#: the two cannot disagree.
#:
#: They existed only in the service layer before, as bare slices — a note longer
#: than the ceiling was quietly cut down on save with nothing said, which in an
#: append-only record is silent data loss on a note somebody meant to keep. The
#: forms now carry the same numbers, so the limit is announced in the box, the
#: browser stops the typing, and the server answers with an error instead of
#: with scissors. The slices stay as a backstop for callers that are not forms.
MAX_REMARK_CHARS = 2000
MAX_NOTE_CHARS = 2000
MAX_INSTRUCTIONS_CHARS = 5000

#: Statuses where a receiving office still has to press "Confirm receipt".
AWAITING_RECEIPT_STATUSES = {Status.IN_TRANSIT, Status.FORWARDED, Status.RETURNED}
#: Statuses that belong in the Tracking module (Completed records move to Documents).
ACTIVE_STATUSES = {
    Status.DRAFT,
    Status.IN_TRANSIT,
    Status.RECEIVED,
    Status.IN_PROCESS,
    Status.FORWARDED,
    Status.RETURNED,
}


class TrackingNumberSequence(models.Model):
    """One counter per office/year/month. Locked with select_for_update on use."""

    office = models.ForeignKey("accounts.Office", on_delete=models.CASCADE, related_name="sequences")
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("office", "year", "month")
        verbose_name = "tracking number sequence"

    def __str__(self) -> str:
        return f"{self.office.code} {self.year}-{self.month:02d}: {self.last_number}"


class TrackingRecordQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Records the user is allowed to see at all."""
        if not user.is_authenticated:
            return self.none()
        if user.is_system_admin:
            return self
        office_id = user.office_id
        conditions = Q(created_by=user) | Q(grants__user=user)
        if office_id:
            conditions |= (
                Q(originating_office_id=office_id)
                | Q(routing_steps__to_office_id=office_id)
                | Q(routing_steps__from_office_id=office_id)
                | Q(grants__office_id=office_id)
            )
        # A draft is only visible to the office that is still preparing it.
        return self.filter(conditions).exclude(~Q(created_by=user), status=Status.DRAFT).distinct()

    def active(self):
        return self.filter(status__in=ACTIVE_STATUSES, is_archived=False)

    def awaiting_receipt_for(self, office):
        return self.filter(
            routing_steps__to_office=office,
            routing_steps__received_at__isnull=True,
        ).distinct()

    def overdue(self):
        return self.filter(due_at__lt=timezone.now()).exclude(status=Status.COMPLETED)

    def with_related(self):
        return self.select_related(
            "originating_office", "current_office", "document_type", "created_by"
        )


class TrackingRecordManager(models.Manager.from_queryset(TrackingRecordQuerySet)):
    """Named at module level so Django's makemigrations can import it by dotted
    path (apps.tracking.models.TrackingRecordManager). An inline
    `models.Manager.from_queryset(TrackingRecordQuerySet)()` has no importable
    name and fails migration serialization with:
        ValueError: Could not find manager ManagerFromTrackingRecordQuerySet
    """


class TrackingRecord(TimeStampedModel):
    """One document moving through the offices."""

    class Priority(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        URGENT = "URGENT", "Urgent"

    class Classification(models.TextChoices):
        INTERNAL = "INTERNAL", "Internal"
        CONFIDENTIAL = "CONFIDENTIAL", "Confidential"
        PUBLIC = "PUBLIC", "Public"

    class RequestedAction(models.TextChoices):
        """The numbered action codes printed on the paper routing slip.

        Numbering starts at 2 because that is what the approved slip uses; the
        values are the codes themselves so a slip and a screen always agree.
        """

        APPROPRIATE_ACTION = "2", "2 · Appropriate Action"
        COMMENT = "3", "3 · Comment / Recommendation"
        STUDY = "4", "4 · Study / Inquiry"
        REPLY_DIRECT = "5", "5 · Reply Direct to Writer"
        SIGNATURE = "6", "6 · For Signature"
        REWRITE = "7", "7 · Rewrite / Redraft / Retype"
        NOTATION_RETURN = "8", "8 · Notation to Return"
        NOTATION_FORWARD = "9", "9 · Notation to Forward"
        REROUTE = "10", "10 · Re-route"
        FILE = "11", "11 · File"

    tracking_number = models.CharField(max_length=48, unique=True, db_index=True)
    subject = models.CharField(max_length=255)
    document_type = models.ForeignKey(
        DocumentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="tracking_records"
    )
    classification = models.CharField(
        max_length=16, choices=Classification.choices, default=Classification.INTERNAL
    )
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.NORMAL)
    requested_action = models.CharField(
        max_length=2,
        choices=RequestedAction.choices,
        blank=True,
        help_text="What the receiving office is being asked to do, as numbered on the routing slip.",
    )

    originating_office = models.ForeignKey(
        "accounts.Office", on_delete=models.PROTECT, related_name="originated_records"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_records"
    )

    instructions = models.TextField(help_text="Action required by the receiving office.")
    remarks = models.TextField(blank=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    current_office = models.ForeignKey(
        "accounts.Office", null=True, blank=True, on_delete=models.SET_NULL, related_name="custody_records"
    )
    current_holder = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="held_records"
    )
    current_batch = models.PositiveSmallIntegerField(default=0)

    due_at = models.DateTimeField(null=True, blank=True)
    first_received_at = models.DateTimeField(null=True, blank=True)
    last_movement_at = models.DateTimeField(default=timezone.now, db_index=True)

    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="completed_records"
    )
    completion_note = models.TextField(blank=True)

    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    objects = TrackingRecordManager()

    class Meta:
        ordering = ["-last_movement_at", "-created_at"]
        verbose_name = "tracking record"
        indexes = [
            models.Index(fields=["status", "-last_movement_at"]),
            models.Index(fields=["originating_office", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.tracking_number} — {self.subject}"

    def get_absolute_url(self) -> str:
        return reverse("tracking:detail", args=[self.pk])

    # -- derived state ----------------------------------------------------
    @property
    def is_overdue(self) -> bool:
        return bool(self.due_at and self.due_at < timezone.now() and self.status != Status.COMPLETED)

    @property
    def awaiting_receipt(self) -> bool:
        return self.status in AWAITING_RECEIPT_STATUSES

    @property
    def display_status(self) -> str:
        """Status shown in lists — overdue wins over everything except completed."""
        if self.status != Status.COMPLETED and self.is_overdue:
            return "OVERDUE"
        return self.status

    @property
    def display_status_label(self) -> str:
        if self.display_status == "OVERDUE":
            return "Overdue"
        return self.get_status_display()

    @property
    def is_editable(self) -> bool:
        return self.status == Status.DRAFT

    @property
    def current_step_queryset(self):
        return self.routing_steps.filter(batch=self.current_batch)

    def current_offices(self):
        return [step.to_office for step in self.current_step_queryset.select_related("to_office")]

    def pending_receipt_offices(self):
        return [
            step.to_office
            for step in self.current_step_queryset.filter(received_at__isnull=True).select_related("to_office")
        ]

    def pending_step_for_office(self, office):
        if office is None:
            return None
        return self.current_step_queryset.filter(to_office=office, received_at__isnull=True).first()

    def has_custody(self, office) -> bool:
        """True when the office currently holds the document (receipt confirmed)."""
        if office is None:
            return False
        return self.current_step_queryset.filter(to_office=office, received_at__isnull=False).exists()

    def can_user_act(self, user) -> bool:
        """Can this user add remarks / forward / complete right now?"""
        if not user.is_authenticated or self.status == Status.COMPLETED:
            return False
        if user.is_system_admin:
            return True
        if self.status == Status.DRAFT:
            return self.created_by_id == user.pk or (
                user.is_records_staff and user.office_id == self.originating_office_id
            )
        return bool(user.office_id) and self.has_custody(user.office)

    def can_user_confirm_receipt(self, user) -> bool:
        if not user.is_authenticated or not user.office_id:
            return False
        return self.pending_step_for_office(user.office) is not None

    def can_user_view(self, user) -> bool:
        return TrackingRecord.objects.filter(pk=self.pk).visible_to(user).exists()

    # -- status bookkeeping ------------------------------------------------
    def recalculate_status(self, *, save: bool = True) -> str:
        """Single source of truth for `status`, `current_office` and `current_holder`."""
        if self.status in {Status.DRAFT, Status.COMPLETED}:
            return self.status

        steps = list(self.current_step_queryset.select_related("to_office", "received_by"))
        if not steps:
            self.status = Status.DRAFT
        else:
            received = [step for step in steps if step.received_at]
            if not received:
                action = steps[0].action
                self.status = {
                    RoutingStep.Action.FORWARD: Status.FORWARDED,
                    RoutingStep.Action.RETURN: Status.RETURNED,
                }.get(action, Status.IN_TRANSIT)
                # Nothing has been received in this batch yet, so the document
                # has not actually left where it last had confirmed custody —
                # `from_office` (the originating office on a first send, or the
                # office that forwarded it). Showing `to_office` here would
                # display the destination before anyone confirmed receiving it,
                # contradicting "sent is not received" the moment it's sent.
                self.current_office = steps[0].from_office
                self.current_holder = None
            else:
                latest = max(received, key=lambda step: step.received_at)
                self.current_office = latest.to_office
                self.current_holder = latest.received_by
                self.status = Status.IN_PROCESS if self.has_activity_after_receipt() else Status.RECEIVED

        if save:
            self.save(update_fields=["status", "current_office", "current_holder", "updated_at"])
        return self.status

    def has_activity_after_receipt(self) -> bool:
        return self.activities.filter(
            batch=self.current_batch,
            event__in=[RecordActivity.Event.REMARK, RecordActivity.Event.ATTACHMENT],
        ).exists()

    def touch_movement(self) -> None:
        self.last_movement_at = timezone.now()
        self.save(update_fields=["last_movement_at", "updated_at"])


class RoutingStep(models.Model):
    """One transfer: office A sends the document to office B and B confirms receipt."""

    class Action(models.TextChoices):
        SEND = "SEND", "Sent"
        FORWARD = "FORWARD", "Forwarded"
        RETURN = "RETURN", "Returned"

    record = models.ForeignKey(TrackingRecord, on_delete=models.CASCADE, related_name="routing_steps")
    sequence = models.PositiveSmallIntegerField()
    batch = models.PositiveSmallIntegerField(
        default=1, help_text="Steps sent together share a batch number."
    )
    action = models.CharField(max_length=8, choices=Action.choices, default=Action.SEND)

    from_office = models.ForeignKey(
        "accounts.Office", null=True, blank=True, on_delete=models.PROTECT, related_name="steps_sent"
    )
    to_office = models.ForeignKey("accounts.Office", on_delete=models.PROTECT, related_name="steps_received")

    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="routing_steps_sent"
    )
    sent_at = models.DateTimeField(default=timezone.now)
    instructions = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True)

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="routing_steps_received",
    )
    received_at = models.DateTimeField(null=True, blank=True)
    receipt_note = models.TextField(blank=True)

    class Meta:
        ordering = ["sequence"]
        unique_together = ("record", "sequence")
        verbose_name = "routing step"
        indexes = [models.Index(fields=["to_office", "received_at"])]

    def __str__(self) -> str:
        return f"{self.record.tracking_number} #{self.sequence} → {self.to_office.code}"

    @property
    def is_received(self) -> bool:
        return self.received_at is not None

    @property
    def is_overdue(self) -> bool:
        return bool(self.due_at and not self.received_at and self.due_at < timezone.now())

    @property
    def turnaround(self):
        if self.received_at:
            return self.received_at - self.sent_at
        return None


class RecordActivity(models.Model):
    """Append-only timeline entry. Nothing here is ever edited or deleted."""

    class Event(models.TextChoices):
        CREATED = "CREATED", "Created"
        SENT = "SENT", "Routed"
        RECEIVED = "RECEIVED", "Receipt confirmed"
        REMARK = "REMARK", "Remark added"
        ATTACHMENT = "ATTACHMENT", "File attached"
        FORWARDED = "FORWARDED", "Forwarded"
        RETURNED = "RETURNED", "Returned"
        COMPLETED = "COMPLETED", "Marked completed"
        ARCHIVED = "ARCHIVED", "Archived"
        ACCESS = "ACCESS", "Access granted"

    record = models.ForeignKey(TrackingRecord, on_delete=models.CASCADE, related_name="activities")
    event = models.CharField(max_length=16, choices=Event.choices)
    batch = models.PositiveSmallIntegerField(default=0)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="record_activities"
    )
    actor_office = models.ForeignKey(
        "accounts.Office", null=True, blank=True, on_delete=models.SET_NULL, related_name="record_activities"
    )
    message = models.CharField(max_length=255)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "record activity"
        verbose_name_plural = "record activities"

    def __str__(self) -> str:
        return f"{self.record_id} {self.event} {self.created_at:%Y-%m-%d %H:%M}"


class Attachment(TimeStampedModel):
    record = models.ForeignKey(TrackingRecord, on_delete=models.CASCADE, related_name="attachments")
    routing_step = models.ForeignKey(
        RoutingStep, null=True, blank=True, on_delete=models.SET_NULL, related_name="attachments"
    )
    file = models.FileField(upload_to=attachment_upload_path, max_length=400)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True, db_index=True)
    note = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="tracking_uploads"
    )

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return self.original_name

    @property
    def extension(self) -> str:
        return (self.original_name.rsplit(".", 1)[-1] if "." in self.original_name else "").upper()


class RecordAccessGrant(models.Model):
    """Explicit access for an office or a single user outside the routing chain."""

    record = models.ForeignKey(TrackingRecord, on_delete=models.CASCADE, related_name="grants")
    office = models.ForeignKey(
        "accounts.Office", null=True, blank=True, on_delete=models.CASCADE, related_name="record_grants"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="record_grants"
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="record_grants_given"
    )
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "record access grant"
        unique_together = ("record", "office", "user")

    def __str__(self) -> str:
        return f"{self.record_id} → {self.office or self.user}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.office_id is None and self.user_id is None:
            raise ValidationError("Choose an office or a user to grant access to.")
