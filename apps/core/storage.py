"""Compensate new storage writes when their database mutation fails."""

import hashlib
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

logger = logging.getLogger("doctrack")
_new_files = ContextVar("new_storage_files", default=None)

# Azure is an optional deployment extra. These are provider write failures,
# not database/programming errors that should escape and roll back the batch.
STORAGE_WRITE_ERRORS = (OSError,)
try:
    from botocore.exceptions import BotoCoreError, ClientError
    STORAGE_WRITE_ERRORS += (BotoCoreError, ClientError)
except ImportError:
    pass
try:
    from azure.core.exceptions import AzureError
    STORAGE_WRITE_ERRORS += (AzureError,)
except ImportError:
    pass


def storage_identity(storage):
    """Bind exact-object cleanup to a backend/location without persisting secrets."""
    from urllib.parse import urlsplit

    identity = {"backend": f"{storage.__class__.__module__}.{storage.__class__.__qualname__}"}
    for name in ("location", "bucket_name", "account_name", "azure_container", "endpoint_url"):
        value = getattr(storage, name, None)
        if value is not None:
            value = str(value)
            if name == "endpoint_url":
                parsed = urlsplit(value)
                value = f"{parsed.scheme}://{parsed.hostname or ''}:{parsed.port or ''}{parsed.path}"
            identity[name] = value
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


@contextmanager
def cleanup_new_files():
    """Enclose the outer transaction too when a caller groups file mutations."""
    files = _new_files.get()
    token = None
    if files is None:
        files = []
        token = _new_files.set(files)
    start = len(files)
    try:
        yield
    except Exception:
        for instance in files[start:]:
            field = instance.file
            if field.name and field._committed:
                try:
                    # A callback can raise after SQL commit. Preserve bytes if
                    # their exact row/name survived; callback order is irrelevant.
                    if type(instance)._default_manager.using(instance._state.db).filter(
                        pk=instance.pk, file=field.name,
                    ).exists():
                        continue
                    field.storage.delete(field.name)
                except Exception:
                    logger.exception("Could not remove new storage object after failed database mutation")
        del files[start:]
        raise
    finally:
        if token is not None:
            _new_files.reset(token)


def compensate_storage(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with cleanup_new_files():
            return function(*args, **kwargs)

    return wrapped


def create_stored_file(model, **fields):
    """Track before save: FileField writes bytes before the SQL INSERT."""
    instance = model(**fields)
    files = _new_files.get()
    if files is None:
        raise RuntimeError("Storage writes require a cleanup_new_files scope")
    if not instance.file._committed:
        files.append(instance)
    instance.save(force_insert=True)
    return instance
