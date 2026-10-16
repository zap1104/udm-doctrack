"""Repository/search totals retain their scope at lifecycle and result limits."""

from unittest.mock import patch

import pytest
from django.db import DatabaseError
from django.utils import timezone

from apps.core.models import Tag
from apps.documents.models import Document
from apps.search.services import autocomplete_terms, search_documents
from apps.tracking.models import Status, TrackingRecord
from apps.tracking.services import create_draft_record
from tests.test_search import make_document


@pytest.mark.django_db
@pytest.mark.parametrize("actor", ["admin", "med_admin"])
def test_repository_pending_matches_tracking_with_a_stale_archive_flag(client, users, memo_type, actor):
    record = create_draft_record(
        user=users["med"], subject="Unfiled legacy record", instructions="Review",
        document_type=memo_type,
    )
    # The lifecycle migration can preserve an old archival flag when no filed
    # Document exists. The relation, rather than the flag, answers "filed?".
    TrackingRecord.objects.filter(pk=record.pk).update(
        status=Status.COMPLETED_PENDING_UPLOAD, completed_at=timezone.now(), is_archived=True,
    )
    client.force_login(users[actor])

    tracking = client.get("/tracking/?scope=pending-upload").context
    repository = client.get("/documents/?view=pending").context

    assert tracking["total"] == 1
    assert repository["pending_count"] == repository["total"] == tracking["total"]
    assert {row.pk for row in repository["pending_upload"]} == {record.pk}
    if actor == "med_admin":
        assert "This repository queue shows documents created by your office." in client.get("/documents/?view=pending").content.decode()


@pytest.mark.django_db
def test_autocomplete_duplicates_do_not_use_up_the_unique_term_limit(users, offices):
    Tag.objects.create(name="policy review")
    Document.objects.create(title="Policy unique", office=offices["MED"])
    Document.objects.create(title="Policy Review", office=offices["MED"])
    for _ in range(5):
        Document.objects.create(title="Policy repeated", office=offices["MED"])

    terms = autocomplete_terms(users["admin"], "policy", limit=3)

    assert terms[0] == "policy review", "tags keep their existing priority"
    assert {term.casefold() for term in terms} == {"policy review", "policy repeated", "policy unique"}
    assert len(terms) == 3


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["description", "author_name", "recipient_name", "signatory"])
def test_full_text_fallback_still_searches_extra_metadata(users, offices, field):
    document = make_document(offices["MED"], "Ordinary record", **{field: "quartzneedle"})
    # Simulate a full-text/trigram failure before SQL is sent, so the fallback
    # remains usable inside the test transaction, just as in an autocommit GET.
    queryset_class = type(Document.objects.all())
    original_annotate = queryset_class.annotate

    def fail_rank_only(queryset, *args, **kwargs):
        if "rank" in kwargs:
            raise DatabaseError("Full-text ranking unavailable")
        return original_annotate(queryset, *args, **kwargs)

    with patch("apps.search.services.trigram_available", return_value=False):
        with patch.object(queryset_class, "annotate", fail_rank_only):
            response = search_documents(
                user=users["admin"], query="quartzneedle", min_relevance=0, log=False,
            )

    assert response.total_matches == 1
    assert {result.document.pk for result in response.results} == {document.pk}


@pytest.mark.django_db
def test_search_reports_results_cut_off_after_every_match_was_evaluated(users, offices):
    for index in range(3):
        make_document(offices["MED"], f"Limit audit policy {index}")

    response = search_documents(
        user=users["admin"], query="Limit audit policy", min_relevance=0, limit=2, log=False,
    )

    assert response.total_matches == response.evaluated_count == 3
    assert len(response.results) == 2
    assert response.truncated
    assert response.limited_count == 1
    assert response.result_limit == 2


@pytest.mark.django_db
def test_search_all_explains_the_result_ceiling(client, users, offices, settings):
    settings.SEARCH_RESULT_LIMIT = 2
    for index in range(3):
        make_document(offices["MED"], f"Limit audit policy {index}")
    client.force_login(users["admin"])

    response = client.get("/search/", {
        "q": "Limit audit policy", "min_relevance": 0, "per_page": "all",
    })

    assert response.context["shown_count"] == 2
    assert "2-result search limit" in response.content.decode()
    assert "1 more evaluated result" in response.content.decode()
