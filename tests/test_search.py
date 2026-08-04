"""Search is the feature the archive lives or dies on. These tests check the
ranking behaves the way the design document describes."""

from __future__ import annotations

import pytest

from apps.documents.models import Document
from apps.search.services import search_documents


def make_document(office, title, text="", **extra):
    document = Document.objects.create(
        title=title, office=office, ocr_text=text, source="UPLOAD", year=2026, **extra
    )
    document.rebuild_index()
    return document


@pytest.mark.django_db
def test_title_match_outranks_a_body_match(users, offices):
    strong = make_document(offices["MED"], "Preventive maintenance of clinic equipment")
    weak = make_document(
        offices["MED"], "Monthly accomplishment report",
        text="Included a note about preventive maintenance scheduling.",
    )

    response = search_documents(user=users["admin"], query="preventive maintenance", min_relevance=0, log=False)
    ranked = [result.document.pk for result in response.results]

    assert ranked.index(strong.pk) < ranked.index(weak.pk)


@pytest.mark.django_db
def test_relevance_is_a_percentage_between_0_and_100(users, offices):
    make_document(offices["MED"], "Clinic supplies request")
    response = search_documents(user=users["admin"], query="clinic supplies", min_relevance=0, log=False)

    for result in response.results:
        assert 0 <= result.relevance <= 100


@pytest.mark.django_db
def test_threshold_hides_weak_matches_but_reports_the_count(users, offices):
    make_document(offices["MED"], "Clinic supplies request")
    make_document(offices["HR"], "Staff leave form", text="Unrelated wording entirely.")

    response = search_documents(user=users["admin"], query="clinic supplies", min_relevance=75, log=False)

    assert all(result.relevance >= 75 for result in response.results)
    assert response.hidden_count >= 0
    assert response.total_matches >= len(response.results)


@pytest.mark.django_db
def test_results_respect_document_permissions(users, offices):
    make_document(offices["MED"], "Medical records inventory")
    response = search_documents(user=users["hr"], query="medical records", min_relevance=0, log=False)
    # An HR user has no claim on a Medical office document unless it was shared.
    assert all(result.document.office.code != "MED" for result in response.results)


@pytest.mark.django_db
def test_matched_in_explains_why_a_result_appeared(users, offices, tag_urgent):
    document = make_document(offices["MED"], "Urgent request for medicines")
    document.tags.add(tag_urgent)
    document.rebuild_index()

    response = search_documents(user=users["admin"], query="urgent", min_relevance=0, log=False)
    result = next(result for result in response.results if result.document.pk == document.pk)

    assert result.matched_in, "every result should explain where the words were found"
