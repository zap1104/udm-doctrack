"""Authorized, judged retrieval measurements, separate from relevance scores."""

import math
import os
import platform
import statistics
import time

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.documents.models import Collection, Document

from .services import search_documents


def evaluate_search_judgments(user, judgments, *, collection):
    actor = User.objects.filter(pk=getattr(user, "pk", None), is_active=True).first()
    if actor is None or actor.role not in {"USER", "VIEWER", "ADMIN", "SYSTEM_ADMIN"}:
        raise PermissionDenied("An active account is required for scoped search evaluation.")
    if collection not in Collection.values or not isinstance(judgments, list) or not 1 <= len(judgments) <= 100:
        raise ValidationError("Supply 1–100 judgments for a valid collection.")
    corpus = Document.objects.visible_to(actor).in_collection(collection).filter(is_active=True)
    rows, versions, first_times, repeated_times = [], set(), [], []
    for judgment in judgments:
        query = judgment.get("query", "")
        relevant = judgment.get("relevant_ids", [])
        threshold = judgment.get("min_relevance", settings.SEARCH_MIN_RELEVANCE_DEFAULT)
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 512
            or not isinstance(relevant, list)
            or len(relevant) > 1000
            or any(type(pk) is not int for pk in relevant)
            or type(threshold) is not int
            or not 0 <= threshold <= 100
        ):
            raise ValidationError("Invalid query, judgment identifiers or relevance threshold.")
        expected = set(corpus.filter(pk__in=relevant).values_list("pk", flat=True))
        started = time.perf_counter()
        response = search_documents(
            user=actor,
            query=query,
            collection=collection,
            min_relevance=threshold,
            limit=min(100, settings.SEARCH_RESULT_LIMIT),
            log=False,
        )
        first_times.append((time.perf_counter() - started) * 1000)
        versions.add(response.scoring_version)
        candidates = set(response.candidate_ids)
        top = [result.document.pk for result in response.results[:5]]
        hits = sum(pk in expected for pk in top)
        dcg = sum((1 if pk in expected else 0) / math.log2(rank + 2) for rank, pk in enumerate(top))
        ideal = sum(1 / math.log2(rank + 2) for rank in range(min(5, len(expected))))
        rows.append(
            {
                "query": query,
                "authorized_relevant": len(expected),
                "supplied_ids_outside_scope": len(set(relevant) - expected),
                "candidate_recall": len(candidates & expected) / len(expected) if expected else None,
                "precision_at_5": hits / 5,
                "success_at_5": int(hits > 0),
                "ndcg_at_5": dcg / ideal if ideal else None,
                "returned": len(response.results),
                "evaluated": response.evaluated_count,
                "candidate_matches": response.total_matches,
                "window_limited": response.truncated,
                "hidden_by_threshold": response.hidden_count,
                "scoring_version": response.scoring_version,
                "min_relevance": threshold,
            }
        )
    for judgment in judgments:
        started = time.perf_counter()
        search_documents(
            user=actor,
            query=judgment["query"],
            collection=collection,
            min_relevance=judgment.get("min_relevance", settings.SEARCH_MIN_RELEVANCE_DEFAULT),
            limit=min(100, settings.SEARCH_RESULT_LIMIT),
            log=False,
        )
        repeated_times.append((time.perf_counter() - started) * 1000)

    def mean(key):
        values = [row[key] for row in rows if row[key] is not None]
        return statistics.mean(values) if values else None

    def timing(values):
        ordered = sorted(values)
        return {
            "p50_ms": statistics.median(ordered),
            "p95_ms": ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)],
            "samples_ms": values,
        }

    return {
        "schema": "judged-search-1",
        "collection": collection,
        "corpus_documents": corpus.count(),
        "fixture_only": all(judgment.get("synthetic") is True for judgment in judgments),
        **{key: mean(key) for key in ("candidate_recall", "precision_at_5", "success_at_5", "ndcg_at_5")},
        "queries": rows,
        "scoring_versions": sorted(versions),
        "latency": {"first_pass": timing(first_times), "repeated_pass": timing(repeated_times)},
        "cache_state": "Uncontrolled local caches; no database or OS cache reset. First pass is not certified cold.",
        "hardware": {
            "system": platform.system(),
            "machine": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "python": platform.python_version(),
        },
        "notes": [
            "Candidate recall uses evaluated document candidates before the relevance threshold; a capped window is not exhaustive recall.",
            "Precision@5 uses a denominator of five, including unfilled ranks. nDCG uses binary supplied judgments.",
            "Supplied judgments do not certify corpus representativeness, OCR accuracy or production search quality.",
        ],
    }
