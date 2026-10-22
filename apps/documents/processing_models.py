"""Derived processing provenance; original file/version records remain immutable."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .processing_contracts import EXTRACTOR_VERSION, Coverage, WorkState


class ExtractionRun(models.Model):
    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE, related_name="extraction_runs")
    file_version = models.ForeignKey("documents.DocumentFileVersion", on_delete=models.CASCADE, related_name="extraction_runs")
    generation = models.PositiveBigIntegerField()
    content_hash = models.CharField(max_length=64, blank=True)
    settings_digest = models.CharField(max_length=64)
    settings_snapshot = models.JSONField(default=dict)
    extractor_version = models.CharField(max_length=32, default=EXTRACTOR_VERSION)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    actor_was_recorded = models.BooleanField(default=False)
    purpose = models.CharField(max_length=32, default="user")
    state = models.CharField(max_length=20, choices=WorkState.choices, default=WorkState.PENDING, db_index=True)
    coverage = models.CharField(max_length=12, choices=Coverage.choices, default=Coverage.UNKNOWN)
    expected_units = models.PositiveIntegerField(null=True, blank=True)
    warnings = models.JSONField(default=list)
    is_current = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    lease_token = models.CharField(max_length=64, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_reason = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["file_version", "generation", "settings_digest"], name="extraction_run_identity")]
        indexes = [models.Index(fields=["state", "next_attempt_at"], name="extraction_recovery_due")]

    def __str__(self):
        return f"Extraction {self.pk}: file version {self.file_version_id}, {self.state}"

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def clean(self):
        if self.file_version_id and self.document_id and self.file_version.document_file.document_id != self.document_id:
            raise ValidationError("Extraction must belong to its file version's document.")


class ExtractionUnit(models.Model):
    run = models.ForeignKey(ExtractionRun, on_delete=models.CASCADE, related_name="units")
    locator_key = models.CharField(max_length=240)
    ordinal = models.PositiveIntegerField()
    kind = models.CharField(max_length=32)
    raw_text = models.TextField(blank=True)
    normalized_text = models.TextField(blank=True)
    method = models.CharField(max_length=64, default="native")
    state = models.CharField(max_length=20, choices=WorkState.choices, default=WorkState.PENDING)
    coverage = models.CharField(max_length=12, choices=Coverage.choices, default=Coverage.UNKNOWN)
    warnings = models.JSONField(default=list)
    truncated = models.BooleanField(default=False)
    provider_confidence = models.FloatField(null=True, blank=True)
    source_metadata = models.JSONField(default=dict)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_reason = models.CharField(max_length=100, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["ordinal", "pk"]
        constraints = [models.UniqueConstraint(fields=["run", "locator_key"], name="extraction_unit_identity")]

    def __str__(self):
        return f"{self.locator_key} (run {self.run_id})"
