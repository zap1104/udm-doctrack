"""Finite source/index reconciliation with no provider dispatch or new actor."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.functions import Length

from .models import Collection, Document


def index_status(document):
    from apps.search.chunks import CHUNK_CHARS, CHUNK_OVERLAP, current_chunks

    from .intelligence_views import current_units

    if not settings.ENABLE_SOURCE_CHUNK_SEARCH:
        return "disabled"
    lengths = list(current_units(document).annotate(length=Length("normalized_text")).values_list("length", flat=True))
    if not lengths:
        return "unknown_legacy"
    step = CHUNK_CHARS - CHUNK_OVERLAP
    expected = sum((length + step - 1) // step for length in lengths)
    return "current" if current_chunks(document.pk).count() == expected else "stale"


def reconcile_processing(*, limit, after_id=0, dry_run=True, collection=None):
    from apps.search.chunks import publish_chunks

    from .ml.dataset import validate_snapshot
    from .ml.lifecycle import invalidate_models
    from .ml.models import TrainingSnapshot

    if type(limit) is not int or not 1 <= limit <= 1000 or type(after_id) is not int or after_id < 0:
        raise ValidationError("Use a batch limit from 1 to 1000 and a nonnegative checkpoint.")
    if collection is not None and collection not in Collection.values:
        raise ValidationError("Choose OVPA or EXTERNAL.")
    population = Document.objects.filter(pk__gt=after_id).order_by("pk")
    if collection:
        population = population.filter(collection=collection)
    ids = list(population.values_list("pk", flat=True)[:limit])
    result = {
        "dry_run": dry_run,
        "document_ids": ids,
        "after_id": ids[-1] if ids else after_id,
        "stale_indexes": 0,
        "rebuilt_indexes": 0,
        "invalid_snapshots": 0,
    }
    for pk in ids:
        with transaction.atomic():
            doc = Document.objects.select_for_update().get(pk=pk)
            if not doc.is_active:
                continue
            if index_status(doc) == "stale":
                result["stale_indexes"] += 1
                if not dry_run:
                    for run in doc.extraction_runs.filter(is_current=True).order_by("pk"):
                        publish_chunks(run, document=doc, expected_revision=str(doc.extraction_generation))
                    result["rebuilt_indexes"] += 1
    snapshots = TrainingSnapshot.objects.filter(sources__document_id__in=ids, invalidated_at__isnull=True).distinct()
    for snapshot in snapshots.iterator(chunk_size=8):
        try:
            validate_snapshot(snapshot)
        except ValidationError:
            result["invalid_snapshots"] += 1
            if not dry_run:
                invalidate_models([snapshot.pk], reason="Reconciliation found changed or withdrawn source evidence")
    return result
