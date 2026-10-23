"""Logical attachment groups and guards for immutable stored file content."""
from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.core.file_lifecycle_models import VersionLifecycleModel, check_lifecycle_update


class ImmutableFileQuerySet(models.QuerySet):
    def _protected_fields(self):
        fields = set(self.model.immutable_fields)
        return fields | {name[:-3] for name in fields if name.endswith("_id")}

    def update(self, **kwargs):
        check_lifecycle_update(kwargs)
        protected = self._protected_fields()
        if protected.intersection(kwargs):
            raise ValidationError("Stored file history cannot be changed. Upload a new version.")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        check_lifecycle_update(fields)
        if self._protected_fields().intersection(fields):
            raise ValidationError("Stored file history cannot be changed. Upload a new version.")
        return super().bulk_update(objs, fields, **kwargs)

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False,
                    update_fields=None, unique_fields=None):
        if update_conflicts:
            check_lifecycle_update(update_fields or [])
        if update_conflicts and self._protected_fields().intersection(update_fields or []):
            raise ValidationError("Stored file history cannot be changed. Upload a new version.")
        return super().bulk_create(objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts, update_fields=update_fields, unique_fields=unique_fields)


class ImmutableFileMixin:
    """Check before FileField can write replacement bytes to storage."""
    immutable_fields = ()

    def _check_immutable(self):
        if self.pk is None:
            return
        previous = type(self)._base_manager.filter(pk=self.pk).values(*self.immutable_fields).first()
        if previous is None:
            return
        for name, old in previous.items():
            current = getattr(self, name)
            if hasattr(current, "name"):
                if not current._committed:
                    raise ValidationError("Stored file content cannot be overwritten. Upload a new version.")
                current = current.name
            if current != old:
                raise ValidationError("Stored file history cannot be changed. Upload a new version.")


class AttachmentVersionMixin(ImmutableFileMixin, VersionLifecycleModel):
    immutable_fields = (
        "record_id", "file", "original_name", "content_type", "size", "checksum", "note",
        "uploaded_by_id", "file_group_id", "version_number", "change_summary",
        "uploader_name_snapshot", "uploader_office_snapshot", "created_at",
    )

    class Meta:
        abstract = True

    @property
    def extension(self):
        return (self.original_name.rsplit(".", 1)[-1] if "." in self.original_name else "").upper()

    def save(self, *args, **kwargs):
        self._check_immutable()
        with transaction.atomic():
            if self._state.adding:
                if not self.file_group_id:
                    group_model = self._meta.get_field("file_group").remote_field.model
                    self.file_group = group_model.objects.create(record_id=self.record_id)
                elif self.file_group.record_id != self.record_id:
                    raise ValidationError("The file group must belong to this record.")
                if self.version_number < 1:
                    raise ValidationError("The version number must be positive.")
                if self.uploaded_by_id:
                    actor = self.uploaded_by
                    self.uploader_name_snapshot = self.uploader_name_snapshot or actor.display_name[:150]
                    self.uploader_office_snapshot = self.uploader_office_snapshot or (actor.office.name[:150] if actor.office_id else "")
            return super().save(*args, **kwargs)


class GroupLifecycleMixin:
    def save(self, *args, **kwargs):
        fields = ("discarded_at", "discarded_by_id", "discard_reason")
        if self.pk:
            previous = type(self)._base_manager.filter(pk=self.pk).values(*fields).first()
            if previous:
                check_lifecycle_update([key for key in fields if getattr(self, key) != previous[key]])
        elif self.discarded_at or self.discarded_by_id or self.discard_reason:
            check_lifecycle_update(fields)
        return super().save(*args, **kwargs)


class GroupLifecycleQuerySet(models.QuerySet):
    def update(self, **kwargs):
        check_lifecycle_update(kwargs)
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        check_lifecycle_update(fields)
        return super().bulk_update(objs, fields, **kwargs)


class AttachmentGroup(GroupLifecycleMixin, models.Model):
    record = models.ForeignKey("tracking.TrackingRecord", on_delete=models.CASCADE, related_name="file_groups")
    discarded_at = models.DateTimeField(null=True, blank=True, editable=False)
    discarded_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="discarded_file_groups", editable=False)
    discard_reason = models.CharField(max_length=2000, blank=True, editable=False)
    objects = GroupLifecycleQuerySet.as_manager()

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"Attachment group {self.pk} for record {self.record_id}"

    @property
    def latest_version(self):
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("versions")
        if prefetched is not None:
            return max(prefetched, key=lambda version: version.version_number, default=None)
        return self.versions.order_by("-version_number").first()


class ExternalAttachmentGroup(GroupLifecycleMixin, models.Model):
    record = models.ForeignKey("tracking.ExternalTrackingRecord", on_delete=models.CASCADE, related_name="file_groups")
    discarded_at = models.DateTimeField(null=True, blank=True, editable=False)
    discarded_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="discarded_external_file_groups", editable=False)
    discard_reason = models.CharField(max_length=2000, blank=True, editable=False)
    objects = GroupLifecycleQuerySet.as_manager()

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"External attachment group {self.pk} for record {self.record_id}"

    @property
    def latest_version(self):
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("versions")
        if prefetched is not None:
            return max(prefetched, key=lambda version: version.version_number, default=None)
        return self.versions.order_by("-version_number").first()
