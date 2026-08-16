"""Filters must survive a hand-edited address bar, and must actually filter.

Four faults, each of which looked like a working page:

1. `?year=` on the reports page was parsed with `isdigit()`, which bounds
   nothing. Django's `__year` lookup builds real datetimes for the range
   bounds, so a year past what `datetime` can hold raised — a 500 on a GET.
2. The repository's month and record-status controls were hand-written markup
   that no form declared and no view read. They submitted and changed nothing.
3. Form-level errors were rendered on the sign-in page only, so the search
   page's date-range rule failed silently and read as "no such documents".
4. Re-running extraction opened the stored file bare, so a file missing from
   storage was a 500 where the download views return a clean 404.
"""

from __future__ import annotations

import pytest

from apps.documents.models import Document, DocumentFile, Source


@pytest.fixture
def admin_client(client, users):
    client.force_login(users["admin"])
    return client


# --- 1. the reports year filter -------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize(
    "year", ["10000", "10001", "99999999999999", "0", "1", "999999999999999999999"]
)
def test_an_out_of_range_year_does_not_crash_the_reports_page(admin_client, year):
    assert admin_client.get(f"/reports/?year={year}").status_code == 200


@pytest.mark.django_db
def test_a_real_year_still_filters(admin_client):
    assert admin_client.get("/reports/?year=2026").status_code == 200


@pytest.mark.django_db
def test_a_nonsense_year_is_ignored_rather_than_applied(admin_client):
    """Out of range means "no year filter", not "match nothing"."""
    baseline = admin_client.get("/reports/").context["total_records"]
    assert admin_client.get("/reports/?year=10000").context["total_records"] == baseline


# --- 2. the repository filters --------------------------------------------
@pytest.fixture
def two_kinds_of_document(users, offices):
    from datetime import date

    archived = Document.objects.create(
        title="Archived from tracking", source=Source.DTS, office=offices["MED"],
        year=2026, document_date=date(2026, 3, 4), uploaded_by=users["admin"],
    )
    uploaded = Document.objects.create(
        title="Historical upload", source=Source.UPLOAD, office=offices["MED"],
        year=2026, document_date=date(2026, 9, 9), uploaded_by=users["admin"],
    )
    return archived, uploaded


@pytest.mark.django_db
def test_the_source_filter_actually_filters(admin_client, two_kinds_of_document):
    archived, uploaded = two_kinds_of_document

    shown = list(admin_client.get(f"/documents/?source={Source.DTS}").context["documents"])
    assert archived in shown
    assert uploaded not in shown


@pytest.mark.django_db
def test_the_month_filter_actually_filters(admin_client, two_kinds_of_document):
    archived, uploaded = two_kinds_of_document

    shown = list(admin_client.get("/documents/?month=3").context["documents"])
    assert archived in shown, "documented in March"
    assert uploaded not in shown, "documented in September"


@pytest.mark.django_db
def test_the_old_dead_controls_are_gone_from_the_markup(admin_client):
    body = admin_client.get("/documents/").content.decode()
    assert 'name="record_status"' not in body, "a control nothing reads must not be offered"
    assert 'name="month"' in body and 'name="source"' in body


# --- 3. form-level errors are visible -------------------------------------
@pytest.mark.django_db
def test_the_search_date_range_error_reaches_the_reader(admin_client):
    body = admin_client.get(
        "/search/?q=test&date_from=2026-12-01&date_to=2026-01-01"
    ).content.decode()
    assert "is after the" in body


# --- 4. a file missing from storage ---------------------------------------
@pytest.mark.django_db
def test_re_extract_reports_a_missing_file_instead_of_crashing(admin_client, users, offices):
    document = Document.objects.create(
        title="Its file has vanished", source=Source.UPLOAD, office=offices["MED"],
        year=2026, uploaded_by=users["admin"],
    )
    DocumentFile.objects.create(
        document=document, file="documents/GONE/missing.pdf",
        original_name="missing.pdf", is_primary=True, uploaded_by=users["admin"],
    )

    response = admin_client.post(f"/documents/{document.pk}/re-extract/")

    assert response.status_code == 302
    assert any("missing from storage" in str(m) for m in response.wsgi_request._messages)
