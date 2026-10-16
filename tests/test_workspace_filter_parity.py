"""Workspace filters answer the same questions as their Search counterparts."""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.documents.models import Document, Source
from apps.search.services import search_documents
from apps.tracking.models import TrackingRecord
from apps.tracking.services import create_draft_record, route_record
from tests.test_search import make_document


def document_ids(response):
    return [document.pk for document in response.context["documents"]]


@pytest.mark.django_db
def test_tracking_query_matches_search_and_export(client, users, offices, memo_type):
    target = create_draft_record(user=users["med"], subject="Filter parity turbine", instructions="Review", document_type=memo_type)
    other = create_draft_record(user=users["med"], subject="Unrelated request", instructions="Review", document_type=memo_type)
    route_record(target, [offices["SUP"]], user=users["med"])
    route_record(other, [offices["SUP"]], user=users["med"])
    client.force_login(users["admin"])
    params = {"office": offices["SUP"].pk, "scope": "incoming", "q": target.tracking_number}

    tracking = client.get("/tracking/", params)
    search = client.get("/search/", {**params, "mode": "tracking"})
    export = client.get("/tracking/reports/export/", {**params, "source": "tracking"})

    assert [row.pk for row in tracking.context["records"]] == [target.pk]
    assert [row.pk for row in search.context["results"]] == [target.pk]
    assert target.tracking_number in export.content.decode()
    assert other.tracking_number not in export.content.decode()
    assert "Search tracking" in tracking.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("sort", ["deadline", "deadline-desc"])
def test_tracking_search_and_export_share_deadline_order(client, users, offices, memo_type, sort):
    records = []
    for offset in (None, 5, 1):
        record = create_draft_record(user=users["med"], subject=f"Deadline parity {offset}", instructions="Review", document_type=memo_type)
        route_record(record, [offices["SUP"]], user=users["med"])
        TrackingRecord.objects.filter(pk=record.pk).update(due_at=timezone.now() + timedelta(days=offset) if offset else None)
        records.append(record)
    client.force_login(users["admin"])
    params = {"office": offices["SUP"].pk, "q": "Deadline parity", "sort": sort}

    tracking = client.get("/tracking/", params)
    search = client.get("/search/", {**params, "mode": "tracking"})
    export = client.get("/tracking/reports/export/", {**params, "source": "tracking"}).content.decode()
    expected = [records[2], records[1], records[0]] if sort == "deadline" else [records[1], records[2], records[0]]

    assert [row.pk for row in tracking.context["records"]] == [row.pk for row in expected]
    assert [row.pk for row in search.context["results"]] == [row.pk for row in expected]
    assert [export.index(row.tracking_number, export.index("Tracking number")) for row in expected] == sorted(export.index(row.tracking_number, export.index("Tracking number")) for row in expected)
    assert "Latest tracking activity first" not in search.content.decode()


@pytest.mark.django_db
def test_repository_matches_search_relevance_and_date_filters(client, users, offices):
    strong = make_document(offices["MED"], "Campus turbine policy", document_date=date(2026, 3, 5))
    weak = make_document(offices["MED"], "Workshop log", text="Campus turbine policy", document_date=date(2026, 3, 6))
    make_document(offices["MED"], "Campus turbine policy old", document_date=date(2025, 3, 5))
    client.force_login(users["admin"])
    params = {"q": "Campus turbine policy", "office": offices["MED"].pk, "date_from": "2026-03-01", "date_to": "2026-03-31", "min_relevance": "0"}

    repository = client.get("/documents/", params)
    search = client.get("/search/", params)

    assert document_ids(repository) == [result.document.pk for result in search.context["results"]]
    assert set(document_ids(repository)) == {strong.pk, weak.pk}
    assert [document.relevance for document in repository.context["documents"]] == [result.relevance for result in search.context["results"]]
    body = repository.content.decode()
    assert 'name="office"' in body
    assert 'name="date_from"' in body and 'name="date_to"' in body
    assert 'name="min_relevance"' in body and 'name="show_all"' in body
    assert 'data-relevance-value>0</span>' in body


@pytest.mark.django_db
def test_repository_threshold_and_show_all_have_real_scores(client, users, offices):
    strong = make_document(offices["MED"], "Strong filing match", reference_number="UDM-OVPA-MED-2026-1234")
    weak = make_document(offices["MED"], "Office summary", text="UDM-OVPA-MED-2026-1234")
    client.force_login(users["admin"])
    params = {"q": strong.reference_number, "min_relevance": 100}

    filtered = client.get("/documents/", params)
    expanded = client.get("/documents/", {**params, "show_all": "on"})

    assert document_ids(filtered) == [strong.pk]
    assert set(document_ids(expanded)) == {strong.pk, weak.pk}
    assert filtered.context["repository_search"].hidden_count == 1
    assert strong.pk in document_ids(filtered)
    assert filtered.context["documents"][0].relevance == 100


@pytest.mark.django_db
def test_repository_ranked_pagination_does_not_drop_rows_at_search_limit(client, users, offices, settings):
    settings.SEARCH_RESULT_LIMIT = 2
    documents = [make_document(offices["MED"], f"Exhaustive filing policy {index}") for index in range(13)]
    client.force_login(users["admin"])
    params = {"q": "Exhaustive filing policy", "min_relevance": 0, "per_page": 10}

    first = client.get("/documents/", params)
    second = client.get("/documents/", {**params, "page": 2})

    assert first.context["total"] == second.context["total"] == 13
    assert len(document_ids(first)) == 10 and len(document_ids(second)) == 3
    assert set(document_ids(first)) | set(document_ids(second)) == {document.pk for document in documents}
    assert not set(document_ids(first)) & set(document_ids(second))


@pytest.mark.django_db
def test_blank_query_relevance_does_not_hide_repository_browse(client, users, offices):
    document = Document.objects.create(title="Plain browse", office=offices["MED"], source=Source.UPLOAD)
    client.force_login(users["med"])

    response = client.get("/documents/", {"min_relevance": 100})

    assert document_ids(response) == [document.pk]
    assert response.context["repository_search"] is None
    assert "100% relevance" not in response.content.decode()


@pytest.mark.django_db
def test_repository_date_range_rejects_reversal_without_dropping_valid_source(client, users, offices):
    keep = make_document(offices["MED"], "Upload dated", document_date=date(2026, 3, 1))
    Document.objects.create(title="From tracking", office=offices["MED"], source=Source.DTS, document_date=date(2026, 3, 1))
    client.force_login(users["admin"])

    response = client.get("/documents/", {"source": Source.UPLOAD, "date_from": "2026-04-01", "date_to": "2026-03-01"})

    assert document_ids(response) == [keep.pk]
    assert response.context["form"].non_field_errors()
    assert "after" in response.content.decode()


@pytest.mark.django_db
def test_repository_ranked_facet_counts_keep_metadata_filters(client, users, offices, memo_type, tag_urgent):
    target = make_document(offices["MED"], "Faceted turbine request", document_type=memo_type, document_date=date(2026, 3, 5))
    target.tags.add(tag_urgent)
    target.rebuild_index()
    make_document(offices["MED"], "Faceted turbine request old", document_type=memo_type, document_date=date(2026, 9, 5))
    client.force_login(users["admin"])

    response = client.get("/documents/", {"q": "Faceted turbine request", "min_relevance": 0, "month": 3, "tag": tag_urgent.pk})

    assert document_ids(response) == [target.pk]
    assert response.context["total"] == 1
    assert response.context["type_folders"][0]["total"] == 1
    assert response.context["repository_search"].total_matches == 1


@pytest.mark.django_db
@pytest.mark.parametrize("office_parameter", ["code", "pk"])
def test_repository_ranked_office_filter_keeps_permission_scope(client, users, offices, office_parameter):
    own = make_document(offices["MED"], "Permission turbine policy")
    foreign = make_document(offices["HR"], "Permission turbine policy")
    client.force_login(users["med"])
    asked = offices["HR"].code if office_parameter == "code" else offices["HR"].pk

    response = client.get("/documents/", {"q": "Permission turbine policy", "office": asked, "min_relevance": 0})

    assert response.context["total"] == 0
    assert foreign.pk not in document_ids(response) and own.pk not in document_ids(response)
    assert response.context["folder_unavailable"]
    assert offices["HR"].pk not in response.context["form"].fields["office"].queryset.values_list("pk", flat=True)


@pytest.mark.django_db
@pytest.mark.parametrize("matching_field", ["title", "ocr_text", "index_meta", "index_extra"])
def test_repository_preserves_plain_matches_before_full_text_indexing(client, users, offices, settings, matching_field):
    settings.SEARCH_ENABLE_TRIGRAM = False
    fields = {"title": "Ordinary legacy file", matching_field: "Legacy turbine policy"}
    document = Document.objects.create(office=offices["MED"], source=Source.UPLOAD, **fields)
    assert document.search_vector is None
    client.force_login(users["admin"])
    params = {"q": "Legacy turbine", "min_relevance": 0}

    repository = client.get("/documents/", params)
    search = client.get("/search/", params)

    assert document_ids(repository) == [document.pk]
    assert [result.document.pk for result in search.context["results"]] == [document.pk]


@pytest.mark.django_db
def test_pending_filing_does_not_offer_irrelevant_search_controls(client, users, offices):
    client.force_login(users["admin"])

    response = client.get("/documents/", {"view": "pending", "min_relevance": 100, "date_from": "2026-03-01"})

    body = response.content.decode()
    assert 'name="min_relevance"' not in body and 'name="show_all"' not in body
    assert 'name="date_from"' not in body
    assert any("filed documents" in str(message) for message in response.context["messages"])


@pytest.mark.django_db
def test_exhaustive_search_cannot_broaden_a_supplied_queryset(users, offices):
    own = make_document(offices["MED"], "Scoped exhaustive query")
    foreign = make_document(offices["HR"], "Scoped exhaustive query")

    response = search_documents(user=users["med_admin"], query="Scoped exhaustive query", min_relevance=0, base_documents=Document.objects.all(), exhaustive=True, log=False)

    assert [result.document.pk for result in response.results] == [own.pk]
    assert foreign.pk not in [result.document.pk for result in response.results]


@pytest.mark.django_db
def test_exhaustive_scoring_releases_large_text_after_calculating_the_score(users, offices):
    text = "turbine maintenance " * 5000
    document = make_document(offices["MED"], "Large turbine filing", text=text)

    response = search_documents(user=users["admin"], query="turbine", min_relevance=0, exhaustive=True, log=False)

    assert response.total_matches == response.evaluated_count == 1
    result = response.results[0]
    assert result.document.pk == document.pk and result.relevance > 0
    assert "Document text" in result.matched_in
    assert "ocr_text" in result.document.get_deferred_fields(), "An unlimited ranking must not retain every full extracted document."
    assert Document.objects.get(pk=document.pk).ocr_text == text


@pytest.mark.django_db
def test_repository_existing_queries_include_weak_matches_until_a_minimum_is_selected(client, users, offices):
    document = Document.objects.create(title="Legacy turbine policy", office=offices["MED"], source=Source.UPLOAD)
    client.force_login(users["admin"])

    response = client.get("/documents/", {"q": "Legacy turbine"})

    assert document_ids(response) == [document.pk]
    assert response.context["form"].cleaned_data["min_relevance"] == 0
    assert 'data-relevance-value>0</span>' in response.content.decode()
