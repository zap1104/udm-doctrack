"""Document management (DMS): completed records, historical uploads, metadata.

Search notes
------------
`search_vector` is a stored PostgreSQL tsvector built from four denormalised
columns so the query never has to join tags or metadata:

    index_title  → weight A   title, reference number, tracking number
    index_meta   → weight B   subject, office, document type, tags
    index_extra  → weight C   metadata values, author, recipient, description
    ocr_text     → weight D   full extracted / OCR text

Rebuild with `Document.rebuild_index()` or `manage.py reindex_documents`.
"""

from __future__ import annotations

from datetime import date

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.models import DocumentType, MetadataFieldDefinition, Tag, TimeStampedModel


def document_upload_path(instance, filename: str) -> str:
    document = instance.document
    office_code = document.office.code if document.office_id else "GENERAL"
    return f"documents/{office_code}/{document.year or timezone.now().year}/{document.pk or 'new'}/{filename}"


class Source(models.TextChoices):
    DTS = "DTS", "Archived from tracking"
    UPLOAD = "UPLOAD", "Uploaded file"
    SCAN = "SCAN", "Scanned document"


class OcrStatus(models.TextChoices):
    PENDING = "PENDING", "Waiting for text extraction"
    RUNNING = "RUNNING", "Extracting"
    DONE = "DONE", "Text extracted"
    EMPTY = "EMPTY", "No text found"
    FAILED = "FAILED", "Extraction failed"
    SKIPPED = "SKIPPED", "Skipped"


class OcrLanguage(models.TextChoices):
    AUTO = "auto", "Automatic (English and Filipino)"
    ENGLISH = "eng", "English"
    FILIPINO = "fil", "Filipino"


class AccessLevel(models.TextChoices):
    OFFICE = "OFFICE", "Owning office only"
    OVPA = "OVPA", "All OVPA offices"
    RESTRICTED = "RESTRICTED", "Named offices or users only"


class DocumentQuerySet(models.QuerySet):
    def visible_to(self, user):
        if not user.is_authenticated:
            return self.none()
        if user.is_system_admin:
            return self
        conditions = Q(access_level=AccessLevel.OVPA) | Q(uploaded_by=user) | Q(grants__user=user)
        if user.office_id:
            conditions |= (
                Q(office_id=user.office_id)
                | Q(grants__office_id=user.office_id)
                | Q(tracking_record__routing_steps__to_office_id=user.office_id)
                | Q(tracking_record__originating_office_id=user.office_id)
            )
        return self.filter(conditions).distinct()

    def with_related(self):
        return self.select_related("office", "document_type", "uploaded_by", "tracking_record").prefetch_related(
            "tags"
        )

    def due_for_retention_review(self, on_date=None):
        return self.filter(retention_until__isnull=False, retention_until__lte=on_date or timezone.localdate())


class DocumentManager(models.Manager.from_queryset(DocumentQuerySet)):
    """Named at module level so Django's makemigrations can import it by dotted
    path (apps.documents.models.DocumentManager). See TrackingRecordManager in
    apps/tracking/models.py for why the inline form fails.
    """


class Document(TimeStampedModel):
    """One archived or imported document with rich, searchable metadata."""

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, help_text="Short abstract shown in search results.")
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.UPLOAD)

    tracking_record = models.OneToOneField(
        "tracking.TrackingRecord",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="archived_document",
    )
    reference_number = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="Tracking or control number, including numbers from paper records.",
    )

    office = models.ForeignKey(
        "accounts.Office", on_delete=models.PROTECT, related_name="documents", verbose_name="owning office"
    )
    document_type = models.ForeignKey(
        DocumentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="documents"
    )
    document_date = models.DateField(null=True, blank=True, help_text="Date printed on the document.")
    year = models.PositiveSmallIntegerField(db_index=True)

    author_name = models.CharField(max_length=150, blank=True, verbose_name="from / author")
    recipient_name = models.CharField(max_length=150, blank=True, verbose_name="to / recipient")
    signatory = models.CharField(max_length=150, blank=True)
    page_count = models.PositiveSmallIntegerField(default=0)

    tags = models.ManyToManyField(Tag, blank=True, related_name="documents")
    access_level = models.CharField(max_length=12, choices=AccessLevel.choices, default=AccessLevel.OFFICE)
    retention_until = models.DateField(null=True, blank=True)

    ocr_text = models.TextField(blank=True)
    ocr_status = models.CharField(max_length=8, choices=OcrStatus.choices, default=OcrStatus.PENDING)
    ocr_engine = models.CharField(max_length=32, blank=True)
    ocr_confidence = models.FloatField(null=True, blank=True)
    ocr_language = models.CharField(max_length=8, choices=OcrLanguage.choices, default=OcrLanguage.AUTO)
    allow_external_ocr = models.BooleanField(
        default=True,
        help_text="Allow scanned content to be sent to the configured external OCR provider.",
    )
    ocr_notes = models.TextField(blank=True, help_text="Operator-visible extraction and retry notes.")

    index_title = models.TextField(blank=True, editable=False)
    index_meta = models.TextField(blank=True, editable=False)
    index_extra = models.TextField(blank=True, editable=False)
    search_vector = SearchVectorField(null=True, editable=False)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="uploaded_documents"
    )
    is_active = models.BooleanField(default=True)

    objects = DocumentManager()

    class Meta:
        ordering = ["-document_date", "-created_at"]
        indexes = [
            GinIndex(fields=["search_vector"], name="doc_search_vector_gin"),
            models.Index(fields=["office", "year"]),
            models.Index(fields=["document_type", "year"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("documents:detail", args=[self.pk])

    def save(self, *args, **kwargs):
        if not self.year:
            self.year = (self.document_date or timezone.localdate()).year
        if self.retention_until is None:
            self.retention_until = self.default_retention_until()
            if self.retention_until and kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"retention_until"}
        super().save(*args, **kwargs)

    def default_retention_until(self):
        if not self.document_type_id or not self.document_type.retention_years:
            return None
        base = self.document_date or (date(self.year, 12, 31) if self.year else timezone.localdate())
        target_year = base.year + self.document_type.retention_years
        try:
            return base.replace(year=target_year)
        except ValueError:
            return base.replace(year=target_year, month=2, day=28)

    @property
    def retention_is_due(self) -> bool:
        return bool(self.retention_until and self.retention_until <= timezone.localdate())

    @property
    def retention_is_due_soon(self) -> bool:
        if not self.retention_until or self.retention_is_due:
            return False
        return (self.retention_until - timezone.localdate()).days <= 90

    # -- indexing ---------------------------------------------------------
    def build_index_blobs(self) -> None:
        """Fill the denormalised columns that the tsvector is built from."""
        tags = " ".join(tag.name for tag in self.tags.all()) if self.pk else ""
        metadata_values = ""
        if self.pk:
            metadata_values = " ".join(
                value.value for value in self.metadata_values.select_related("field") if value.field.is_searchable
            )
        self.index_title = " ".join(
            filter(None, [self.title, self.reference_number, getattr(self.tracking_record, "tracking_number", "")])
        )[:4000]
        self.index_meta = " ".join(
            filter(
                None,
                [
                    self.office.name if self.office_id else "",
                    self.office.code if self.office_id else "",
                    self.document_type.name if self.document_type_id else "",
                    tags,
                    str(self.year or ""),
                ],
            )
        )[:4000]
        self.index_extra = " ".join(
            filter(None, [self.description, self.author_name, self.recipient_name, self.signatory, metadata_values])
        )[:8000]

    def rebuild_index(self) -> None:
        """Update the blobs and the stored tsvector for this one document."""
        from django.contrib.postgres.search import SearchVector

        self.build_index_blobs()
        Document.objects.filter(pk=self.pk).update(
            index_title=self.index_title, index_meta=self.index_meta, index_extra=self.index_extra
        )
        config = settings.SEARCH_CONFIG
        vector = (
            SearchVector("index_title", weight="A", config=config)
            + SearchVector("index_meta", weight="B", config=config)
            + SearchVector("index_extra", weight="C", config=config)
            + SearchVector("ocr_text", weight="D", config=config)
        )
        Document.objects.filter(pk=self.pk).update(search_vector=vector)

    # -- helpers ----------------------------------------------------------
    @property
    def primary_file(self):
        return self.files.filter(is_primary=True).first() or self.files.first()

    @property
    def tag_names(self) -> list[str]:
        return [tag.name for tag in self.tags.all()]

    @property
    def has_text(self) -> bool:
        return bool(self.ocr_text.strip())

    @property
    def snippet(self) -> str:
        text = (self.description or self.ocr_text or "").strip()
        return (text[:220] + "…") if len(text) > 220 else text

    def can_user_view(self, user) -> bool:
        return Document.objects.filter(pk=self.pk).visible_to(user).exists()

    def can_user_edit(self, user) -> bool:
        if user.is_system_admin:
            return True
        if self.uploaded_by_id == user.pk:
            return True
        return bool(user.office_id) and user.office_id == self.office_id and user.is_records_staff


class DocumentFile(TimeStampedModel):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="files")
    file = models.FileField(upload_to=document_upload_path, max_length=400)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True, db_index=True)
    is_primary = models.BooleanField(default=False)
    page_count = models.PositiveSmallIntegerField(default=0)
    extracted_chars = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="document_files"
    )

    class Meta:
        ordering = ["-is_primary", "created_at"]

    def __str__(self) -> str:
        return self.original_name

    @property
    def extension(self) -> str:
        return (self.original_name.rsplit(".", 1)[-1] if "." in self.original_name else "").upper()


class DocumentMetadata(models.Model):
    """Value of an admin-defined metadata field for one document."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="metadata_values")
    field = models.ForeignKey(MetadataFieldDefinition, on_delete=models.CASCADE, related_name="values")
    value = models.CharField(max_length=500, blank=True)
    value_normalised = models.CharField(max_length=500, blank=True, editable=False, db_index=True)
    source = models.CharField(
        max_length=16,
        default="USER",
        help_text="USER for typed values, RULE/AI for suggestions accepted by the uploader.",
    )

    class Meta:
        unique_together = ("document", "field")
        ordering = ["field__sort_order", "field__label"]
        verbose_name = "metadata value"
        verbose_name_plural = "metadata values"

    def __str__(self) -> str:
        return f"{self.field.label}: {self.value}"

    def save(self, *args, **kwargs):
        self.value_normalised = " ".join(str(self.value or "").split()).lower()[:500]
        super().save(*args, **kwargs)


class MetadataSuggestion(models.Model):
    """What the engine proposed, and what the uploader kept.

    Every row is one training example for the future AI engine — export with
    `manage.py export_training_data`.
    """

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="suggestions")
    engine = models.CharField(max_length=32, default="rules")
    engine_version = models.CharField(max_length=16, default="1.0")
    suggested = models.JSONField(default=dict)
    accepted = models.JSONField(default=dict, blank=True)
    text_sample = models.TextField(blank=True, help_text="First part of the extracted text the engine saw.")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_suggestions"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "metadata suggestion"

    def __str__(self) -> str:
        return f"Suggestion for {self.document_id} ({self.engine})"

    @property
    def was_edited(self) -> bool:
        if not self.accepted:
            return False
        return any(self.accepted.get(key) != value for key, value in (self.suggested or {}).items())


class DocumentAccessGrant(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="grants")
    office = models.ForeignKey(
        "accounts.Office", null=True, blank=True, on_delete=models.CASCADE, related_name="document_grants"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="document_grants"
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="document_grants_given"
    )
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("document", "office", "user")
        verbose_name = "document access grant"

    def __str__(self) -> str:
        return f"{self.document_id} → {self.office or self.user}"


class SearchQueryLog(models.Model):
    """Every search, for tuning relevance later (and for the Reports page)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="search_logs"
    )
    query = models.CharField(max_length=255)
    filters = models.JSONField(default=dict, blank=True)
    result_count = models.PositiveIntegerField(default=0)
    top_score = models.FloatField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    clicked_document = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.SET_NULL, related_name="search_clicks"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "search query log"

    def __str__(self) -> str:
        return f"{self.query} ({self.result_count})"
