"""Reports shows each reader what their scope can honestly answer.

Four kinds of reader open this page: a system administrator (the university), an
office administrator (their office, or one they pick), an office user and a
viewer (their office). Two faults came from giving all of them one layout:

- An office administrator who had picked nothing read a page titled with their
  office that counted everything they could see, with no incoming/outgoing
  split, and a prompt to pick the office they were already reading.
- An office's report ranked every office by documents received and handovers,
  built from the documents that office touched, so each row read as another
  office's figure while counting a fraction of it.

And the repository half was missing the figures a records office keeps it for:
how much is due for retention review.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.documents.models import Document, Source
from apps.tracking.models import RoutingStep
from tests.test_filter_agreement import traffic  # noqa: F401 — fixture, used by name

REPORTS = "/tracking/reports/"
REPOSITORY = "/documents/"


@pytest.fixture
def routed(traffic):  # noqa: F811
    """`traffic` from test_filter_agreement: documents sent both ways between
    MED, SUP and HR, received, in process and unreceived."""
    return traffic


# --- whose report it is ---------------------------------------------------------
@pytest.mark.django_db
def test_an_office_administrator_reads_their_own_office_by_default(client, users, offices, routed):
    client.force_login(users["med_admin"])

    response = client.get(REPORTS)
    context = response.context
    body = response.content.decode()

    assert context["scope_office"] == offices["MED"]
    assert context["filters"]["defaulted"] is True
    assert "Pick an office above to split these" not in body
    assert '<option value="">All offices</option>' not in body, "an empty choice is their own office"


@pytest.mark.django_db
def test_a_system_administrator_reads_the_university_by_default(client, users, routed):
    client.force_login(users["admin"])

    response = client.get(REPORTS)

    assert response.context["scope_office"] is None
    assert response.context["university_wide"] is True
    assert '<option value="">All offices</option>' in response.content.decode()


@pytest.mark.django_db
def test_an_office_administrator_can_still_pick_another_office(client, users, offices, routed):
    client.force_login(users["med_admin"])

    context = client.get(f"{REPORTS}?office={offices['SUP'].pk}").context

    assert context["scope_office"] == offices["SUP"]
    assert context["filters"]["defaulted"] is False


# --- rankings for the university, the office's own figures otherwise ------------
RANKINGS = ("Documents received by office", "Handovers by office: sent and confirmed")


@pytest.mark.django_db
def test_the_university_report_ranks_offices(client, users, routed):
    client.force_login(users["admin"])

    response = client.get(REPORTS)
    body = response.content.decode()

    assert all(title in body for title in RANKINGS)
    assert response.context["office_volume"]["rows"]
    assert "office_activity" not in response.context


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["sup", "viewer", "med_admin"])
def test_an_office_report_does_not_rank_other_offices(client, users, routed, who):
    client.force_login(users[who])

    response = client.get(REPORTS)
    body = response.content.decode()

    assert not any(title in body for title in RANKINGS)
    assert "office_volume" not in response.context and "office_flow" not in response.context
    assert response.context["office_activity"] is not None


@pytest.mark.django_db
@pytest.mark.parametrize(("who", "code"), [("sup", "SUP"), ("med_admin", "MED"), ("viewer", "MED")])
def test_an_offices_own_figures_are_its_whole_figures(client, users, offices, routed, who, code):
    """Every handover to or from an office is on a document it touched, so the
    office's own row is complete. Counted independently, straight off the
    routing steps, with no scope at all."""
    office = offices[code]
    client.force_login(users[who])

    activity = client.get(REPORTS).context["office_activity"]

    steps = RoutingStep.objects
    assert activity["received"] == steps.filter(to_office=office, received_at__isnull=False).count()
    assert activity["sent"] == steps.filter(from_office=office).count()
    assert activity["sent_confirmed"] == steps.filter(
        from_office=office, received_at__isnull=False
    ).count()
    assert activity["sent_waiting"] == activity["sent"] - activity["sent_confirmed"]
    assert activity["sent"] > 0, "the fixture routes documents from every office it names"


# --- the repository half: retention, for everyone -------------------------------
@pytest.fixture
def scheduled(users, offices, memo_type):
    """MED documents in each retention state, and one SUP document past due."""
    today = timezone.localdate()
    made = []
    for title, office, until in (
        ("Past due", "MED", today - timedelta(days=10)),
        ("Also past due", "MED", today - timedelta(days=400)),
        ("Due next month", "MED", today + timedelta(days=30)),
        ("Due in two years", "MED", today + timedelta(days=700)),
        ("Never scheduled", "MED", None),
        ("SUP past due", "SUP", today - timedelta(days=5)),
    ):
        document = Document.objects.create(
            title=title, office=offices[office], document_type=memo_type,
            year=today.year, source=Source.UPLOAD, uploaded_by=users["med_admin"],
        )
        # Written past save(), which fills an empty retention date from the
        # document type; "Never scheduled" has to stay empty to be the case.
        Document.objects.filter(pk=document.pk).update(retention_until=until)
        made.append(document)
    return made


def _listed(client, url):
    return client.get(f"{url}&per_page=100" if "?" in url else f"{url}?per_page=100").context[
        "page_obj"
    ].paginator.count


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["admin", "med_admin", "med", "viewer"])
def test_every_retention_figure_is_the_list_it_opens(client, users, scheduled, who):
    client.force_login(users[who])

    context = client.get(REPORTS).context
    retention = context["retention"]

    assert retention["due"] == _listed(client, retention["due_url"])
    assert retention["soon"] == _listed(client, retention["soon_url"])
    assert retention["unscheduled"] == _listed(client, retention["unscheduled_url"])
    assert context["total_documents"] == _listed(client, retention["all_url"])


@pytest.mark.django_db
def test_retention_counts_what_is_past_and_what_is_coming(client, users, scheduled):
    client.force_login(users["med_admin"])

    retention = client.get(REPORTS).context["retention"]

    assert (retention["due"], retention["soon"], retention["unscheduled"]) == (2, 1, 1)


@pytest.mark.django_db
@pytest.mark.parametrize(("who", "shown"), [("admin", True), ("med_admin", True), ("med", False), ("viewer", False)])
def test_repository_upkeep_is_for_administrators(client, users, scheduled, who, shown):
    """Tagging and text extraction are an administrator's to fix."""
    client.force_login(users[who])

    response = client.get(REPORTS)

    assert ("extraction" in response.context) is shown
    assert ("Repository upkeep" in response.content.decode()) is shown


@pytest.mark.django_db
def test_the_types_in_use_card_is_gone(client, users, scheduled):
    """It counted the type panel's rows, "Unclassified" and "Other" included."""
    client.force_login(users["admin"])

    assert "Document types in use" not in client.get(REPORTS).content.decode()


# --- a document nobody can open is not counted ----------------------------------
@pytest.mark.django_db
def test_a_deactivated_document_leaves_every_count(client, users, scheduled):
    """The repository, search and the document page all hide it; the dashboard
    and Reports went on counting it, so a figure could exceed its list."""
    client.force_login(users["admin"])
    before_report = client.get(REPORTS).context["total_documents"]
    before_ring = client.get("/").context["repository_donut"]["total"]

    Document.objects.filter(title="Past due").update(is_active=False)

    assert client.get(REPORTS).context["total_documents"] == before_report - 1
    assert client.get("/").context["repository_donut"]["total"] == before_ring - 1
    assert client.get(REPORTS).context["total_documents"] == _listed(client, REPOSITORY)
