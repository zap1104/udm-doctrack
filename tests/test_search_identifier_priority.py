"""Exact identifier lookups survive the bounded ranking candidate window."""

from unittest.mock import patch

import pytest
from django.db import DatabaseError

from apps.documents.models import Document, Source
from apps.search.services import search_documents
from apps.tracking.services import create_draft_record, route_record
from tests.test_search import make_document


@pytest.mark.django_db
@pytest.mark.parametrize("indexed", [False, True], ids=["pending-index", "indexed"])
def test_exact_reference_is_kept_ahead_of_incidental_number_mentions(
    users, offices, settings, indexed,
):
    settings.SEARCH_ENABLE_TRIGRAM = False
    settings.SEARCH_RESULT_LIMIT = 1
    reference = "UDM-MED-2026-9000"
    exact = Document.objects.create(
        title="Approved equipment request", office=offices["MED"],
        reference_number=reference, source=Source.UPLOAD, year=2026,
    )
    if indexed:
        exact.rebuild_index()

    # These are stronger raw full-text matches but not the identified file.
    # There are more of them than the two-candidate window for a one-result
    # search, so a candidate cut-off before the identifier bonus loses exact.
    for index in range(3):
        make_document(
            offices["MED"],
            f"{reference} {reference} {reference} supporting notes {index}",
        )
    # Prioritising the identifier must still operate inside visibility.
    hidden = make_document(
        offices["HR"], "Private personnel document", reference_number=reference,
    )

    response = search_documents(
        user=users["med"], query=reference, min_relevance=100, log=False,
    )

    assert response.total_matches == 4
    assert [result.document.pk for result in response.results] == [exact.pk]
    assert response.results[0].relevance == 100
    assert hidden.pk not in {result.document.pk for result in response.results}
    assert response.evaluated_count <= 2


@pytest.mark.django_db
def test_linked_tracking_number_is_findable_before_its_index_is_built(
    users, offices, settings,
):
    settings.SEARCH_ENABLE_TRIGRAM = False
    settings.SEARCH_RESULT_LIMIT = 1
    record = create_draft_record(
        user=users["med"], subject="Linked equipment request", instructions="Review",
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    document = Document.objects.create(
        title="Filed equipment request", office=offices["MED"],
        tracking_record=record, reference_number="Separate archive reference",
        source=Source.DTS, year=2026,
    )

    response = search_documents(
        user=users["med"], query=record.tracking_number, min_relevance=100,
        log=False,
    )

    assert [result.document.pk for result in response.results] == [document.pk]
    assert response.results[0].relevance == 100
    assert "Tracking number" in response.results[0].matched_in


@pytest.mark.django_db
def test_plain_search_fallback_keeps_the_exact_reference_in_its_result_window(
    users, offices, settings,
):
    settings.SEARCH_ENABLE_TRIGRAM = False
    reference = "UDM-MED-2026-9001"
    exact = make_document(
        offices["MED"], "Archived equipment request", reference_number=reference,
    )
    for index in range(3):
        make_document(offices["MED"], f"{reference} supporting notes {index}")
    queryset_class = type(Document.objects.all())
    original_annotate = queryset_class.annotate

    def fail_rank_only(queryset, *args, **kwargs):
        if "rank" in kwargs:
            raise DatabaseError("Full-text ranking unavailable")
        return original_annotate(queryset, *args, **kwargs)

    with patch.object(queryset_class, "annotate", fail_rank_only):
        response = search_documents(
            user=users["med"], query=reference, min_relevance=100,
            limit=1, log=False,
        )

    assert response.total_matches == 4
    assert [result.document.pk for result in response.results] == [exact.pk]
    assert response.evaluated_count == 1


@pytest.mark.django_db
def test_exact_identifier_priority_keeps_metadata_and_base_queryset_filters(
    users, offices, settings,
):
    settings.SEARCH_ENABLE_TRIGRAM = False
    reference = "UDM-MED-2026-9002"
    exact = make_document(
        offices["MED"], "Archived equipment request", reference_number=reference,
    )
    other = make_document(offices["SUP"], f"{reference} supporting notes")

    for filters in (
        {"office": offices["SUP"]},
        {"base_documents": Document.objects.filter(pk=other.pk)},
    ):
        response = search_documents(
            user=users["admin"], query=reference, min_relevance=100, log=False,
            **filters,
        )
        assert response.total_matches == 1
        assert not response.results
        assert exact.pk not in {result.document.pk for result in response.results}


@pytest.mark.django_db
def test_missing_full_text_vectors_do_not_displace_a_stronger_indexed_match(
    users, offices, settings,
):
    settings.SEARCH_ENABLE_TRIGRAM = False
    settings.SEARCH_RESULT_LIMIT = 1
    indexed = make_document(offices["MED"], "Turbine turbine turbine turbine")
    for index in range(3):
        Document.objects.create(
            title=f"Turbine unindexed notes {index}", office=offices["MED"],
            source=Source.UPLOAD, year=2026,
        )

    response = search_documents(
        user=users["med"], query="turbine", min_relevance=50, log=False,
    )

    assert response.total_matches == 4
    assert [result.document.pk for result in response.results] == [indexed.pk]
    assert response.evaluated_count <= 2
