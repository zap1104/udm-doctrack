"""The Tracking list defines the three offices on every row, above the rows."""

from __future__ import annotations

import pytest


@pytest.mark.django_db
def test_the_three_offices_are_defined_above_the_table(client, users):
    client.force_login(users["sup"])
    body = client.get("/tracking/").content.decode()

    terms = body.index('<dl class="office-terms">')
    assert terms < body.index("<table")
    for term in ("Originating office", "Current office", "Receiving office"):
        assert f"<dt>{term}</dt>" in body, term
    assert 'title="Holds it now: the last office to confirm receipt.">Current office</th>' in body
    assert "Where is the document?" not in body, "the note under the table is gone"
