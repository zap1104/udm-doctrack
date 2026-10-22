"""Current source chunks extend recall without changing document populations."""
from importlib.util import find_spec

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.documents.models import Document, DocumentFile
from apps.documents.processing import process_run, request_processing
from apps.search.services import search_documents

pytestmark = pytest.mark.django_db(transaction=True)


def chunk_api():
    assert find_spec("apps.search.chunks"), "Current-source chunk search is missing"
    from apps.search import chunks
    return chunks


@pytest.fixture
def make_source(users, settings, tmp_path):
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    settings.ENABLE_SOURCE_CHUNK_SEARCH = True
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.MEDIA_ROOT = tmp_path
    def create(text, *, owner="med", title="Ordinary record"):
        user = users[owner]
        document = Document.objects.create(title=title, office=user.office, uploaded_by=user, year=2026)
        DocumentFile.objects.create(document=document, file=SimpleUploadedFile("source.txt", text.encode()), original_name="source.txt")
        run = request_processing(document, user=user)[0]
        process_run(run.pk, expected_generation=run.generation)
        document.refresh_from_db()
        return document, run
    return create


def test_late_source_phrase_is_found_once_with_file_and_unit(make_source, users):
    api = chunk_api()
    document, run = make_source("early discussion " * 15000 + " lateappendixcanary <img src=x onerror=alert(1)> ")
    assert "lateappendixcanary" not in document.ocr_text
    api.publish_chunks(run, document=document, expected_revision=str(document.extraction_generation))
    result = search_documents(user=users["med"], query="lateappendixcanary", min_relevance=0, show_below_threshold=True, log=False)
    assert [row.document.pk for row in result.results] == [document.pk]
    snippet = api.source_snippet(document, ["lateappendixcanary"])
    assert snippet["filename"] == "source.txt"
    assert snippet["unit_id"] == run.units.get().pk
    assert "<mark>lateappendixcanary</mark>" in snippet["html"]
    assert "&lt;img" in snippet["html"]
    assert "<img" not in snippet["html"]


def test_chunk_publication_is_idempotent_and_old_run_is_not_searchable(make_source, users):
    api = chunk_api()
    from apps.documents.models import SearchChunk
    document, run = make_source("opening " * 30000 + " previousonlycanary ")
    api.publish_chunks(run, document=document, expected_revision=str(document.extraction_generation))
    count = SearchChunk.objects.filter(unit__run=run).count()
    api.publish_chunks(run, document=document, expected_revision=str(document.extraction_generation))
    assert SearchChunk.objects.filter(unit__run=run).count() == count
    assert count > 1
    type(run).objects.filter(pk=run.pk).update(is_current=False)
    response = search_documents(user=users["med"], query="previousonlycanary", min_relevance=0, show_below_threshold=True, log=False)
    assert not response.results


def test_restricted_office_chunk_cannot_leak_results_snippets_or_counts(make_source, users):
    api = chunk_api()
    document, run = make_source("ordinary " * 30000 + " privatehrcanary ", owner="hr")
    api.publish_chunks(run, document=document, expected_revision=str(document.extraction_generation))
    response = search_documents(user=users["med"], query="privatehrcanary", min_relevance=0, show_below_threshold=True,
        log=False, base_documents=Document.objects.all())
    assert response.results == []
    assert response.total_matches == 0
    assert response.hidden_count == 0


def test_long_document_cannot_monopolize_document_candidate_window(make_source, users):
    api = chunk_api()
    expected = set()
    for index, repetitions in enumerate([10000, 1, 1, 1]):
        document, run = make_source(("sharedcanary reasonable content. " * repetitions), title=f"Neutral record {index}")
        api.publish_chunks(run, document=document, expected_revision=str(document.extraction_generation))
        expected.add(document.pk)
    response = search_documents(user=users["med"], query="sharedcanary", min_relevance=0, show_below_threshold=True, log=False, limit=3)
    assert len(response.results) == 3
    assert len({row.document.pk for row in response.results}) == 3
    exhaustive = search_documents(user=users["med"], query="sharedcanary", min_relevance=0,
        show_below_threshold=True, log=False, limit=1, exhaustive=True)
    assert {row.document.pk for row in exhaustive.results} == expected
    assert exhaustive.total_matches == 4


def test_source_rank_survives_missing_metadata_vector(make_source, users):
    api = chunk_api()
    document, run = make_source("ordinary " * 30000 + " ranktailcanary ")
    api.publish_chunks(run, document=document, expected_revision=str(document.extraction_generation))
    Document.objects.filter(pk=document.pk).update(metadata_search_vector=None)
    result = search_documents(user=users["med"], query="ranktailcanary", min_relevance=0, show_below_threshold=True, log=False)
    assert result.results[0].text_score > 0


def test_document_correlated_query_plan_runs_on_postgresql(make_source, users, tmp_path):
    import json

    api = chunk_api()
    document, run = make_source("queryplanfixture " * 500)
    api.publish_chunks(run, document=document, expected_revision=str(document.extraction_generation))
    base = Document.objects.visible_to(users["med"]).in_collection().filter(is_active=True)
    query = base.annotate(**api.chunk_annotations(base, "queryplanfixture")).filter(_chunk_match=True)
    plan = json.loads(query.explain(format="json"))
    (tmp_path / "source-query-plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    assert set(query.values_list("pk", flat=True)) == {document.pk}
