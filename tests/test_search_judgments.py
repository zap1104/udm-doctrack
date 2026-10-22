import json
from importlib.util import find_spec
from pathlib import Path

import pytest

from apps.core.models import Tag
from apps.documents.models import Document
from apps.documents.tagging import approve_alias

pytestmark = pytest.mark.django_db


def test_judged_retrieval_reports_candidate_and_visible_metrics_separately(users, settings):
    assert find_spec("apps.search.evaluation"), "Judged search evaluation is missing"
    from apps.search.evaluation import evaluate_search_judgments

    fixture = json.loads((Path(__file__).parent / "fixtures/search_judgments.json").read_text())
    keys = {}
    for row in fixture["documents"]:
        doc = Document.objects.create(
            title=row["title"],
            ocr_text=row["body"],
            reference_number=row.get("reference", ""),
            year=2026,
            office=users["med"].office,
            uploaded_by=users["med"],
        )
        doc.rebuild_index()
        keys[row["key"]] = doc.pk
    tag = Tag.objects.create(name="purchase request", globally_approved=True)
    approve_alias(tag.pk, "pr", user=users["admin"])
    judgments = [
        {**row, "relevant_ids": [keys[key] for key in row["relevant_keys"]], "synthetic": True}
        for row in fixture["judgments"]
    ]
    settings.ENABLE_APPROVED_SEARCH_ALIASES = False
    baseline = evaluate_search_judgments(users["med"], judgments, collection="OVPA")
    settings.ENABLE_APPROVED_SEARCH_ALIASES = True
    expanded = evaluate_search_judgments(users["med"], judgments, collection="OVPA")
    assert expanded["candidate_recall"] > baseline["candidate_recall"]
    assert expanded["fixture_only"] is True
    assert expanded["corpus_documents"] == 4
    assert set(expanded) >= {"precision_at_5", "success_at_5", "ndcg_at_5", "queries", "latency", "cache_state"}
    strict = evaluate_search_judgments(
        users["med"],
        [{"query": "pr", "relevant_ids": [keys["purchasing"]], "min_relevance": 100, "synthetic": True}],
        collection="OVPA",
    )
    assert strict["candidate_recall"] == 1
    assert strict["success_at_5"] == 0
