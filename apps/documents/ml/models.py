from django.conf import settings
from django.db import models

from ..tag_models import AppendOnly


class TrainingSnapshot(AppendOnly):
    office = models.ForeignKey("accounts.Office", on_delete=models.PROTECT)
    collection = models.CharField(max_length=8)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField(default=dict, editable=False)
    digest = models.CharField(max_length=64)
    invalidated_at = models.DateTimeField(null=True)
    invalidation_reason = models.CharField(max_length=255, blank=True)
    synthetic = models.BooleanField(default=False)

    def __str__(self):
        return f"Training snapshot {self.pk} ({self.office_id}/{self.collection})"


class SnapshotSource(models.Model):
    snapshot = models.ForeignKey(TrainingSnapshot, on_delete=models.CASCADE, related_name="sources")
    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE, related_name="training_snapshots")
    input_revision = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["snapshot", "document"], name="unique_snapshot_source")]

    def __str__(self):
        return f"Snapshot {self.snapshot_id}: document {self.document_id}"


class TagModel(models.Model):
    snapshot = models.ForeignKey(TrainingSnapshot, on_delete=models.PROTECT, related_name="models")
    office = models.ForeignKey("accounts.Office", on_delete=models.PROTECT)
    collection = models.CharField(max_length=8)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="trained_tag_models"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    artifact = models.BinaryField(editable=False)
    artifact_digest = models.CharField(max_length=64)
    artifact_origin = models.CharField(max_length=32)
    vocabulary = models.CharField(max_length=64)
    evaluation = models.JSONField(default=dict)
    runtime = models.JSONField(default=dict)
    mode = models.CharField(
        max_length=16,
        default="candidate",
        choices=[(v, v) for v in ("candidate", "shadow", "suggestion", "automatic", "retired", "invalid")],
    )
    is_active = models.BooleanField(default=False)
    invalidated_at = models.DateTimeField(null=True)
    invalidation_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["office", "collection"],
                condition=models.Q(is_active=True),
                name="unique_active_tag_model_scope",
            )
        ]

    def __str__(self):
        return f"Topic model {self.pk} ({self.office_id}/{self.collection}: {self.mode})"
