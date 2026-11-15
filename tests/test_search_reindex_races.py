"""Repository metadata and stored search indexes agree after a concurrent rebuild."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread

import pytest
from django.db import connections

from apps.core.models import Tag
from apps.documents.models import Document
from apps.documents.services import reindex_all, save_document_metadata
from apps.search.services import search_documents


@pytest.mark.django_db(transaction=True)
def test_reindex_from_stale_snapshot_preserves_new_repository_metadata(users, settings, monkeypatch):
    settings.SEARCH_ENABLE_TRIGRAM = False
    actor = users["med_admin"]
    document = Document.objects.create(title="Old filing title", year=2026, office=actor.office, uploaded_by=actor)
    document.tags.add(Tag.objects.create(name="obsidianfilingneedle"))
    document.rebuild_index()

    request_thread = current_thread()
    snapshot_loaded = Event()
    edit_committed = Event()
    rebuild_index = Document.rebuild_index

    def pause_before_rebuild(instance):
        if current_thread() is not request_thread:
            snapshot_loaded.set()
            assert edit_committed.wait(timeout=15)
        rebuild_index(instance)

    def run_reindex():
        try:
            return reindex_all(Document.objects.filter(pk=document.pk))
        finally:
            connections.close_all()

    monkeypatch.setattr(Document, "rebuild_index", pause_before_rebuild)
    with ThreadPoolExecutor(max_workers=1) as worker:
        reindex = worker.submit(run_reindex)
        try:
            assert snapshot_loaded.wait(timeout=15), "Reindexing never loaded the original document."
            save_document_metadata(
                document,
                user=actor,
                data={"title": "New filing title", "year": 2026},
                tag_names=["quartzfilingneedle"],
                metadata_values={},
            )
        finally:
            edit_committed.set()
        assert reindex.result(timeout=15) == 1

    document.refresh_from_db()
    assert document.index_title == "New filing title"
    for query, expected in (("quartzfilingneedle", {document.pk}), ("obsidianfilingneedle", set())):
        response = search_documents(user=actor, query=query, min_relevance=0, log=False)
        assert {result.document.pk for result in response.results} == expected
