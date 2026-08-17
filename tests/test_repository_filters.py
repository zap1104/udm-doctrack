"""The repository filter row must offer only choices that lead somewhere.

A dropdown listing every tag in the system is a menu of dead ends: most of
them are on no document the reader can see, so each pick answers "no results"
for a filter that was never going to match. The same rule the tracking page's
status dropdown already follows.

The row also used to hang every filter off a single `if form.is_valid()`, so
one unrecognised value — a stale bookmark, a tag since deleted — silently
dropped *all* of them and returned the whole repository while the controls
still showed a narrow search.
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.core.models import DocumentType, Tag
from apps.documents.models import Document, Source


@pytest.fixture
def admin_client(client, users):
    client.force_login(users["admin"])
    return client


@pytest.fixture
def repository(users, offices, memo_type):
    """Two documents, plus master data that is deliberately never used."""
    march = Document.objects.create(
        title="March upload", source=Source.UPLOAD, office=offices["MED"],
        document_type=memo_type, year=2026, document_date=date(2026, 3, 4),
        uploaded_by=users["admin"],
    )
    september = Document.objects.create(
        title="September archive", source=Source.DTS, office=offices["MED"],
        year=2026, document_date=date(2026, 9, 9), uploaded_by=users["admin"],
    )
    used, _ = Tag.get_or_create_by_name("maintenance")
    march.tags.add(used)
    # Master data that exists but is on no document at all.
    DocumentType.objects.create(code="UNUSED", name="Never Filed Type")
    Tag.get_or_create_by_name("orphan tag")
    return march, september


def _choice_values(form, name):
    return [value for value, _label in form.fields[name].choices if value]


# --- only choices that lead somewhere -------------------------------------
@pytest.mark.django_db
def test_unused_document_types_are_not_offered(admin_client, repository):
    form = admin_client.get("/documents/").context["form"]
    labels = [str(label) for _v, label in form.fields["document_type"].choices]

    assert "Memorandum" in labels
    assert "Never Filed Type" not in labels


@pytest.mark.django_db
def test_unused_tags_are_not_offered(admin_client, repository):
    form = admin_client.get("/documents/").context["form"]
    labels = [str(label) for _v, label in form.fields["tag"].choices]

    assert "maintenance" in labels
    assert "orphan tag" not in labels


@pytest.mark.django_db
def test_only_months_that_hold_something_are_offered(admin_client, repository):
    form = admin_client.get("/documents/").context["form"]
    assert set(_choice_values(form, "month")) == {"3", "9"}


@pytest.mark.django_db
def test_only_sources_present_are_offered(admin_client, repository):
    form = admin_client.get("/documents/").context["form"]
    assert set(_choice_values(form, "source")) == {Source.UPLOAD, Source.DTS}
    assert Source.SCAN not in _choice_values(form, "source"), "nothing was scanned"


@pytest.mark.django_db
def test_no_offered_option_is_a_dead_end(admin_client, repository):
    """The whole point: every pick must return at least one record."""
    form = admin_client.get("/documents/").context["form"]
    for name in ("document_type", "tag", "source", "year", "month"):
        for value in _choice_values(form, name):
            count = admin_client.get(f"/documents/?{name}={value}").context[
                "page_obj"
            ].paginator.count
            assert count > 0, f"{name}={value} is offered but matches nothing"


# --- ordering --------------------------------------------------------------
@pytest.mark.django_db
def test_months_read_in_calendar_order(admin_client, repository):
    form = admin_client.get("/documents/").context["form"]
    values = [int(v) for v in _choice_values(form, "month")]
    assert values == sorted(values)


@pytest.mark.django_db
def test_years_read_newest_first(admin_client, users, offices):
    for year in (2024, 2026, 2025):
        Document.objects.create(
            title=f"Doc {year}", source=Source.UPLOAD, office=offices["MED"],
            year=year, document_date=date(year, 1, 1), uploaded_by=users["admin"],
        )
    form = admin_client.get("/documents/").context["form"]
    years = [int(v) for v in _choice_values(form, "year")]
    assert years == sorted(years, reverse=True)


@pytest.mark.django_db
def test_tags_lead_with_the_most_used(admin_client, users, offices):
    common, _ = Tag.get_or_create_by_name("common")
    rare, _ = Tag.get_or_create_by_name("rare")
    for index in range(3):
        doc = Document.objects.create(
            title=f"Doc {index}", source=Source.UPLOAD, office=offices["MED"],
            year=2026, uploaded_by=users["admin"],
        )
        doc.tags.add(common)
    Tag.objects.filter(pk=common.pk).update(usage_count=3)
    solo = Document.objects.create(
        title="Solo", source=Source.UPLOAD, office=offices["MED"],
        year=2026, uploaded_by=users["admin"],
    )
    solo.tags.add(rare)
    Tag.objects.filter(pk=rare.pk).update(usage_count=1)

    form = admin_client.get("/documents/").context["form"]
    labels = [str(label) for _v, label in form.fields["tag"].choices if _v]
    assert labels.index("common") < labels.index("rare")


@pytest.mark.django_db
def test_the_row_reads_in_a_sensible_order(admin_client, repository):
    """Query, then what kind of thing, then when — with year and month adjacent."""
    form = admin_client.get("/documents/").context["form"]
    assert list(form.fields) == ["q", "document_type", "tag", "source", "year", "month", "retention"]


# --- one bad filter must not drop the rest --------------------------------
@pytest.mark.django_db
def test_an_unrecognised_filter_does_not_drop_the_others(admin_client, repository):
    only_uploads = admin_client.get(f"/documents/?source={Source.UPLOAD}").context[
        "page_obj"
    ].paginator.count
    everything = admin_client.get("/documents/").context["page_obj"].paginator.count
    assert only_uploads < everything, "fixture must make the filter meaningful"

    response = admin_client.get(f"/documents/?source={Source.UPLOAD}&tag=999999")

    assert response.context["page_obj"].paginator.count == only_uploads
    assert any("Ignored a filter" in str(m) for m in response.context["messages"])
