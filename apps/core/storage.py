"""Compensate new storage writes when their database mutation fails."""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

logger = logging.getLogger("doctrack")
_new_files = ContextVar("new_storage_files", default=None)


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
