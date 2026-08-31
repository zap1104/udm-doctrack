"""Search: two explicit modes, full-text coverage, relevance, and scoping.

Most of this is "confirm and keep" — the behaviour is already right and these
tests exist so it stays right. 5.4 in particular is a security property, and a
security property with no test is a security property until somebody refactors.
"""

from __future__ import annotations

import pytest
from django.conf import settings

from apps.documents.models import AccessLevel, Document, Source
from apps.search.forms import ADVANCED, QUICK, SearchForm

SEARCH = "/search/"

#: Search with the display threshold wound fully open.
#:
#: The default is 75, and a document matching only on words inside the file
#: scores below it — deliberately, because the threshold exists to keep weak
#: body matches out of a busy list. Every test below is about *coverage* and
#: *scoping*, so they ask what search found rather than what the threshold then
#: chose to show; the threshold has its own tests.
ALL = "mode=advanced&min_relevance=0&show_all=on"


def found_pks(response):
    return {result.document.pk for result in response.context["results"]}


def _indexed(**fields):
    """Create a document and build its search index.

    `rebuild_index()` is what puts a document into the search vector; creating
    one without it produces a row the repository lists and search cannot find,
    which is a state the app itself never reaches — every write path calls it.
    """
    document = Document.objects.create(**fields)
    document.rebuild_index()
    return document


@pytest.fixture
def med_document(db, users, offices, memo_type):
    """A MED document whose distinguishing words are only in the file text."""
    return _indexed(
        title="Annual inventory", office=offices["MED"], year=2026,
        document_type=memo_type, uploaded_by=users["med"], source=Source.UPLOAD,
        access_level=AccessLevel.OFFICE,
        ocr_text="Turbine calibration schedule for the pumphouse.",
    )


@pytest.fixture
def hr_document(db, users, offices, memo_type):
    return _indexed(
        title="Personnel roster", office=offices["HR"], year=2026,
        document_type=memo_type, uploaded_by=users["hr"], source=Source.UPLOAD,
        access_level=AccessLevel.OFFICE,
        ocr_text="Turbine calibration appears here too, in HR's own file.",
    )


# --- 5.1 quick vs advanced --------------------------------------------------
@pytest.mark.django_db
def test_the_page_says_which_search_this_is(client, users):
    client.force_login(users["med"])
    body = client.get(SEARCH).content.decode()

    assert "Quick search" in body
    assert "Advanced search" in body


@pytest.mark.django_db
def test_quick_is_the_default_and_hides_the_filters(client, users):
    client.force_login(users["med"])
    response = client.get(f"{SEARCH}?q=inventory")

    assert response.context["mode"] == QUICK
    assert response.context["is_advanced"] is False
    assert "Minimum relevance" not in response.content.decode()


@pytest.mark.django_db
def test_advanced_shows_them(client, users):
    client.force_login(users["med"])
    response = client.get(f"{SEARCH}?mode=advanced&q=inventory")

    assert response.context["is_advanced"] is True
    assert "Minimum relevance" in response.content.decode()


@pytest.mark.django_db
def test_a_link_carrying_a_filter_opens_in_advanced(client, users, offices):
    """A saved URL predating the modes would otherwise apply its own filter in
    quick mode, where the reader cannot see it."""
    client.force_login(users["med"])
    response = client.get(f"{SEARCH}?office={offices['MED'].pk}")

    assert response.context["mode"] == ADVANCED


@pytest.mark.django_db
def test_quick_mode_ignores_a_filter_left_in_the_url(client, users, offices):
    """Explicitly quick means the query and nothing else — an invisible filter
    is the confusion the split exists to remove."""
    form = SearchForm(
        {"mode": QUICK, "q": "inventory", "office": str(offices["HR"].pk)}, years=[2026]
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["office"] is None
    assert form.mode_in_use == QUICK


@pytest.mark.django_db
def test_the_repository_link_opens_the_detailed_search(client, users):
    """Repository search is the detailed one; tracking's stays minimal."""
    client.force_login(users["med"])
    body = client.get("/documents/").content.decode()

    assert "mode=advanced" in body


# --- 5.2 more than metadata -------------------------------------------------
@pytest.mark.django_db
def test_search_finds_words_that_only_exist_inside_the_file(client, users, med_document):
    """The condition the relevance feature survives on: "turbine" is in the
    extracted text and in no metadata field."""
    assert "turbine" not in med_document.title.lower()

    client.force_login(users["med"])
    response = client.get(f"{SEARCH}?{ALL}&q=turbine")

    assert med_document.pk in found_pks(response)


# --- 5.3 relevance is a display filter --------------------------------------
def test_the_threshold_is_settings_driven_and_defaults_to_75():
    assert settings.SEARCH_MIN_RELEVANCE_DEFAULT == 75


@pytest.mark.django_db
def test_nothing_below_the_threshold_is_silently_dropped(client, users, med_document):
    """Hidden, and the page says how many — never discarded."""
    client.force_login(users["med"])
    response = client.get(f"{SEARCH}?mode=advanced&q=turbine&min_relevance=100")
    search = response.context["response"]

    # Found, counted, and reported as hidden — never quietly discarded.
    assert search.total_matches >= 1, "the match is still found"
    assert search.hidden_count == search.total_matches - len(search.results)
    assert search.hidden_count >= 1, "and it is below a threshold of 100"


@pytest.mark.django_db
def test_the_hidden_results_can_be_shown(client, users, med_document):
    client.force_login(users["med"])
    response = client.get(f"{SEARCH}?mode=advanced&q=turbine&min_relevance=100&show_all=on")
    search = response.context["response"]

    assert search.hidden_count == 0
    assert med_document.pk in found_pks(response)


@pytest.mark.django_db
def test_the_page_calls_it_relevance_and_never_accuracy(client, users, med_document):
    client.force_login(users["med"])
    body = client.get(f"{SEARCH}?mode=advanced&q=turbine").content.decode().lower()

    assert "relevance" in body
    assert "accuracy" not in body


# --- 5.4 scoping (security) -------------------------------------------------
@pytest.mark.django_db
def test_an_office_cannot_search_another_offices_documents(
    client, users, med_document, hr_document
):
    """Never HR seeing MED. Both files contain the same words, so only the
    scoping can be what separates them."""
    client.force_login(users["hr"])
    response = client.get(f"{SEARCH}?{ALL}&q=turbine")
    found = found_pks(response)

    assert hr_document.pk in found, "its own document"
    assert med_document.pk not in found, "another office's"


@pytest.mark.django_db
def test_the_same_query_the_other_way_round(client, users, med_document, hr_document):
    client.force_login(users["med"])
    response = client.get(f"{SEARCH}?{ALL}&q=turbine")
    found = found_pks(response)

    assert med_document.pk in found
    assert hr_document.pk not in found


@pytest.mark.django_db
def test_only_a_system_admin_searches_across_every_office(
    client, users, med_document, hr_document
):
    client.force_login(users["admin"])
    response = client.get(f"{SEARCH}?{ALL}&q=turbine")
    found = found_pks(response)

    assert {med_document.pk, hr_document.pk} <= found


@pytest.mark.django_db
def test_an_office_admin_is_not_a_system_admin_for_search(
    client, users, med_document, hr_document
):
    """The role split must not have widened search by accident."""
    client.force_login(users["med_admin"])
    response = client.get(f"{SEARCH}?{ALL}&q=turbine")
    found = found_pks(response)

    assert med_document.pk in found
    assert hr_document.pk not in found


@pytest.mark.django_db
def test_filtering_by_another_office_still_returns_nothing(
    client, users, med_document, hr_document
):
    """The office filter narrows what you may see; it cannot widen it."""
    client.force_login(users["hr"])
    response = client.get(
        f"{SEARCH}?{ALL}&q=turbine&office={med_document.office_id}"
    )
    found = found_pks(response)

    assert found == set()


@pytest.mark.django_db
def test_a_viewer_searches_only_their_own_office(client, users, med_document, hr_document):
    client.force_login(users["viewer"])
    response = client.get(f"{SEARCH}?{ALL}&q=turbine")
    found = found_pks(response)

    assert med_document.pk in found, "viewer is in MED"
    assert hr_document.pk not in found


@pytest.mark.django_db
def test_autocomplete_is_scoped_too(client, users, med_document, hr_document):
    """A suggestion list is a search result with fewer characters."""
    client.force_login(users["hr"])
    body = client.get("/search/autocomplete/?q=pumphouse").content.decode().lower()

    assert "pumphouse" not in body, "a MED-only word must not be suggested to HR"
