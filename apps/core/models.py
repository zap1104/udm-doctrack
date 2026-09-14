"""Shared building blocks and master data for UDM DocTrack.

Everything here is data an administrator can edit without a developer:
document types, tags, tag rules and custom metadata fields.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.text import slugify


class TimeStampedModel(models.Model):
    """Adds created_at / updated_at to any model."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ActiveManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


class DocumentType(TimeStampedModel):
    """Memorandum, Letter, Work Order, Purchase Request, Endorsement, ..."""

    code = models.CharField(max_length=24, unique=True, help_text="Short code, e.g. MEMO.")
    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=255, blank=True)
    retention_years = models.PositiveSmallIntegerField(
        default=5, help_text="How long records of this type are normally kept."
    )
    sort_order = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "document type"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)


class Tag(TimeStampedModel):
    """A keyword used for filing and searching."""

    class Category(models.TextChoices):
        SUBJECT = "SUBJECT", "Subject"
        PROCESS = "PROCESS", "Process"
        OFFICE = "OFFICE", "Office"
        PROJECT = "PROJECT", "Project"
        OTHER = "OTHER", "Other"

    name = models.CharField(max_length=64, unique=True)
    slug = models.SlugField(max_length=72, unique=True, blank=True)
    category = models.CharField(max_length=16, choices=Category.choices, default=Category.SUBJECT)
    description = models.CharField(max_length=255, blank=True)
    usage_count = models.PositiveIntegerField(default=0, editable=False)
    is_active = models.BooleanField(default=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.name = " ".join(self.name.split()).lower()
        if not self.slug:
            self.slug = slugify(self.name)[:72] or "tag"
        super().save(*args, **kwargs)

    @classmethod
    def get_or_create_by_name(cls, raw_name: str, category: str = Category.SUBJECT):
        name = " ".join(str(raw_name).split()).lower()[:64]
        if not name:
            return None, False
        existing = cls.objects.filter(name=name).first()
        if existing:
            return existing, False
        return cls.objects.create(name=name, category=category), True


class TagRule(TimeStampedModel):
    """Deterministic rule that suggests metadata from extracted document text.

    This is the *non-AI* recommendation engine described in the flowchart.
    A future AI engine writes into the same suggestion structure, so the UI
    and the review step never have to change.
    """

    class MatchType(models.TextChoices):
        CONTAINS = "CONTAINS", "Text contains phrase"
        ALL_WORDS = "ALL_WORDS", "Text contains all words"
        ANY_WORD = "ANY_WORD", "Text contains any word"
        REGEX = "REGEX", "Regular expression"

    class Field(models.TextChoices):
        FULL_TEXT = "FULL_TEXT", "Full extracted text"
        TITLE = "TITLE", "Title / filename"
        FIRST_PAGE = "FIRST_PAGE", "First 2000 characters"

    name = models.CharField(max_length=120)
    pattern = models.CharField(
        max_length=255,
        help_text="Phrase, comma-separated words, or regular expression depending on match type.",
    )
    match_type = models.CharField(max_length=16, choices=MatchType.choices, default=MatchType.CONTAINS)
    search_field = models.CharField(max_length=16, choices=Field.choices, default=Field.FULL_TEXT)

    suggest_tag = models.ForeignKey(
        Tag, null=True, blank=True, on_delete=models.CASCADE, related_name="rules"
    )
    suggest_document_type = models.ForeignKey(
        DocumentType, null=True, blank=True, on_delete=models.CASCADE, related_name="rules"
    )
    suggest_office = models.ForeignKey(
        "accounts.Office", null=True, blank=True, on_delete=models.CASCADE, related_name="tag_rules"
    )
    suggest_metadata_key = models.CharField(
        max_length=64, blank=True, help_text="Optional metadata field key to fill."
    )
    suggest_metadata_value = models.CharField(max_length=255, blank=True)

    confidence = models.FloatField(default=0.8, help_text="0.0 – 1.0 confidence when this rule fires.")
    priority = models.PositiveSmallIntegerField(default=100, help_text="Lower runs first.")
    is_active = models.BooleanField(default=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        ordering = ["priority", "name"]
        verbose_name = "metadata rule"

    def __str__(self) -> str:
        return self.name


class MetadataFieldDefinition(TimeStampedModel):
    """Admin-defined metadata field, so new fields do not need a code change."""

    class FieldType(models.TextChoices):
        TEXT = "TEXT", "Text"
        LONG_TEXT = "LONG_TEXT", "Long text"
        NUMBER = "NUMBER", "Number"
        DATE = "DATE", "Date"
        CHOICE = "CHOICE", "Choice list"
        BOOLEAN = "BOOLEAN", "Yes / No"

    key = models.SlugField(
        max_length=64, unique=True, help_text="Machine key, e.g. control_number. Never change it later."
    )
    label = models.CharField(max_length=120)
    field_type = models.CharField(max_length=16, choices=FieldType.choices, default=FieldType.TEXT)
    choices_csv = models.CharField(
        max_length=500, blank=True, help_text="Comma-separated options (choice fields only)."
    )
    help_text = models.CharField(max_length=255, blank=True)
    is_required = models.BooleanField(default=False)
    is_searchable = models.BooleanField(default=True, help_text="Include the value in the search index.")
    show_in_list = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        ordering = ["sort_order", "label"]
        verbose_name = "metadata field"

    def __str__(self) -> str:
        return self.label

    @property
    def choices(self) -> list[str]:
        return [choice.strip() for choice in self.choices_csv.split(",") if choice.strip()]


class AuditLog(models.Model):
    """Append-only record of who did what. Never edited, never deleted by the app."""

    class Action(models.TextChoices):
        LOGIN = "LOGIN", "Signed in"
        LOGIN_FAILED = "LOGIN_FAILED", "Failed sign-in"
        LOGOUT = "LOGOUT", "Signed out"
        CREATE = "CREATE", "Created"
        UPDATE = "UPDATE", "Updated"
        DELETE = "DELETE", "Deleted"
        ROUTE = "ROUTE", "Routed"
        RECEIVE = "RECEIVE", "Confirmed receipt"
        COMPLETE = "COMPLETE", "Completed"
        ARCHIVE = "ARCHIVE", "Archived"
        DOWNLOAD = "DOWNLOAD", "Downloaded"
        PRINT = "PRINT", "Printed"
        SEARCH = "SEARCH", "Searched"
        PERMISSION = "PERMISSION", "Changed access"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries"
    )
    actor_label = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=16, choices=Action.choices)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    summary = models.CharField(max_length=255)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    extra = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "audit log entry"
        verbose_name_plural = "audit log"
        indexes = [
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["action", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_label} {self.action}"


class NotificationQuerySet(models.QuerySet):
    def unread_for(self, user):
        if not getattr(user, "is_authenticated", False) or not user.office_id:
            return self.none()
        return self.filter(office_id=user.office_id, resolved_at__isnull=True).exclude(reads__user=user)


class Notification(models.Model):
    """One office-level event; read state belongs to each user, not the office."""

    class Kind(models.TextChoices):
        ROUTED = "ROUTED", "Needs receipt"
        RECEIVED = "RECEIVED", "Receipt confirmed"
        COMPLETED = "COMPLETED", "Completed"
        SHARED = "SHARED", "Shared with your office"
        #: To the office that *sent* it: still nobody has confirmed receipt.
        #: The routed notice goes to the recipient and tells the sender
        #: nothing, so a document that quietly went unreceived was invisible to
        #: the one office with a reason to chase it.
        UNRECEIVED = "UNRECEIVED", "Still not received"
        #: To whoever is holding it: the deadline has passed. Raised by the
        #: deadline itself, so setting one is what arms the notice — a deadline
        #: nothing acts on is a note in a field.
        OVERDUE = "OVERDUE", "Past its deadline"

    office = models.ForeignKey("accounts.Office", on_delete=models.CASCADE, related_name="notifications")
    tracking_record = models.ForeignKey(
        "tracking.TrackingRecord", null=True, blank=True, on_delete=models.SET_NULL, related_name="notifications"
    )
    document = models.ForeignKey(
        "documents.Document", null=True, blank=True, on_delete=models.SET_NULL, related_name="notifications"
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    title = models.CharField(max_length=160)
    message = models.CharField(max_length=255)
    url = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["office", "resolved_at", "-created_at"],
                name="notif_office_resolved_created",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.office_id})"


class NotificationRead(models.Model):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_reads")
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["notification", "user"], name="uniq_notification_read_user")]
        indexes = [models.Index(fields=["user"], name="notif_read_user_idx")]

    def __str__(self):
        return f"{self.user} read notification {self.notification_id}"


class NotificationPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preferences")
    in_app_enabled = models.BooleanField(default=True)

    def __str__(self):
        return f"Notification preferences for {self.user}"
