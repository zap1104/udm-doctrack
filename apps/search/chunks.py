"""Current-unit indexes and document-diverse retrieval, never global chunk top-K."""

from __future__ import annotations

import hashlib
from itertools import islice

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, F, FloatField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, Length, Substr

from apps.documents.models import Document, DocumentFileVersion, ExtractionRun, SearchChunk
from apps.documents.processing_contracts import resolve_file_version

CHUNK_CHARS = 4000
CHUNK_OVERLAP = 512
INDEX_VERSION = "source-chunks-1"


def current_chunks(document_id=None):
    latest = (
        DocumentFileVersion.objects.filter(document_file_id=OuterRef("unit__run__file_version__document_file_id"))
        .order_by("-version_number", "-pk")
        .values("pk")[:1]
    )
    query = SearchChunk.objects.annotate(_latest_version=Subquery(latest)).filter(
        unit__run__is_current=True,
        unit__run__document__is_active=True,
        unit__run__file_version_id=F("_latest_version"),
        unit__run__file_version__document_file__document_id=F("unit__run__document_id"),
        end_offset__lte=Length("unit__normalized_text"),
        text=Substr("unit__normalized_text", F("start_offset") + 1, F("end_offset") - F("start_offset")),
    )
    return query.filter(unit__run__document_id=document_id) if document_id is not None else query


@transaction.atomic
def publish_chunks(run, *, document, expected_revision: str) -> int:
    current = Document.objects.select_for_update().get(pk=document.pk)
    if not current.is_active or str(current.extraction_generation) != expected_revision:
        raise ValidationError("The document changed before source indexing.")
    run = (
        ExtractionRun.objects.select_for_update()
        .select_related("file_version__document_file")
        .get(pk=run.pk, document=current)
    )
    if not run.is_current or resolve_file_version(run.file_version.document_file).pk != run.file_version_id:
        raise ValidationError("Only the current file's published extraction can be indexed.")
    count = 0
    revisions = []
    for unit in run.units.only("pk", "normalized_text").order_by("ordinal", "pk"):
        digest = hashlib.sha256((INDEX_VERSION + run.settings_digest + unit.normalized_text).encode()).hexdigest()
        revisions.append(digest)
        rows = []
        for ordinal, start in enumerate(range(0, len(unit.normalized_text), CHUNK_CHARS - CHUNK_OVERLAP), 1):
            text = unit.normalized_text[start : start + CHUNK_CHARS]
            rows.append(
                SearchChunk(
                    unit=unit,
                    ordinal=ordinal,
                    start_offset=start,
                    end_offset=start + len(text),
                    text=text,
                    index_revision=digest,
                )
            )
        SearchChunk.objects.bulk_create(rows, ignore_conflicts=True, batch_size=100)
        SearchChunk.objects.filter(unit=unit).exclude(index_revision=digest).delete()
        SearchChunk.objects.filter(unit=unit, index_revision=digest).update(
            search_vector=SearchVector("text", weight="D", config=settings.SEARCH_CONFIG)
        )
        count += len(rows)
    index_revision = hashlib.sha256((INDEX_VERSION + "".join(revisions)).encode()).hexdigest()
    Document.objects.filter(pk=current.pk).update(search_index_revision=index_revision)
    return count


def chunk_annotations(base_documents, raw_query: str, *, queries=None, prefix="") -> dict:
    """Correlated to each authorized outer document before its result limit."""
    if queries:
        from .aliases import combined_query

        query = combined_query(queries)
    else:
        query = SearchQuery(raw_query, config=settings.SEARCH_CONFIG, search_type="websearch")
    chunks = current_chunks(OuterRef("pk"))
    conditions = Q(search_vector=query)
    for value in queries or [raw_query]:
        conditions |= Q(text__icontains=value)
    matching = (
        chunks.filter(conditions)
        .annotate(_score=SearchRank("search_vector", query, weights=[0.10, 0.25, 0.60, 1.0], cover_density=True))
        .order_by("-_score", "unit__ordinal", "ordinal", "pk")
    )
    return {
        f"_{prefix}has_chunks": Exists(chunks),
        f"_{prefix}chunk_match": Exists(matching),
        f"_{prefix}chunk_rank": Coalesce(
            Subquery(matching.values("_score")[:1], output_field=FloatField()), Value(0.0)
        ),
        f"_{prefix}source_chunk_id": Subquery(matching.values("pk")[:1]),
    }


def plain_chunk_match(raw_query, *, queries=None):
    conditions = Q(pk__in=[])
    for value in queries or [raw_query]:
        conditions |= Q(text__icontains=value)
    return Exists(current_chunks(OuterRef("pk")).filter(conditions))


def attach_source_chunks(candidates):
    """One bounded metadata/text query per candidate batch, avoiding N+1 reads."""
    iterator = iter(candidates)
    while batch := list(islice(iterator, 200)):
        ids = [getattr(document, "_source_chunk_id", None) for document in batch]
        rows = (
            current_chunks()
            .filter(pk__in=[pk for pk in ids if pk is not None])
            .values(
                "pk",
                "text",
                "start_offset",
                "unit_id",
                "unit__locator_key",
                "unit__run__document_id",
                "unit__run__file_version__original_name",
            )
        )
        by_id = {row["pk"]: row for row in rows}
        for document in batch:
            if not hasattr(document, "_source_chunk_id"):
                yield document
                continue
            row = by_id.get(getattr(document, "_source_chunk_id", None))
            document._source_chunk = row if row and row["unit__run__document_id"] == document.pk else None
            yield document


def source_snippet(document, tokens: list[str]) -> dict:
    from .services import _highlight

    cached = hasattr(document, "_source_chunk")
    row = getattr(document, "_source_chunk", None)
    if not cached:
        conditions = Q(pk__in=[])
        for token in tokens:
            conditions |= Q(text__icontains=token)
        row = (
            current_chunks(document.pk)
            .filter(conditions)
            .order_by("unit__ordinal", "ordinal", "pk")
            .values("text", "start_offset", "unit_id", "unit__locator_key", "unit__run__file_version__original_name")
            .first()
        )
    if not row:
        return {}
    text = row["text"]
    position = next((text.lower().find(token.lower()) for token in tokens if token.lower() in text.lower()), 0)
    start = max(0, position - 80)
    excerpt = text[start : start + 240]
    return {
        "html": ("…" if start else "") + _highlight(excerpt, tokens) + ("…" if start + 240 < len(text) else ""),
        "filename": row["unit__run__file_version__original_name"],
        "locator": row["unit__locator_key"],
        "unit_id": row["unit_id"],
        "source_offset": row["start_offset"] + start,
    }
