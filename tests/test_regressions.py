"""Bugs that were live in the system once. Each test is the shape of the report.

Grouped here rather than scattered through the behaviour suites because what
they have in common is not a feature — it is that the code looked right.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.template import Context, Template

from apps.core.templatetags.doctrack import pagination_url
from apps.core.views import _csv_cell
from apps.search.services import _highlight
from apps.tracking.models import RoutingStep, TrackingRecord
from apps.tracking.services import confirm_receipt, create_draft_record, route_record


@pytest.fixture
def held_by_sup(users, offices):
    """A record that MED sent and SUP has confirmed receipt of."""
    record = create_draft_record(
        user=users["med"], subject="Supply request for the workshop", instructions="For action."
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    return record


# ---------------------------------------------------------------------------
# Routing provenance
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_forward_records_the_office_that_actually_held_the_document(held_by_sup, users, offices):
    """An administrator can act for any office, so their own office is not the answer.

    This used to write `from_office = user.office`, so an administrator sitting
    in Records forwarding a document Supply was holding produced a step saying
    the document left Records — and moved `current_office` there to match.
    """
    route_record(held_by_sup, [offices["HR"]], user=users["admin"], action=RoutingStep.Action.FORWARD)
    held_by_sup.refresh_from_db()
    step = held_by_sup.routing_steps.order_by("-sequence").first()

    assert step.from_office == offices["SUP"]
    assert held_by_sup.current_office == offices["SUP"]


@pytest.mark.django_db
def test_forward_by_an_administrator_with_no_office_still_names_a_sender(held_by_sup, users, offices):
    """`from_office = None` erased the record's location entirely."""
    admin = users["admin"]
    admin.office = None
    admin.save(update_fields=["office"])

    route_record(held_by_sup, [offices["HR"]], user=admin, action=RoutingStep.Action.FORWARD)
    held_by_sup.refresh_from_db()
    step = held_by_sup.routing_steps.order_by("-sequence").first()

    assert step.from_office == offices["SUP"]
    assert held_by_sup.current_office is not None


@pytest.mark.django_db
def test_a_document_cannot_be_sent_to_the_office_already_holding_it(held_by_sup, users, offices):
    """The guard was skipped whenever the sender was unknown — exactly when it mattered."""
    admin = users["admin"]
    admin.office = None
    admin.save(update_fields=["office"])

    with pytest.raises(ValidationError):
        route_record(held_by_sup, [offices["SUP"]], user=admin, action=RoutingStep.Action.FORWARD)


@pytest.mark.django_db
def test_an_ordinary_forward_is_unchanged(held_by_sup, users, offices):
    """The office holding the document is still the sender when it forwards itself."""
    route_record(held_by_sup, [offices["HR"]], user=users["sup"], action=RoutingStep.Action.FORWARD)
    step = held_by_sup.routing_steps.order_by("-sequence").first()

    assert step.from_office == offices["SUP"]


# ---------------------------------------------------------------------------
# Choice fields
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_an_unknown_priority_is_refused_rather_than_stored(users):
    """`objects.create()` does not police choices, so "HIGH" was stored verbatim
    and `get_priority_display()` echoed the raw code back at the reader."""
    with pytest.raises(ValidationError):
        create_draft_record(
            user=users["med"], subject="Priority probe", instructions="x", priority="HIGH"
        )


@pytest.mark.django_db
def test_valid_priorities_still_pass(users):
    record = create_draft_record(
        user=users["med"], subject="Priority probe two", instructions="x", priority="URGENT"
    )
    assert record.priority == TrackingRecord.Priority.URGENT
    assert record.get_priority_display() == "Urgent"


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
def test_a_document_date_is_shown_when_it_exists():
    """`|date:"d M Y"|default:x|date:"d M Y"` ran the date filter over its own
    output, and the date filter returns "" for a string — so the date rendered
    blank precisely when the document had one."""
    import datetime

    template = Template('{{ document.document_date|default:document.created_at|date:"d M Y" }}')

    class WithDate:
        document_date = datetime.date(2025, 8, 5)
        created_at = datetime.datetime(2026, 1, 1, 9, 0)

    class WithoutDate:
        document_date = None
        created_at = datetime.datetime(2026, 1, 1, 9, 0)

    assert template.render(Context({"document": WithDate()})) == "05 Aug 2025"
    assert template.render(Context({"document": WithoutDate()})) == "01 Jan 2026"


def _context_for(query: str) -> Context:
    request = HttpRequest()
    request.GET = request.GET.copy()
    for pair in query.split("&"):
        if pair:
            key, _, value = pair.partition("=")
            request.GET[key] = value
    return Context({"request": request})


def test_pagination_replaces_the_page_number_instead_of_appending_one():
    """`?page=3&page=2` yields "2" from a QueryDict, so Next re-served the page
    you were already on and Tracking could not get past page two."""
    url = pagination_url(_context_for("page=2&scope=inbox"), 3)

    assert url.count("page=") == 1
    assert "page=3" in url


def test_pagination_keeps_the_filters():
    """The repository passed no querystring at all, so paging dropped the search."""
    url = pagination_url(_context_for("q=report&year=2025"), 2)

    assert "q=report" in url
    assert "year=2025" in url
    assert "page=2" in url


# ---------------------------------------------------------------------------
# Output escaping
# ---------------------------------------------------------------------------
def test_snippet_highlighting_does_not_match_its_own_markup():
    """Substituting once per token let a later token match the <mark> tag an
    earlier one had inserted, and the "amp" of an escaped &amp;."""
    assert _highlight("marks and mar", ["marks", "mar"]) == "<mark>marks</mark> and <mark>mar</mark>"
    assert _highlight("R&D amp", ["amp"]) == "R&amp;D <mark>amp</mark>"


def test_snippet_highlighting_still_escapes_html():
    assert "<script>" not in _highlight("<script>alert(1)</script>", ["alert"])


@pytest.mark.parametrize("value", ["=cmd|'/c calc'!A1", "+1+1", "-2+3", "@SUM(A1)"])
def test_csv_export_neutralises_spreadsheet_formulas(value):
    """A subject line is free text and the export is opened in Excel."""
    assert _csv_cell(value).startswith("'")


@pytest.mark.parametrize("value", ["Request for supplies", "UDM-OVPA-MED-2026-08-0001", ""])
def test_csv_export_leaves_ordinary_values_alone(value):
    assert _csv_cell(value) == value


# ---------------------------------------------------------------------------
# Forced password change
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_forced_password_change_cannot_be_walked_around(client, users):
    """It was one redirect at sign-in, so a bookmark or the Back button let the
    account carry on using the password the administrator had handed out."""
    user = users["med"]
    type(user).objects.filter(pk=user.pk).update(must_change_password=True)
    client.force_login(user)

    for path in ("/", "/tracking/", "/documents/", "/search/"):
        response = client.get(path)
        assert response.status_code == 302, path
        assert response["Location"] == "/accounts/password/", path


@pytest.mark.django_db
def test_the_password_page_itself_stays_reachable(client, users):
    """A forced change that redirects the change page to itself is a locked account."""
    user = users["med"]
    type(user).objects.filter(pk=user.pk).update(must_change_password=True)
    client.force_login(user)

    assert client.get("/accounts/password/").status_code == 200


@pytest.mark.django_db
def test_normal_accounts_are_not_redirected(client, users):
    client.force_login(users["med"])
    assert client.get("/").status_code == 200
