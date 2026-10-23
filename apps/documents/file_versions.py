"""Immutable repository snapshots and copies of complete Tracking history."""
import hashlib

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.file_lifecycle_models import VersionLifecycleModel, lifecycle_write
from apps.tracking.file_version_models import ImmutableFileMixin, ImmutableFileQuerySet


class DocumentFileVersion(ImmutableFileMixin, VersionLifecycleModel):
    document_file = models.ForeignKey("documents.DocumentFile", on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField(default=1)
    file = models.FileField(upload_to="documents/history/%Y/%m/", max_length=400, blank=True)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True, db_index=True)
    change_summary = models.CharField(max_length=1000, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="document_file_versions")
    uploader_name_snapshot = models.CharField(max_length=150, blank=True)
    uploader_office_snapshot = models.CharField(max_length=150, blank=True)
    uploaded_at = models.DateTimeField(null=True, blank=True)
    objects = ImmutableFileQuerySet.as_manager()
    immutable_fields = ("document_file_id", "version_number", "file", "original_name", "content_type", "size",
        "checksum", "change_summary", "uploaded_by_id", "uploader_name_snapshot", "uploader_office_snapshot", "uploaded_at")

    class Meta:
        ordering = ["version_number", "pk"]
        constraints = [models.UniqueConstraint(fields=["document_file", "version_number"], name="document_file_version_unique")]

    def __str__(self):
        return f"{self.original_name} (version {self.version_number})"

    def save(self, *args, **kwargs):
        self._check_immutable()
        if self.version_number < 1:
            raise ValidationError("The version number must be positive.")
        return super().save(*args, **kwargs)

    @property
    def extension(self):
        return (self.original_name.rsplit(".", 1)[-1] if "." in self.original_name else "").upper()


def initial_snapshot(document_file):
    actor = document_file.uploaded_by
    return DocumentFileVersion.objects.create(document_file=document_file, version_number=1,
        file=document_file.file.name, original_name=document_file.original_name, content_type=document_file.content_type,
        size=document_file.size, checksum=document_file.checksum, uploaded_by=actor,
        uploader_name_snapshot=actor.display_name[:150] if actor else "",
        uploader_office_snapshot=actor.office.name[:150] if actor and actor.office_id else "",
        uploaded_at=document_file.created_at)


def _copy_source_version(source, target, created_files):
    """Hash and copy one source in bounded chunks, retaining no payload bytes."""
    try:
        with source.file.open("rb") as stream:
            digest = hashlib.sha256()
            size = 0
            for chunk in stream.chunks(chunk_size=64 * 1024):
                digest.update(chunk)
                size += len(chunk)
            checksum = digest.hexdigest()
            if source.checksum and checksum != source.checksum:
                raise ValidationError("Filing stopped because file history content does not match its checksum.")
            stream.seek(0)
            target.file.save(source.original_name, stream, save=False)
            created_files.append((target.file.storage, target.file.name))
        return size, checksum
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError("Filing stopped because an attachment could not be read or copied. Restore the file and retry filing.") from exc


def copy_tracking_history(record, document, created_files):
    """Copy each source blob once; return only current root metadata.

    The caller owns the SQL transaction and compensates every registered key.
    A later unreadable/corrupt version rolls back and removes earlier copies.
    The final snapshot shares its root's new key. Historical bytes are never
    accumulated in memory, regardless of the number of versions in a group.
    """
    from .models import DocumentFile
    roots = []
    for group in record.file_groups.filter(discarded_at__isnull=True).prefetch_related("versions"):
        versions = sorted(group.versions.all(), key=lambda row: row.version_number)
        if not versions or [row.version_number for row in versions] != list(range(1, len(versions) + 1)):
            raise ValidationError("Filing stopped because this file's version history is incomplete.")
        latest = versions[-1]
        if not latest.available_content:
            raise ValidationError("Filing stopped because the current file is unavailable.")
        root = DocumentFile(document=document, original_name=latest.original_name, content_type=latest.content_type,
            size=latest.size, checksum=latest.checksum, is_primary=not roots,
            uploaded_by=latest.uploaded_by)
        size, checksum = _copy_source_version(latest, root, created_files)
        root.size = latest.size or size
        root.checksum = latest.checksum or checksum
        root.save(create_snapshot=False)
        for source in versions:
            snapshot = DocumentFileVersion(document_file=root, version_number=source.version_number,
                original_name=source.original_name, content_type=source.content_type, size=source.size,
                checksum=source.checksum, change_summary=source.change_summary, uploaded_by=source.uploaded_by,
                uploader_name_snapshot=source.uploader_name_snapshot, uploader_office_snapshot=source.uploader_office_snapshot,
                uploaded_at=source.created_at)
            if source.retired_at is not None:
                snapshot.retired_at = source.retired_at
                snapshot.retirement_reason = source.retirement_reason
            elif source.pk == latest.pk:
                snapshot.file = root.file.name
                snapshot.size = root.size
                snapshot.checksum = root.checksum
            else:
                size, checksum = _copy_source_version(source, snapshot, created_files)
                snapshot.size = source.size or size
                snapshot.checksum = source.checksum or checksum
            with lifecycle_write():
                snapshot.save()
        roots.append(root)
    return roots
