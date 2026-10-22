"""Independent custody ledger for offices without DocTrack accounts."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower, Trim
from django.urls import reverse
from django.utils import timezone

from apps.core.document_types import DocumentTypesMixin
from apps.core.models import TimeStampedModel

from .file_version_models import AttachmentVersionMixin, ImmutableFileQuerySet
from .models import Status


def external_attachment_path(instance, filename):
    return f"external-tracking/{instance.record_id}/{filename}"


class ExternalOfficeQuerySet(models.QuerySet):
    def visible_to(self, user):
        if not getattr(user, "is_authenticated", False) or not user.is_active:
            return self.none()
        if user.is_system_admin:
            return self
        if not user.office_id:
            return self.none()
        return self.filter(managing_office_id=user.office_id)


class ExternalOffice(TimeStampedModel):
    managing_office = models.ForeignKey("accounts.Office", on_delete=models.PROTECT, related_name="external_directory")
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=30, blank=True)
    contact_person = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=60, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    objects = ExternalOfficeQuerySet.as_manager()

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(Lower(Trim("name")), "managing_office", name="external_office_unique_owner_name")
        ]

    def __str__(self):
        return self.name

    @property
    def label(self):
        return self.code or self.name


class ExternalRecordQuerySet(models.QuerySet):
    def visible_to(self, user):
        if (
            not getattr(user, "is_authenticated", False)
            or not user.is_active
            or user.role not in {"USER", "VIEWER", "ADMIN", "SYSTEM_ADMIN"}
        ):
            return self.none()
        if user.is_system_admin:
            return self
        if not user.office_id:
            return self.none()
        own = Q(managing_office_id=user.office_id)
        involved = Q(routing_steps__from_office_id=user.office_id) | Q(routing_steps__to_office_id=user.office_id)
        scope = self.filter(own | (involved & ~Q(status=Status.DRAFT))).distinct()
        return scope if user.is_office_admin else scope.exclude(status=Status.DRAFT)

    def with_related(self):
        return self.select_related(
            "managing_office", "current_office", "current_external_office", "created_by", "document_type"
        ).prefetch_related("document_types")


class ExternalTrackingRecord(DocumentTypesMixin, TimeStampedModel):
    managing_office = models.ForeignKey(
        "accounts.Office", on_delete=models.PROTECT, related_name="external_tracking_records"
    )
    tracking_number = models.CharField(max_length=100, unique=True)
    original_reference = models.CharField(max_length=200, blank=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    document_type = models.ForeignKey(
        "core.DocumentType", null=True, blank=True, on_delete=models.PROTECT, related_name="external_tracking_records"
    )
    document_types = models.ManyToManyField("core.DocumentType", blank=True, related_name="external_records")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT, db_index=True)
    revision = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="external_records_created"
    )
    current_office = models.ForeignKey(
        "accounts.Office", null=True, blank=True, on_delete=models.PROTECT, related_name="external_custody_records"
    )
    current_external_office = models.ForeignKey(
        ExternalOffice, null=True, blank=True, on_delete=models.PROTECT, related_name="custody_records"
    )
    current_office_snapshot = models.CharField(max_length=150, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    filed_at = models.DateTimeField(null=True, blank=True)
    objects = ExternalRecordQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(current_office__isnull=False, current_external_office__isnull=True)
                    | Q(current_office__isnull=True, current_external_office__isnull=False)
                ),
                name="external_case_custodian_xor",
            )
        ]

    def __str__(self):
        return self.display_tracking_number

    @property
    def display_tracking_number(self):
        return "Draft" if self.tracking_number.startswith("DRAFT-") else self.tracking_number

    @property
    def current_step(self):
        return self.routing_steps.order_by("-sequence").first()

    def can_manage(self, user):
        return bool(
            getattr(user, "is_authenticated", False)
            and user.is_active
            and user.is_office_admin
            and (user.is_system_admin or user.office_id == self.managing_office_id)
        )

    def can_act(self, user):
        if not getattr(user, "is_authenticated", False) or not user.is_active or not user.can_start_work:
            return False
        step = self.current_step
        if step and not step.received_at:
            return False
        if self.current_external_office_id:
            return self.can_manage(user)
        return bool(user.office_id and user.office_id == self.current_office_id)

    def can_confirm_receipt(self, user):
        step = self.current_step
        return bool(
            step
            and step.to_office_id
            and not step.received_at
            and getattr(user, "is_active", False)
            and user.can_start_work
            and user.office_id == step.to_office_id
        )

    def direction_for(self, office):
        office_id = getattr(office, "pk", office)
        step = self.current_step
        if step and step.to_office_id == office_id:
            return "incoming"
        if step and step.from_office_id == office_id:
            return "outgoing"
        return "managed"

    def get_absolute_url(self):
        return reverse("tracking:external_detail", kwargs={"pk": self.pk})


class ExternalNumberSequence(models.Model):
    office = models.ForeignKey("accounts.Office", on_delete=models.PROTECT)
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["office", "year", "month"], name="external_number_scope")]

    def __str__(self):
        return f"{self.office_id} {self.year}-{self.month:02d}: {self.last_number}"


class ExternalRoutingStep(models.Model):
    class Action(models.TextChoices):
        SEND = "SEND", "Sent"
        FORWARD = "FORWARD", "Forwarded"
        RETURN = "RETURN", "Returned"

    record = models.ForeignKey(ExternalTrackingRecord, on_delete=models.CASCADE, related_name="routing_steps")
    sequence = models.PositiveIntegerField()
    action = models.CharField(max_length=8, choices=Action.choices, default=Action.SEND)
    from_office = models.ForeignKey(
        "accounts.Office", null=True, blank=True, on_delete=models.PROTECT, related_name="external_steps_sent"
    )
    from_external_office = models.ForeignKey(
        ExternalOffice, null=True, blank=True, on_delete=models.PROTECT, related_name="steps_sent"
    )
    to_office = models.ForeignKey(
        "accounts.Office", null=True, blank=True, on_delete=models.PROTECT, related_name="external_steps_received"
    )
    to_external_office = models.ForeignKey(
        ExternalOffice, null=True, blank=True, on_delete=models.PROTECT, related_name="steps_received"
    )
    from_name_snapshot = models.CharField(max_length=150)
    to_name_snapshot = models.CharField(max_length=150)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="external_steps_sent"
    )
    sent_recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="external_dispatches_recorded",
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="external_steps_received",
    )
    receipt_recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="external_receipts_recorded",
    )
    sent_at = models.DateTimeField(default=timezone.now)
    sent_recorded_at = models.DateTimeField(default=timezone.now)
    received_at = models.DateTimeField(null=True, blank=True)
    received_recorded_at = models.DateTimeField(null=True, blank=True)
    instructions = models.TextField(blank=True)
    receipt_note = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["record", "sequence"], name="external_step_sequence"),
            models.UniqueConstraint(
                fields=["record"], condition=Q(received_at__isnull=True), name="external_one_pending_recipient"
            ),
            models.CheckConstraint(
                condition=(
                    Q(from_office__isnull=False, from_external_office__isnull=True)
                    | Q(from_office__isnull=True, from_external_office__isnull=False)
                ),
                name="external_step_sender_xor",
            ),
            models.CheckConstraint(
                condition=(
                    Q(to_office__isnull=False, to_external_office__isnull=True)
                    | Q(to_office__isnull=True, to_external_office__isnull=False)
                ),
                name="external_step_recipient_xor",
            ),
            models.CheckConstraint(
                condition=Q(received_at__isnull=True) | Q(received_at__gte=models.F("sent_at")),
                name="external_step_receipt_chronology",
            ),
        ]

    def __str__(self):
        return f"{self.record_id} #{self.sequence}"

    @property
    def is_received(self):
        return self.received_at is not None


class ExternalActivity(models.Model):
    record = models.ForeignKey(ExternalTrackingRecord, on_delete=models.CASCADE, related_name="activities")
    routing_step = models.ForeignKey(
        ExternalRoutingStep, null=True, blank=True, on_delete=models.PROTECT, related_name="activities"
    )
    event = models.CharField(max_length=24)
    message = models.CharField(max_length=255)
    detail = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="external_activities"
    )
    actor_office = models.ForeignKey(
        "accounts.Office", null=True, on_delete=models.SET_NULL, related_name="external_activities"
    )
    actor_name_snapshot = models.CharField(max_length=150)
    actor_office_snapshot = models.CharField(max_length=150, blank=True)
    actual_at = models.DateTimeField(null=True, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["recorded_at", "pk"]

    def __str__(self):
        return self.message

    @property
    def created_at(self):
        return self.recorded_at


class ExternalAttachment(AttachmentVersionMixin, TimeStampedModel):
    file_group = models.ForeignKey("tracking.ExternalAttachmentGroup", on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField(default=1)
    change_summary = models.CharField(max_length=1000, blank=True)
    uploader_name_snapshot = models.CharField(max_length=150, blank=True)
    uploader_office_snapshot = models.CharField(max_length=150, blank=True)
    objects = ImmutableFileQuerySet.as_manager()
    record = models.ForeignKey(ExternalTrackingRecord, on_delete=models.CASCADE, related_name="attachments")
    routing_step = models.ForeignKey(
        ExternalRoutingStep, null=True, blank=True, on_delete=models.PROTECT, related_name="attachments"
    )
    file = models.FileField(upload_to=external_attachment_path, max_length=400)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True, db_index=True)
    note = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="external_uploads"
    )

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [models.UniqueConstraint(fields=["file_group", "version_number"], name="external_attachment_group_version_unique")]

    def __str__(self):
        return self.original_name


class ExternalActionRevision(models.Model):
    record = models.ForeignKey(ExternalTrackingRecord, on_delete=models.CASCADE, related_name="revisions")
    step = models.ForeignKey(ExternalRoutingStep, null=True, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    reason = models.TextField()
    old_values = models.JSONField(default=dict)
    new_values = models.JSONField(default=dict)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="external_revisions"
    )
    actor_name_snapshot = models.CharField(max_length=150)
    actor_office_snapshot = models.CharField(max_length=150, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["recorded_at", "pk"]
        constraints = [models.UniqueConstraint(fields=["record", "revision"], name="external_revision_unique")]

    def __str__(self):
        return f"{self.record_id} revision {self.revision}"
