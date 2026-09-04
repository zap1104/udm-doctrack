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
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from apps.core.models import DocumentType, TimeStampedModel


def attachment_upload_path(instance, filename: str) -> str:
    record = instance.record
    return f"tracking/{record.created_at:%Y/%m}/{record.tracking_number}/{filename}"


class Status(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    #: Sent and nobody has confirmed it yet. Named for what the reader needs to
    #: do about it — somebody owes a receipt — rather than for where the paper
    #: is, which "In transit" described and which no office can act on.
    #:
    #: This is the *only* awaiting-receipt status. FORWARDED and RETURNED used
    #: to exist alongside it and meant precisely the same thing: a document sent
    #: on, with a receipt outstanding. Three names for one state split every
    #: queue and every filter three ways, so a document waiting on the same act
    #: appeared under a different label depending on which hop it was on. The
    #: distinction that does matter — how it got here — is kept where it belongs
    #: and cannot be lost: RoutingStep.Action and RecordActivity.Event both still
    #: carry FORWARD/FORWARDED and RETURN/RETURNED.
    PENDING_RECEIPT = "PENDING_RECEIPT", "Pending receipt"
    RECEIVED = "RECEIVED", "Received"
    IN_PROCESS = "IN_PROCESS", "In process"
    #: The office has finished its work, but an administrator has not yet
    #: approved the record into the Document Repository. The record stays in
    #: Tracking for this stage — see ACTIVE_STATUSES.
    COMPLETED_PENDING_UPLOAD = "COMPLETED_PENDING_UPLOAD", "Completed - pending upload"
    #: Approved into the repository. A record only reaches this once a Document
    #: exists for it, so COMPLETED now means "filed", not merely "finished".
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
AWAITING_RECEIPT_STATUSES = {Status.PENDING_RECEIPT}

#: The work is finished: the record no longer moves between offices, cannot be
#: routed, and nobody owes a receipt on it. Both halves of the completion are
#: here, because almost every rule that used to ask "is this COMPLETED?" meant
#: "is the work over?" rather than "is it in the repository?". The few places
#: that mean the narrower thing test Status.COMPLETED directly.
COMPLETED_STATUSES = {Status.COMPLETED_PENDING_UPLOAD, Status.COMPLETED}

#: Statuses that belong in the Document Tracking module. COMPLETED_PENDING_UPLOAD
#: is here on purpose: a finished record stays visible in Tracking until an
#: administrator approves it into the repository, which is the act that files it.
ACTIVE_STATUSES = {
    Status.DRAFT,
    Status.PENDING_RECEIPT,
    Status.RECEIVED,
    Status.IN_PROCESS,
    Status.COMPLETED_PENDING_UPLOAD,
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
        # Scoped to the current batch, like every other inbox query here. An
        # unscoped version matches steps from *any* earlier batch that was
        # never received, so a document long since forwarded on would still be
        # reported as waiting for this office to confirm it.
        return self.filter(
            routing_steps__to_office=office,
            routing_steps__received_at__isnull=True,
            routing_steps__batch=F("current_batch"),
        ).distinct()

    def pending_filing(self):
        """Finished, and waiting for an administrator to approve it into the
        repository.

        This queue lives on the **Tracking** page. It used to live on the
        Repository page, for a reason that no longer holds: completing a record
        set it straight to COMPLETED, which dropped it out of Tracking, so a
        record completed with the "file it now" box unticked was in neither
        module and the repository page was the only place left to surface it
        from. Approval is now an explicit stage of the tracking lifecycle
        (COMPLETED_PENDING_UPLOAD is in ACTIVE_STATUSES), so the record never
        leaves Tracking until the act that files it has actually happened, and
        the queue belongs beside the records it is about.

        Asks the relation as well as the status, because the question is
        literally "is there a document for it?" — the two are written together,
        but the relation is the one that cannot go stale.
        """
        return self.filter(status=Status.COMPLETED_PENDING_UPLOAD, archived_document__isnull=True)

    def overdue(self):
        return self.filter(due_at__lt=timezone.now()).exclude(status__in=COMPLETED_STATUSES)

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

    # 32, not 16: "COMPLETED_PENDING_UPLOAD" is 24 characters and would be
    # truncated on the way into a 16-column, storing a status no filter matches.
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT, db_index=True)
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

    #: Who approved this record into the repository, and when. Stored rather
    #: than inferred from the timeline, because this is the pair that answers
    #: "did anybody other than the finisher look at this before it became
    #: permanent?" — and a report cannot ask that of a text message.
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="approved_records",
    )

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
        return bool(
            self.due_at and self.due_at < timezone.now() and self.status not in COMPLETED_STATUSES
        )

    @property
    def awaiting_receipt(self) -> bool:
        return self.status in AWAITING_RECEIPT_STATUSES

    # `display_status` and `display_status_label` stood here and returned
    # "Overdue" in place of the stage whenever the deadline had passed. Three of
    # six records then showed a stage the reader could not see: "overdue" says
    # the deadline went by, not whether anybody has signed for the document,
    # which is the part somebody has to act on. Overdue is a tag beside the
    # status now — see templates/partials/_overdue_tag.html — so read `status`
    # and `get_status_display()` directly and render the tag alongside.

    @property
    def has_tracking_number(self) -> bool:
        """False while the record is a draft carrying a placeholder.

        Tested on the status rather than by matching the placeholder's shape, so
        a change to how placeholders are spelled cannot leak one onto a screen.
        """
        return self.status != Status.DRAFT

    @property
    def display_tracking_number(self) -> str:
        """What to print wherever a tracking number appears.

        A draft has no number to show — it is issued on send, so that an
        abandoned draft leaves no gap in the office's series. Showing the
        placeholder would be worse than showing nothing: it looks like a
        reference somebody could quote, and it would be quoted.
        """
        return self.tracking_number if self.has_tracking_number else "Not yet assigned"

    @property
    def is_editable(self) -> bool:
        return self.status == Status.DRAFT

    @property
    def receiving_offices(self):
        """Where the document is going in its current batch.

        Distinct from `current_office`, which is where it *is*. Three offices
        matter to a reader — who raised it, who holds it, and who owes the next
        act — and showing only the first two leaves the most useful of the three
        off the page: "which office are we waiting on" is the question the
        record is usually opened to answer.
        """
        return [step.to_office for step in self.current_step_queryset.select_related("to_office")]

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

    def is_incoming_for(self, office) -> bool:
        """Addressed to this office in the current batch — confirmed or not.

        Derived, never stored. "Incoming" and "Outgoing" are the two directions
        a document can face, and a document faces different directions for
        different offices at the same moment: the batch that is outgoing for
        Supply is incoming for HR. A stored status could only ever record one of
        those, so the answer is computed per office instead.
        """
        if office is None:
            return False
        office_id = getattr(office, "pk", office)
        return self.current_step_queryset.filter(to_office_id=office_id).exists()

    def is_outgoing_for(self, office) -> bool:
        """Sent onward by this office in the current batch."""
        if office is None:
            return False
        office_id = getattr(office, "pk", office)
        return self.current_step_queryset.filter(from_office_id=office_id).exists()

    def can_user_act(self, user) -> bool:
        """Can this user add remarks / forward / complete right now?"""
        if not user.is_authenticated or self.status in COMPLETED_STATUSES:
            return False
        if user.is_viewer:
            return False
        if user.is_system_admin:
            return True
        if self.status == Status.DRAFT:
            return self.created_by_id == user.pk or (
                user.is_records_staff and user.office_id == self.originating_office_id
            )
        return bool(user.office_id) and self.has_custody(user.office)

    def can_user_confirm_receipt(self, user) -> bool:
        if not user.is_authenticated or not user.office_id or user.is_viewer:
            return False
        return self.pending_step_for_office(user.office) is not None

    def can_user_approve_upload(self, user) -> bool:
        """Who may approve a finished record into the Document Repository.

        `can_user_act` cannot answer this: it is deliberately False once the
        work is over, and approval only ever happens after completion. Without a
        rule of its own the endpoint had no permission check at all, so anyone
        who could *read* the record — an office it merely passed through, or
        somebody holding a read-only access grant — could push it into the
        repository, copying every attachment and writing an ARCHIVED entry into
        the append-only history under their name.

        Approval is an administrator's act, not the finishing office's: the
        point of the COMPLETED_PENDING_UPLOAD stage is that somebody looks at
        the work before it becomes a permanent repository record. A system
        administrator may approve anywhere; an office administrator only for
        their own office's documents.

        Was `can_user_archive`, which let any handling office file its own work
        and so had nobody reviewing anything.

        Self-approval is deliberately *not* blocked. In a one- or two-person
        office the administrator is often the person who finished the work, and
        refusing them would deadlock the queue with nobody to escalate to — a
        control that cannot be satisfied gets worked around or turns the queue
        into a graveyard. It is recorded instead: `approved_by` is stored on the
        record and the audit entry names a self-approval as such, so the cases
        where nobody independent looked are visible rather than prevented.
        """
        if not user.is_authenticated or user.is_viewer:
            return False
        if self.status != Status.COMPLETED_PENDING_UPLOAD:
            return False
        if user.is_system_admin:
            return True
        if not user.is_office_admin:
            return False
        return bool(user.office_id) and user.office_id in {
            self.originating_office_id,
            self.current_office_id,
        }

    def can_user_reopen(self, user) -> bool:
        """Who may send a completed record back into active tracking.

        Completing a record is otherwise a one-way door: a record marked
        completed by mistake cannot be routed (`route_record` refuses it),
        cannot be acted on (`can_user_act` is False once COMPLETED), and has no
        way back — even though the refusal message tells the reader to "reopen
        it before routing again".

        Deliberately narrower than archiving, and only before the record is
        filed. Once a Document exists the record is part of the repository, and
        withdrawing it from there is a different act with different
        consequences than correcting a premature completion.

        The originating office is *not* included: it should not be able to pull
        a document back into play after another office finished the work. The
        office that completed it can correct its own mistake, and an
        administrator can correct anyone's.
        """
        if not user.is_authenticated or user.is_viewer:
            return False
        # Only from the pending-upload stage. Once approved the record is part
        # of the repository, and withdrawing it from there is a different act.
        if self.status != Status.COMPLETED_PENDING_UPLOAD or self.is_archived:
            return False
        if user.is_system_admin:
            return True
        if self.completed_by_id == user.pk:
            return True
        if user.is_office_admin and user.office_id in {
            self.originating_office_id,
            self.current_office_id,
        }:
            return True
        return bool(user.office_id) and user.office_id == self.current_office_id

    def can_user_view(self, user) -> bool:
        return TrackingRecord.objects.filter(pk=self.pk).visible_to(user).exists()

    # -- status bookkeeping ------------------------------------------------
    def recalculate_status(self, *, save: bool = True) -> str:
        """Single source of truth for `status`, `current_office` and `current_holder`."""
        if self.status == Status.DRAFT or self.status in COMPLETED_STATUSES:
            return self.status

        steps = list(self.current_step_queryset.select_related("to_office", "received_by"))
        if not steps:
            self.status = Status.DRAFT
        else:
            received = [step for step in steps if step.received_at]
            if not received:
                # One status for one state. A forward and a return are both a
                # document sent on with a receipt outstanding, and the step's
                # own `action` already records which of the two it was.
                self.status = Status.PENDING_RECEIPT
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
        #: Read and print are acts on the document too. A view-only account
        #: leaves no other trace, so without these two the people who can only
        #: look at a record are the people the history says nothing about.
        VIEWED = "VIEWED", "Opened"
        PRINTED = "PRINTED", "Routing slip printed"

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


#: Logged, but kept out of the timeline the record page renders. VIEWED is
#: written every time somebody opens a record, so showing it would bury the
#: movement history under a list of who looked — and, since the timeline is on
#: the page being opened, each read would lengthen the thing being read. The
#: rows are still there, still append-only, and still visible in the audit log.
QUIET_EVENTS = {RecordActivity.Event.VIEWED}


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
