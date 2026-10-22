"""Search over the document repository.

How a result gets its percentage
--------------------------------
Three signals are combined. Every weight is a setting, and the formula is
written out in docs/SEARCH_DESIGN.md so nobody has to reverse-engineer it.

1. `text`  — PostgreSQL `ts_rank_cd` over the weighted tsvector
             (title A, office/type/tags B, metadata C, OCR text D).
             Raw ranks are unbounded, so they are squashed into 0–1 with
             `rank / (rank + k)`; k is SEARCH_RANK_SATURATION_K.
2. `fuzzy` — trigram similarity on the title/metadata blob, which catches
             misspellings ("maintenence") and partial words.
3. `field` — a deterministic bonus for the matches a records officer cares
             about: exact tracking number, words in the title, tag hits,
             office code, document type.

    relevance % = 100 × (0.55·text + 0.20·fuzzy + 0.25·field)

An exact tracking / reference number match short-circuits to 100%.

The number is labelled **relevance**, never "accuracy": it describes how well a
record matches the query, not whether the system was right.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from itertools import chain

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramSimilarity
from django.db import DatabaseError, connection
from django.db.models import BigIntegerField, BooleanField, Case, F, FloatField, IntegerField, Q, Value, When
from django.db.models.functions import Coalesce, Greatest
from django.utils.html import escape

from apps.core.document_types import document_type_match
from apps.documents.models import Collection, Document, SearchQueryLog

logger = logging.getLogger("doctrack")

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_/]*")
IDENTIFIER_RE = re.compile(r"^[A-Za-z]{2,}(?:-[A-Za-z0-9]+){2,}$")

# ts_rank_cd weights are given in the order D, C, B, A.
RANK_WEIGHTS = [0.10, 0.25, 0.60, 1.00]

_TRIGRAM_STATE: dict[str, bool] = {}


def trigram_available() -> bool:
    """True when the pg_trgm extension exists. Checked once per process."""
    if not settings.SEARCH_ENABLE_TRIGRAM:
        return False
    if "value" in _TRIGRAM_STATE:
        return _TRIGRAM_STATE["value"]
    available = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
            available = cursor.fetchone() is not None
    except DatabaseError:
        available = False
    if not available:
        logger.info("pg_trgm is not installed — fuzzy matching is off. Run: manage.py init_db")
    _TRIGRAM_STATE["value"] = available
    return available


@dataclass
class SearchResult:
    document: Document
    relevance: int
    text_score: float
    fuzzy_score: float
    field_score: float
    matched_in: list[str] = field(default_factory=list)
    snippet_html: str = ""
    source_filename: str = ""
    source_locator: str = ""
    source_unit_id: int | None = None

    @property
    def below_threshold(self) -> bool:
        return False


@dataclass
class SearchResponse:
    results: list[SearchResult] = field(default_factory=list)
    hidden_count: int = 0
    total_matches: int = 0
    evaluated_count: int = 0
    truncated: bool = False
    limited_count: int = 0
    result_limit: int = 0
    duration_ms: int = 0
    query: str = ""
    used_fuzzy: bool = False
    explanation: str = ""
    log_id: int | None = None
    scoring_version: str = "lexical-1"
    expanded_terms: list[str] = field(default_factory=list)
    candidate_ids: list[int] = field(default_factory=list)


def tokenize(raw_query: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(raw_query or "") if len(token) > 1]


def search_documents(
    *,
    user,
    query: str = "",
    year=None,
    office=None,
    document_type=None,
    tag=None,
    source=None,
    date_from=None,
    date_to=None,
    min_relevance: int | None = None,
    show_below_threshold: bool = False,
    limit: int | None = None,
    log: bool = True,
    base_documents=None,
    exhaustive: bool = False,
    collection=Collection.OVPA,
) -> SearchResponse:
    """Score one permission-scoped corpus.

    Search keeps its existing top-result ceiling. The Repository can request
    every match for normal pagination, reading candidates in batches. A caller's
    supplied queryset only narrows visibility; it can never grant access.
    """
    started = time.perf_counter()
    raw_query = " ".join((query or "").split())
    limit = limit or settings.SEARCH_RESULT_LIMIT
    min_relevance = settings.SEARCH_MIN_RELEVANCE_DEFAULT if min_relevance is None else int(min_relevance)

    visible = Document.objects.visible_to(user).in_collection(collection).filter(is_active=True)
    base = visible if base_documents is None else base_documents.filter(pk__in=visible.order_by().values("pk"))
    base = base.with_related()
    if year:
        base = base.filter(year=year)
    if office:
        base = base.filter(office=office)
    if document_type:
        base = base.filter(document_type_match(document_type)).distinct()
    if tag:
        base = base.filter(tags=tag)
    if source:
        base = base.filter(source=source)
    if date_from:
        base = base.filter(document_date__gte=date_from)
    if date_to:
        base = base.filter(document_date__lte=date_to)

    # ---- browse mode: filters only, no query text -------------------------
    if not raw_query:
        total = base.distinct().count()
        ordered = base.distinct().order_by("-document_date", "-created_at", "-pk")
        documents = list(ordered.iterator(chunk_size=200)) if exhaustive else list(ordered[:limit])
        response = SearchResponse(
            results=[
                SearchResult(document=document, relevance=0, text_score=0, fuzzy_score=0, field_score=0)
                for document in documents
            ],
            total_matches=total,
            evaluated_count=len(documents),
            truncated=total > len(documents),
            limited_count=total - len(documents),
            result_limit=total if exhaustive else limit,
            query="",
            explanation="Showing the newest records that match the filters.",
        )
        response.duration_ms = int((time.perf_counter() - started) * 1000)
        return response

    tokens = tokenize(raw_query)
    use_fuzzy = trigram_available()
    expanded_terms = []
    if settings.ENABLE_APPROVED_SEARCH_ALIASES:
        from .aliases import expand_query

        expanded_terms = expand_query(user, raw_query, collection=collection)

    search_query = SearchQuery(raw_query, config=settings.SEARCH_CONFIG, search_type="websearch")
    annotations = {
        "rank": SearchRank(
            F("search_vector"), search_query, weights=RANK_WEIGHTS, cover_density=True, normalization=Value(0)
        )
    }
    if settings.ENABLE_SOURCE_CHUNK_SEARCH:
        from .chunks import chunk_annotations

        legacy_rank = annotations.pop("rank")
        annotations.update(chunk_annotations(base, raw_query))
        annotations["_metadata_rank"] = SearchRank(
            F("metadata_search_vector"), search_query, weights=RANK_WEIGHTS, cover_density=True, normalization=Value(0)
        )
        annotations["rank"] = Case(
            When(_has_chunks=True, then=Coalesce(F("_metadata_rank"), Value(0.0)) + F("_chunk_rank")),
            default=legacy_rank,
            output_field=FloatField(),
        )
    if use_fuzzy:
        annotations["similarity"] = Greatest(
            TrigramSimilarity("index_title", raw_query), TrigramSimilarity("index_meta", raw_query)
        )
    else:
        annotations["similarity"] = Value(0.0, output_field=FloatField())

    expanded_conditions = Q(pk__in=[])
    if expanded_terms:
        from .aliases import combined_query

        expanded_query = combined_query(expanded_terms)
        expanded_conditions = Q(search_vector=expanded_query)
        for term in expanded_terms:
            expanded_conditions |= (
                Q(title__icontains=term)
                | Q(index_title__icontains=term)
                | Q(index_meta__icontains=term)
                | Q(index_extra__icontains=term)
                | Q(ocr_text__icontains=term)
            )
        expanded_rank = SearchRank(
            F("search_vector"), expanded_query, weights=RANK_WEIGHTS, cover_density=True, normalization=Value(0)
        )
        if settings.ENABLE_SOURCE_CHUNK_SEARCH:
            annotations.update(chunk_annotations(base, raw_query, queries=expanded_terms, prefix="expanded_"))
            metadata_rank = SearchRank(
                F("metadata_search_vector"),
                expanded_query,
                weights=RANK_WEIGHTS,
                cover_density=True,
                normalization=Value(0),
            )
            expanded_rank = Case(
                When(_expanded_has_chunks=True, then=Coalesce(metadata_rank, Value(0.0)) + F("_expanded_chunk_rank")),
                default=expanded_rank,
                output_field=FloatField(),
            )
            annotations["_source_is_expanded"] = Case(
                When(_chunk_match=False, _expanded_chunk_match=True, then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            )
            annotations["_source_chunk_id"] = Case(
                When(_chunk_match=True, then=annotations["_source_chunk_id"]),
                default=annotations["_expanded_source_chunk_id"],
                output_field=BigIntegerField(),
            )
            expanded_conditions |= Q(_expanded_chunk_match=True)
        annotations["_expanded_rank"] = expanded_rank
        k = Value(max(settings.SEARCH_RANK_SATURATION_K, 1e-6))
        original_rank = Coalesce(F("rank"), Value(0.0))
        alias_rank = Coalesce(F("_expanded_rank"), Value(0.0))
        annotations["_candidate_text_score"] = Greatest(
            original_rank / (original_rank + k), Value(0.5) * alias_rank / (alias_rank + k)
        )

    plain_conditions = (
        Q(title__icontains=raw_query)
        | Q(index_title__icontains=raw_query)
        | Q(index_meta__icontains=raw_query)
        | Q(index_extra__icontains=raw_query)
        | Q(ocr_text__icontains=raw_query)
        | Q(reference_number__icontains=raw_query)
    )
    # Exact identifiers score 100% after candidates are read. Keep them ahead
    # of incidental mentions before the bounded candidate window is cut, and
    # admit a linked tracking number even while its index is being rebuilt.
    # This is still applied only to the permission- and filter-scoped base.
    identifier_conditions = (
        (
            Q(reference_number__iexact=raw_query)
            | Q(tracking_record__tracking_number__iexact=raw_query)
            | Q(external_tracking_record__tracking_number__iexact=raw_query)
            | Q(external_tracking_record__original_reference__iexact=raw_query)
        )
        if len(raw_query) >= 6
        else Q(pk__in=[])
    )
    identifier_priority = Case(
        When(identifier_conditions, then=Value(1)),
        default=Value(0),
        output_field=IntegerField(),
    )
    # Metadata/text remain findable while a legacy or newly filed record is
    # waiting for its stored full-text index to be rebuilt.
    conditions = Q(search_vector=search_query) | plain_conditions | identifier_conditions
    if expanded_terms:
        conditions |= expanded_conditions
    if settings.ENABLE_SOURCE_CHUNK_SEARCH:
        conditions |= Q(_chunk_match=True)
    if use_fuzzy:
        conditions |= Q(similarity__gte=0.28)

    try:
        queryset = (
            base.annotate(_identifier_priority=identifier_priority, **annotations)
            .filter(conditions)
            .distinct()
            .order_by(
                "-_identifier_priority",
                F("_candidate_text_score" if expanded_terms else "rank").desc(nulls_last=True),
                "-similarity",
                "-document_date",
                "-pk",
            )
        )
        total = queryset.count()
        if exhaustive:
            iterator = queryset.iterator(chunk_size=200)
            # Execute the ranking query inside the fallback guard, not after
            # leaving it when the first batch is read.
            first = next(iterator, None)
            candidates = chain([first] if first is not None else [], iterator)
        else:
            candidates = list(queryset[: limit * 2])
    except DatabaseError as exc:  # e.g. pg_trgm dropped after the process started
        logger.warning("Search fell back to plain matching: %s", exc)
        _TRIGRAM_STATE["value"] = False
        fallback = base
        if expanded_terms:
            for term in expanded_terms:
                plain_conditions |= (
                    Q(title__icontains=term)
                    | Q(index_title__icontains=term)
                    | Q(index_meta__icontains=term)
                    | Q(index_extra__icontains=term)
                    | Q(ocr_text__icontains=term)
                )
        if settings.ENABLE_SOURCE_CHUNK_SEARCH:
            from .chunks import plain_chunk_match

            fallback = fallback.annotate(_chunk_match=plain_chunk_match(raw_query))
            plain_conditions |= Q(_chunk_match=True)
            if expanded_terms:
                fallback = fallback.annotate(_expanded_chunk_match=plain_chunk_match(raw_query, queries=expanded_terms))
                fallback = fallback.annotate(
                    _source_is_expanded=Case(
                        When(_chunk_match=False, _expanded_chunk_match=True, then=Value(True)),
                        default=Value(False),
                    )
                )
                plain_conditions |= Q(_expanded_chunk_match=True)
        fallback = (
            fallback.filter(plain_conditions | identifier_conditions)
            .annotate(_identifier_priority=identifier_priority)
            .distinct()
            .order_by("-_identifier_priority", "-document_date", "-pk")
        )
        total = fallback.count()
        candidates = fallback.iterator(chunk_size=200) if exhaustive else list(fallback[:limit])
        use_fuzzy = False

    if settings.ENABLE_SOURCE_CHUNK_SEARCH:
        from .chunks import attach_source_chunks

        candidates = attach_source_chunks(candidates)
    results: list[SearchResult] = []
    for document in candidates:
        text_score = _saturate(float(getattr(document, "rank", 0.0) or 0.0))
        alias_text_score = 0.5 * _saturate(float(getattr(document, "_expanded_rank", 0.0) or 0.0))
        alias_scored = alias_text_score > text_score
        text_score = max(text_score, alias_text_score)
        fuzzy_score = float(getattr(document, "similarity", 0.0) or 0.0)
        field_score, matched_in = _field_score(document, raw_query, tokens)
        source_evidence = {}
        alias_source = bool(getattr(document, "_source_is_expanded", False))
        snippet_tokens = tokenize(" ".join(expanded_terms)) if alias_source or alias_scored else tokens
        if settings.ENABLE_SOURCE_CHUNK_SEARCH:
            from .chunks import source_snippet

            source_evidence = source_snippet(document, snippet_tokens)
            if source_evidence and not alias_source and "Document text" not in matched_in:
                matched_in.append("Document text")
                field_score = min(1.0, field_score + 0.05)
        if alias_scored or alias_source:
            matched_in.append("Approved alias")

        relevance = 100.0 * (
            settings.SEARCH_WEIGHT_TEXT * text_score
            + settings.SEARCH_WEIGHT_FUZZY * fuzzy_score
            + settings.SEARCH_WEIGHT_FIELD * field_score
        )
        if _is_identifier_hit(document, raw_query):
            relevance = 100.0
            if "Tracking number" not in matched_in:
                matched_in.insert(0, "Tracking number")

        results.append(
            SearchResult(
                document=document,
                relevance=int(round(max(0.0, min(100.0, relevance)))),
                text_score=round(text_score, 4),
                fuzzy_score=round(fuzzy_score, 4),
                field_score=round(field_score, 4),
                matched_in=matched_in,
                snippet_html=source_evidence.get("html") or _snippet(document, snippet_tokens),
                source_filename=source_evidence.get("filename", ""),
                source_locator=source_evidence.get("locator", ""),
                source_unit_id=source_evidence.get("unit_id"),
            )
        )
        if exhaustive:
            # Keep compact scored records while evaluating an unlimited corpus.
            # Django loads these fields lazily if a later caller needs them;
            # the score, match explanation and short snippet are already saved.
            for field_name in (
                "ocr_text",
                "search_vector",
                "metadata_search_vector",
                "index_title",
                "index_meta",
                "index_extra",
                "description",
            ):
                document.__dict__.pop(field_name, None)

        document.__dict__.pop("_source_chunk", None)
    results.sort(
        key=lambda item: (
            -item.relevance,
            -(item.document.document_date.toordinal() if item.document.document_date else 0),
            -item.document.pk,
        )
    )
    visible = results if show_below_threshold else [item for item in results if item.relevance >= min_relevance]
    hidden = 0 if show_below_threshold else len(results) - len(visible)

    evaluated_count = len(results)
    returned = visible if exhaustive else visible[:limit]
    response = SearchResponse(
        results=returned,
        hidden_count=hidden,
        total_matches=total,
        evaluated_count=evaluated_count,
        truncated=not exhaustive and (total > evaluated_count or len(visible) > limit),
        limited_count=0 if exhaustive else max(0, len(visible) - limit),
        result_limit=total if exhaustive else limit,
        query=raw_query,
        scoring_version=("lexical-chunks-aliases-1" if settings.ENABLE_SOURCE_CHUNK_SEARCH else "lexical-aliases-1")
        if expanded_terms
        else ("lexical-chunks-1" if settings.ENABLE_SOURCE_CHUNK_SEARCH else "lexical-1"),
        expanded_terms=expanded_terms,
        candidate_ids=[item.document.pk for item in results],
        used_fuzzy=use_fuzzy,
        duration_ms=int((time.perf_counter() - started) * 1000),
        explanation=(
            "Ranked by relevance: title and tracking number count most, then office, type and tags, "
            "then metadata, then the text inside the file."
            + (
                " Approved vocabulary variants use half weight for text matching; original identifiers, field and fuzzy signals remain unchanged. Expanded-only results may be below your relevance threshold."
                if expanded_terms
                else ""
            )
        ),
    )

    if log:
        try:
            query_log = SearchQueryLog.objects.create(
                user=user if user.is_authenticated else None,
                query=raw_query[:255],
                filters={
                    "year": year,
                    "office": getattr(office, "code", None),
                    "document_type": getattr(document_type, "code", None),
                    "tag": getattr(tag, "name", None),
                    "min_relevance": min_relevance,
                    "scoring_version": response.scoring_version,
                },
                result_count=len(response.results),
                top_score=response.results[0].relevance if response.results else None,
                duration_ms=response.duration_ms,
            )
            response.log_id = query_log.pk
        except DatabaseError:  # logging must never break a search
            logger.warning("Could not write the search log entry")

    return response


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------
def _saturate(rank: float) -> float:
    k = max(settings.SEARCH_RANK_SATURATION_K, 1e-6)
    return rank / (rank + k) if rank > 0 else 0.0


def _is_identifier_hit(document: Document, raw_query: str) -> bool:
    needle = raw_query.strip().lower()
    if not needle or len(needle) < 6:
        return False
    candidates = [document.reference_number or ""]
    if document.tracking_origin:
        candidates.append(document.tracking_origin.tracking_number)
        candidates.append(getattr(document.tracking_origin, "original_reference", ""))
    return any(needle == value.lower() for value in candidates if value)


def _field_score(document: Document, raw_query: str, tokens: list[str]) -> tuple[float, list[str]]:
    """Deterministic bonus + a human explanation of where the match came from."""
    title = (document.title or "").lower()
    reference = (document.reference_number or "").lower()
    office_code = (document.office.code if document.office_id else "").lower()
    office_name = (document.office.name if document.office_id else "").lower()
    type_name = document.document_type_names.lower()
    tags = [tag.name.lower() for tag in document.tags.all()]
    metadata = (document.index_extra or "").lower()
    body = (document.ocr_text or "").lower()

    score = 0.0
    matched: list[str] = []

    if tokens and all(token in title for token in tokens):
        score += 0.55
        matched.append("Title")
    elif any(token in title for token in tokens):
        score += 0.30
        matched.append("Title")

    if any(token in reference for token in tokens):
        score += 0.25
        matched.append("Reference number")

    tag_hits = sum(1 for token in tokens if any(token in tag for tag in tags))
    if tag_hits:
        score += min(0.25, 0.12 * tag_hits)
        matched.append("Tags")

    if any(token == office_code for token in tokens) or (office_name and office_name in raw_query.lower()):
        score += 0.15
        matched.append("Office")

    if type_name and any(token in type_name for token in tokens):
        score += 0.10
        matched.append("Document type")

    if any(token in metadata for token in tokens):
        score += 0.08
        matched.append("Metadata")

    if any(token in body for token in tokens):
        score += 0.05
        matched.append("Document text")

    return min(score, 1.0), matched


def _snippet(document: Document, tokens: list[str], width: int = 240) -> str:
    """A short, escaped extract with the query words marked."""
    haystack = (document.description or "").strip() or (document.ocr_text or "").strip()
    if not haystack:
        return ""
    lowered = haystack.lower()
    position = -1
    for token in tokens:
        position = lowered.find(token)
        if position != -1:
            break
    if position == -1:
        excerpt = haystack[:width]
        prefix, suffix = "", "…" if len(haystack) > width else ""
    else:
        start = max(0, position - width // 3)
        excerpt = haystack[start : start + width]
        prefix = "…" if start > 0 else ""
        suffix = "…" if start + width < len(haystack) else ""

    return f"{prefix}{_highlight(' '.join(excerpt.split()), tokens)}{suffix}"


def _highlight(text: str, tokens: list[str]) -> str:
    """Escape `text`, then wrap the query words in <mark>, in one pass.

    One pass matters. Highlighting used to run a substitution per token over
    already-escaped, already-marked-up text, so each round could match inside
    what the previous rounds had inserted: searching "marks mar" wrapped
    "marks", then matched the "mar" of the `<mark>` tag itself and produced
    `<<mark>mar</mark>k>`. Escaping does the same thing from the other side —
    an "amp" token matches the middle of the `&amp;` that escaping just wrote.

    Matching against the raw text and escaping each piece as it is emitted
    means neither the tags nor the entities are ever in the haystack.
    """
    words = sorted({token for token in tokens if len(token) >= 3}, key=len, reverse=True)
    if not words:
        return escape(text)

    pattern = re.compile("|".join(re.escape(word) for word in words), re.IGNORECASE)
    parts: list[str] = []
    position = 0
    for match in pattern.finditer(text):
        parts.append(escape(text[position : match.start()]))
        parts.append(f"<mark>{escape(match.group(0))}</mark>")
        position = match.end()
    parts.append(escape(text[position:]))
    return "".join(parts)


def autocomplete_terms(user, prefix: str, limit: int = 8) -> list[str]:
    """Tag and title suggestions for the search box."""

    prefix = (prefix or "").strip().lower()
    if len(prefix) < 2:
        return []
    from django.db.models import Max, Min
    from django.db.models.functions import Lower

    from apps.documents.services import suggested_tags_for

    tags = list(suggested_tags_for(user).filter(name__istartswith=prefix).values_list("name", flat=True)[:limit])
    remaining = limit - len(tags)
    if remaining > 0:
        titles = (
            Document.objects.visible_to(user)
            .in_collection()
            .filter(is_active=True, title__icontains=prefix)
            .annotate(suggestion=Lower("title"))
            .exclude(suggestion__in=[term.casefold() for term in tags])
            .values("suggestion")
            .annotate(title_text=Min("title"), latest=Max("created_at"))
            .order_by("-latest", "suggestion")
            .values_list("title_text", flat=True)[:remaining]
        )
        tags.extend(titles)
    return tags[:limit]
