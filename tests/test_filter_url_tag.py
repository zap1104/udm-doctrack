"""The `filter_url` tag: a filter selection expressed as a link.

This is what lets a multi-select filter be a row of pills rather than a form —
each link already carries the selection that clicking it would produce, so
there is nothing to submit and no Apply button.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from apps.core.templatetags.doctrack import filter_url


def url(query, param, value, multi=False, path="/tracking/"):
    request = RequestFactory().get(f"{path}?{query}" if query else path)
    return filter_url({"request": request}, param, value, multi=multi)


# --- single select ----------------------------------------------------------
def test_it_sets_a_value_on_an_empty_query():
    assert url("", "owner", "mine") == "?owner=mine"


def test_it_replaces_rather_than_appends():
    """A QueryDict yields the last value for a repeated key, so appending would
    work by accident here and break the moment anything read getlist()."""
    assert url("owner=custody", "owner", "mine") == "?owner=mine"


def test_clicking_the_active_value_clears_it():
    assert url("owner=mine", "owner", "mine") == "/tracking/"


def test_the_empty_value_clears_it_too():
    """The "All I can see" pill."""
    assert url("owner=mine", "owner", "") == "/tracking/"


def test_other_parameters_ride_along():
    assert url("scope=overdue", "owner", "mine") == "?scope=overdue&owner=mine"


# --- multi select -----------------------------------------------------------
def test_multi_adds_a_value():
    assert url("", "offices", 3, multi=True) == "?offices=3"


def test_multi_keeps_what_is_already_chosen():
    assert url("offices=3", "offices", 7, multi=True) == "?offices=3&offices=7"


def test_multi_removes_a_value_that_is_already_chosen():
    assert url("offices=3&offices=7", "offices", 3, multi=True) == "?offices=7"


def test_removing_the_last_value_drops_the_parameter():
    assert url("offices=3", "offices", 3, multi=True) == "/tracking/"


def test_multi_keeps_the_active_queue():
    """Without this, filtering by office would drop the reader back to All
    Active — which is what the form's hidden inputs used to prevent."""
    result = url("scope=overdue", "offices", 3, multi=True)

    assert "scope=overdue" in result
    assert "offices=3" in result


def test_an_integer_and_its_string_are_the_same_value():
    """Model pks arrive as ints from the template and as strings from the URL."""
    assert url("offices=3", "offices", 3, multi=True) == "/tracking/"


# --- pagination -------------------------------------------------------------
@pytest.mark.parametrize("multi", [False, True])
def test_changing_a_filter_returns_to_the_first_page(multi):
    """Page four of the old filter is not page four of the new one, and keeping
    it lands the reader on an empty table."""
    result = url("page=4&scope=overdue", "offices" if multi else "owner",
                 3 if multi else "mine", multi=multi)

    assert "page=" not in result
    assert "scope=overdue" in result


def test_it_survives_a_context_with_no_request():
    """Rendered outside a request — a system check, a mail template — it must
    not raise."""
    assert filter_url({}, "owner", "mine") == "?owner=mine"
