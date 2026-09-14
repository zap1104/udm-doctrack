"""The rows-per-page control that every long list carries.

Each list page used to hard-code its own page size — 20 on Tracking, 24 in the
Repository, 25 on Notifications, a flat `[:300]` slice on the audit log — and
none of them let the reader change it. These cover the three things that made
that worth replacing with one shared control: the size has to be honoured, a
bad size must not take the page down, and changing the size must not strand the
reader on a page number that no longer exists.
"""

from __future__ import annotations

import pathlib
from urllib.parse import parse_qs, urlparse

import pytest
from django.test import RequestFactory

from apps.core.pagination import (
    ALL_CAP,
    ALL_TOKEN,
    DEFAULT_PAGE_SIZE,
    PAGE_SIZE_CHOICES,
    paginate,
    resolve_page_size,
)
from apps.core.templatetags.doctrack import page_size_url, pagination_url
from apps.tracking.services import create_draft_record

TRACKING = "/tracking/"


def request_for(query=""):
    return RequestFactory().get(f"/tracking/?{query}" if query else TRACKING)


def params(link):
    """The link's query as a dict.

    Parsed rather than substring-matched: `access_per_page=25` contains the
    literal "page=25", so `"page=2" in link` is true of a link that does not
    carry `page` at all. That is not a hypothetical — it is what the first draft
    of these assertions did.
    """
    return parse_qs(urlparse(link).query)


# --- resolving the parameter ------------------------------------------------
@pytest.mark.parametrize("raw,expected", [("10", 10), ("25", 25), ("50", 50), ("100", 100)])
def test_it_honours_every_offered_size(raw, expected):
    assert resolve_page_size(request_for(f"per_page={raw}")) == (expected, raw)


def test_all_is_a_word_and_resolves_to_the_cap():
    """A word rather than a number, so `?per_page=100000` is simply not in the
    list — the audit log grows for the life of the installation and nothing
    should be able to ask for the whole of it in one response."""
    assert resolve_page_size(request_for("per_page=all")) == (ALL_CAP, ALL_TOKEN)


@pytest.mark.parametrize("raw", ["", "0", "-5", "7", "100000", "abc", "25; DROP TABLE"])
def test_an_unrecognised_size_falls_back_instead_of_raising(raw):
    """These arrive from stale bookmarks and hand-edited URLs. A page size is
    never worth a 500."""
    assert resolve_page_size(request_for(f"per_page={raw}")) == (
        DEFAULT_PAGE_SIZE, str(DEFAULT_PAGE_SIZE),
    )


def test_the_default_is_one_of_the_offered_sizes():
    """The control marks the current size active by matching its token. A
    default outside the list would leave every option looking unselected."""
    assert str(DEFAULT_PAGE_SIZE) in {token for token, _label in PAGE_SIZE_CHOICES}


# --- paging -----------------------------------------------------------------
def test_it_pages_at_the_size_that_was_asked_for():
    context = paginate(request_for("per_page=10"), list(range(95)))

    assert len(context["page_obj"].object_list) == 10
    assert context["page_obj"].paginator.num_pages == 10
    assert context["page_size"] == "10"


def test_two_lists_on_one_page_move_independently():
    """The audit screen carries the system trail and the record-access trail.
    One `page` between them would have meant one Next link moving both."""
    request = request_for("page=3&access_page=1&per_page=10&access_per_page=25")

    system = paginate(request, list(range(500)), page_param="page", size_param="per_page")
    access = paginate(request, list(range(500)), page_param="access_page", size_param="access_per_page")

    assert system["page_obj"].number == 3
    assert access["page_obj"].number == 1
    assert (system["page_size"], access["page_size"]) == ("10", "25")


# --- the links the control renders ------------------------------------------
def test_changing_the_size_returns_to_the_first_page():
    """Page four at ten rows is usually past the end at a hundred, and landing
    the reader on an empty table is the one thing a size change must not do."""
    link = params(page_size_url({"request": request_for("page=4&scope=overdue")}, "100"))

    assert "page" not in link
    assert link["per_page"] == ["100"]
    assert link["scope"] == ["overdue"]


def test_changing_the_size_keeps_every_filter():
    link = params(page_size_url({"request": request_for("scope=inbox&offices=3&offices=7")}, "50"))

    assert link["scope"] == ["inbox"]
    assert link["offices"] == ["3", "7"]


def test_a_second_lists_size_survives_paging_the_first():
    """Both panels' state rides in one query string, so a Next click on one has
    to leave the other exactly where the administrator left it."""
    request = request_for("per_page=10&page=2&access_per_page=all&access_page=4")

    link = params(pagination_url({"request": request}, 3))

    assert link["page"] == ["3"]
    assert link["access_page"] == ["4"]
    assert link["access_per_page"] == ["all"]


def test_sizing_one_list_does_not_reset_the_others_page():
    request = request_for("per_page=10&page=2&access_per_page=25&access_page=4")

    link = params(page_size_url({"request": request}, "50", "per_page", "page"))

    assert link["per_page"] == ["50"]
    assert "page" not in link
    assert link["access_page"] == ["4"]
    assert link["access_per_page"] == ["25"]


def test_the_tags_survive_a_context_with_no_request():
    """Rendered outside a request — a system check, a mail template."""
    assert params(page_size_url({}, "10")) == {"per_page": ["10"]}
    assert params(pagination_url({}, 2)) == {"page": ["2"]}


# --- end to end -------------------------------------------------------------
@pytest.mark.django_db
def test_the_tracking_page_honours_the_size(client, users, memo_type):
    for index in range(12):
        create_draft_record(
            user=users["med"], subject=f"Request {index}", instructions="For action.",
            document_type=memo_type,
        )
    client.force_login(users["med"])

    short = client.get(f"{TRACKING}?owner=mine&per_page=10")
    long = client.get(f"{TRACKING}?owner=mine&per_page=100")

    assert len(short.context["page_obj"].object_list) == 10
    assert short.context["page_obj"].paginator.num_pages == 2
    assert len(long.context["page_obj"].object_list) == 12
    assert long.context["page_size"] == "100"


@pytest.mark.django_db
def test_a_nonsense_size_still_renders_the_page(client, users):
    """`?per_page=abc` is a stale link, not an attack surface for a 500."""
    client.force_login(users["med"])

    response = client.get(f"{TRACKING}?per_page=abc")

    assert response.status_code == 200
    assert response.context["page_size"] == str(DEFAULT_PAGE_SIZE)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url",
    [
        "/tracking/",
        "/documents/",
        "/notifications/",
        "/search/?q=memo",
        "/search/?mode=tracking&q=memo",
    ],
)
def test_every_long_list_offers_the_control(client, users, url):
    """One control, in one place, on every page that lists more than a
    screenful — so there is one thing to learn rather than five."""
    client.force_login(users["med"])

    response = client.get(url)

    assert response.status_code == 200
    assert response.context["page_size_choices"] == PAGE_SIZE_CHOICES
    assert b'class="page-size-options"' in response.content


@pytest.mark.django_db
def test_the_size_control_sits_above_the_list_and_the_steps_below(client, users, memo_type):
    """Two halves, in the two places they are used. How long the page is, and
    where in the results it sits, are wanted before reading the rows; Previous
    and Next are wanted at the end of them. A single bar under the table put the
    control a full page of scrolling away from the reader trying to shorten it."""
    for index in range(12):
        create_draft_record(
            user=users["med"], subject=f"Request {index}", instructions="For action.",
            document_type=memo_type,
        )
    client.force_login(users["med"])

    body = client.get(f"{TRACKING}?owner=mine&per_page=10").content.decode()
    size = body.index("page-controls--size")
    table = body.index("<table")
    steps = body.index("page-controls--steps")

    assert size < table < steps


@pytest.mark.django_db
def test_the_picker_sits_at_the_right_edge(client, users, memo_type):
    """Ranged right, on the same edge as the "Export CSV" and "Advanced
    relevance search" links these lists already carry — that column is where
    this app puts the things you *do* to a list. The picker is last in the row
    so it is the piece actually touching the edge; the count reads to its left,
    being a fact rather than a control."""
    # Enough for a second page, so the position count is there to be ordered.
    for index in range(12):
        create_draft_record(
            user=users["med"], subject=f"Request {index}", instructions="For action.",
            document_type=memo_type,
        )
    client.force_login(users["med"])

    body = client.get(f"{TRACKING}?owner=mine&per_page=10").content.decode()
    row = body[body.index("page-controls--size"):body.index("</div>", body.index("page-controls--size"))]

    assert row.index("page-size-count") < row.index("page-size-options")

    css = pathlib.Path("static/css/doctrack.css").read_text(encoding="utf-8")
    assert ".page-controls--size" in css and "justify-content:flex-end" in css


@pytest.mark.django_db
def test_the_position_count_is_dropped_when_there_is_no_position(client, users, memo_type):
    """"1–3 of 3" under "3 active records matched" is the same fact twice in two
    vocabularies. What the count adds over a total the page has already stated is
    *where in it* this page sits, and on a single page there is no such thing."""
    create_draft_record(
        user=users["med"], subject="Request", instructions="For action.",
        document_type=memo_type,
    )
    client.force_login(users["med"])

    one_page = client.get(f"{TRACKING}?owner=mine").content.decode()
    assert "page-size-options" in one_page
    assert "page-size-count" not in one_page

    for index in range(30):
        create_draft_record(
            user=users["med"], subject=f"Request {index}", instructions="For action.",
            document_type=memo_type,
        )
    many = client.get(f"{TRACKING}?owner=mine&per_page=10").content.decode()
    assert "page-size-count" in many


@pytest.mark.django_db
def test_the_size_control_does_not_borrow_the_filter_rows_word(client, users):
    """The Tracking page already has a filter row labelled "Show" — all I can
    see / mine / in my custody. A second Show right above it would be two
    answers to a question the reader asked once."""
    client.force_login(users["med"])

    body = client.get(TRACKING).content.decode()

    assert 'class="page-size-label"' in body
    assert ">Per page<" in body


@pytest.mark.django_db
def test_the_audit_screen_offers_one_control_per_trail(client, users):
    client.force_login(users["admin"])

    response = client.get("/administration/audit-log/")

    assert response.status_code == 200
    assert response.context["system_page"]["size_param"] == "per_page"
    assert response.context["access_page"]["size_param"] == "access_per_page"
    assert response.content.count(b'class="page-size-options"') == 2
