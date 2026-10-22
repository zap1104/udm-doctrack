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
