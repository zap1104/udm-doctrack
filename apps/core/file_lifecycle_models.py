"""Content availability is separate from immutable upload provenance."""
from contextlib import contextmanager
from contextvars import ContextVar

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

_lifecycle_write = ContextVar("file_lifecycle_write", default=False)
LIFECYCLE_FIELDS = frozenset({"storage_identity", "retired_at", "retirement_reason", "discarded_at", "discarded_by", "discarded_by_id", "discard_reason"})


@contextmanager
def lifecycle_write():
    """Private service capability, never inferred from a user's role."""
    token = _lifecycle_write.set(True)
    try:
        yield
    finally:
        _lifecycle_write.reset(token)


def check_lifecycle_update(fields):
    if LIFECYCLE_FIELDS.intersection(fields) and not _lifecycle_write.get():
        raise ValidationError("File availability can only be changed by the file lifecycle service.")


class VersionLifecycleModel(models.Model):
    storage_identity = models.CharField(max_length=64, blank=True, editable=False)
    retired_at = models.DateTimeField(null=True, blank=True, editable=False, db_index=True)
    retirement_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.retired_at is not None and not _lifecycle_write.get():
                raise ValidationError("Audit-only history must be created by the file lifecycle service.")
            if self.file.name and not self.storage_identity:
                from .storage import storage_identity

                self.storage_identity = storage_identity(self.file.storage)
        elif not _lifecycle_write.get():
            old = type(self)._base_manager.filter(pk=self.pk).values("storage_identity", "retired_at", "retirement_reason").first()
            if old and any(getattr(self, name) != value for name, value in old.items()):
                raise ValidationError("File availability can only be changed by the file lifecycle service.")
        return super().save(*args, **kwargs)

    @property
    def available_content(self):
        return bool(self.file.name) and self.retired_at is None


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("File lifecycle receipts are append-only.")

    def bulk_update(self, *args, **kwargs):
        raise ValidationError("File lifecycle receipts are append-only.")

    def bulk_create(self, objs, **kwargs):
        if kwargs.get("update_conflicts"):
            raise ValidationError("File lifecycle receipts are append-only.")
        return super().bulk_create(objs, **kwargs)

    def delete(self):
        raise ValidationError("File lifecycle receipts are append-only.")


class FileLifecycleEvent(models.Model):
    """Minimal permanent receipt. Scalar IDs survive parent/account purging."""
    resource_kind = models.CharField(max_length=20)
    parent_id = models.PositiveBigIntegerField()
    version_id = models.PositiveBigIntegerField(null=True, blank=True)
    group_id = models.PositiveBigIntegerField(null=True, blank=True)
    collection = models.CharField(max_length=8, default="OVPA")
    office_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True)
    actor_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True)
    actor_name_snapshot = models.CharField(max_length=150, blank=True)
    actor_office_snapshot = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=20)
    reason = models.CharField(max_length=2000)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-pk"]
        indexes = [models.Index(fields=["resource_kind", "parent_id", "group_id"], name="file_lifecycle_parent_idx")]

    def __str__(self):
        return f"{self.action}: {self.resource_kind} #{self.parent_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self)._base_manager.filter(pk=self.pk).exists():
            raise ValidationError("File lifecycle receipts are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("File lifecycle receipts are append-only.")


class StorageCleanupIntent(models.Model):
    storage_identity = models.CharField(max_length=64, blank=True)
    storage_key = models.CharField(max_length=400)
    reason = models.CharField(max_length=255)
    resource_kind = models.CharField(max_length=20, blank=True)
    parent_id = models.PositiveBigIntegerField(null=True, blank=True)
    version_id = models.PositiveBigIntegerField(null=True, blank=True)
    collection = models.CharField(max_length=8, default="OVPA")
    office_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True)
    state = models.CharField(max_length=12, default="pending", choices=[
        (value, value.title()) for value in ("pending", "running", "complete", "blocked", "retry")])
    attempts = models.PositiveIntegerField(default=0)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    retry_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="legacy_aliases", editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["storage_identity", "storage_key"], name="file_cleanup_exact_key_unique")]
        indexes = [models.Index(fields=["state", "retry_at"], name="file_cleanup_due_idx")]

    def __str__(self):
        return f"File cleanup #{self.pk}: {self.state}"
