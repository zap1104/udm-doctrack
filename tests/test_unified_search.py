"""One search page, two corpora.

`/search/` searches the repository by default and active tracking records when
asked to. The two are branched rather than unioned: nothing scores a tracking
record and nothing gives a filed document a queue, so a single result shape
would fit neither.

The regression that matters most here is the first one — every bookmark and
link that predates the toggle points at a bare `/search/`, and must behave
exactly as it did.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.search.forms import REPOSITORY, TRACKING, TrackingSearchForm
from apps.tracking.forms import TrackingFilterForm
from apps.tracking.models import Status, TrackingRecord
from apps.tracking.services import (
    apply_scope,
    confirm_receipt,
    create_draft_record,
    filter_records,
    route_record,
)

SEARCH = "/search/"
TRACK = f"/search/?mode={TRACKING}"


@pytest.fixture
def med_to_sup(users, offices, memo_type):
    """MED raised "Electrical supplies"; SUP holds it."""
    record = create_draft_record(
        user=users["med"], subject="Electrical supplies request", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    return record


@pytest.fixture
def hr_record(users, offices, memo_type):
    record = create_draft_record(
        user=users["hr"], subject="Personnel movement", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["hr"])
    record.refresh_from_db()
    return record


def found(response):
    return set(response.context["results"])


# --- Group A: the extracted filter ------------------------------------------
@pytest.mark.django_db
def test_filter_records_matches_the_number_the_subject_and_the_offices(
    med_to_sup, users, offices
):
    base = TrackingRecord.objects.visible_to(users["admin"])

    assert med_to_sup in filter_records(base, query=med_to_sup.tracking_number)
    assert med_to_sup in filter_records(base, query="electrical")
    assert med_to_sup in filter_records(base, query=offices["MED"].code), "originating code"
    assert med_to_sup in filter_records(base, query=offices["SUP"].name), "current office name"
    assert med_to_sup not in filter_records(base, query="nothing like this")


@pytest.mark.django_db
def test_filter_records_narrows_by_status(med_to_sup, users):
    base = TrackingRecord.objects.visible_to(users["admin"])

    assert med_to_sup in filter_records(base, status=Status.RECEIVED)
    assert med_to_sup not in filter_records(base, status=Status.PENDING_RECEIPT)


@pytest.mark.django_db
def test_filter_records_treats_overdue_as_derived(med_to_sup, users):
    """Overdue is not a stored status — it is a due date in the past."""
    base = TrackingRecord.objects.visible_to(users["admin"])
    assert med_to_sup not in filter_records(base, status="OVERDUE")

    med_to_sup.due_at = timezone.now() - timezone.timedelta(days=1)
    med_to_sup.save(update_fields=["due_at"])

    assert med_to_sup in filter_records(base, status="OVERDUE")


@pytest.mark.django_db
def test_filter_records_uses_the_originating_office(med_to_sup, users, offices):
    """Same rule as the workspace: who raised it, not who holds it."""
    base = TrackingRecord.objects.visible_to(users["admin"])

    assert med_to_sup in filter_records(base, offices=[offices["MED"]])
    assert med_to_sup not in filter_records(base, offices=[offices["SUP"]])


@pytest.mark.django_db
def test_filter_records_leaves_scope_alone(med_to_sup, users):
    """Scope is the caller's job, applied after — the two compose."""
    base = TrackingRecord.objects.visible_to(users["med"])
    filtered = filter_records(base, query="electrical")

    assert med_to_sup in filtered
    assert med_to_sup not in apply_scope(filtered, "mine", users["sup"]), (
        "SUP did not create it"
    )


@pytest.mark.django_db
def test_the_workspace_still_filters_the_same_way(client, med_to_sup, users, offices):
    """Group A was a refactor. The page it came out of must not have moved."""
    client.force_login(users["admin"])

    by_office = client.get(f"/tracking/?offices={offices['MED'].pk}")
    assert med_to_sup in by_office.context["records"]

    by_status = client.get(f"/tracking/?status={Status.RECEIVED}")
    assert med_to_sup in by_status.context["records"]


# --- Group B: repository mode is untouched ----------------------------------
@pytest.mark.django_db
def test_a_bare_search_url_is_still_the_repository(client, users):
    client.force_login(users["med"])
    response = client.get(SEARCH)

    assert response.status_code == 200
    assert response.context["mode"] == REPOSITORY
    assert response.context["is_tracking"] is False


@pytest.mark.django_db
def test_repository_mode_never_returns_tracking_records(client, med_to_sup, users):
    """The two corpora stay separate — this is not a union."""
    client.force_login(users["admin"])
    response = client.get(f"{SEARCH}?q=electrical")

    for result in response.context["results"]:
        assert not isinstance(result, TrackingRecord)


# --- Group B: tracking mode --------------------------------------------------
@pytest.mark.django_db
def test_tracking_mode_finds_a_record_by_subject(client, med_to_sup, users):
    client.force_login(users["admin"])
    response = client.get(f"{TRACK}&q=electrical")

    assert med_to_sup in found(response)


@pytest.mark.django_db
def test_tracking_mode_finds_a_record_by_its_number(client, med_to_sup, users):
    client.force_login(users["admin"])
    response = client.get(f"{TRACK}&q={med_to_sup.tracking_number}")

    assert med_to_sup in found(response)


@pytest.mark.django_db
def test_tracking_mode_agrees_with_the_workspace_for_the_same_querystring(
    client, med_to_sup, hr_record, users, offices
):
    """The acceptance criterion: same semantics, different page."""
    client.force_login(users["admin"])
    query = f"status={Status.PENDING_RECEIPT}&offices={offices['HR'].pk}"

    from_search = found(client.get(f"{TRACK}&{query}"))
    from_workspace = set(client.get(f"/tracking/?{query}").context["records"])

    assert from_search == from_workspace


@pytest.mark.django_db
def test_tracking_mode_applies_scope(client, med_to_sup, users):
    client.force_login(users["med"])

    assert med_to_sup in found(client.get(f"{TRACK}&scope=outgoing"))
    assert med_to_sup not in found(client.get(f"{TRACK}&scope=incoming"))


@pytest.mark.django_db
def test_tracking_mode_respects_visibility(client, med_to_sup, users):
    """A search surface, not a new access path."""
    client.force_login(users["hr"])
    response = client.get(f"{TRACK}&q=electrical")

    assert med_to_sup not in found(response), "HR had nothing to do with it"


@pytest.mark.django_db
def test_tracking_mode_shows_only_active_records(client, med_to_sup, users):
    """`active_for` is the base, so an approved record is repository business."""
    from apps.documents.services import archive_tracking_record
    from apps.tracking.services import complete_record

    complete_record(med_to_sup, user=users["sup"])
    med_to_sup.refresh_from_db()
    archive_tracking_record(med_to_sup, user=users["sup_admin"])
    med_to_sup.refresh_from_db()

    client.force_login(users["admin"])
    assert med_to_sup not in found(client.get(f"{TRACK}&q=electrical"))


@pytest.mark.django_db
def test_tracking_mode_paginates(client, users, offices, memo_type):
    from apps.tracking.services import PAGE_SIZE

    for index in range(PAGE_SIZE + 3):
        record = create_draft_record(
            user=users["med"], subject=f"Bulk request {index}", instructions="x",
            document_type=memo_type,
        )
        route_record(record, [offices["SUP"]], user=users["med"])

    client.force_login(users["admin"])
    response = client.get(f"{TRACK}&q=Bulk")

    assert len(response.context["results"]) == PAGE_SIZE
    assert response.context["page_obj"].has_next() is True


@pytest.mark.django_db
def test_an_empty_tracking_search_offers_the_prompt_rather_than_everything(
    client, med_to_sup, users
):
    client.force_login(users["admin"])
    response = client.get(TRACK)

    assert response.context["has_searched"] is False
    assert "Search active tracking records" in response.content.decode()


# --- Group C/D: what each mode renders --------------------------------------
@pytest.mark.django_db
def test_tracking_mode_renders_status_pills_not_relevance(client, med_to_sup, users):
    client.force_login(users["admin"])
    body = client.get(f"{TRACK}&q=electrical").content.decode()

    assert med_to_sup.tracking_number in body
    assert "Received" in body
    assert "relevance" not in body.lower()


@pytest.mark.django_db
def test_repository_mode_still_renders_its_own_filters(client, users):
    client.force_login(users["admin"])
    body = client.get(SEARCH).content.decode()

    assert "Minimum relevance" in body
    assert "Include results below the threshold" in body


@pytest.mark.django_db
def test_the_mode_pills_use_the_shared_pill_class(client, users):
    """One pill language across the app, not a third style for this page."""
    client.force_login(users["admin"])
    body = client.get(SEARCH).content.decode()

    assert "pill-toggle" in body


@pytest.mark.django_db
@pytest.mark.parametrize("url", [SEARCH, TRACK, f"{SEARCH}?mode=bogus"])
def test_every_mode_renders_without_error(client, med_to_sup, users, url):
    client.force_login(users["admin"])
    assert client.get(url).status_code == 200


# --- the shared form definition ----------------------------------------------
def test_the_tracking_search_form_reuses_the_workspace_definition():
    """A subclass, so status/scope/offices/owner have one definition and the
    two pages' choice lists cannot drift apart."""
    assert issubclass(TrackingSearchForm, TrackingFilterForm)

    workspace = TrackingFilterForm()
    search = TrackingSearchForm()
    for name in ("status", "scope", "offices", "owner"):
        assert search.fields[name].__class__ is workspace.fields[name].__class__
    assert list(search.fields["scope"].choices) == list(workspace.fields["scope"].choices)


def test_only_the_search_form_has_a_text_box():
    """The workspace traded its box for the queue pills; this page is the
    search surface, so the box belongs to it."""
    assert "q" in TrackingSearchForm().fields
    assert "q" not in TrackingFilterForm().fields
