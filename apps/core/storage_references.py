"""Exact-key references and a common PostgreSQL guard for aliasing/removal."""
import hashlib
from contextlib import contextmanager

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q

from .storage import storage_identity


@contextmanager
def locked_storage_key(identity, key):
    digest = int.from_bytes(hashlib.sha256(f"{identity}:{key}".encode()).digest()[:8], "big", signed=True)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [digest])
        yield


def storage_for_identity(identity):
    from apps.core.models import StagedUpload
    from apps.documents.models import DocumentFileVersion
    from apps.tracking.models import Attachment, ExternalAttachment
    if not identity:
        return None
    for model in (StagedUpload, Attachment, ExternalAttachment, DocumentFileVersion):
        storage = model._meta.get_field("file").storage
        if storage_identity(storage) == identity:
            return storage
    return None


def referenced_storage_key(identity, key, *, excluding=None, include_audit=False):
    from apps.core.models import StagedUpload
    from apps.documents.models import DocumentFile, DocumentFileVersion
    from apps.tracking.models import Attachment, ExternalAttachment
    excluding = excluding or {}
    roots = DocumentFile.objects.filter(file=key)
    versions = DocumentFileVersion.objects.filter(file=key)
    if excluding.get("document"):
        roots = roots.exclude(document_id=excluding["document"])
        versions = versions.exclude(document_file__document_id=excluding["document"])
    # Roots have no independent backend field. An unknown historical reference
    # must conservatively preserve this key rather than infer a new backend.
    if roots.exists():
        return True
    identity_scope = Q(storage_identity=identity) | Q(storage_identity="")
    if not include_audit:
        versions = versions.filter(Q(retired_at__isnull=True) | Q(document_file__document__is_active=False))
    if versions.filter(identity_scope).exists():
        return True
    for model in (Attachment, ExternalAttachment):
        query = model.objects.filter(file=key).filter(identity_scope)
        if not include_audit:
            query = query.filter(retired_at__isnull=True)
        if query.exists():
            return True
    stages = StagedUpload.objects.filter(file=key).filter(identity_scope)
    if not include_audit:
        stages = stages.filter(state="saved", draft__state="open")
    return stages.exists()


@contextmanager
def guarded_file_reference(field):
    """Only used when an existing immutable key is attached to a new row."""
    from apps.core.models import StorageCleanupIntent
    if not field.name or not field._committed:
        yield
        return
    identity = storage_identity(field.storage)
    with locked_storage_key(identity, field.name):
        if StorageCleanupIntent.objects.filter(storage_identity=identity, storage_key=field.name).exists() and not field.storage.exists(field.name):
            raise ValidationError("This stored content was removed. Reload the current file.")
        yield
