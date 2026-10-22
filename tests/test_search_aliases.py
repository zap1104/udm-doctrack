from importlib.util import find_spec

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.models import Tag
from apps.documents.models import Document, DocumentFile, TagAlias
from apps.documents.tagging import approve_alias
from apps.search.services import search_documents

pytestmark = pytest.mark.django_db(transaction=True)


def api():
    assert find_spec("apps.search.aliases"), "Approved scoped query expansion is missing"
    from apps.search import aliases

    return aliases


def test_approved_alias_recovers_only_authorized_documents_and_keeps_exact_priority(users, settings):
    api()
    settings.ENABLE_APPROVED_SEARCH_ALIASES = True
    tag = Tag.objects.create(name="purchase request", globally_approved=True)
    approve_alias(tag.pk, "pr", user=users["admin"])
    made = []
    for owner, title in [
        ("med", "PR memo"),
        ("med", "Purchase request memo"),
        ("hr", "Purchase request restricted canary"),
    ]:
        doc = Document.objects.create(
            title=title, year=2026, office=users[owner].office, uploaded_by=users[owner], access_level="OFFICE"
        )
        doc.rebuild_index()
        made.append(doc)
    result = search_documents(
        user=users["med"], query="pr", min_relevance=0, show_below_threshold=True, exhaustive=True, log=False
    )
    assert {row.document.pk for row in result.results} == {made[0].pk, made[1].pk}
    scores = {row.document.pk: row for row in result.results}
    assert scores[made[0].pk].relevance > scores[made[1].pk].relevance
    assert "Approved alias" in scores[made[1].pk].matched_in
    made[0].reference_number = "UDM-MED-2026-10-0001"
    made[0].save(update_fields=["reference_number"])
    made[0].rebuild_index()
    exact = search_documents(user=users["med"], query=made[0].reference_number, log=False)
    assert exact.results[0].document.pk == made[0].pk
    assert exact.results[0].relevance == 100
    assert result.scoring_version.endswith("aliases-1")


def test_unapproved_private_aliases_never_expand_and_expansion_is_bounded(users):
    service = api()
    private = Tag.objects.create(name="private office topic")
    TagAlias.objects.create(tag=private, phrase="privatecanaryacronym", approved_by=users["admin"])
    assert service.expand_query(users["med"], "privatecanaryacronym", collection="OVPA") == []
    public = Tag.objects.create(name="approved public topic", globally_approved=True)
    for i in range(12):
        approve_alias(public.pk, f"public acronym {i}", user=users["admin"])
    variants = service.expand_query(users["med"], public.name, collection="OVPA")
    assert len(variants) == 8
    assert all("private" not in value for value in variants)


def test_private_alias_merge_does_not_approve_its_global_disclosure(users):
    service = api()
    from apps.documents.tagging import merge_tags

    private = Tag.objects.create(name="private topic")
    target = Tag.objects.create(name="approved destination", globally_approved=True)
    alias = TagAlias.objects.create(tag=private, phrase="privatecanaryalias", approved_by=users["admin"])
    merge_tags(private.pk, target.pk, user=users["admin"], reason="Duplicate topic")
    alias.refresh_from_db()
    assert alias.tag_id == target.pk
    assert not alias.is_active
    assert service.expand_query(users["med"], "privatecanaryalias", collection="OVPA") == []


def test_alias_finds_late_current_source_text_with_filename(users, settings, tmp_path):
    api()
    from apps.documents.processing import process_run, request_processing

    settings.ENABLE_APPROVED_SEARCH_ALIASES = True
    settings.ENABLE_SOURCE_UNIT_EXTRACTION = True
    settings.ENABLE_SOURCE_CHUNK_SEARCH = True
    settings.ENABLE_BACKGROUND_TASKS = False
    settings.MEDIA_ROOT = tmp_path
    tag = Tag.objects.create(name="purchase request", globally_approved=True)
    approve_alias(tag.pk, "pr", user=users["admin"])
    doc = Document.objects.create(title="Late source", year=2026, office=users["med"].office, uploaded_by=users["med"])
    text = "ordinary appendix discussion " * 9000 + " purchase request required here "
    DocumentFile.objects.create(
        document=doc, file=SimpleUploadedFile("late.txt", text.encode()), original_name="late.txt"
    )
    run = request_processing(doc, user=users["med"])[0]
    process_run(run.pk, expected_generation=run.generation)
    doc.refresh_from_db()
    assert "purchase request" not in doc.ocr_text
    result = search_documents(user=users["med"], query="pr", min_relevance=0, show_below_threshold=True, log=False)
    assert [row.document.pk for row in result.results] == [doc.pk]
    assert result.results[0].source_filename == "late.txt"
    assert "<mark>purchase</mark>" in result.results[0].snippet_html
    from unittest.mock import patch

    from django.db import DatabaseError
    from django.db.models import QuerySet

    from apps.search.services import _TRIGRAM_STATE

    previous = dict(_TRIGRAM_STATE)
    real_count = QuerySet.count
    failed = False
    def fail_once(queryset):
        nonlocal failed
        if not failed:
            failed = True
            raise DatabaseError('simulated ranking failure')
        return real_count(queryset)
    try:
        with patch.object(QuerySet, 'count', fail_once):
            fallback = search_documents(user=users['med'], query='pr', min_relevance=0, show_below_threshold=True, log=False)
        assert [row.document.pk for row in fallback.results] == [doc.pk]
        assert fallback.results[0].source_filename == 'late.txt'
        assert 'Approved alias' in fallback.results[0].matched_in
    finally:
        _TRIGRAM_STATE.clear()
        _TRIGRAM_STATE.update(previous)
