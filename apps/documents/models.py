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

from datetime import date, timedelta
from uuid import uuid4

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.document_types import DocumentTypesMixin
from apps.core.models import DocumentType, MetadataFieldDefinition, Tag, TimeStampedModel
from apps.tracking.file_version_models import ImmutableFileMixin, ImmutableFileQuerySet


def document_upload_path(instance, filename: str) -> str:
    document = instance.document
    office_code = document.office.code if document.office_id else "GENERAL"
    return f"documents/{office_code}/{document.year or timezone.now().year}/{document.pk or 'new'}/{uuid4().hex}/{filename}"


class Source(models.TextChoices):
    DTS = "DTS", "Archived from tracking"
    UPLOAD = "UPLOAD", "Uploaded file"
    SCAN = "SCAN", "Scanned document"


class Collection(models.TextChoices):
    OVPA = "OVPA", "OVPA"
    EXTERNAL = "EXTERNAL", "External UDM offices"


#: The one rule for "completed" versus "historical" in the repository.
#:
#: Completed means the document came out of tracking — a record the offices
#: worked and an administrator approved into the repository. Everything else is
#: historical: a file or a scan of something that happened before, or outside,
#: the system.
#:
#: Named as the DTS side only, and the other side derived by exclusion, so a
#: fourth source added later is historical without anybody remembering to list
#: it. That is what the repository tile already does with its `{% else %}` — and
#: what the dashboard breakdown did not: it tested `source == UPLOAD` for
#: historical, so a scanned document was counted Completed on the ring while the
#: tile beside it read Historical. Same document, two answers, because the rule
#: existed twice.
COMPLETED_SOURCE = Source.DTS

#: The repository filter's name for "every origin but tracking": the
#: historical side of the same rule, by exclusion, so the Historical figure on
#: the dashboard and the page its segment opens cannot count different sets.
HISTORICAL_FILTER = "historical"
DELETION_RECOVERY_DAYS = 30


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
    def in_collection(self, collection=Collection.OVPA):
        return self.filter(collection=collection)

    def visible_to(self, user):
        if (not user.is_authenticated or not user.is_active
                or user.role not in {"USER", "VIEWER", "ADMIN", "SYSTEM_ADMIN"}):
            return self.none()
        if user.is_system_admin:
            return self
        if not user.office_id:
            return self.none()
        if user.is_office_admin:
            if not user.office_id:
                return self.none()
            # Administrative duties never expand the office's repository.
            # OVPA sharing, grants and a previous uploader identity cannot
            # turn this office-scoped role into a cross-office reader.
            return self.filter(office_id=user.office_id).filter(
                ~Q(access_level=AccessLevel.RESTRICTED) | Q(uploaded_by=user)
                | Q(grants__user=user) | Q(grants__office_id=user.office_id)
            ).distinct()
        conditions = Q(access_level=AccessLevel.OVPA) | Q(uploaded_by=user) | Q(grants__user=user)
        if user.office_id:
            office_history = (
                Q(office_id=user.office_id)
                | Q(tracking_record__routing_steps__to_office_id=user.office_id)
                | Q(tracking_record__originating_office_id=user.office_id)
                | Q(external_tracking_record__routing_steps__to_office_id=user.office_id)
                | Q(external_tracking_record__routing_steps__from_office_id=user.office_id)
            )
            conditions |= Q(grants__office_id=user.office_id) | (office_history & ~Q(access_level=AccessLevel.RESTRICTED))
        return self.filter(conditions).distinct()

    def with_related(self):
        return self.select_related("office", "document_type", "uploaded_by", "tracking_record", "external_tracking_record").prefetch_related(
            "tags", "document_types"
        )

    def due_for_retention_review(self, on_date=None):
        return self.filter(retention_until__isnull=False, retention_until__lte=on_date or timezone.localdate())

    def upcoming_deletion_review(self, on_date=None):
        today = on_date or timezone.localdate()
        return self.filter(retention_until__gt=today, retention_until__lte=today + timedelta(days=90))


class DocumentManager(models.Manager.from_queryset(DocumentQuerySet)):
    """Named at module level so Django's makemigrations can import it by dotted
    path (apps.documents.models.DocumentManager). See TrackingRecordManager in
    apps/tracking/models.py for why the inline form fails.
    """


class Document(DocumentTypesMixin, TimeStampedModel):
    """One archived or imported document with rich, searchable metadata."""

    title = models.CharField(max_length=255)
    training_eligible = models.BooleanField(default=False, help_text="Explicit permission to use this record for scoped local topic training.")
    training_family = models.CharField(max_length=64, blank=True, help_text="Optional shared family ID for closely related records.")
    description = models.TextField(blank=True, help_text="Short abstract shown in search results.")
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.UPLOAD)
    collection = models.CharField(max_length=8, choices=Collection.choices, default=Collection.OVPA, db_index=True)
    external_tracking_record = models.OneToOneField(
        "tracking.ExternalTrackingRecord", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="archived_document",
    )
    external_offices = models.ManyToManyField(
        "tracking.ExternalOffice", through="ExternalDocumentOffice", blank=True, related_name="documents",
    )

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
    document_types = models.ManyToManyField(DocumentType, blank=True, related_name="typed_documents")
    document_date = models.DateField(null=True, blank=True, help_text="Date printed on the document.")
    year = models.PositiveSmallIntegerField(db_index=True)

    author_name = models.CharField(max_length=150, blank=True, verbose_name="from / author")
    recipient_name = models.CharField(max_length=150, blank=True, verbose_name="to / recipient")
    signatory = models.CharField(max_length=150, blank=True)
    page_count = models.PositiveIntegerField(default=0)

    tags = models.ManyToManyField(Tag, blank=True, related_name="documents")
    access_level = models.CharField(max_length=12, choices=AccessLevel.choices, default=AccessLevel.OFFICE)
    retention_until = models.DateField(null=True, blank=True, verbose_name="deletion review date")

    ocr_text = models.TextField(blank=True)
    ocr_status = models.CharField(max_length=8, choices=OcrStatus.choices, default=OcrStatus.PENDING)
    extraction_generation = models.PositiveBigIntegerField(default=0, editable=False)
    extraction_failed_generation = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    ocr_engine = models.CharField(max_length=32, blank=True)
    ocr_confidence = models.FloatField(null=True, blank=True)
    ocr_language = models.CharField(max_length=8, choices=OcrLanguage.choices, default=OcrLanguage.AUTO)
    allow_external_ocr = models.BooleanField(
        default=False,
        help_text="Allow scanned content to be sent to the configured external OCR provider.",
    )
    ocr_notes = models.TextField(blank=True, help_text="Operator-visible extraction and retry notes.")

    index_title = models.TextField(blank=True, editable=False)
    index_meta = models.TextField(blank=True, editable=False)
    index_extra = models.TextField(blank=True, editable=False)
    search_vector = SearchVectorField(null=True, editable=False)
    metadata_search_vector = SearchVectorField(null=True, editable=False)
    search_index_revision = models.CharField(max_length=64, blank=True, editable=False)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="uploaded_documents"
    )
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True, editable=False)
    purge_attempted_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = DocumentManager()

    class Meta:
        ordering = ["-document_date", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(tracking_record__isnull=True) | Q(external_tracking_record__isnull=True), name="document_one_tracking_origin"),
            models.CheckConstraint(condition=Q(tracking_record__isnull=True) | Q(collection=Collection.OVPA), name="document_legacy_origin_collection"),
            models.CheckConstraint(condition=Q(external_tracking_record__isnull=True) | Q(collection=Collection.EXTERNAL), name="document_external_origin_collection"),
        ]
        indexes = [
            GinIndex(fields=["search_vector"], name="doc_search_vector_gin"),
            GinIndex(fields=["metadata_search_vector"], name="doc_metadata_vector_gin"),
            models.Index(fields=["office", "year"]),
            models.Index(fields=["document_type", "year"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("documents:detail", args=[self.pk])

    def retain_extraction_failure(self, notes=""):
        """Retain a generation's failure while sibling jobs change run state.

        Callers hold the document lock and persist these fields with their
        extraction result. Legacy FAILED rows are adopted when next touched;
        no historical text or notes need a bulk data migration.
        """
        prior = self.ocr_notes if (
            self.extraction_failed_generation is not None or self.ocr_status == OcrStatus.FAILED
        ) else ""
        lines = list(dict.fromkeys(line for line in f"{prior}\n{notes}".splitlines() if line.strip()))
        self.extraction_failed_generation = self.extraction_generation
        self.ocr_status = OcrStatus.FAILED
        self.ocr_notes = "\n".join(lines)[:4000] or (
            "A saved file's text extraction did not finish. Retry extraction to read all document files."
        )

    @property
    def tracking_origin(self):
        return self.external_tracking_record if self.external_tracking_record_id else self.tracking_record

    @property
    def repository_url(self):
        return reverse("documents:external_repository" if self.collection == Collection.EXTERNAL else "documents:repository")

    @property
    def is_external(self):
        return self.collection == Collection.EXTERNAL

    def save(self, *args, **kwargs):
        if not self.year:
            self.year = (self.document_date or timezone.localdate()).year
        if self._state.adding and self.retention_until is None:
            self.retention_until = self.default_retention_until()
            if self.retention_until and kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"retention_until"}
        super().save(*args, **kwargs)

    def default_retention_until(self):
        if not self.document_type_id or not self.document_type.retention_years:
            return None
        if not self.document_date and self.year and not 1 <= self.year <= date.max.year:
            return None
        base = self.document_date or (date(self.year, 12, 31) if self.year else timezone.localdate())
        target_year = base.year + self.document_type.retention_years
        if target_year > date.max.year:
            return None
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
            filter(None, [self.title, self.reference_number, getattr(self.tracking_origin, "tracking_number", ""), getattr(self.tracking_origin, "original_reference", "")])
        )[:4000]
        self.index_meta = " ".join(
            filter(
                None,
                [
                    self.office.name if self.office_id else "",
                    self.office.code if self.office_id else "",
                    self.document_type_names,
                    tags,
                    " ".join(self.external_office_links.values_list("name_snapshot", flat=True)) if self.pk and self.is_external else "",
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
        metadata_vector = (SearchVector("index_title", weight="A", config=config)
            + SearchVector("index_meta", weight="B", config=config)
            + SearchVector("index_extra", weight="C", config=config))
        Document.objects.filter(pk=self.pk).update(search_vector=vector, metadata_search_vector=metadata_vector)

    # -- helpers ----------------------------------------------------------
    @property
    def primary_file(self):
        return self.files.filter(is_primary=True).first() or self.files.first()

    @property
    def file_page_count(self) -> int:
        """Known pages across every attachment, including partial extraction jobs."""
        return self.files.aggregate(total=models.Sum("page_count"))["total"] or 0

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
        return Document.objects.filter(pk=self.pk, is_active=True).visible_to(user).exists()

    def can_user_edit(self, user) -> bool:
        if not self.is_active or not user.is_authenticated or not user.can_start_work:
            return False
        if user.is_system_admin:
            return True
        if self.is_external and not user.is_office_admin:
            return False
        if not self.can_user_view(user):
            return False
        # Authorship and grants can preserve read access after a transfer.
        # They do not give a former owning office permission to change records.
        return bool(user.office_id) and user.office_id == self.office_id and user.is_records_staff

    def can_user_review_deletion(self, user, *, include_deleted=False) -> bool:
        if not user.is_authenticated or not user.can_start_work or not user.is_office_admin:
            return False
        if not include_deleted and not self.is_active:
            return False
        if not user.is_system_admin and self.office_id != user.office_id:
            return False
        return Document.objects.filter(pk=self.pk).visible_to(user).exists()

    @property
    def deletion_purge_at(self):
        return self.deleted_at + timedelta(days=DELETION_RECOVERY_DAYS) if self.deleted_at else None

    @property
    def deletion_recovery_open(self):
        return bool(not self.is_active and self.deletion_purge_at and timezone.now() < self.deletion_purge_at)


class ExternalDocumentOffice(models.Model):
    """Counterpart names are retained even when directory entries are renamed."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="external_office_links")
    external_office = models.ForeignKey("tracking.ExternalOffice", on_delete=models.PROTECT, related_name="document_links")
    name_snapshot = models.CharField(max_length=255)
    code_snapshot = models.CharField(max_length=32, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["document", "external_office"], name="unique_external_document_office")]

    def __str__(self):
        return self.name_snapshot


class DeletionReview(models.Model):
    """Human decisions remain append-only until permanent repository cleanup."""

    class Decision(models.TextChoices):
        KEEP = "KEEP", "Kept and rescheduled"
        DELETE = "DELETE", "Removed from repository"
        RESTORE = "RESTORE", "Restored to repository"

    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="deletion_reviews")
    decision = models.CharField(max_length=8, choices=Decision.choices)
    reason = models.TextField(max_length=2000)
    previous_review_date = models.DateField(null=True, blank=True)
    next_review_date = models.DateField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    reviewer_label = models.CharField(max_length=150)
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-reviewed_at", "-pk"]

    def __str__(self):
        return f"{self.get_decision_display()}: {self.document}"


class DocumentFile(ImmutableFileMixin, TimeStampedModel):
    immutable_fields = ("document_id", "file", "original_name", "content_type", "size", "checksum", "uploaded_by_id", "created_at")
    objects = ImmutableFileQuerySet.as_manager()
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="files")
    file = models.FileField(upload_to=document_upload_path, max_length=400)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True, db_index=True)
    is_primary = models.BooleanField(default=False)
    page_count = models.PositiveIntegerField(default=0)
    extracted_chars = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="document_files"
    )

    class Meta:
        ordering = ["-is_primary", "created_at"]

    def __str__(self) -> str:
        return self.original_name

    def save(self, *args, create_snapshot=True, **kwargs):
        from django.db import transaction

        from apps.core.storage_references import guarded_file_reference

        from .file_versions import initial_snapshot
        self._check_immutable()
        creating = self._state.adding
        with transaction.atomic(), guarded_file_reference(self.file):
            result = super().save(*args, **kwargs)
            if creating and create_snapshot:
                initial_snapshot(self)
            return result

    @property
    def version_number(self):
        cached = getattr(self, "_prefetched_objects_cache", {}).get("versions")
        if cached is not None:
            return max((row.version_number for row in cached), default=1)
        return self.versions.order_by("-version_number").values_list("version_number", flat=True).first() or 1

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

    Historical metadata is not a supervised negative-label dataset. Explicit
    TagReview decisions are captured separately for scoped training snapshots.
    """

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="suggestions")
    extraction_run = models.OneToOneField("documents.ExtractionRun", null=True, blank=True,
        on_delete=models.CASCADE, related_name="suggestion_record")
    engine = models.CharField(max_length=32, default="rules")
    engine_version = models.CharField(max_length=64, default="1.0")
    suggested = models.JSONField(default=dict)
    input_revision = models.CharField(max_length=64, blank=True)
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


class SearchResultClick(models.Model):
    """One result navigation event, stored separately so one query may have many clicks."""

    query_log = models.ForeignKey(SearchQueryLog, on_delete=models.CASCADE, related_name="result_clicks")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="search_result_clicks"
    )
    document = models.ForeignKey(Document, null=True, on_delete=models.SET_NULL, related_name="search_result_click_events")
    rank = models.PositiveSmallIntegerField()
    relevance = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Query {self.query_log_id} clicked document {self.document_id} at rank {self.rank}"


from .auto_models import TagApplicationIntent  # noqa: E402,F401
from .file_versions import DocumentFileVersion  # noqa: E402,F401
from .intelligence_models import SavedDocumentView  # noqa: E402,F401
from .ml.models import SnapshotSource, TagModel, TrainingSnapshot  # noqa: E402,F401
from .processing_models import ExtractionRun, ExtractionUnit, InferenceIntent, SearchChunk  # noqa: E402,F401
from .tag_models import TagAlias, TagContribution, TagPrediction, TagReview, VocabularyRevision  # noqa: E402,F401
